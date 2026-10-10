"""按保存原件复核模型输入资格，原始行及其分类保持不变。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from rg.ingest.common import Parsed, parse

if TYPE_CHECKING:
    from rg.store.database import Store


def saved_parse(store: Store, row: Mapping[str, Any] | sqlite3.Row) -> Parsed:
    return parse(row["parser"], json.loads(store.raw(row["event_id"])))[row["record_index"]]


def current_exclusion(
    store: Store, row: Mapping[str, Any] | sqlite3.Row, event: Parsed | None = None
) -> str | None:
    if row["exclude_reason"]:
        return row["exclude_reason"]
    if row["kind"] not in {"tool_call", "tool_result", "file_edit", "plan_update"}:
        return None
    event = event or saved_parse(store, row)
    if event.excluded:
        return event.excluded
    if event.kind != "tool_result" or not event.call_id:
        return None
    calls = store.db.execute(
        "SELECT r.*,f.parser FROM raw_events r JOIN source_files f USING(file_instance_id) "
        "WHERE r.session_pk=? AND r.call_id=? AND r.alias_of IS NULL "
        "AND r.kind IN ('tool_call','file_edit','plan_update') LIMIT 2",
        (row["session_pk"], event.call_id),
    ).fetchall()
    if len(calls) != 1:
        return None
    reason = saved_parse(store, calls[0]).excluded
    return reason if reason in {"injected_by_rg", "unknown_tool_namespace"} else None
