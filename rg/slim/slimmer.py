from __future__ import annotations

import json
import re
from typing import Any

from rg.ingest.common import parse
from rg.ingest.spans import event_span
from rg.slim.tokens import TokenCounter
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

SLIM_VERSION = "3"


def summarize(kind: str, text: str, tool: str | None, call_id: str | None) -> str:
    if kind in {"user_msg", "assistant_msg", "plan_update"}:
        return text
    if kind == "tool_result":
        lines = text.splitlines()
        errors = [x[:200] for x in lines if re.search(r"error|exception|失败|错误", x, re.I)][:10]
        edges = [x[:200] for x in lines[:5]] + [x[:200] for x in lines[-5:]]
        from rg.derive.runtime import EXEC_TOOLS, POLL_TOOLS, execution_result

        result = execution_result(text) if tool in EXEC_TOOLS | POLL_TOOLS else None
        return dumps(
            {
                "call_id": call_id,
                "bytes": len(text.encode()),
                "exit_code": result.exit_code
                if result and result.exit_code is not None
                else "unknown",
                "preview": edges,
                "errors": errors,
            }
        )
    if kind == "file_edit":
        return f"编辑 {tool} {text[:200]} call_id={call_id}（完整补丁见原文对象）"
    return f"调用 {tool} {text[:200]} call_id={call_id}"


def cache_count(store: Store, counter: TokenCounter, text: str) -> int:
    key = digest(dumps([counter.provider, counter.model, text]).encode())
    row = store.db.execute("SELECT tokens FROM token_cache WHERE cache_key = ?", (key,)).fetchone()
    if row:
        return row[0]
    tokens = counter.count_text(text)
    if not isinstance(tokens, int) or tokens < 0:
        raise ValueError("提供方返回非法 token 数")
    store.db.execute(
        "INSERT OR IGNORE INTO token_cache VALUES (?, ?, ?, ?, ?)",
        (key, counter.provider, counter.model, tokens, now()),
    )
    return tokens


def slim_session(
    store: Store, session_id: int, counter: TokenCounter, event_ids: set[int] | None = None
) -> list[dict[str, Any]]:
    from rg.extract.privacy import ProjectCounter

    permission = store.db.execute(
        "SELECT p.remote_model_allowed,p.project_id FROM sessions s JOIN projects p "
        "USING(project_id) WHERE session_pk = ?",
        (session_id,),
    ).fetchone()
    if getattr(counter, "remote", False) and (not permission or not permission[0]):
        raise PermissionError("本项目禁止远程模型计数和生成；请先预览遮盖后的片段并明确启用")
    if permission:
        if not isinstance(counter, ProjectCounter):
            counter = ProjectCounter(store, permission[1], counter)
    rows = store.db.execute(
        "SELECT r.*, f.parser FROM raw_events r JOIN source_files f "
        "USING(file_instance_id) WHERE r.session_pk = ? ORDER BY r.seq",
        (session_id,),
    ).fetchall()
    for row in rows:
        if event_ids is not None and row["event_id"] not in event_ids:
            continue
        reason = row["exclude_reason"]
        mirror = store.db.execute(
            "SELECT reason FROM dedupe_links WHERE alias_id = ?", (row["event_id"],)
        ).fetchone()
        if mirror:
            reason = mirror[0]
        if reason:
            with store.transaction() as db:
                db.execute("DELETE FROM slim_events WHERE event_id = ?", (row["event_id"],))
                db.execute(
                    "UPDATE coverage SET status = ? WHERE event_id = ?",
                    (f"excluded:{reason}", row["event_id"]),
                )
            continue
        existing = store.db.execute(
            "SELECT slim_version FROM slim_events WHERE event_id = ?", (row["event_id"],)
        ).fetchone()
        counter_version = counter.provider + ":" + counter.model + ":" + SLIM_VERSION
        policy = getattr(counter, "policy", None)
        if policy and policy.rule_id:
            counter_version += ":" + policy.identity
        if existing and existing[0] == counter_version:
            continue
        raw = store.raw(row["event_id"])
        event = parse(row["parser"], json.loads(raw))[row["record_index"]]
        text = summarize(row["kind"], event.text, row["tool_name"], row["call_id"])
        safe_text = text
        if isinstance(counter, ProjectCounter):
            from rg.extract.redact import redact

            safe_text = summarize(
                row["kind"],
                redact(event.text.encode(), counter.policy.patterns).data.decode(),
                row["tool_name"],
                row["call_id"],
            )
        tokens = cache_count(store, counter, safe_text)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO slim_events VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET text=excluded.text,tokens=excluded.tokens,"
                "slim_version=excluded.slim_version,dropped_bytes=excluded.dropped_bytes",
                (
                    row["event_id"],
                    text,
                    tokens,
                    counter_version,
                    max(0, len(raw) - len(text.encode())),
                ),
            )
            db.execute(
                "UPDATE coverage SET status = 'covered' WHERE event_id = ? AND stage = ?",
                (row["event_id"], "slim"),
            )
    result = [
        dict(row)
        for row in store.db.execute(
            "SELECT r.event_id, r.kind, r.role, r.call_id, s.text, s.tokens FROM raw_events r "
            "JOIN slim_events s USING(event_id) WHERE r.session_pk = ? ORDER BY r.seq",
            (session_id,),
        )
        if event_ids is None or row["event_id"] in event_ids
    ]
    for event in result:
        row = store.db.execute(
            "SELECT f.parser, r.record_index FROM raw_events r JOIN source_files f "
            "USING(file_instance_id) WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()
        raw = store.raw(event["event_id"])
        if isinstance(counter, ProjectCounter):
            from rg.extract.redact import redact

            parsed = parse(row["parser"], json.loads(raw))[row["record_index"]]
            event["text"] = summarize(
                event["kind"],
                redact(parsed.text.encode(), counter.policy.patterns).data.decode(),
                parsed.tool_name,
                event["call_id"],
            )
        bounds = event_span(raw, row["parser"], row["record_index"])
        if bounds:
            event["raw_start"], event["raw_end"], event["raw_is_string"] = bounds
            if event["kind"] in {"user_msg", "assistant_msg"}:
                event["raw_text"] = raw[bounds[0] : bounds[1]].decode()
    return result
