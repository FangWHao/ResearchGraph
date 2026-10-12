"""明确授权的目录规则只作用于未来、未人工绑定项目的记录。"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from rg.ingest.project_context import path_value
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

MAX_ROOTS = 2000


def policies(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT a.* FROM project_assignment_actions a JOIN active_projects p USING(project_id) "
            "WHERE a.action_id=(SELECT max(n.action_id) FROM project_assignment_actions n "
            "WHERE n.project_id=a.project_id) ORDER BY a.project_id"
        )
    ]


def registry(db: sqlite3.Connection) -> tuple[list[dict[str, Any]], int]:
    total = db.execute("SELECT count(*) FROM effective_source_roots").fetchone()[0]
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT e.root_id,e.project_id,e.host_id,e.path,e.kind,"
            "(e.project_id=r.project_id) AS registered FROM effective_source_roots e "
            "JOIN source_roots r USING(root_id) ORDER BY e.root_id,e.project_id LIMIT ?",
            (MAX_ROOTS + 1,),
        )
    ]
    return rows, total


def signature(db: sqlite3.Connection) -> str:
    rows, total = registry(db)
    return digest(dumps([total, rows, policies(db)]).encode())


def _reason(root: dict[str, Any], rows: list[dict[str, Any]], total: int) -> str:
    if total > MAX_ROOTS:
        return "root_registry_limit"
    if root["host_id"] != "local":
        return "nonlocal_root"
    path = path_value(root["path"])
    if path is None:
        return "invalid_path"
    if not root["registered"]:
        return "borrowed_membership"
    for other in rows:
        candidate = path_value(other["path"])
        if (
            other["host_id"] == "local"
            and other["project_id"] != root["project_id"]
            and candidate is not None
            and type(candidate) is type(path)
            and (path.is_relative_to(candidate) or candidate.is_relative_to(path))
        ):
            return "overlapping_projects"
    return "registered_root"


def listing(
    store: Store,
    project: str,
    limit: int = 100,
    offset: int = 0,
    expected_revision: int | None = None,
    expected_scope_key: str | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError("自动归属分页参数非法")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValueError("自动归属修订非法")
    if expected_scope_key is not None and (
        not isinstance(expected_scope_key, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_scope_key) is None
    ):
        raise ValueError("自动归属摘要非法")
    if bool(expected_revision is not None) != bool(expected_scope_key is not None) or (
        offset and expected_revision is None
    ):
        raise ValueError("续页必须同时带修订和目录策略摘要")
    with store.snapshot():
        if not store.db.execute(
            "SELECT 1 FROM active_projects WHERE project_id=?", (project,)
        ).fetchone():
            raise ValueError("项目不存在")
        revision, scope = store.revision(), signature(store.db)
        if expected_revision is not None and (
            expected_revision != revision or expected_scope_key != scope
        ):
            raise ConflictError("目录或策略已变化，请重新读取第一页")
        roots, total = registry(store.db)
        page = store.db.execute(
            "SELECT e.root_id,e.project_id,e.host_id,e.path,e.kind,"
            "(e.project_id=r.project_id) AS registered FROM effective_source_roots e "
            "JOIN source_roots r USING(root_id) WHERE e.project_id=? "
            "ORDER BY e.root_id LIMIT ? OFFSET ?",
            (project, limit, offset),
        ).fetchall()
        values = []
        for row in page:
            value = dict(row)
            reason = _reason(value, roots, total)
            value.pop("registered")
            values.append(value | {"applicable": reason == "registered_root", "reason": reason})
        count = store.db.execute(
            "SELECT count(*) FROM effective_source_roots WHERE project_id=?", (project,)
        ).fetchone()[0]
        policy = next((p for p in policies(store.db) if p["project_id"] == project), None)
        return {
            "available": True,
            "project_id": project,
            "revision": revision,
            "scope_key": scope,
            "enabled": bool(policy and policy["enabled"]),
            "policy": policy,
            "boundary": "future_unassigned_records",
            "future_roots": True,
            "roots": values,
            "total": count,
            "next_offset": offset + len(page) if offset + len(page) < count else None,
        }


def configure(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    if set(body) != {"project_id", "enabled", "actor", "expected_revision", "expected_scope_key"}:
        raise ValueError("自动归属请求字段无效")
    if not isinstance(body["project_id"], str) or not body["project_id"]:
        raise ValueError("项目参数非法")
    actor = body["actor"]
    if (
        not isinstance(actor, str)
        or not actor.startswith("human:")
        or not actor[6:].strip()
        or len(actor) > 120
        or "\0" in actor
        or type(body["enabled"]) is not bool
    ):
        raise ValueError("自动归属需要明确人工操作者和布尔开关")
    with store.transaction() as db:
        current = listing(
            store,
            body["project_id"],
            expected_revision=body["expected_revision"],
            expected_scope_key=body["expected_scope_key"],
        )
        if body["expected_revision"] is None or body["expected_scope_key"] is None:
            raise ValueError("保存必须核对当前修订和目录摘要")
        if current["enabled"] != body["enabled"]:
            boundary = db.execute("SELECT coalesce(max(event_id),0) FROM raw_events").fetchone()[0]
            db.execute(
                "INSERT INTO project_assignment_actions(project_id,enabled,actor,after_event_id,"
                "recorded_at) VALUES (?,?,?,?,?)",
                (body["project_id"], int(body["enabled"]), actor, boundary, now()),
            )
        return listing(store, body["project_id"])
