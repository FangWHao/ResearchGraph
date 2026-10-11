"""已清除原件的无正文缺口与隔离续接身份，同游标原子提交。"""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from rg.store.database import now
from rg.store.record_denials import key


def continuation(db: sqlite3.Connection, file_id: int, offset: int, physical: int) -> int:
    """沿已有缺口续接；显式分区后的新增尾部仍进入独立收件箱。"""
    previous = db.execute(
        "SELECT c.logical_session_pk,s.project_id FROM source_continuations c "
        "LEFT JOIN sessions s ON s.session_pk=c.logical_session_pk "
        "WHERE c.file_instance_id=? AND c.from_offset<=? "
        "ORDER BY c.from_offset DESC,c.continuation_id DESC LIMIT 1",
        (file_id, offset),
    ).fetchone()
    if previous is None:
        return physical
    origin = db.execute("SELECT * FROM sessions WHERE session_pk=?", (physical,)).fetchone()
    project = origin["project_id"] if origin["project_basis"] != "fragment_container" else None
    exists = db.execute(
        "SELECT 1 FROM sessions WHERE session_pk=?", (previous["logical_session_pk"],)
    ).fetchone()
    if exists and previous["project_id"] == project:
        return previous["logical_session_pk"]
    return _append(db, file_id, offset, origin, project)


def _append(
    db: sqlite3.Connection, file_id: int, offset: int, origin: sqlite3.Row, project: str | None
) -> int:
    cursor = db.execute(
        "INSERT INTO sessions(tool,native_session_id,agent_id,cwd,git_branch,tool_version,"
        "project_id,project_basis,physical_session_pk,fragment_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            origin["tool"],
            origin["native_session_id"],
            origin["agent_id"],
            None,
            origin["git_branch"],
            origin["tool_version"],
            project,
            "manual" if project else "unassigned",
            origin["session_pk"],
            "privacy:" + str(uuid4()),
        ),
    )
    session = cursor.lastrowid
    if session is None:
        raise RuntimeError("未获得清除后续接身份")
    db.execute(
        "INSERT INTO source_continuations(file_instance_id,from_offset,logical_session_pk,"
        "recorded_at) VALUES (?,?,?,?)",
        (file_id, offset, session, now()),
    )
    return session


def skip(db: sqlite3.Connection, file_id: int, start: int, end: int, tool: str, sha: str) -> None:
    """仅在调用方已实测命中持久摘要索引后调用，不保存可读原件身份。"""
    db.execute(
        "INSERT INTO source_record_skips(file_instance_id,byte_start,byte_end,record_key,"
        "recorded_at) VALUES (?,?,?,?,?)",
        (file_id, start, end, key(tool, sha), now()),
    )
    origin = db.execute(
        "SELECT s.* FROM sessions s JOIN source_files f USING(session_pk) "
        "WHERE f.file_instance_id=?",
        (file_id,),
    ).fetchone()
    project = origin["project_id"] if origin["project_basis"] != "fragment_container" else None
    _append(db, file_id, end, origin, project)


def advance_metadata(db: sqlite3.Connection, file_id: int, column: str) -> int:
    """元数据游标只越过已提交、字节连续的清除缺口，不读取原件。"""
    if column not in {"chain_observed_offset", "parent_observed_offset"}:
        raise ValueError("未知元数据游标")
    row = db.execute(
        f"SELECT {column},committed_offset FROM source_files WHERE file_instance_id=?", (file_id,)
    ).fetchone()
    original, end = row
    offset = original
    if db.execute("PRAGMA user_version").fetchone()[0] >= 32:
        while gap := db.execute(
            "SELECT byte_end FROM source_record_skips WHERE file_instance_id=? "
            "AND byte_start=? AND byte_end<=?",
            (file_id, offset, end),
        ).fetchone():
            offset = gap[0]
        if offset != original:
            db.execute(
                f"UPDATE source_files SET {column}=? WHERE file_instance_id=? AND {column}=?",
                (offset, file_id, original),
            )
    return offset
