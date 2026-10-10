from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rg.ingest.common import Parsed, injection, parse
from rg.store.database import Store, now
from rg.store.locking import exclusive
from rg.store.objects import digest

PARSER_VERSION = "2"


def _session(store: Store, path: Path, tool: str, project_id: str | None) -> int | None:
    with path.open("rb") as stream:
        first = stream.readline()
    try:
        record = json.loads(first)
    except (ValueError, UnicodeError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    native = (
        payload.get("id")
        if tool == "codex" and record.get("type") == "session_meta"
        else record.get("sessionId")
    ) or path.stem
    agent = record.get("agentId") or (path.stem if "subagents" in path.parts else "")
    cwd = payload.get("cwd") if tool == "codex" else record.get("cwd")
    native = native if isinstance(native, str) and native else path.stem
    agent = agent if isinstance(agent, str) else ""
    cwd = cwd if isinstance(cwd, str) else None
    from rg.store.clear_denials import denied

    if denied(
        store.root,
        project=project_id,
        tool=tool,
        native=native,
        paths=[str(path), *([cwd] if cwd else [])],
    ):
        return None
    with store.transaction() as db:
        db.execute(
            "INSERT OR IGNORE INTO sessions "
            "(tool, native_session_id, agent_id, cwd, project_id, project_basis) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tool, native, agent, cwd, project_id, "manual" if project_id else "unassigned"),
        )
        row = db.execute(
            "SELECT session_pk, project_id FROM sessions "
            "WHERE tool = ? AND native_session_id = ? AND agent_id = ?",
            (tool, native, agent),
        ).fetchone()
        if row["project_id"] and project_id and row["project_id"] != project_id:
            raise ValueError("会话已属于其他项目，不能静默改归属")
        if project_id and row["project_id"] is None:
            db.execute(
                "UPDATE sessions SET project_id = ?, project_basis = 'manual' WHERE session_pk = ?",
                (project_id, row["session_pk"]),
            )
        if agent:
            parent = db.execute(
                "SELECT session_pk FROM sessions WHERE tool = ? "
                "AND native_session_id = ? AND agent_id = ''",
                (tool, native),
            ).fetchone()
            if parent:
                db.execute(
                    "UPDATE sessions SET parent_session_pk = ? WHERE session_pk = ?",
                    (parent[0], row["session_pk"]),
                )
        else:
            db.execute(
                "UPDATE sessions SET parent_session_pk = ? WHERE tool = ? "
                "AND native_session_id = ? AND agent_id != ''",
                (row["session_pk"], tool, native),
            )
        return row["session_pk"]


def _instance(store: Store, path: Path, tool: str, session: int) -> sqlite3.Row:
    stat = path.stat()
    token = f"{stat.st_dev}:{stat.st_ino}"
    previous = store.db.execute(
        "SELECT * FROM source_files WHERE path = ? "
        "AND status = 'active' ORDER BY file_instance_id DESC LIMIT 1",
        (str(path),),
    ).fetchone()
    changed = False
    if previous:
        with path.open("rb") as stream:
            prefix = stream.read(previous["prefix_length"])
        changed = (
            stat.st_size < previous["committed_offset"]
            or digest(prefix) != previous["prefix_sha256"]
            or token != previous["source_token"]
        )
        if not changed:
            return previous
    with store.transaction() as db:
        if previous:
            status = "truncated" if stat.st_size < previous["committed_offset"] else "rotated"
            db.execute(
                "UPDATE source_files SET status = ? WHERE file_instance_id = ?",
                (status, previous["file_instance_id"]),
            )
        cursor = db.execute(
            "INSERT INTO source_files "
            "(session_pk, path, prefix_sha256, parser, parser_version, first_seen, source_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session, str(path), digest(b""), tool, PARSER_VERSION, now(), token),
        )
        return db.execute(
            "SELECT * FROM source_files WHERE file_instance_id = ?", (cursor.lastrowid,)
        ).fetchone()


def _alias(
    db: sqlite3.Connection,
    event: Parsed,
    session: int,
    file_id: int,
    line_sha: str,
    record: dict[str, Any],
) -> int | None:
    if event.native_id:
        matches = db.execute(
            "SELECT r.event_id, r.object_sha256, r.record_index, r.tool_name, f.parser "
            "FROM raw_events r JOIN source_files f USING(file_instance_id) "
            "WHERE r.native_id = ? AND r.kind = ? AND r.alias_of IS NULL "
            "AND f.parser = (SELECT tool FROM sessions WHERE session_pk = ?) "
            "AND (f.parser='claude' OR r.session_pk=?)",
            (event.native_id, event.kind, session, session),
        ).fetchall()
        for match in matches:
            # UUID 才是 resume 的身份键；字段顺序、父链等变化不应产生新正文。
            # 内容冲突则保留两条，不能把未知变动静默吞掉。
            from rg.store.objects import ObjectStore

            root = Path(db.execute("PRAGMA database_list").fetchone()[2]).parent / "objects"
            old_record = json.loads(ObjectStore(root).get(match["object_sha256"]))
            old = parse(match["parser"], old_record)[match["record_index"]]
            if event.kind == "tool_result" and (
                old_record.get("toolUseResult") != record.get("toolUseResult")
                or old_record.get("payload") != record.get("payload")
                or old_record.get("message", {}).get("content")
                != record.get("message", {}).get("content")
            ):
                continue
            if (old.text, old.kind, old.tool_name or match["tool_name"], old.call_id, old.role) == (
                event.text,
                event.kind,
                event.tool_name,
                event.call_id,
                event.role,
            ):
                return match["event_id"]
        return None
    else:
        row = db.execute(
            "SELECT event_id FROM raw_events WHERE session_pk = ? "
            "AND file_instance_id != ? AND line_sha256 = ? AND kind = ? "
            "AND alias_of IS NULL LIMIT 1",
            (session, file_id, line_sha, event.kind),
        ).fetchone()
    return row[0] if row else None


def _mirror(
    db: sqlite3.Connection, session: int, event_id: int, record: dict[str, Any], event: Parsed
) -> None:
    if event.kind not in {"user_msg", "assistant_msg"}:
        return
    source = record.get("type")
    if source not in {"event_msg", "response_item"}:
        return
    other = "response_item" if source == "event_msg" else "event_msg"
    # 相同内容的不同回合不能合并；只配对尚未使用的相反来源镜像。
    signature = digest((event.kind + "\0" + event.text).encode())
    candidates = db.execute(
        "SELECT event_id, source FROM message_fingerprints WHERE session_pk = ? "
        "AND signature = ? AND source = ? AND paired = 0 ORDER BY event_id LIMIT 1",
        (session, signature, other),
    ).fetchone()
    db.execute(
        "INSERT INTO message_fingerprints VALUES (?, ?, ?, ?, ?)",
        (event_id, session, signature, source, int(candidates is not None)),
    )
    if candidates:
        alias = event_id if source == "event_msg" else candidates[0]
        canonical = candidates[0] if source == "event_msg" else event_id
        db.execute(
            "INSERT OR IGNORE INTO dedupe_links VALUES (?, ?, ?)", (alias, canonical, "mirror")
        )
        db.execute(
            "UPDATE message_fingerprints SET paired = 1 WHERE event_id = ?", (candidates[0],)
        )


def scan_file(
    store: Store,
    path: Path,
    tool: str,
    project_id: str | None = None,
    fault: Callable[[], None] | None = None,
) -> dict[str, int]:
    if tool not in {"claude", "codex"}:
        raise ValueError("来源必须为 claude 或 codex")
    path = path.resolve()
    key = digest(str(path).encode())
    with exclusive(store.root / "locks" / "sources" / (key + ".lock"), "该来源文件正在扫描"):
        return _scan_file(store, path, tool, project_id, fault)


def _scan_file(
    store: Store, path: Path, tool: str, project_id: str | None, fault: Callable[[], None] | None
) -> dict[str, int]:
    session = _session(store, path, tool, project_id)
    if session is None:
        return {"privacy_blocked": 1}
    instance = _instance(store, path, tool, session)
    file_id, offset = instance["file_instance_id"], instance["committed_offset"]
    counts: Counter[str] = Counter()
    with path.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                with store.transaction() as db:
                    db.execute(
                        "UPDATE source_files SET tail_fragment = ?, last_read = ? "
                        "WHERE file_instance_id = ?",
                        (line, now(), file_id),
                    )
                counts["tail_fragments"] += 1
                break
            sha = store.objects.put(line)
            status = "parsed"
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                record = {}
                status = "bad_json"
            if not isinstance(record, dict):
                record = {}
                status = "invalid_record"
            try:
                events = parse(tool, record) if status == "parsed" else []
            except (ValueError, UnicodeError, TypeError, AttributeError):
                status = "parser_error"
                events = []
            if status != "parsed":
                events = [Parsed("unknown", excluded="bad_json")]
            end = offset + len(line)
            with store.transaction() as db:
                event_ids: list[int] = []
                seq = db.execute(
                    "SELECT coalesce(max(seq), 0) FROM raw_events WHERE session_pk = ?", (session,)
                ).fetchone()[0]
                for index, event in enumerate(events):
                    if event.call_id and event.kind == "tool_result":
                        call = db.execute(
                            "SELECT tool_name FROM raw_events WHERE session_pk = ? "
                            "AND call_id = ? AND tool_name IS NOT NULL "
                            "ORDER BY event_id DESC LIMIT 1",
                            (session, event.call_id),
                        ).fetchone()
                        if call and call[0]:
                            event.tool_name = call[0]
                            injection(event)
                    alias = _alias(db, event, session, file_id, sha, record)
                    excluded = "duplicate" if alias else event.excluded
                    seq += 1
                    cursor = db.execute(
                        "INSERT INTO raw_events "
                        "(session_pk, file_instance_id, record_index, byte_start, byte_end, "
                        "object_sha256, native_id, seq, kind, role, tool_name, call_id, model, "
                        "occurred_at, recorded_at, line_sha256, alias_of, exclude_reason) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            session,
                            file_id,
                            index,
                            offset,
                            end,
                            sha,
                            event.native_id,
                            seq,
                            event.kind,
                            event.role,
                            event.tool_name,
                            event.call_id,
                            event.model,
                            event.timestamp,
                            now(),
                            sha,
                            alias,
                            excluded,
                        ),
                    )
                    event_id = cursor.lastrowid
                    if event_id is None:
                        raise RuntimeError("未获得事件 ID")
                    event_ids.append(event_id)
                    if not alias and not event.excluded:
                        _mirror(db, session, event_id, record, event)
                        if event.kind in {"user_msg", "assistant_msg", "plan_update"}:
                            db.execute(
                                "INSERT INTO event_search VALUES (?, ?)", (event_id, event.text)
                            )
                    db.execute(
                        "DELETE FROM event_search WHERE event_id IN "
                        "(SELECT alias_id FROM dedupe_links)"
                    )
                    for stage in ["slim", "pass1", "pass2"]:
                        db.execute(
                            "INSERT INTO coverage VALUES (?, ?, NULL, ?)",
                            (event_id, stage, f"excluded:{excluded}" if excluded else "pending"),
                        )
                    counts["events"] += 1
                    counts["aliases" if alias else event.kind] += 1
                from rg.ingest.catalog import append

                append(db, file_id, PARSER_VERSION, tool, record, events, event_ids, status)
                if fault:
                    fault()
                prefix_length = min(end, 4096)
                with path.open("rb") as prefix_stream:
                    prefix = prefix_stream.read(prefix_length)
                db.execute(
                    "UPDATE source_files SET committed_offset = ?, tail_fragment = NULL, "
                    "prefix_sha256 = ?, prefix_length = ?, last_read = ? "
                    "WHERE file_instance_id = ?",
                    (end, digest(prefix), prefix_length, now(), file_id),
                )
                db.execute(
                    "UPDATE sessions SET first_at = coalesce(first_at, ?), last_at = ? "
                    "WHERE session_pk = ?",
                    (events[0].timestamp, events[-1].timestamp, session),
                )
            offset = end
    if counts.get("events") and store.db.execute("PRAGMA user_version").fetchone()[0] >= 8:
        from rg.derive.worker import derive
        from rg.store.locking import TaskBusy

        try:
            derive(store, session=session)
        except TaskBusy:
            counts["l1_busy"] += 1
    return dict(counts)


def mark_deleted(store: Store) -> int:
    missing = [
        row[0]
        for row in store.db.execute(
            "SELECT file_instance_id, path FROM source_files WHERE status = 'active'"
        )
        if not Path(row[1]).exists()
    ]
    with store.transaction() as db:
        db.executemany(
            "UPDATE source_files SET status = 'deleted_at_source' WHERE file_instance_id = ?",
            [(x,) for x in missing],
        )
    return len(missing)
