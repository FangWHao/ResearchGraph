from __future__ import annotations

import json
from typing import Any

from rg.ingest.common import Parsed, text_content


def parse_codex(record: dict[str, Any]) -> list[Parsed]:
    kind = record.get("type")
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return [Parsed("unknown", excluded="unknown_payload")]
    if kind in {"session_meta", "turn_context"}:
        return [Parsed("meta", excluded="metadata")]
    if kind == "compacted":
        return [Parsed("compact_boundary", excluded="compaction_replay")]
    if kind == "event_msg":
        t = payload.get("type")
        if t in {"exec_command_begin", "exec_command_end", "patch_apply_end"}:
            return [
                Parsed(
                    "meta" if t == "exec_command_begin" else "tool_result",
                    json.dumps(payload, ensure_ascii=False),
                    native_id=f"{t}:{payload['call_id']}" if payload.get("call_id") else None,
                    call_id=payload.get("call_id"),
                    tool_name="apply_patch" if t == "patch_apply_end" else "exec_command",
                    excluded="execution_metadata" if t == "exec_command_begin" else None,
                )
            ]
        if t in {"user_message", "agent_message"}:
            role = "user" if t == "user_message" else "assistant"
            return [Parsed(f"{role}_msg", str(payload.get("message", "")), role=role)]
        if t in {
            "task_started",
            "task_complete",
            "turn_aborted",
            "token_count",
            "agent_reasoning",
            "thread_goal_updated",
        }:
            return [Parsed("meta", excluded="metadata")]
        return [Parsed("unknown", excluded="unknown_type")]
    if kind != "response_item":
        return [Parsed("unknown", excluded="unknown_type")]
    t = payload.get("type")
    if t == "message":
        role = payload.get("role")
        mapped = f"{role}_msg" if role in {"user", "assistant"} else "meta"
        blocks = payload.get("content", [])
        known = {"input_text", "output_text", "text"}
        events = [
            Parsed(mapped, str(x.get("text", "")), role=role, native_id=payload.get("id"))
            if isinstance(x, dict) and x.get("type") in known
            else Parsed("unknown", excluded="unknown_content")
            for x in blocks
        ]
        return events or [Parsed("meta", excluded="empty_message")]
    if t in {"function_call", "custom_tool_call"}:
        name = str(payload.get("name", ""))
        mapped = (
            "file_edit"
            if name in {"apply_patch", "functions.apply_patch"}
            else (
                "plan_update" if name in {"update_plan", "functions.update_plan"} else "tool_call"
            )
        )
        return [
            Parsed(
                mapped,
                text_content(payload.get("arguments") or payload.get("input", "")),
                native_id=payload.get("call_id"),
                call_id=payload.get("call_id"),
                tool_name=name,
            )
        ]
    if t in {"function_call_output", "custom_tool_call_output"}:
        return [
            Parsed(
                "tool_result",
                text_content(payload.get("output", "")),
                native_id=payload.get("call_id"),
                call_id=payload.get("call_id"),
            )
        ]
    if t == "reasoning":
        return [Parsed("thinking", excluded="encrypted")]
    return [Parsed("unknown", excluded="unknown_type")]
