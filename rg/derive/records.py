from __future__ import annotations

import json
import posixpath
import re
from typing import Any

from rg.store.database import Store

Event = dict[str, Any]


def event(store: Store, event_id: int) -> Event:
    row = store.db.execute(
        "SELECT r.*,s.project_id,s.cwd AS session_cwd,s.tool "
        "FROM raw_events r JOIN sessions s USING(session_pk) WHERE r.event_id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise ValueError("原始事件不存在")
    value = dict(row)
    value["record"] = json.loads(store.raw(event_id))
    return value


def block(value: Event) -> dict[str, Any]:
    record = value["record"]
    if value["tool"] == "codex":
        return record.get("payload", {})
    content = record.get("message", {}).get("content", [])
    index = value["record_index"]
    if isinstance(content, list) and index < len(content) and isinstance(content[index], dict):
        return content[index]
    return {}


def arguments(value: Event) -> dict[str, Any]:
    item = block(value)
    if value["tool"] == "codex" and item.get("type") == "exec_command_begin":
        return {"command": item.get("command"), "workdir": item.get("cwd")}
    raw = item.get("input", {}) if value["tool"] == "claude" else item.get("arguments", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def cwd(store: Store, value: Event) -> str | None:
    explicit = arguments(value).get("workdir") or arguments(value).get("cwd")
    if explicit is not None:
        return explicit if isinstance(explicit, str) and explicit.startswith("/") else None
    if value["tool"] == "claude":
        path = value["record"].get("cwd") or value["session_cwd"]
        return path if isinstance(path, str) else None
    for row in store.db.execute(
        "SELECT event_id FROM raw_events WHERE session_pk=? AND seq<=? AND kind='meta' "
        "ORDER BY seq DESC",
        (value["session_pk"], value["seq"]),
    ):
        metadata = event(store, row[0])["record"]
        if metadata.get("type") in {"session_meta", "turn_context"}:
            path = metadata.get("payload", {}).get("cwd")
            if isinstance(path, str):
                return path
    return None


def bound_path(
    store: Store, project: str, path: Any, working_dir: str | None
) -> tuple[str | None, str | None]:
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or "\\" in path
        or re.match(r"\w+:", path)
    ):
        return None, None
    if not path.startswith("/") and (not working_dir or not working_dir.startswith("/")):
        return None, None
    absolute = posixpath.normpath(
        path if path.startswith("/") else posixpath.join(working_dir or "", path)
    )
    roots = [
        row
        for row in store.db.execute(
            "SELECT root_id,path FROM source_roots WHERE project_id=? AND host_id='local'",
            (project,),
        )
        if absolute == row["path"] or absolute.startswith(row["path"].rstrip("/") + "/")
    ]
    if not roots:
        return None, None
    selected = max(roots, key=lambda row: len(row["path"]))
    return selected["root_id"], absolute


def calls(store: Store, value: Event) -> list[Event]:
    if not value["call_id"]:
        return []
    result = [
        event(store, row[0])
        for row in store.db.execute(
            "SELECT event_id FROM raw_events WHERE session_pk=? AND call_id=? "
            "AND kind IN ('tool_call','file_edit') AND alias_of IS NULL AND exclude_reason IS NULL "
            "ORDER BY seq",
            (value["session_pk"], value["call_id"]),
        )
    ]
    if not result:
        result = [
            event(store, row[0])
            for row in store.db.execute(
                "SELECT event_id FROM raw_events WHERE session_pk=? AND call_id=? "
                "AND kind='meta' AND tool_name='exec_command' "
                "AND exclude_reason='execution_metadata' AND alias_of IS NULL ORDER BY seq",
                (value["session_pk"], value["call_id"]),
            )
        ]
    return result
