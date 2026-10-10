from __future__ import annotations

import math
import sqlite3
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from rg.extract.worker import Worker
from rg.record.schema import validate_scope
from rg.store.database import Store, dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest
from rg.store.privacy import read as privacy_policy

STATES = ("queued", "running", "done", "partial", "paused", "blocked", "cancelled")


def sessions(
    worker: Worker, project: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    store = worker.store
    if (
        project is not None
        and not store.db.execute(
            "SELECT 1 FROM projects WHERE project_id=?",
            (project,),
        ).fetchone()
    ):
        raise ValueError("项目不存在")
    rows = store.db.execute(
        "SELECT s.session_pk,s.project_id,max(r.event_id) AS max_event_id,p.remote_model_allowed "
        "FROM sessions s JOIN raw_events r USING(session_pk) LEFT JOIN projects "
        "p USING(project_id) "
        "WHERE s.tool IN ('claude','codex') AND (? IS NULL OR s.project_id=?) "
        "GROUP BY s.session_pk ORDER BY s.session_pk",
        (project, project),
    ).fetchall()
    eligible = []
    skipped: Counter[str] = Counter()
    for row in rows:
        if row["project_id"] is None:
            skipped["unassigned"] += 1
        elif worker.provider.remote and not row["remote_model_allowed"]:
            skipped["remote_disabled"] += 1
        else:
            eligible.append(dict(row))
    return eligible, dict(skipped)


def journal(
    store: Store, queue: int, kind: str, owner: str | None, details: dict[str, Any]
) -> None:
    store.db.execute(
        "INSERT INTO extraction_queue_events(queue_id,owner_id,kind,details,recorded_at) "
        "VALUES (?,?,?,?,?)",
        (queue, owner, kind, dumps(details), now()),
    )


class Queue:
    """持有数据目录系统锁的单调度器；网络期间不持有数据库事务。"""

    def __init__(
        self, worker: Worker, project: str | None = None, scope: dict[str, str] | None = None
    ):
        self.worker, self.store, self.project = worker, worker.store, project
        if type(worker.daily_budget) is not int or worker.daily_budget < 1:
            raise ValueError("每日额度须为正整数")
        if (
            project is not None
            and not self.store.db.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project,)
            ).fetchone()
        ):
            raise ValueError("项目不存在")
        self.scope = validate_scope(scope)
        self.config = digest(
            dumps([worker.configuration(self.scope), worker.daily_budget, "queue-v1"]).encode()
        )

    def _recover(self) -> int:
        # 仅在取得 extract-queue.lock 后调用；running 的记录本身不是进程存活证明。
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT queue_id,owner_id FROM extraction_queue WHERE state='running'"
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE extraction_queue SET "
                    "state='queued',owner_id=NULL,updated_at=? WHERE queue_id=?",
                    (now(), row["queue_id"]),
                )
                journal(
                    self.store,
                    row["queue_id"],
                    "recovered",
                    row["owner_id"],
                    {"proof": "exclusive_process_lock_acquired"},
                )
        return len(rows)

    def _input(self, project: str, session: int, maximum: int) -> str:
        snapshot = self.store.db.execute(
            "SELECT r.event_id,r.object_sha256,r.exclude_reason,r.seq,r.kind,r.call_id,"
            "d.alias_id FROM raw_events r LEFT JOIN dedupe_links d ON d.alias_id=r.event_id "
            "WHERE r.session_pk=? AND r.event_id<=? ORDER BY r.seq",
            (session, maximum),
        ).fetchall()
        identity = digest(dumps([list(value) for value in snapshot]).encode())
        policy = privacy_policy(self.store, project)
        return digest(dumps([identity, policy.identity]).encode()) if policy.rule_id else identity

    def _discover(self, retry_failed: bool) -> dict[str, int]:
        rows, skipped = sessions(self.worker, self.project)
        created = 0
        with self.store.transaction() as db:
            for row in rows:
                input_key = self._input(row["project_id"], row["session_pk"], row["max_event_id"])
                task_key = digest(
                    dumps([self.config, row["project_id"], row["session_pk"], input_key]).encode()
                )
                previous = db.execute(
                    "SELECT * FROM extraction_queue WHERE task_key=?", (task_key,)
                ).fetchone()
                if previous and not (
                    previous["state"] in {"blocked", "cancelled"}
                    or retry_failed
                    and previous["state"] == "partial"
                ):
                    continue
                # 新内容替代尚未执行的旧任务；日额度的等待时间不能因追加事件被绕过。
                old = db.execute(
                    "SELECT * FROM extraction_queue WHERE session_pk=? "
                    "AND state IN ('queued','paused','blocked') AND queue_id!=?",
                    (row["session_pk"], previous["queue_id"] if previous else -1),
                ).fetchall()
                waits = [
                    value["next_attempt_at"]
                    for value in old
                    if value["config_key"] == self.config
                    and value["defer_reason"] == "daily_budget"
                    and value["next_attempt_at"]
                ]
                if (
                    previous
                    and previous["defer_reason"] == "daily_budget"
                    and previous["next_attempt_at"]
                ):
                    waits.append(previous["next_attempt_at"])
                ready = max(waits) if waits else None
                state = "paused" if ready and ready > now() else "queued"
                for value in old:
                    db.execute(
                        "UPDATE extraction_queue SET "
                        "state='cancelled',updated_at=? WHERE queue_id=?",
                        (now(), value["queue_id"]),
                    )
                    journal(
                        self.store,
                        value["queue_id"],
                        "superseded",
                        None,
                        {"new_task_key": task_key},
                    )
                if previous:
                    db.execute(
                        "UPDATE extraction_queue SET state=?,error=NULL,defer_reason=?,"
                        "next_attempt_at=?,updated_at=? WHERE queue_id=?",
                        (
                            state,
                            "daily_budget" if state == "paused" else None,
                            ready,
                            now(),
                            previous["queue_id"],
                        ),
                    )
                    journal(
                        self.store,
                        previous["queue_id"],
                        "resumed",
                        None,
                        {"explicit_retry": retry_failed, "previous_state": previous["state"]},
                    )
                    continue
                queue_id = db.execute(
                    "INSERT INTO "
                    "extraction_queue(task_key,session_pk,project_id,config_key,input_key,"
                    "max_event_id,provider,model,scope,state,defer_reason,"
                    "next_attempt_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_key,
                        row["session_pk"],
                        row["project_id"],
                        self.config,
                        input_key,
                        row["max_event_id"],
                        self.worker.provider.provider,
                        self.worker.provider.model,
                        dumps(self.scope) if self.scope is not None else None,
                        state,
                        "daily_budget" if state == "paused" else None,
                        ready,
                        now(),
                        now(),
                    ),
                ).lastrowid
                if queue_id is None:
                    raise RuntimeError("提取任务写入失败")
                journal(
                    self.store, queue_id, "enqueued", None, {"max_event_id": row["max_event_id"]}
                )
                created += 1
        return {"enqueued": created, "eligible_sessions": len(rows), **skipped}

    def _claim(self, queue_id: int) -> tuple[sqlite3.Row, str] | None:
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM extraction_queue WHERE queue_id=?", (queue_id,)
            ).fetchone()
            if (
                row is None
                or row["state"] not in {"queued", "paused"}
                or row["next_attempt_at"]
                and row["next_attempt_at"] > now()
            ):
                return None
            permission = db.execute(
                "SELECT remote_model_allowed FROM projects WHERE project_id=?", (row["project_id"],)
            ).fetchone()
            if self.worker.provider.remote and (not permission or not permission[0]):
                db.execute(
                    "UPDATE extraction_queue SET "
                    "state='blocked',defer_reason='remote_disabled',updated_at=? WHERE queue_id=?",
                    (now(), queue_id),
                )
                journal(self.store, queue_id, "blocked", None, {"reason": "remote_disabled"})
                return None
            if (
                self._input(row["project_id"], row["session_pk"], row["max_event_id"])
                != row["input_key"]
            ):
                db.execute(
                    "UPDATE extraction_queue SET state='cancelled',defer_reason='input_changed',"
                    "updated_at=? WHERE queue_id=?",
                    (now(), queue_id),
                )
                journal(self.store, queue_id, "cancelled", None, {"reason": "input_changed"})
                return None
            owner = str(uuid.uuid4())
            db.execute(
                "UPDATE extraction_queue SET "
                "state='running',owner_id=?,attempts=attempts+1,error=NULL,"
                "defer_reason=NULL,next_attempt_at=NULL,updated_at=? WHERE queue_id=?",
                (owner, now(), queue_id),
            )
            journal(self.store, queue_id, "claimed", owner, {"max_event_id": row["max_event_id"]})
            return row, owner

    def _finish(
        self, queue: int, owner: str, state: str, result: dict[str, int], reason: str | None = None
    ) -> None:
        ready = None
        if state == "paused":
            ready = (
                datetime.fromisoformat(now())
                .astimezone(UTC)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)
            ).isoformat()
        with self.store.transaction() as db:
            changed = db.execute(
                "UPDATE extraction_queue SET "
                "state=?,owner_id=NULL,result=?,error=?,defer_reason=?,"
                "next_attempt_at=?,updated_at=? WHERE queue_id=? AND owner_id=? "
                "AND state='running'",
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
            if changed.rowcount != 1:
                raise RuntimeError("提取任务归属已变化，拒绝提交旧执行器结果")
            journal(
                self.store,
                queue,
                "finished",
                owner,
                {"state": state, "result": result, "reason": reason, "next_attempt_at": ready},
            )

    @staticmethod
    def validate_limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("单轮提取任务数需为 1 到 200")

    def _run(self, limit: int, retry_failed: bool) -> dict[str, int]:
        self.validate_limit(limit)
        recovered = self._recover()
        counts: Counter[str] = Counter(self._discover(retry_failed))
        counts["recovered"] = recovered
        rows = self.store.db.execute(
            "SELECT queue_id FROM extraction_queue WHERE config_key=? AND (? IS "
            "NULL OR project_id=?) "
            "AND state IN ('queued','paused') AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
            "ORDER BY updated_at,queue_id LIMIT ?",
            (self.config, self.project, self.project, now(), limit),
        ).fetchall()
        for row in rows:
            policy = privacy_policy(
                self.store,
                self.store.db.execute(
                    "SELECT project_id FROM extraction_queue WHERE queue_id=?", (row[0],)
                ).fetchone()[0],
            )
            claimed = self._claim(row[0])
            if claimed is None:
                continue
            task, owner = claimed
            try:
                result = self.worker.process(
                    task["session_pk"],
                    self.scope,
                    retry_failed,
                    max_event_id=task["max_event_id"],
                    expected_privacy=policy.identity,
                )
                state = (
                    "paused"
                    if result.get("paused")
                    else "queued"
                    if result.get("busy")
                    else "partial"
                    if result.get("manual")
                    else "done"
                )
                reason = (
                    "daily_budget"
                    if state == "paused"
                    else "session_busy"
                    if state == "queued"
                    else None
                )
            except TaskBusy:
                result = {"busy": 1}
                state = "queued"
                reason = "session_busy"
            except PermissionError:
                result = {}
                state = "blocked"
                reason = "remote_disabled"
            except (OSError, ValueError, RuntimeError) as error:
                # 错误只存类名，提供方异常不把请求或凭据带进队列。
                result = {}
                state = "partial"
                reason = type(error).__name__
            # 所有权提交失败不能被当作提供方失败再提交一次。
            self._finish(row[0], owner, state, result, reason)
            counts["processed"] += 1
            counts[state] += 1
            counts["claims"] += result.get("claims", 0)
        return dict(counts)

    def run(self, limit: int = 20, retry_failed: bool = False) -> dict[str, int]:
        with exclusive(
            self.store.root / "locks" / "extract-queue.lock", "该数据目录已有提取调度器"
        ):
            return self._run(limit, retry_failed)

    def watch(
        self, interval: float = 2, limit: int = 20, retry_failed: bool = False
    ) -> Iterator[dict[str, Any]]:
        from rg.artifacts.service import Service
        from rg.ingest.watch import cycle

        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("轮询间隔必须为有限正数")
        self.validate_limit(limit)
        with (
            exclusive(self.store.root / "locks" / "extract-queue.lock", "该数据目录已有提取调度器"),
            Service(self.store) as service,
        ):
            first = True
            while True:
                try:
                    scanned = cycle(self.store)
                except TaskBusy:
                    scanned = {"busy": 1}
                service.tick()
                yield {"scan": scanned, "extract": self._run(limit, retry_failed and first)}
                first = False
                time.sleep(interval)
