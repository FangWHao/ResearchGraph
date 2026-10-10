"""按物理记录追加解析类型与工具版本观测，不复制正文。"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from rg.ingest.common import Parsed
from rg.store.database import dumps, now
from rg.store.objects import digest

VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
CLAUDE_RECORDS = {
    "user",
    "assistant",
    "file-history-snapshot",
    "queue-operation",
    "progress",
    "summary",
}
CODEX_RECORDS = {"session_meta", "turn_context", "compacted", "event_msg", "response_item"}


def _label(value: Any) -> str:
    if value is None:
        return "<missing>"
    if not isinstance(value, str):
        return f"<invalid:{type(value).__name__}>"
    raw = value.encode("utf-8", errors="surrogatepass")
    if (
        len(value) > 128
        or len(raw) > 512
        or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
    ):
        return "sha256:" + digest(raw)
    return value or "<empty>"


def version(record: dict[str, Any], tool: str, previous: str | None) -> tuple[str | None, str]:
    source = record
    if tool == "codex":
        if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
            return (previous, "file_context") if previous else (None, "unknown")
        source = record["payload"]
    key = "version" if tool == "claude" else "cli_version"
    if key not in source:
        return (previous, "file_context") if previous else (None, "unknown")
    value = source[key]
    if isinstance(value, str) and len(value) <= 96 and VERSION.fullmatch(value):
        return value, "direct_record"
    return None, "invalid"


def types(
    record: dict[str, Any], tool: str, events: list[Parsed], event_ids: list[int], status: str
) -> list[dict[str, Any]]:
    found: dict[tuple[str, str, bool], dict[str, Any]] = {}

    def add(category: str, value: Any, recognized: bool, event_id: int) -> None:
        name = _label(value)
        key = category, name, recognized
        if key in found:
            found[key]["count"] += 1
            found[key]["last_event_id"] = event_id
        else:
            found[key] = {
                "category": category,
                "type_name": name,
                "recognized": recognized,
                "count": 1,
                "event_id": event_id,
                "last_event_id": event_id,
            }

    first = event_ids[0]
    if status in {"bad_json", "invalid_record"}:
        add("record", f"<{status}>", False, first)
        return list(found.values())
    kind = record.get("type")
    known = CLAUDE_RECORDS if tool == "claude" else CODEX_RECORDS
    recognized = isinstance(kind, str) and kind in known
    if tool == "claude" and events[0].kind in {"compact_summary", "compact_boundary"}:
        recognized = True
    add("record", kind, recognized, first)
    if tool == "claude":
        if kind not in ("user", "assistant") or record.get("isCompactSummary"):
            return list(found.values())
        message = record.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if isinstance(blocks, str):
            add("content", "text", status == "parsed", first)
            return list(found.values())
    else:
        payload = record.get("payload")
        if kind not in ("event_msg", "response_item"):
            return list(found.values())
        subtype = payload.get("type") if isinstance(payload, dict) else None
        # 已知消息封装内的未知内容，不冒充未知封装类型。
        known_payload = status == "parsed" and events[0].excluded not in {
            "unknown_type",
            "unknown_payload",
            "bad_json",
        }
        add("payload", subtype, known_payload, first)
        if not isinstance(payload, dict):
            return list(found.values())
        if subtype in ("function_call_output", "custom_tool_call_output"):
            _nested(payload.get("output"), "output_content", first, add)
            return list(found.values())
        if subtype != "message":
            return list(found.values())
        blocks = payload.get("content", [])
    if not isinstance(blocks, list):
        add("content", f"<invalid:{type(blocks).__name__}>", False, first)
        return list(found.values())
    for index, block in enumerate(blocks):
        event_id = event_ids[min(index, len(event_ids) - 1)]
        event = events[min(index, len(events) - 1)]
        name = block.get("type") if isinstance(block, dict) else f"<invalid:{type(block).__name__}>"
        add("content", name, status == "parsed" and event.kind != "unknown", event_id)
        if tool == "claude" and isinstance(block, dict) and name == "tool_result":
            _nested(block.get("content"), "tool_result_content", event_id, add)
    return list(found.values())


def _nested(value: Any, category: str, event_id: int, add: Any) -> None:
    if not isinstance(value, list):
        return
    for block in value:
        name = block.get("type") if isinstance(block, dict) else f"<invalid:{type(block).__name__}>"
        recognized = isinstance(block, dict) and (
            name in ("text", "input_text", "output_text") or (name is None and "text" in block)
        )
        add(category, name, recognized, event_id)


def append(
    db: sqlite3.Connection,
    file_id: int,
    parser_version: str,
    tool: str,
    record: dict[str, Any],
    events: list[Parsed],
    event_ids: list[int],
    status: str,
) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] < 19:
        return
    previous = db.execute(
        "SELECT tool_version FROM parser_records WHERE file_instance_id=? "
        "ORDER BY record_id DESC LIMIT 1",
        (file_id,),
    ).fetchone()
    tool_version, basis = version(record, tool, previous[0] if previous else None)
    observations = types(record, tool, events, event_ids, status)
    db.execute(
        "INSERT INTO parser_records(event_id,file_instance_id,parser_version,tool_version,"
        "version_basis,status,types,unknown_events,unknown_types,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            event_ids[0],
            file_id,
            parser_version,
            tool_version,
            basis,
            status,
            dumps(observations),
            sum(event.kind == "unknown" for event in events),
            sum(item["count"] for item in observations if not item["recognized"]),
            now(),
        ),
    )
