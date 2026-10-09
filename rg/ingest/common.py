from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

RG_BLOCK = re.compile(r"<rg-context\b[^>]*>.*?(?:</rg-context>|$)", re.DOTALL)


@dataclass
class Parsed:
    kind: str
    text: str = ""
    role: str | None = None
    native_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    model: str | None = None
    excluded: str | None = None
    timestamp: str | None = None


def text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict))
    return json.dumps(value, ensure_ascii=False)


def injection(event: Parsed) -> Parsed:
    if event.tool_name and (
        event.tool_name.startswith("research.")
        or re.search(r"(?:__|\.)research(?:__|\.)", event.tool_name)
    ):
        event.excluded = "injected_by_rg"
    if RG_BLOCK.search(event.text):
        remaining = RG_BLOCK.sub("", event.text).strip()
        if not remaining:
            event.kind = "injected_context"
            event.excluded = "injected_by_rg"
        event.text = remaining
    if event.role in {"developer", "system"}:
        event.excluded = event.excluded or "system_prompt"
    if "<INSTRUCTIONS>" in event.text and (
        "AGENTS.md" in event.text or "AGENTS.md" in event.text[:300]
    ):
        event.excluded = "agent_instructions"
    return event


def parse(tool: str, record: dict[str, Any]) -> list[Parsed]:
    from rg.ingest.claude import parse_claude
    from rg.ingest.codex import parse_codex

    events = parse_claude(record) if tool == "claude" else parse_codex(record)
    for index, event in enumerate(events):
        if event.native_id:
            event.native_id += f":{index}"
        event.timestamp = record.get("timestamp") or record.get("createdAt")
        injection(event)
    return events
