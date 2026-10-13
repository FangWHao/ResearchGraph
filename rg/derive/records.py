from __future__ import annotations

import json
from typing import Any

from rg.ingest.project_context import saved_cwd
from rg.store.database import Store
from rg.store.lexical_paths import absolute, native_absolute, resolve, within
from rg.store.ownership import read_table

Event = dict[str, Any]


def event(store: Store, event_id: int) -> Event:
    row = store.db.execute(
        "SELECT r.*,s.project_id,s.cwd AS session_cwd,s.tool "
        "FROM effective_raw_events r JOIN sessions s USING(session_pk) WHERE r.event_id=?",
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
        return {
            "command": item.get("command"),
            **({"workdir": item["cwd"]} if "cwd" in item else {}),
        }
    raw = item.get("input", {}) if value["tool"] == "claude" else item.get("arguments", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def cwd(store: Store, value: Event) -> str | None:
    args = arguments(value)
    for field in ("workdir", "cwd"):
        if field in args:
            return native_absolute(args[field])
    if value["tool"] == "claude" and "cwd" in value["record"]:
        return native_absolute(value["record"]["cwd"])
    return saved_cwd(store, value["event_id"])


def bound_path(
    store: Store, project: str, path: Any, working_dir: str | None
) -> tuple[str | None, str | None]:
    resolved = resolve(path, working_dir)
    key = absolute(resolved)
    if resolved is None or key is None:
        return None, None
    roots = [
        row
        for row in store.db.execute(
            f"SELECT root_id,project_id,path FROM {read_table(store.db, 'source_roots')} "
            "WHERE host_id='local'",
        )
        if (root := absolute(row["path"])) is not None and within(key, root)
    ]
    if not roots:
        return None, None
    selected = max(roots, key=lambda row: len(row["path"]))
    if selected["project_id"] != project or any(
        row["project_id"] != project and absolute(row["path"]) == absolute(selected["path"])
        for row in roots
    ):
        return None, None
    return selected["root_id"], resolved


def calls(store: Store, value: Event) -> list[Event]:
    if not value["call_id"]:
        return []
    result = [
        event(store, row[0])
        for row in store.db.execute(
            "SELECT event_id FROM effective_raw_events WHERE session_pk=? AND call_id=? "
            "AND kind IN ('tool_call','file_edit') AND alias_of IS NULL AND exclude_reason IS NULL "
            "ORDER BY seq",
            (value["session_pk"], value["call_id"]),
        )
    ]
    if native_patch(value, "patch_apply_end") or native_patch(value, "patch_apply_begin"):
        begins = patch_begins(store, value)
        if len(result) > 1:
            return result
        if len(begins) > 1:
            return begins
        # 外层执行器请求不是补丁请求；原生回执只沿同会话、同调用的开始记录。
        if result and result[0]["kind"] == "file_edit":
            return result
        return begins
    if not result:
        result = [
            event(store, row[0])
            for row in store.db.execute(
                "SELECT event_id FROM effective_raw_events WHERE session_pk=? AND call_id=? "
                "AND kind='meta' AND tool_name='exec_command' "
                "AND exclude_reason='execution_metadata' AND alias_of IS NULL ORDER BY seq",
                (value["session_pk"], value["call_id"]),
            )
        ]
    return result


def native_patch(value: Event, kind: str) -> bool:
    return (
        value["tool"] == "codex"
        and value["record"].get("type") == "event_msg"
        and block(value).get("type") == kind
    )


def patch_begins(store: Store, value: Event) -> list[Event]:
    return [
        candidate
        for row in store.db.execute(
            "SELECT event_id FROM effective_raw_events WHERE session_pk=? AND call_id=? "
            "AND kind='meta' AND tool_name='apply_patch' "
            "AND exclude_reason='execution_metadata' AND alias_of IS NULL ORDER BY seq",
            (value["session_pk"], value["call_id"]),
        )
        if native_patch(candidate := event(store, row[0]), "patch_apply_begin")
    ]
