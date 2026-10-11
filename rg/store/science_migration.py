"""以已核对的闭包迁移当前归属；整个数据库修改在同一事务中提交。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from contextlib import ExitStack
from typing import Any

from rg.store.database import ConflictError, Store, dumps, now
from rg.store.fragment_dependencies import MAX_NODES, Closure, _preview
from rg.store.fragments import FIELDS, _assign, _validate, selection
from rg.store.locking import exclusive
from rg.store.objects import digest
from rg.store.ownership import relation
from rg.store.ownership_schema import OWNED_TABLES, SESSION_TABLES

EXTRA = {"expected_plan_sha256", "include_required_records"}


def _request(body: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    if set(body) != FIELDS | EXTRA:
        raise ValueError("科研迁移请求字段不完整或含未知字段")
    request = {k: body[k] for k in FIELDS}
    request_id, _ = _validate(request)
    if request["project_id"] is None:
        raise ValueError("已有科研记录的迁移需要正式目标项目")
    plan = body["expected_plan_sha256"]
    if (
        not isinstance(plan, str)
        or len(plan) != 64
        or any(c not in "0123456789abcdef" for c in plan)
    ):
        raise ValueError("科研迁移需要有效预检摘要")
    if body["include_required_records"] is not True:
        raise ValueError("科研迁移需要明确纳入全部必需记录和依赖")
    business = {
        k: body[k]
        for k in (
            "session",
            "source",
            "first_event",
            "last_event",
            "project_id",
            "actor",
            "expected_plan_sha256",
            "include_required_records",
        )
    }
    return request, request_id, digest(dumps(business).encode())


def _replay(store: Store, request_id: str, signature: str) -> dict[str, Any] | None:
    row = store.db.execute(
        "SELECT intent_sha256,response_json FROM science_migrations WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row:
        if row[0] != signature:
            raise ConflictError("请求编号已用于不同科研迁移意图")
        return json.loads(row[1]) | {"replayed": True}
    if store.db.execute(
        "SELECT 1 FROM fragment_assignments WHERE request_id=?", (request_id,)
    ).fetchone():
        raise ConflictError("请求编号已用于普通归属")
    from rg.record.events import previous

    previous(store, request_id, "science_migration", signature)
    return None


def migrate(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    request, request_id, signature = _request(body)
    replay = _replay(store, request_id, signature)
    if replay:
        return replay
    # 全来源锁防止闭包另一文件正在扫描；只读取注册路径，不打开原始日志。
    paths = [r[0] for r in store.db.execute("SELECT DISTINCT path FROM source_files ORDER BY path")]
    sessions = [
        r[0] for r in store.db.execute("SELECT session_pk FROM sessions ORDER BY session_pk")
    ]
    with ExitStack() as locks:
        for path in paths:
            locks.enter_context(
                exclusive(
                    store.root / "locks" / "sources" / (digest(path.encode()) + ".lock"),
                    "科研依赖来源正在扫描",
                )
            )
        for name in ("extract-queue", "postprocess", "derive", "artifacts"):
            locks.enter_context(
                exclusive(store.root / "locks" / f"{name}.lock", "科研依赖正在处理")
            )
        for session in sessions:
            locks.enter_context(
                exclusive(store.root / "locks" / "sessions" / f"{session}.lock", "科研依赖正在提取")
            )
        with store.transaction():
            replay = _replay(store, request_id, signature)
            if replay:
                return replay
            _, chosen = selection(store, request, max_records=MAX_NODES)
            plan = _preview(store, request, chosen, 100, 0, body["expected_plan_sha256"])
            if not plan["complete"]:
                raise ConflictError("科研闭包不完整，不能迁移")
            graph = Closure(store.db)
            # 预检已完成预算校验；重复展开仍受相同预算保护。
            store.db.set_progress_handler(graph.interrupt, 1000)
            try:
                for record in chosen:
                    graph.add("raw_events", record["event_id"])
                graph.expand()
            finally:
                store.db.set_progress_handler(None, 0)
            return _apply(store, request, request_id, signature, plan, graph)


def _apply(
    store: Store,
    request: dict[str, Any],
    request_id: str,
    signature: str,
    plan: dict[str, Any],
    graph: Closure,
) -> dict[str, Any]:
    if graph.issues:
        raise ConflictError("科研闭包无法完整核对")
    _idle(graph)
    by_origin: dict[int, list[dict[str, Any]]] = defaultdict(list)
    old_sessions = {
        (table, rowid): store.db.execute(
            f"SELECT session_pk FROM {relation(store.db, table)} WHERE rowid=?", (rowid,)
        ).fetchone()[0]
        for table, rowid in graph.nodes
        if table in SESSION_TABLES
    }
    for table, rowid in graph.nodes:
        if table == "jobs":
            payload = json.loads(
                store.db.execute(
                    "SELECT payload FROM effective_jobs WHERE job_id=?", (rowid,)
                ).fetchone()[0]
            )
            if type(payload.get("session_pk")) is int:
                old_sessions[(table, rowid)] = payload["session_pk"]
    for (table, _event), _row in sorted(graph.rows.items()):
        if table != "raw_events" or _row["record_index"] != 0:
            continue
        by_origin[_row["session_pk"]].append(_row)
    targets: dict[int, int] = {}
    partitions = 0
    for origin, records in sorted(by_origin.items()):
        native = store.db.execute("SELECT * FROM sessions WHERE session_pk=?", (origin,)).fetchone()
        if native["fragment_id"]:
            raise ConflictError("闭包包含非本地原生物理会话；需保留其原归属")
        result = _assign(
            store,
            request | {"session": origin},
            request_id,
            signature,
            chosen_override=records,
            session_targets=targets,
        )
        partitions += result["partitions_created"]
    at = now()
    moved = Counter(table for table, _ in graph.nodes if table != "raw_events")
    result = {
        "request_id": request_id,
        "revision": store.revision() + 1,
        "replayed": False,
        "migration_applied": True,
        "assignments_applied": True,
        "project_id": request["project_id"],
        "session_pk": request["session"],
        "file_instance_id": request["source"],
        "first_event_id": request["first_event"],
        "last_event_id": request["last_event"],
        "plan_sha256": plan["plan_sha256"],
        "records_assigned": sum(map(len, by_origin.values())),
        "selected_records": plan["selected_records"],
        "required_records_included": plan["required_records_total"],
        "partitions_created": partitions,
        "dependencies_migrated": dict(sorted(moved.items())),
        "model_calls": 0,
        "scientific_records_rewritten": 0,
    }
    migration_id = store.db.execute(
        "INSERT INTO science_migrations(request_id,intent_sha256,plan_sha256,"
        "project_id,actor,response_json,recorded_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            request_id,
            signature,
            plan["plan_sha256"],
            request["project_id"],
            request["actor"],
            dumps(result),
            at,
        ),
    ).lastrowid
    for (table, rowid), _row in sorted(graph.rows.items()):
        if table not in OWNED_TABLES and table != "raw_events":
            continue
        session = old_sessions.get((table, rowid))
        if session is not None:
            session = targets.get(session, session)
        store.db.execute(
            "INSERT INTO science_ownership(migration_id,table_name,record_rowid,"
            "project_id,session_pk,recorded_at) "
            "VALUES (?,?,?,?,?,?)",
            (migration_id, table, rowid, request["project_id"], session, at),
        )
    store.db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
    from rg.derive.eligibility import assess

    for table, rowid in sorted(graph.nodes):
        if table == "claims":
            assess(store, rowid)
    if store.db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ConflictError("科研迁移外键校验失败")
    return result


def _idle(graph: Closure) -> None:
    for (table, _), row in graph.rows.items():
        if table == "model_attempts" and row["finished_at"] is None:
            raise ConflictError("科研闭包仍有未结束的模型调用")
        if (
            table in {"jobs", "extraction_queue", "pipeline_queue", "artifact_jobs"}
            and row["state"] == "running"
        ):
            raise ConflictError("科研闭包仍有运行中的任务")
