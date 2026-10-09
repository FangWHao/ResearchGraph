from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.budgets import Budgets
from rg.extract.locator import CUE_PATTERNS, positions, units, windows
from rg.extract.monitor import set_status
from rg.extract.paths import file_paths
from rg.extract.provider import Provider
from rg.extract.redact import model_input, redact
from rg.extract.schemas import PASS1_SCHEMA, PASS2_SCHEMA
from rg.extract.segmenter import Segment, segment, split
from rg.extract.validate import InvalidClaim, persist
from rg.extract.working_set import working_set
from rg.ingest.common import parse
from rg.slim.slimmer import slim_session
from rg.slim.tokens import BudgetExceeded, CountingUnavailable, DailyBudgetExceeded
from rg.store.database import Store, dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest

CUES = re.compile("|".join(pattern.pattern for pattern in CUE_PATTERNS.values()), re.I)


def _permission(store: Store, project_id: str, provider: Provider) -> None:
    row = store.db.execute(
        "SELECT remote_model_allowed FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    if not row or (provider.remote and not row[0]):
        raise PermissionError("本项目未允许远程提取，计数请求也不会发送")


class Worker:
    def __init__(
        self,
        store: Store,
        provider: Provider,
        input_budget: int = 128000,
        output_budget: int = 4000,
        daily_budget: int = 500000,
    ):
        self.budgets = Budgets(input_tokens=input_budget, output_tokens=output_budget)
        self.store = store
        self.provider = provider
        self.input_budget = input_budget
        self.output_budget = output_budget
        self.daily_budget = daily_budget
        self.attempt_ids: dict[int, int] = {}

    def invoke(
        self,
        stage: str,
        item: Segment,
        project_id: str,
        content: str,
        schema: dict[str, Any],
        working_ids: list[str],
        input_limit: int | None = None,
        output_limit: int | None = None,
    ) -> tuple[int, Any]:
        if any(value is not None and value <= 0 for value in (input_limit, output_limit)):
            raise ValueError("调用预算必须为正整数")
        limit = min(
            self.input_budget, input_limit if input_limit is not None else self.input_budget
        )
        output_budget = min(
            self.output_budget, output_limit if output_limit is not None else self.output_budget
        )
        _permission(self.store, project_id, self.provider)
        prompt = Path(__file__).with_name("prompts").joinpath(stage + ".txt").read_text()
        content = model_input(content, stage)
        request = self.provider.request(prompt, content, schema, output_budget)
        prompt_version = digest(prompt.encode())
        schema_version = 2 if stage == "pass1" else 1
        key = digest(
            dumps(
                [
                    stage,
                    project_id,
                    request,
                    self.provider.provider,
                    self.provider.model,
                    item.segment_id,
                    working_ids,
                    prompt_version,
                    schema_version,
                    asdict(self.budgets),
                    limit,
                ]
            ).encode()
        )
        with exclusive(
            self.store.root / "locks" / "jobs" / (key + ".lock"),
            "该提取任务正在运行，请稍后重试",
        ):
            cached = self.store.db.execute(
                "SELECT * FROM extraction_runs WHERE job_key = ?", (key,)
            ).fetchone()
            if (
                cached
                and cached["status"] in {"ok", "validated", "lookup"}
                and cached["output_json"]
            ):
                attempt = self.store.db.execute(
                    "SELECT attempt_id FROM model_attempts WHERE extraction_run_id=? "
                    "AND status='validated' ORDER BY attempt_id DESC LIMIT 1",
                    (cached["extraction_run_id"],),
                ).fetchone()
                if attempt:
                    self.attempt_ids[cached["extraction_run_id"]] = attempt[0]
                return cached["extraction_run_id"], json.loads(cached["output_json"])
            self._check_model_limits()
            # 相同失败输入的重试使用同一 run，并保留失败状态供监控。
            if cached:
                run_id = cached["extraction_run_id"]
            else:
                cursor = self.store.db.execute(
                    "INSERT INTO extraction_runs (job_key, stage, provider, model, prompt_version, "
                    "schema_version, input_event_ids, working_set_ids, budget_tokens, "
                    "status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        key,
                        stage,
                        self.provider.provider,
                        self.provider.model,
                        prompt_version,
                        schema_version,
                        dumps(item.input_ids),
                        dumps(working_ids),
                        limit,
                        now(),
                    ),
                )
                run_id = cursor.lastrowid
                if run_id is None:
                    raise RuntimeError("无法建立提取运行")
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE extraction_runs SET status='pending', error=NULL, output_json=NULL, "
                    "input_tokens=NULL, measured_input_tokens=NULL, output_tokens=NULL, "
                    "stop_reason=NULL WHERE extraction_run_id=?",
                    (run_id,),
                )
                attempt = db.execute(
                    "INSERT INTO model_attempts (extraction_run_id,project_id,stage,provider,model,"
                    "segment_id,input_budget,output_budget,status,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,'pending',?)",
                    (
                        run_id,
                        project_id,
                        stage,
                        self.provider.provider,
                        self.provider.model,
                        item.segment_id,
                        limit,
                        output_budget,
                        now(),
                    ),
                ).lastrowid
                if attempt is None:
                    raise RuntimeError("无法登记调用尝试")
                self.attempt_ids[run_id] = attempt
            try:
                count = self.provider.count_request(request)
                self.store.db.execute(
                    "UPDATE model_attempts SET measured_input_tokens=? WHERE attempt_id=?",
                    (count, attempt),
                )
                self.store.db.execute(
                    "UPDATE extraction_runs SET input_tokens = ?, measured_input_tokens = ? "
                    "WHERE extraction_run_id = ?",
                    (count, count, run_id),
                )
                if count > limit:
                    self._status(run_id, "over_budget", "输入超过预算，未发送")
                    raise BudgetExceeded("输入超过预算")
                header = self.provider.request(prompt, "", schema, output_budget)
                header_key = digest(
                    dumps(
                        ["request-header", self.provider.provider, self.provider.model, header]
                    ).encode()
                )
                header_row = self.store.db.execute(
                    "SELECT tokens FROM token_cache WHERE cache_key = ?", (header_key,)
                ).fetchone()
                header_tokens = header_row[0] if header_row else self.provider.count_request(header)
                if not header_row:
                    self.store.db.execute(
                        "INSERT OR IGNORE INTO token_cache VALUES (?, ?, ?, ?, ?)",
                        (
                            header_key,
                            self.provider.provider,
                            self.provider.model,
                            header_tokens,
                            now(),
                        ),
                    )
                if header_tokens > self.budgets.header_tokens or (
                    stage == "pass1"
                    and self.provider.count_text(content) > self.budgets.content_tokens
                ):
                    self._status(
                        run_id, "over_budget", "指令/schema 或片段内容超过分配预算，未发送"
                    )
                    raise BudgetExceeded("组件预算超限")
                day = now()[:10]
                self.store.db.execute(
                    "UPDATE model_attempts SET usage_day=? WHERE attempt_id=?", (day, attempt)
                )
                reserve = count + output_budget
                with self.store.transaction() as db:
                    db.execute("INSERT OR IGNORE INTO daily_usage VALUES (?, 0)", (day,))
                    used = db.execute(
                        "SELECT reserved_tokens FROM daily_usage WHERE day = ?", (day,)
                    ).fetchone()[0]
                    if used + reserve > self.daily_budget:
                        raise DailyBudgetExceeded("每日 token 上限已达到，暂停队列")
                    db.execute(
                        "UPDATE daily_usage SET reserved_tokens = reserved_tokens + ? "
                        "WHERE day = ?",
                        (reserve, day),
                    )
                self.store.db.execute(
                    "UPDATE model_attempts SET sent=1 WHERE attempt_id=?", (attempt,)
                )
                result = self.provider.generate(request)
                self.store.db.execute(
                    "UPDATE model_attempts SET input_tokens=?, output_tokens=?, stop_reason=? "
                    "WHERE attempt_id=?",
                    (
                        result.input_tokens if result.input_tokens >= 0 else None,
                        result.output_tokens if result.output_tokens >= 0 else None,
                        result.stop_reason,
                        attempt,
                    ),
                )
                if result.input_tokens >= 0 and result.output_tokens >= 0:
                    self.store.db.execute(
                        "UPDATE daily_usage SET reserved_tokens = reserved_tokens - ? + ? "
                        "WHERE day = ?",
                        (reserve, result.input_tokens + result.output_tokens, day),
                    )
                self.store.db.execute(
                    "UPDATE extraction_runs SET input_tokens = ?, output_tokens = ?, "
                    "stop_reason = ? "
                    "WHERE extraction_run_id = ?",
                    (result.input_tokens, result.output_tokens, result.stop_reason, run_id),
                )
                if result.contaminated:
                    self._status(run_id, "contaminated", "执行环境被压缩，结果作废")
                    raise InvalidClaim("污染结果")
                if result.stop_reason in {"length", "max_tokens", "max_output_tokens"}:
                    self._status(run_id, "truncated", "输出截断，结果作废")
                    raise InvalidClaim("截断结果")
                if result.stop_reason not in {"stop", "end_turn", "tool_use"}:
                    self._status(run_id, "failed", "模型未正常完成")
                    raise InvalidClaim("模型未正常完成")
                if result.input_tokens > limit or result.output_tokens > output_budget:
                    self._status(run_id, "over_budget", "实际用量超过预算，结果作废")
                    raise InvalidClaim("实际用量超过预算")
                try:
                    output = json.loads(result.text)
                except (ValueError, TypeError):
                    self._status(run_id, "truncated", "JSON 不完整或无效")
                    raise InvalidClaim("无效 JSON") from None
                if list(Draft202012Validator(schema).iter_errors(output)):
                    self._status(run_id, "invalid", "输出 schema 校验失败", "schema")
                    raise InvalidClaim("输出 schema 校验失败")
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE extraction_runs SET output_json=? WHERE extraction_run_id=?",
                        (dumps(output), run_id),
                    )
                    set_status(self.store, run_id, "validated", attempt_id=attempt)
                return run_id, output
            except CountingUnavailable:
                self._status(run_id, "failed", "计数不可用，未发送生成请求", "counting")
                raise
            except DailyBudgetExceeded:
                self._status(run_id, "budget_paused", "每日预算不足，未发送", "daily_budget")
                raise
            except BudgetExceeded:
                if (
                    self.store.db.execute(
                        "SELECT status FROM extraction_runs WHERE extraction_run_id = ?", (run_id,)
                    ).fetchone()[0]
                    != "over_budget"
                ):
                    self._status(run_id, "over_budget", "每日预算不足，未发送")
                raise
            except RuntimeError:
                self._status(run_id, "failed", "提供方调用失败", "provider")
                raise

    def _status(
        self, run_id: int, status: str, error: str, failure_kind: str | None = None
    ) -> None:
        set_status(self.store, run_id, status, error, failure_kind, self.attempt_ids.get(run_id))

    def _check_model_limits(self) -> None:
        validator = getattr(self.provider, "validate_budget", None)
        if validator is not None:
            validator(self.input_budget, self.output_budget)

    def _coverage(self, item: Segment, stage: str, status: str) -> None:
        with self.store.transaction() as db:
            for event_id in item.ids:
                db.execute(
                    "INSERT OR REPLACE INTO segment_coverage VALUES (?, ?, ?, ?)",
                    (item.segment_id, event_id, stage, status),
                )
                pending = db.execute(
                    "SELECT 1 FROM segment_coverage WHERE event_id = ? "
                    "AND stage = ? AND status = 'pending' LIMIT 1",
                    (event_id, stage),
                ).fetchone()
                db.execute(
                    "UPDATE coverage SET status = ?, segment_id = ? "
                    "WHERE event_id = ? AND stage = ?",
                    ("pending" if pending else status, item.segment_id, event_id, stage),
                )

    def process(self, session_id: int, scope: dict[str, str] | None = None) -> dict[str, int]:
        if type(session_id) is not int or session_id < 1:
            raise ValueError("会话 ID 必须为正整数")
        with exclusive(
            self.store.root / "locks" / "sessions" / f"{session_id}.lock",
            "该会话的提取 worker 正在运行，请稍后重试",
        ):
            return self._process(session_id, scope)

    def _process(self, session_id: int, scope: dict[str, str] | None = None) -> dict[str, int]:
        row = self.store.db.execute(
            "SELECT project_id FROM sessions WHERE session_pk = ?", (session_id,)
        ).fetchone()
        if not row or not row[0]:
            raise ValueError("会话未归属项目，不能猜测归属")
        project_id = row[0]
        _permission(self.store, project_id, self.provider)
        raw_identity = [
            tuple(x)
            for x in self.store.db.execute(
                "SELECT event_id, object_sha256, exclude_reason FROM raw_events "
                "WHERE session_pk = ? ORDER BY seq",
                (session_id,),
            )
        ]
        review_id = self.store.db.execute(
            "SELECT coalesce(max(action_id), 0) FROM review_actions"
        ).fetchone()[0]
        prompts = [
            Path(__file__).with_name("prompts").joinpath(stage + ".txt").read_text()
            for stage in ["pass1", "pass2"]
        ]
        cache_key = digest(
            dumps(
                [
                    session_id,
                    raw_identity,
                    self.provider.provider,
                    self.provider.model,
                    prompts,
                    PASS1_SCHEMA,
                    PASS2_SCHEMA,
                    review_id,
                    self.input_budget,
                    self.output_budget,
                    "overlap-v1",
                    scope,
                ]
            ).encode()
        )
        previous = self.store.db.execute(
            "SELECT segments FROM session_results WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if previous:
            return {"segments": previous[0], "claims": 0, "manual": 0}
        self._check_model_limits()
        events = slim_session(self.store, session_id, self.provider)
        items = segment(events, self.provider, self.budgets.content_tokens)
        for item in items:
            for stage in ["pass1", "pass2"]:
                self._coverage(item, stage, "pending")
        stats = {"segments": len(items), "claims": 0, "manual": 0}
        for item in items:
            self._process_item(item, project_id, stats, 0, scope)
            if stats.get("paused") or stats.get("busy"):
                break
        pending = self.store.db.execute(
            "SELECT count(*) FROM coverage c JOIN raw_events r USING(event_id) "
            "WHERE r.session_pk = ? AND c.status = 'pending'",
            (session_id,),
        ).fetchone()[0]
        if stats["manual"] == 0 and pending == 0 and not stats.get("busy"):
            for queued in self.store.db.execute(
                "SELECT job_id,payload FROM jobs WHERE kind='budget_pause' AND state='queued'"
            ).fetchall():
                context = json.loads(queued["payload"])
                if (
                    context.get("session_pk") == session_id
                    and context.get("provider") == self.provider.provider
                    and context.get("model") == self.provider.model
                ):
                    self.store.db.execute(
                        "UPDATE jobs SET state='done', error=NULL, updated_at=? WHERE job_id=?",
                        (now(), queued["job_id"]),
                    )
            self.store.db.execute(
                "INSERT OR IGNORE INTO session_results VALUES (?, ?, ?, ?)",
                (cache_key, session_id, len(items), now()),
            )
        return stats

    def _process_item(
        self,
        item: Segment,
        project_id: str,
        stats: dict[str, int],
        failures: int,
        scope: dict[str, str] | None = None,
    ) -> None:
        if item.context_gaps:
            payload = dumps({"segment_id": item.segment_id, "gaps": item.context_gaps})
            if not self.store.db.execute(
                "SELECT 1 FROM jobs WHERE kind = 'context_gap' AND payload = ?", (payload,)
            ).fetchone():
                self.store.db.execute(
                    "INSERT INTO jobs (kind, payload, state, attempts, updated_at) "
                    "VALUES ('context_gap', ?, 'done', 0, ?)",
                    (payload, now()),
                )
        run_id: int | None = None
        try:
            input_units = units(self.store, item, self.provider)
            content = dumps(input_units)
            pass1_run, locations = self.invoke(
                "pass1", item, project_id, redact(content.encode()).data.decode(), PASS1_SCHEMA, []
            )
            try:
                located = positions(input_units, locations["candidates"])
            except InvalidClaim:
                self._status(pass1_run, "invalid", "pass1 原文位置校验失败", "citation")
                raise
            with self.store.transaction() as db:
                for location in located:
                    db.execute(
                        "INSERT OR IGNORE INTO candidate_locations VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            pass1_run,
                            location["event_id"],
                            *location["byte_range"],
                            location["cue"],
                            location["source"],
                        ),
                    )
                set_status(self.store, pass1_run, "ok", attempt_id=self.attempt_ids.get(pass1_run))
            self._coverage(item, "pass1", "covered")
            if not located:
                self._coverage(item, "pass2", "excluded:no_candidate")
                return
            content = redact(dumps(input_units).encode()).data.decode()
            working = working_set(
                self.store,
                project_id,
                content,
                self.provider,
                before_event=item.ids[0],
                scope=scope,
                paths=self._paths(item),
                before_offset=item.events[0].get("raw_start"),
            )
            raw_input, ranges, owned_ranges = windows(self.store, item, input_units, located)
            content_windows = {
                "raw_windows": raw_input,
                "slim": json.loads(redact(content.encode()).data.decode()),
            }
            if self.provider.count_text(dumps(content_windows)) > self.budgets.content_tokens:
                raise BudgetExceeded("pass2 原文与瘦身窗口超过片段分配预算")
            for round_index in range(2):
                input_data = {
                    "segment_id": item.segment_id,
                    "raw_windows": raw_input,
                    "slim": json.loads(redact(content.encode()).data.decode()),
                    "working_set": working,
                    "context_gaps": item.context_gaps,
                    "scope_filter": scope,
                }
                run_id, output = self.invoke(
                    "pass2",
                    item,
                    project_id,
                    dumps(input_data),
                    PASS2_SCHEMA,
                    [x["id"] for x in working],
                )
                if output["lookup_terms"] and round_index == 0:
                    set_status(
                        self.store, run_id, "lookup", attempt_id=self.attempt_ids.get(run_id)
                    )
                    working = working_set(
                        self.store,
                        project_id,
                        content,
                        self.provider,
                        output["lookup_terms"],
                        before_event=item.ids[0],
                        scope=scope,
                        paths=self._paths(item),
                        before_offset=item.events[0].get("raw_start"),
                    )
                    continue
                cached = self.store.db.execute(
                    "SELECT status FROM extraction_runs WHERE extraction_run_id = ?", (run_id,)
                ).fetchone()
                if cached[0] != "ok":
                    claim_ids = persist(
                        self.store,
                        output,
                        project_id,
                        run_id,
                        {x["id"] for x in working},
                        set(item.input_ids),
                        item.segment_id,
                        ranges,
                        owned_ranges,
                        scope,
                        attempt_id=self.attempt_ids.get(run_id),
                    )
                    set_status(self.store, run_id, "ok", attempt_id=self.attempt_ids.get(run_id))
                    stats["claims"] += len(claim_ids)
                self._coverage(item, "pass2", "covered")
                return
        except TaskBusy:
            stats["busy"] = 1
            return
        except DailyBudgetExceeded:
            session = self.store.db.execute(
                "SELECT session_pk FROM raw_events WHERE event_id=?", (item.ids[0],)
            ).fetchone()[0]
            context = dumps(
                {
                    "session_pk": session,
                    "project_id": project_id,
                    "provider": self.provider.provider,
                    "model": self.provider.model,
                    "segment_id": item.segment_id,
                    "event_ids": item.ids,
                }
            )
            if not self.store.db.execute(
                "SELECT 1 FROM jobs WHERE kind='budget_pause' AND payload=? AND state='queued'",
                (context,),
            ).fetchone():
                self.store.db.execute(
                    "INSERT INTO jobs (kind,payload,state,attempts,error,updated_at) "
                    "VALUES ('budget_pause', ?, 'queued', 0, 'daily_budget', ?)",
                    (context, now()),
                )
            stats["paused"] = 1
            return
        except (InvalidClaim, BudgetExceeded, RuntimeError) as error:
            if run_id and isinstance(error, InvalidClaim):
                current = self.store.db.execute(
                    "SELECT status FROM extraction_runs WHERE extraction_run_id = ?", (run_id,)
                ).fetchone()[0]
                if current == "validated":
                    self._status(run_id, "invalid", str(error), error.failure_kind)
            children = split(item)
            if failures < 2 and children and not isinstance(error, CountingUnavailable):
                for child in children:
                    for stage in ["pass1", "pass2"]:
                        self._coverage(child, stage, "pending")
                # 父片段的 pending 由子片段替代，不能把失败当作完成。
                self.store.db.execute(
                    "DELETE FROM segment_coverage WHERE segment_id = ?", (item.segment_id,)
                )
                for child in children:
                    self._process_item(child, project_id, stats, failures + 1, scope)
                    if stats.get("paused") or stats.get("busy"):
                        break
                return
            self.store.db.execute(
                "INSERT INTO jobs (kind, payload, state, attempts, error, updated_at) "
                "VALUES ('manual_review', ?, 'failed', ?, ?, ?)",
                (
                    dumps(
                        {
                            "segment_id": item.segment_id,
                            "event_ids": item.ids,
                            "context_event_ids": [e["event_id"] for e in item.context_events],
                            "context_gaps": item.context_gaps,
                        }
                    ),
                    failures + 1,
                    type(error).__name__,
                    now(),
                ),
            )
            stats["manual"] += 1

    def _paths(self, item: Segment) -> set[str]:
        paths: set[str] = set()
        for event in item.events:
            if event["kind"] in {"tool_call", "file_edit"}:
                row = self.store.db.execute(
                    "SELECT f.parser, r.record_index FROM raw_events r JOIN source_files f "
                    "USING(file_instance_id) WHERE r.event_id = ?",
                    (event["event_id"],),
                ).fetchone()
                parsed = parse(row["parser"], json.loads(self.store.raw(event["event_id"])))
                paths.update(file_paths(parsed[row["record_index"]].text))
        return paths
