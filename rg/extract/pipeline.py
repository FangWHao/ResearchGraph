from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from rg.extract.linker import LINK_SCHEMA, link
from rg.extract.overview import OVERVIEW_SCHEMA, ChangedInput, overview, records
from rg.extract.queue import Queue
from rg.extract.worker import Worker
from rg.slim.tokens import DailyBudgetExceeded
from rg.store.database import dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest


class Stages:
    """关联和概览的持久调度；每轮持有系统锁，网络期间不占数据库事务。"""

    def __init__(self, worker: Worker, project: str | None, scope: dict | None, link_limit: int):
        if type(link_limit) is not int or not 1 <= link_limit <= 1000:
            raise ValueError("单项目每轮新关联尝试数需为 1 到 1000")
        self.worker, self.store = worker, worker.store
        self.project, self.scope, self.link_limit = project, scope, link_limit
        self.configs = {}
        for stage, schema in (("link", LINK_SCHEMA), ("overview", OVERVIEW_SCHEMA)):
            prompt = Path(__file__).with_name("prompts").joinpath(stage + ".txt").read_text()
            self.configs[stage] = digest(
                dumps(
                    [
                        stage,
                        worker.provider.provider,
                        worker.provider.model,
                        worker.provider.request(
                            prompt,
                            "",
                            schema,
                            min(worker.output_budget, 1000)
                            if stage == "link"
                            else worker.output_budget,
                        ),
                        worker.input_budget,
                        worker.output_budget,
                        worker.daily_budget,
                        scope,
                        "pipeline-v1",
                    ]
                ).encode()
            )

    def _journal(self, queue: int, kind: str, owner: str | None, details: dict) -> None:
        self.store.db.execute(
            "INSERT INTO pipeline_queue_events(queue_id,kind,owner_id,details,recorded_at) "
            "VALUES (?,?,?,?,?)",
            (queue, kind, owner, dumps(details), now()),
        )

    def _recover(self) -> int:
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT queue_id,owner_id FROM pipeline_queue WHERE state='running'"
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE pipeline_queue SET state='queued',owner_id=NULL,updated_at=? "
                    "WHERE queue_id=?",
                    (now(), row["queue_id"]),
                )
                self._journal(
                    row["queue_id"],
                    "recovered",
                    row["owner_id"],
                    {"proof": "exclusive_process_lock_acquired"},
                )
        return len(rows)

    def _input(self, project: str, stage: str, session: int | None) -> str:
        source: Any = records(self.store, project, session, self.scope)
        if stage == "link":
            source = [
                [r for r in source if r["claim_type"] == "entity_version"],
                [
                    list(r)
                    for r in self.store.db.execute(
                        "SELECT version_id,path,algo,digest,evidence_event_id "
                        "FROM artifact_versions "
                        "WHERE project_id=? ORDER BY version_id",
                        (project,),
                    ).fetchall()
                ],
                [
                    list(r)
                    for r in self.store.db.execute(
                        "SELECT ce.claim_id,es.span_id,es.quote_sha256,r.object_sha256,"
                        "r.exclude_reason,d.alias_id FROM claim_evidence ce "
                        "JOIN claims c USING(claim_id) JOIN entities e "
                        "USING(entity_id) JOIN evidence_spans es USING(span_id) JOIN raw_events r "
                        "USING(event_id) LEFT JOIN dedupe_links d ON d.alias_id=r.event_id "
                        "WHERE e.project_id=? AND c.claim_type='entity_version' "
                        "ORDER BY ce.claim_id,es.span_id",
                        (project,),
                    ).fetchall()
                ],
            ]
        return digest(dumps(source).encode())

    def _retry_plan(self, project: str) -> dict[str, int]:
        return dict(
            self.store.db.execute(
                "SELECT job_key,attempts FROM link_progress WHERE project_id=? AND provider=? "
                "AND model=? AND state='failed'",
                (project, self.worker.provider.provider, self.worker.provider.model),
            ).fetchall()
        )

    def _published(self, task: sqlite3.Row) -> bool:
        if task["stage"] != "overview" or task["state"] != "done":
            return True
        destination = self.destination(task["project_id"], task["session_pk"])
        try:
            result = json.loads(task["result"] or "{}")
            return not destination.is_symlink() and digest(destination.read_bytes()) == result.get(
                "sha256"
            )
        except (OSError, ValueError):
            return False

    def _enqueue(self, project: str, stage: str, session: int | None, retry: bool) -> int:
        config = self.configs[stage]
        target = "project" if session is None else f"session:{session}"
        with self.store.transaction() as db:
            input_key = self._input(project, stage, session)
            task_key = digest(dumps([config, project, target, input_key]).encode())
            previous = db.execute(
                "SELECT * FROM pipeline_queue WHERE task_key=?", (task_key,)
            ).fetchone()
            published = previous is None or self._published(previous)
            retained = previous and not (
                previous["state"] in {"blocked", "cancelled"}
                or not published
                or retry
                and previous["state"] == "partial"
            )
            old = db.execute(
                "SELECT * FROM pipeline_queue WHERE project_id=? AND stage=? AND target_key=? "
                "AND scope IS ? AND state IN ('queued','paused','blocked') AND queue_id!=?",
                (
                    project,
                    stage,
                    target,
                    dumps(self.scope) if self.scope else None,
                    previous["queue_id"] if previous else -1,
                ),
            ).fetchall()
            waits = [
                r
                for r in [*old, *([previous] if previous else [])]
                if r["config_key"] == config
                and r["next_attempt_at"]
                and r["next_attempt_at"] > now()
            ]
            waiting = max(waits, key=lambda r: r["next_attempt_at"]) if waits else None
            ready = waiting["next_attempt_at"] if waiting else None
            reason = waiting["defer_reason"] if waiting else None
            state = "paused" if waiting and waiting["state"] == "paused" else "queued"
            retry_plan = self._retry_plan(project) if retry and stage == "link" else {}
            for row in old:
                if not retry and row["config_key"] == config and row["retry_plan"]:
                    retry_plan.update(json.loads(row["retry_plan"]))
                db.execute(
                    "UPDATE pipeline_queue SET state='cancelled',updated_at=? WHERE queue_id=?",
                    (now(), row["queue_id"]),
                )
                self._journal(row["queue_id"], "superseded", None, {"new_task_key": task_key})
            if retained:
                return 0
            if previous:
                db.execute(
                    "UPDATE pipeline_queue SET state=?,retry_plan=?,error=NULL,defer_reason=?,"
                    "next_attempt_at=?,updated_at=? WHERE queue_id=?",
                    (
                        state,
                        dumps(retry_plan),
                        reason,
                        ready,
                        now(),
                        previous["queue_id"],
                    ),
                )
                self._journal(
                    previous["queue_id"],
                    "resumed",
                    None,
                    {
                        "explicit_retry": retry,
                        "previous_state": previous["state"],
                        "output_missing_or_changed": not published,
                    },
                )
                return 0
            queue = db.execute(
                "INSERT INTO pipeline_queue(task_key,project_id,session_pk,stage,target_key,"
                "config_key,input_key,scope,state,retry_plan,defer_reason,next_attempt_at,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_key,
                    project,
                    session,
                    stage,
                    target,
                    config,
                    input_key,
                    dumps(self.scope) if self.scope else None,
                    state,
                    dumps(retry_plan),
                    reason,
                    ready,
                    now(),
                    now(),
                ),
            ).lastrowid
            if queue is None:
                raise RuntimeError("流水线任务未写入")
            self._journal(queue, "enqueued", None, {"input_key": input_key, "stage": stage})
        return 1

    def _claim(self, queue: int) -> tuple[sqlite3.Row, str] | None:
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM pipeline_queue WHERE queue_id=?", (queue,)).fetchone()
            if (
                row is None
                or row["state"] not in {"queued", "paused"}
                or (row["next_attempt_at"] and row["next_attempt_at"] > now())
            ):
                return None
            permission = db.execute(
                "SELECT remote_model_allowed FROM projects WHERE project_id=?", (row["project_id"],)
            ).fetchone()
            if self.worker.provider.remote and (permission is None or not permission[0]):
                db.execute(
                    "UPDATE pipeline_queue SET state='blocked',defer_reason='remote_disabled',"
                    "updated_at=? WHERE queue_id=?",
                    (now(), queue),
                )
                self._journal(queue, "blocked", None, {"reason": "remote_disabled"})
                return None
            if (
                row["stage"] == "overview"
                and db.execute(
                    "SELECT 1 FROM pipeline_queue WHERE project_id=? "
                    "AND stage='link' AND config_key=? "
                    "AND state IN ('queued','running','paused','blocked') LIMIT 1",
                    (row["project_id"], self.configs["link"]),
                ).fetchone()
            ):
                return None
            if self._input(row["project_id"], row["stage"], row["session_pk"]) != row["input_key"]:
                db.execute(
                    "UPDATE pipeline_queue SET state='cancelled',updated_at=? WHERE queue_id=?",
                    (now(), queue),
                )
                self._journal(queue, "input_changed", None, {})
                return None
            owner = str(uuid.uuid4())
            db.execute(
                "UPDATE pipeline_queue SET state='running',owner_id=?,attempts=attempts+1,"
                "error=NULL,defer_reason=NULL,next_attempt_at=NULL,updated_at=? WHERE queue_id=?",
                (owner, now(), queue),
            )
            self._journal(queue, "claimed", owner, {})
            return row, owner

    def _finish(
        self,
        queue: int,
        owner: str,
        state: str,
        result: dict,
        reason: str | None = None,
        delay: int = 0,
    ) -> None:
        ready = None
        if delay:
            ready = (datetime.fromisoformat(now()) + timedelta(seconds=delay)).isoformat()
        if reason == "daily_budget":
            ready = (
                datetime.fromisoformat(now())
                .astimezone(UTC)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)
            ).isoformat()
        with self.store.transaction() as db:
            updated = db.execute(
                "UPDATE pipeline_queue SET state=?,owner_id=NULL,result=?,error=?,defer_reason=?,"
                "next_attempt_at=?,updated_at=? WHERE queue_id=? "
                "AND owner_id=? AND state='running'",
                (
                    state,
                    dumps(result),
                    reason if state == "partial" else None,
                    reason,
                    ready,
                    now(),
                    queue,
                    owner,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("流水线任务归属已变化，拒绝旧执行器提交")
            self._journal(
                queue,
                "finished",
                owner,
                {"state": state, "result": result, "reason": reason, "next_attempt_at": ready},
            )

    def destination(self, project: str, session: int | None) -> Path:
        scope = digest(dumps(self.scope).encode())
        return (
            self.store.root
            / "overviews"
            / digest(project.encode())
            / scope
            / ("project.md" if session is None else f"session-{session}.md")
        )

    def _execute(self, task: sqlite3.Row) -> tuple[str, dict, str | None, int]:
        project, stage, session = task["project_id"], task["stage"], task["session_pk"]
        if stage == "link":
            retry_plan = json.loads(task["retry_plan"] or "{}")
            result = link(
                self.worker,
                project,
                self.link_limit,
                self.scope,
                bool(retry_plan),
                retry_attempts=retry_plan,
            )
            if result.get("paused"):
                return "paused", result, result.get("pause_reason", "provider_unavailable"), 30
            if result.get("has_more"):
                return "queued", result, "next_link_page", 0
            if self._input(project, stage, session) != task["input_key"]:
                return "cancelled", result, "input_changed", 0
            state = "partial" if result.get("manual") or result.get("skipped_failed") else "done"
            return state, result, "link_review" if state == "partial" else None, 0
        destination = self.destination(project, session)
        result = overview(
            self.worker, project, destination, session, self.scope, expected_input=task["input_key"]
        )
        published_sha = result.pop("artifact_sha256")
        return (
            "done",
            result
            | {
                "output": str(destination.relative_to(self.store.root)),
                "sha256": published_sha,
            },
            None,
            0,
        )

    def _process(self, stage: str, limit: int, counts: Counter) -> None:
        rows = self.store.db.execute(
            "SELECT q.queue_id FROM pipeline_queue q WHERE q.stage=? AND q.config_key=? "
            "AND (? IS NULL OR q.project_id=?) AND q.state IN ('queued','paused') "
            "AND (q.next_attempt_at IS NULL OR q.next_attempt_at<=?) "
            "AND (q.stage!='overview' OR NOT EXISTS (SELECT 1 FROM pipeline_queue l WHERE "
            "l.project_id=q.project_id AND l.stage='link' AND l.config_key=? "
            "AND l.state IN ('queued','running','paused','blocked'))) "
            "ORDER BY q.updated_at,q.queue_id LIMIT ?",
            (
                stage,
                self.configs[stage],
                self.project,
                self.project,
                now(),
                self.configs["link"],
                limit,
            ),
        ).fetchall()
        for row in rows:
            claimed = self._claim(row[0])
            if claimed is None:
                continue
            task, owner = claimed
            try:
                state, result, reason, delay = self._execute(task)
            except DailyBudgetExceeded:
                state, result, reason, delay = "paused", {}, "daily_budget", 0
            except TaskBusy:
                state, result, reason, delay = "queued", {}, "stage_busy", 2
            except ChangedInput:
                state, result, reason, delay = "cancelled", {}, "input_changed", 0
            except PermissionError:
                state, result, reason, delay = "blocked", {}, "remote_disabled", 0
            except RuntimeError as error:
                state, result, reason, delay = "paused", {}, type(error).__name__, 30
            except (OSError, ValueError) as error:
                state, result, reason, delay = "partial", {}, type(error).__name__, 0
            self._finish(row[0], owner, state, result, reason, delay)
            counts[f"{stage}_processed"] += 1
            counts[f"{stage}_{state}"] += 1
            counts["link_claims"] += result.get("claims", 0) if stage == "link" else 0

    def run(self, limit: int, retry_failed: bool = False) -> dict[str, int]:
        Queue.validate_limit(limit)
        with exclusive(self.store.root / "locks" / "postprocess.lock", "流水线调度器正在运行"):
            counts: Counter = Counter()
            counts["pipeline_recovered"] = self._recover()
            projects = self.store.db.execute(
                "SELECT project_id,remote_model_allowed FROM projects WHERE (? IS NULL OR "
                "project_id=?) ORDER BY project_id",
                (self.project, self.project),
            ).fetchall()
            eligible = [
                p["project_id"]
                for p in projects
                if not self.worker.provider.remote or p["remote_model_allowed"]
            ]
            counts["pipeline_remote_disabled"] = len(projects) - len(eligible)
            for project in eligible:
                counts["link_enqueued"] += self._enqueue(project, "link", None, retry_failed)
            self._process("link", limit, counts)
            for project in eligible:
                # 部分关系可进入概览；还在分页、等待额度或服务恢复时不提前发布概览。
                waiting = self.store.db.execute(
                    "SELECT 1 FROM pipeline_queue WHERE project_id=? AND stage='link' "
                    "AND config_key=? AND state IN ('queued','running','paused','blocked') LIMIT 1",
                    (project, self.configs["link"]),
                ).fetchone()
                if waiting:
                    continue
                counts["overview_enqueued"] += self._enqueue(
                    project, "overview", None, retry_failed
                )
                sessions = self.store.db.execute(
                    "SELECT session_pk FROM sessions WHERE project_id=? "
                    "AND tool IN ('claude','codex') "
                    "ORDER BY session_pk",
                    (project,),
                ).fetchall()
                for session in sessions:
                    counts["overview_enqueued"] += self._enqueue(
                        project, "overview", session[0], retry_failed
                    )
            self._process("overview", limit, counts)
            return dict(counts)


class Pipeline(Queue):
    def __init__(
        self,
        worker: Worker,
        project: str | None = None,
        scope: dict[str, str] | None = None,
        link_limit: int = 50,
    ):
        super().__init__(worker, project, scope)
        self.stages = Stages(worker, project, self.scope, link_limit)

    def _run(self, limit: int, retry_failed: bool) -> dict[str, int]:
        result = super()._run(limit, retry_failed)
        try:
            result.update(self.stages.run(limit, retry_failed))
        except TaskBusy:
            result["pipeline_busy"] = 1
        return result
