from __future__ import annotations

import json
from typing import Any

from rg.ingest.common import Parsed, text_content


def parse_claude(record: dict[str, Any]) -> list[Parsed]:
    kind = record.get("type")
    native = record.get("uuid")
    message = record.get("message") or {}
    if record.get("isCompactSummary"):
        return [Parsed("compact_summary", excluded="compact_summary", native_id=native)]
    if kind == "system" and record.get("subtype") == "compact_boundary":
        return [Parsed("compact_boundary", native_id=native, excluded="compaction_boundary")]
    if kind in {"file-history-snapshot", "queue-operation", "progress", "summary"}:
        return [Parsed("meta", native_id=native, excluded="metadata")]
    if kind not in {"user", "assistant"}:
        return [Parsed("unknown", native_id=native, excluded="unknown_type")]
    content = message.get("content", "")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [Parsed("unknown", native_id=native, excluded="unknown_content")]
    events = []
    for block in content:
        if not isinstance(block, dict):
            events.append(Parsed("unknown", excluded="unknown_content"))
            continue
        common = {"role": kind, "native_id": native, "model": message.get("model")}
        block_type = block.get("type")
        if block_type == "text":
            events.append(Parsed(f"{kind}_msg", str(block.get("text", "")), **common))
        elif block_type == "tool_use":
            name = str(block.get("name", ""))
            mapped = (
                "plan_update"
                if name in {"TaskCreate", "TaskUpdate", "TodoWrite"}
                else ("file_edit" if name in {"Edit", "Write", "MultiEdit"} else "tool_call")
            )
            events.append(
                Parsed(
                    mapped,
                    json.dumps(block.get("input", {}), ensure_ascii=False),
                    call_id=block.get("id"),
                    tool_name=name,
                    **common,
                )
            )
        elif block_type == "tool_result":
            events.append(
                Parsed(
                    "tool_result",
                    text_content(block.get("content", "")),
                    call_id=block.get("tool_use_id"),
                    **common,
                )
            )
        elif block_type in {"thinking", "redacted_thinking"}:
            events.append(Parsed("thinking", excluded="thinking", **common))
        else:
            events.append(Parsed("unknown", excluded="unknown_content", **common))
    return events or [Parsed("meta", native_id=native, excluded="empty_message")]
