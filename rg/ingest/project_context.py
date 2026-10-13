"""逐物理记录保存目录声明；它是片段归属线索，不是项目确认。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

import zstandard

from rg.ingest.catalog import CLAUDE_RECORDS, CODEX_RECORDS
from rg.store.database import Store, now
from rg.store.lexical_paths import native_absolute
from rg.store.objects import digest

BACKFILL_BATCH = 32
RECORD_BYTES = 262144
BATCH_BYTES = 1048576
MAX_PATH_BYTES = 16384


def saved_cwd(store: Store, event: int) -> str | None:
    """只在同一逻辑片段及物理来源内继承原生目录，未知观测阻断。"""
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 30:
        return None
    row = store.db.execute(
        "SELECT o.state,o.cwd FROM effective_raw_events current "
        "JOIN effective_raw_events r ON r.file_instance_id=current.file_instance_id "
        "AND r.session_pk=current.session_pk AND r.byte_start<=current.byte_start "
        "AND r.record_index=0 LEFT JOIN project_context_observations o "
        "ON o.observation_id=(SELECT max(c.observation_id) "
        "FROM project_context_observations c WHERE c.event_id=r.event_id) "
        "WHERE current.event_id=? AND (o.observation_id IS NULL OR o.state!='absent') "
        "ORDER BY r.byte_start DESC LIMIT 1",
        (event,),
    ).fetchone()
    return native_absolute(row["cwd"]) if row and row["state"] == "declared" else None


def path_value(value: Any) -> PurePath | None:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_BYTES:
        return None
    try:
        if len(value.encode("utf-8")) > MAX_PATH_BYTES or "\0" in value:
            return None
    except UnicodeError:
        return None
    path = PureWindowsPath(value) if PureWindowsPath(value).is_absolute() else PurePosixPath(value)
    return path if path.is_absolute() and ".." not in path.parts else None


def declaration(tool: str, record: dict[str, Any], status: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "state": "absent",
        "source": "none",
        "cwd": None,
        "cwd_sha256": None,
        "reason": "no_context_declaration",
    }
    if status != "parsed":
        value.update(state="unavailable", reason=status)
        return value
    kind = record.get("type")
    known = CODEX_RECORDS if tool == "codex" else CLAUDE_RECORDS | {"system"}
    if not isinstance(kind, str) or kind not in known:
        value.update(state="unavailable", reason="unknown_record")
        return value
    source: Any = record
    if tool == "codex":
        if kind not in {"session_meta", "turn_context"}:
            return value
        value["source"] = "codex_session" if kind == "session_meta" else "codex_turn"
        source = record.get("payload")
    elif "cwd" not in record:
        return value
    else:
        value["source"] = "claude_record"
    if not isinstance(source, dict):
        value.update(state="invalid", reason="invalid_context_payload")
        return value
    if "cwd" not in source:
        value.update(state="invalid", reason="missing_cwd")
        return value
    cwd = source["cwd"]
    if isinstance(cwd, str) and len(cwd) <= MAX_PATH_BYTES:
        value["cwd_sha256"] = digest(cwd.encode("utf-8", errors="surrogatepass"))
    if path_value(cwd) is None:
        value.update(state="invalid", reason="invalid_cwd")
        return value
    value.update(state="declared", cwd=cwd, reason="native_record")
    return value


def observe(
    db: sqlite3.Connection,
    file_id: int,
    event: int,
    tool: str,
    record: dict[str, Any],
    status: str,
) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] < 30:
        return
    append(db, file_id, event, declaration(tool, record, status))


def append(db: sqlite3.Connection, file_id: int, event: int, value: dict[str, Any]) -> None:
    if not db.execute(
        "SELECT 1 FROM raw_events WHERE event_id=? AND file_instance_id=? AND record_index=0",
        (event, file_id),
    ).fetchone():
        raise ValueError("目录观测必须指向本来源物理记录的首事件")
    columns = ("state", "source", "cwd", "cwd_sha256", "reason")
    previous = db.execute(
        "SELECT state,source,cwd,cwd_sha256,reason FROM project_context_observations "
        "WHERE event_id=? ORDER BY observation_id DESC LIMIT 1",
        (event,),
    ).fetchone()
    current = tuple(value[name] for name in columns)
    if previous is not None:
        if tuple(previous) == current:
            return
        if previous[0] != "unavailable" or previous[4] != "saved_record_unavailable":
            raise ValueError("已观测的原生目录声明不能被另一解释覆盖")
    db.execute(
        "INSERT INTO project_context_observations "
        "(event_id,file_instance_id,state,source,cwd,cwd_sha256,reason,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            event,
            file_id,
            value["state"],
            value["source"],
            value["cwd"],
            value["cwd_sha256"],
            value["reason"],
            now(),
        ),
    )


def _saved(store: Store, sha: str, size: int) -> bytes:
    # 压缩大小和解压大小都受限；不因超大回放对象而整块读入内存。
    path = store.objects.path(sha)
    if path.stat().st_size > min(RECORD_BYTES, size + 4096):
        raise ValueError("saved_record_limit")
    with path.open("rb") as stream:
        with zstandard.ZstdDecompressor().stream_reader(stream) as reader:
            data = reader.read(min(size + 1, RECORD_BYTES + 1))
    if len(data) != size or len(data) > RECORD_BYTES or digest(data) != sha:
        raise ValueError("saved_record_integrity")
    return data


def backfill_file(store: Store, file_id: int, *, retry_unavailable: bool = False) -> int:
    """每次只补32条／1 MiB已存记录，不读取原会话路径或改写原件。"""
    if type(file_id) is not int or not 0 < file_id <= 9223372036854775807:
        raise ValueError("来源编号必须为范围内的正整数")
    if type(retry_unavailable) is not bool:
        raise ValueError("补记重试开关必须为布尔值")
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 30:
        return 0
    rows = store.db.execute(
        "SELECT r.event_id,r.object_sha256,r.byte_end-r.byte_start AS size,f.parser,p.status "
        "FROM raw_events r JOIN source_files f USING(file_instance_id) "
        "LEFT JOIN parser_records p ON p.event_id=r.event_id "
        "LEFT JOIN project_context_observations o ON o.observation_id=("
        "SELECT max(c.observation_id) FROM project_context_observations c "
        "WHERE c.event_id=r.event_id) "
        "WHERE r.file_instance_id=? AND r.record_index=0 AND (o.observation_id IS NULL OR "
        "(? AND o.state='unavailable' AND o.reason='saved_record_unavailable')) "
        "ORDER BY r.byte_start LIMIT ?",
        (file_id, retry_unavailable, BACKFILL_BATCH),
    ).fetchall()
    consumed, count = 0, 0
    for row in rows:
        size = row["size"]
        if 0 < size <= RECORD_BYTES and consumed + size + 1 > BATCH_BYTES:
            break
        value = {
            "state": "unavailable",
            "source": "none",
            "cwd": None,
            "cwd_sha256": None,
            "reason": "saved_record_limit",
        }
        if 0 < size <= RECORD_BYTES:
            consumed += size + 1
            try:
                record = json.loads(_saved(store, row["object_sha256"], size))
                status = row["status"] or (
                    "parsed" if isinstance(record, dict) else "invalid_record"
                )
                value = declaration(
                    row["parser"], record if isinstance(record, dict) else {}, status
                )
            except (OSError, ValueError, UnicodeError, zstandard.ZstdError):
                value["reason"] = "saved_record_unavailable"
        with store.transaction() as db:
            append(db, file_id, row["event_id"], value)
        count += 1
    return count
