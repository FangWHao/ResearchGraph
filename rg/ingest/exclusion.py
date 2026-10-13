"""按保存原件复核模型输入资格，原始行及其分类保持不变。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import zstandard

from rg.ingest.claude import parse_claude
from rg.ingest.codex import parse_codex
from rg.ingest.common import RG_BLOCK, Parsed, parse

if TYPE_CHECKING:
    from rg.store.database import Store


def saved_parse(store: Store, row: Mapping[str, Any] | sqlite3.Row) -> Parsed:
    return parse(row["parser"], json.loads(store.raw(row["event_id"])))[row["record_index"]]


def current_exclusion(
    store: Store,
    row: Mapping[str, Any] | sqlite3.Row,
    event: Parsed | None = None,
    *,
    raw: bytes | None = None,
    call_visible: Callable[[sqlite3.Row], bool] | None = None,
) -> str | None:
    if row["exclude_reason"]:
        return row["exclude_reason"]
    if row["kind"] not in {"tool_call", "tool_result", "file_edit", "plan_update"}:
        return None
    raw = raw if raw is not None else store.raw(row["event_id"])
    event = event or parse(row["parser"], json.loads(raw))[row["record_index"]]
    if event.excluded:
        return event.excluded
    if event.kind == "tool_result":
        # 一条Claude记录可含多个结果；只检查本逻辑事件的原正文，避免排除其它结果。
        original = (parse_claude if row["parser"] == "claude" else parse_codex)(json.loads(raw))
        if RG_BLOCK.search(original[row["record_index"]].text):
            return "injected_by_rg"
    if event.kind != "tool_result" or not event.call_id:
        return None
    calls = store.db.execute(
        "SELECT r.*,f.parser FROM effective_raw_events r JOIN "
        "source_files f USING(file_instance_id) "
        "WHERE r.session_pk=? AND r.call_id=? AND r.alias_of IS NULL "
        "AND r.kind IN ('tool_call','file_edit','plan_update') LIMIT 2",
        (row["session_pk"], event.call_id),
    ).fetchall()
    if len(calls) != 1:
        return None
    if call_visible is not None and not call_visible(calls[0]):
        return None
    reason = saved_parse(store, calls[0]).excluded
    return reason if reason in {"injected_by_rg", "unknown_tool_namespace"} else None


def source_exclusion(
    store: Store,
    row: Mapping[str, Any] | sqlite3.Row,
    *,
    raw: bytes,
    call_visible: Callable[[sqlite3.Row], bool] | None = None,
) -> str | None:
    """资格复核失败时保留已读原文，禁止把无法核对的事件作为输入。"""
    try:
        return current_exclusion(store, row, raw=raw, call_visible=call_visible)
    except (OSError, ValueError, IndexError, KeyError, TypeError, zstandard.ZstdError):
        return "source_unavailable"
