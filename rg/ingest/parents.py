"""保存 Codex 原生父线程声明；可重建的关系投影不改变项目归属。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from typing import Any

import zstandard

from rg.store.database import Store, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest

UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
BACKFILL_BATCH = 256


def native_id(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and UUID.fullmatch(value) else None


def declaration(payload: Any, identity: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "native_id": None,
        "parent_id": None,
        "other_parent_id": None,
        "state": "invalid",
        "basis": "none",
        "reason": "invalid_payload",
    }
    if not isinstance(payload, dict):
        return result
    child = native_id(payload.get("id"))
    result["native_id"] = child
    if child is None or child != native_id(identity):
        result["reason"] = "invalid_identity" if child is None else "identity_mismatch"
        return result
    top = payload.get("parent_thread_id")
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    legacy = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    depth = spawn.get("depth") if isinstance(spawn, dict) else None
    has_top = top is not None
    has_legacy = isinstance(subagent, dict) and "thread_spawn" in subagent
    result["basis"] = (
        "both"
        if has_top and has_legacy
        else ("top_level" if has_top else "thread_spawn" if has_legacy else "none")
    )
    parent, older = native_id(top), native_id(legacy)
    result["parent_id"] = parent or older
    if parent and older and parent != older:
        result.update(state="conflict", other_parent_id=older, reason="parent_mismatch")
    elif has_top and parent is None:
        result["reason"] = "invalid_top_parent"
    elif has_legacy and (not isinstance(spawn, dict) or older is None):
        result["reason"] = "invalid_spawn_parent"
    elif has_legacy and (type(depth) is not int or not 0 <= depth <= 2147483647):
        result["reason"] = "invalid_spawn_depth"
    elif parent == child or older == child:
        result["reason"] = "self_parent"
    elif result["parent_id"]:
        result.update(state="declared", reason="native_header")
    else:
        result.update(state="not_declared", reason="parent_not_declared")
    return result


def observe(db: sqlite3.Connection, session: int, event: int, record: dict[str, Any]) -> None:
    if record.get("type") != "session_meta":
        return
    identity = db.execute(
        "SELECT native_session_id FROM sessions WHERE session_pk=? AND tool='codex'", (session,)
    ).fetchone()
    if identity is None:
        return
    value = declaration(record.get("payload"), identity[0])
    db.execute(
        "INSERT OR IGNORE INTO session_parent_observations "
        "(event_id,session_pk,native_id,parent_id,other_parent_id,state,basis,reason,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event,
            session,
            value["native_id"],
            value["parent_id"],
            value["other_parent_id"],
            value["state"],
            value["basis"],
            value["reason"],
            now(),
        ),
    )


def checkpoint(db: sqlite3.Connection, event: int) -> bool:
    """只有连续已观察的新记录才能推进；新尾部不会跨过旧来源缺口。"""
    result = db.execute(
        "UPDATE source_files SET parent_observed_offset=(SELECT byte_end FROM raw_events "
        "WHERE event_id=?) WHERE file_instance_id=(SELECT file_instance_id FROM raw_events "
        "WHERE event_id=?) AND parent_observed_offset=(SELECT byte_start FROM raw_events "
        "WHERE event_id=?)",
        (event, event, event),
    )
    return result.rowcount == 1


def _legacy_backfill(store: Store, file_id: int) -> None:
    """扫描旧来源时只读取已存 L0 中可识别的头记录，新增观测时间使用当前时间。"""
    rows = store.db.execute(
        "SELECT r.event_id,r.session_pk FROM raw_events r WHERE r.file_instance_id=? "
        "AND r.kind='meta' AND r.record_index=0 AND NOT EXISTS "
        "(SELECT 1 FROM session_parent_observations o WHERE o.event_id=r.event_id) "
        "AND (r.byte_start=0 OR EXISTS (SELECT 1 FROM parser_records p,json_each(p.types) t "
        "WHERE p.event_id=r.event_id AND json_extract(t.value,'$.category')='record' "
        "AND json_extract(t.value,'$.type_name')='session_meta')) ORDER BY r.event_id",
        (file_id,),
    ).fetchall()
    for row in rows:
        record = json.loads(store.raw(row["event_id"]))
        if isinstance(record, dict):
            with store.transaction() as db:
                observe(db, row["session_pk"], row["event_id"], record)


def backfill_file(store: Store, file_id: int) -> int:
    """每批核对至多256条已存物理记录；不读取原路径、不补造L0时间。"""
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 23:
        _legacy_backfill(store, file_id)
        return 0
    rows = store.db.execute(
        "SELECT r.event_id,r.session_pk,r.byte_start,r.byte_end,"
        "(r.kind='meta' AND (r.byte_start=0 OR EXISTS "
        "(SELECT 1 FROM parser_records p,json_each(p.types) t WHERE p.event_id=r.event_id "
        "AND json_extract(t.value,'$.category')='record' "
        "AND json_extract(t.value,'$.type_name')='session_meta'))) AS eligible,"
        "EXISTS(SELECT 1 FROM session_parent_observations o WHERE o.event_id=r.event_id) "
        "AS observed FROM raw_events r JOIN source_files f USING(file_instance_id) "
        "WHERE f.file_instance_id=? AND f.parser='codex' AND r.record_index=0 "
        "AND r.byte_start>=f.parent_observed_offset AND r.byte_end<=f.committed_offset "
        "ORDER BY r.byte_start LIMIT ?",
        (file_id, BACKFILL_BATCH),
    ).fetchall()
    if not rows:
        return 0
    with store.transaction() as db:
        offset = db.execute(
            "SELECT parent_observed_offset FROM source_files WHERE file_instance_id=?", (file_id,)
        ).fetchone()[0]
        for row in rows:
            if row["byte_start"] != offset:
                raise ValueError("已存原文记录不连续，父线程补记保留缺口")
            if row["eligible"] and not row["observed"]:
                try:
                    raw = store.raw(row["event_id"])
                except zstandard.ZstdError as exc:
                    raise ValueError("已存原文对象损坏，父线程补记保留缺口") from exc
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeError):
                    record = {}
                if isinstance(record, dict):
                    observe(db, row["session_pk"], row["event_id"], record)
            if not checkpoint(db, row["event_id"]):
                raise RuntimeError("父线程补记游标变化，保留原批次")
            offset = row["byte_end"]
    return len(rows)


def backfill_saved(store: Store, *, skip_files: set[int] | None = None) -> dict[str, int]:
    """逐来源恢复已存头；损坏、被锁或原件消失的来源不会阻塞其它来源。"""
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 23:
        return {}
    counts: Counter[str] = Counter()
    rows = store.db.execute(
        "SELECT file_instance_id,path FROM source_files WHERE parser='codex' "
        "AND parent_observed_offset<committed_offset ORDER BY file_instance_id"
    ).fetchall()
    for row in rows:
        if row["file_instance_id"] in (skip_files or set()):
            continue
        try:
            key = digest(row["path"].encode())
            with exclusive(store.root / "locks" / "sources" / (key + ".lock"), "来源正在扫描"):
                counts["parent_backfilled"] += backfill_file(store, row["file_instance_id"])
        except TaskBusy:
            counts["parent_busy"] += 1
        except (OSError, ValueError, RuntimeError, zstandard.ZstdError):
            counts["parent_errors"] += 1
    refresh(store)
    return dict(counts)


def metadata_status(db: sqlite3.Connection) -> tuple[dict[int, bool], bool]:
    rows = db.execute(
        "SELECT session_pk,min(parent_observed_offset>=committed_offset) AS complete "
        "FROM source_files WHERE parser='codex' GROUP BY session_pk"
    ).fetchall()
    status = {row["session_pk"]: bool(row["complete"]) for row in rows}
    return status, all(status.values())


def resolve(db: sqlite3.Connection) -> tuple[dict[int, dict[str, Any]], dict[int, sqlite3.Row]]:
    """在调用方的快照内解读全部声明；不依赖可能尚未恢复的关系投影。"""
    sessions = {r["session_pk"]: r for r in db.execute("SELECT * FROM sessions WHERE tool='codex'")}
    modern = db.execute("PRAGMA user_version").fetchone()[0] >= 23
    complete, catalog_complete = metadata_status(db) if modern else ({}, None)
    facts: dict[int, list[sqlite3.Row]] = {pk: [] for pk in sessions}
    identities: dict[str, set[int]] = {}
    for row in db.execute("SELECT * FROM session_parent_observations ORDER BY event_id"):
        facts[row["session_pk"]].append(row)
        if row["native_id"] == native_id(sessions[row["session_pk"]]["native_session_id"]):
            if row["native_id"] is not None:
                identities.setdefault(row["native_id"], set()).add(row["session_pk"])
    result: dict[int, dict[str, Any]] = {}
    for pk, rows in facts.items():
        parents = {r["parent_id"] for r in rows if r["state"] == "declared"}
        state, parent_pk = "unobserved", None
        if any(r["state"] == "conflict" for r in rows) or len(parents) > 1:
            state = "conflicting"
        elif any(r["state"] == "invalid" for r in rows):
            state = "invalid"
        elif modern and (not complete.get(pk, True) or (parents and not catalog_complete)):
            state = "metadata_incomplete"
        elif parents:
            matches = identities.get(next(iter(parents)), set())
            if len(matches) == 1:
                parent_pk = next(iter(matches))
                state = "linked"
            else:
                state = "ambiguous_parent" if matches else "missing_parent"
        elif rows:
            state = "no_parent_declared"
        result[pk] = {
            "state": state,
            "parent_session_pk": parent_pk,
            "rows": rows,
            "source_metadata_complete": complete.get(pk, True) if modern else None,
            "identity_metadata_complete": catalog_complete,
        }
    edges = {pk: v["parent_session_pk"] for pk, v in result.items() if v["state"] == "linked"}
    visited: set[int] = set()
    for first in edges:
        path: list[int] = []
        positions: dict[int, int] = {}
        node = first
        while node in edges and node not in visited:
            if node in positions:
                for pk in path[positions[node] :]:
                    result[pk].update(state="cycle", parent_session_pk=None)
                break
            positions[node] = len(path)
            path.append(node)
            node = edges[node]
        visited.update(path)
    return result, sessions


def refresh(store: Store) -> None:
    with store.transaction() as db:
        relations, sessions = resolve(db)
        projected = {}
        for pk, value in relations.items():
            parent = value["parent_session_pk"]
            if parent is not None and sessions[parent]["project_id"] != sessions[pk]["project_id"]:
                parent = None
            projected[pk] = parent
        db.executemany(
            "UPDATE sessions SET parent_session_pk=? WHERE session_pk=?",
            [
                (parent, pk)
                for pk, parent in projected.items()
                if sessions[pk]["parent_session_pk"] != parent
            ],
        )
