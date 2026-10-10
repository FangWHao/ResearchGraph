from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.differences import confirmed_differences
from rg.extract.schemas import CLAIM_SCHEMA
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

MAX_GRAPH_CLAIMS = 2000
MAX_WINDOW_BYTES = 24000


class NotFound(ValueError):
    pass


def page(values: dict[str, str], maximum: int = 200) -> tuple[int, int]:
    limit, offset = int(values.get("limit", "50")), int(values.get("offset", "0"))
    if not 1 <= limit <= maximum or offset < 0:
        raise ValueError("页长或偏移超出范围")
    return limit, offset


def project_exists(store: Store, project: str | None) -> None:
    if (
        project
        and not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()
    ):
        raise NotFound("项目不存在")


def projects(store: Store) -> dict[str, Any]:
    result = []
    for row in store.db.execute("SELECT * FROM projects ORDER BY name, project_id"):
        project = dict(row)
        project["sessions"] = store.db.execute(
            "SELECT count(*) FROM sessions WHERE project_id=?", (row["project_id"],)
        ).fetchone()[0]
        project["claims"] = store.db.execute(
            "SELECT count(*) FROM claims c JOIN entities e USING(entity_id) WHERE e.project_id=?",
            (row["project_id"],),
        ).fetchone()[0]
        result.append(project)
    return {"revision": store.revision(), "projects": result}


def _spans(store: Store, claim_id: int) -> list[dict[str, Any]]:
    rows = store.db.execute(
        "SELECT e.*, c.role, r.session_pk, r.seq, r.occurred_at, r.recorded_at, "
        "f.path, r.byte_start AS source_byte_start, r.byte_end AS source_byte_end "
        "FROM claim_evidence c JOIN evidence_spans e USING(span_id) "
        "JOIN raw_events r USING(event_id) JOIN source_files f USING(file_instance_id) "
        "WHERE c.claim_id=? ORDER BY r.seq,e.byte_start,e.span_id",
        (claim_id,),
    )
    return [dict(row) for row in rows]


def _claim(store: Store, row: sqlite3.Row, comparisons: bool = False) -> dict[str, Any]:
    from rg.record.resolve import pending

    result = dict(row)
    result["payload"] = json.loads(row["payload"])
    result["scope"] = json.loads(row["scope"]) if row["scope"] else None
    result["effective_state"] = store.claim_state(row["claim_id"])
    result["pending_decision"] = pending(store, row["claim_id"])
    last = store.db.execute(
        "SELECT * FROM review_actions WHERE claim_id=? ORDER BY action_id DESC LIMIT 1",
        (row["claim_id"],),
    ).fetchone()
    result["review"] = dict(last) if last else None
    actor = last["actor"] if last else row["actor"]
    result["confirmation_source"] = (
        ("human" if actor.startswith("human:") else "rule" if actor.startswith("rule:") else None)
        if result["effective_state"] == "confirmed"
        else None
    )
    result["evidence"] = _spans(store, row["claim_id"])
    coverage = store.db.execute(
        "SELECT DISTINCT c.segment_id,r.session_pk FROM claim_evidence ce "
        "JOIN evidence_spans s USING(span_id) JOIN raw_events r USING(event_id) "
        "LEFT JOIN coverage c ON c.event_id=r.event_id AND c.stage='pass2' "
        "WHERE ce.claim_id=? ORDER BY r.session_pk,c.segment_id",
        (row["claim_id"],),
    ).fetchall()
    result["groups"] = [dict(item) for item in coverage]
    result["replacement_ids"] = [
        item[0]
        for item in store.db.execute(
            "SELECT claim_id FROM claims WHERE replaces_claim=? ORDER BY claim_id",
            (row["claim_id"],),
        )
    ]
    result["human_comparisons"] = (
        confirmed_differences(store, row["claim_id"]) if comparisons else []
    )
    return result


def claim(store: Store, claim_id: int) -> dict[str, Any]:
    row = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
    if row is None:
        raise NotFound("记录不存在")
    result = _claim(store, row, comparisons=True)
    result["review_history"] = [
        dict(item)
        for item in store.db.execute(
            "SELECT * FROM review_actions WHERE claim_id=? ORDER BY action_id", (claim_id,)
        )
    ]
    if row["replaces_claim"]:
        original = store.db.execute(
            "SELECT payload,scope FROM claims WHERE claim_id=?", (row["replaces_claim"],)
        ).fetchone()
        result["replaces"] = {
            "claim_id": row["replaces_claim"],
            "payload": json.loads(original["payload"]),
            "scope": json.loads(original["scope"]) if original["scope"] else None,
        }
    return {"revision": store.revision(), "claim": result}


def claims(store: Store, values: dict[str, str]) -> dict[str, Any]:
    limit, offset = page(values)
    project = values.get("project") or None
    state = values.get("state", "all")
    if state not in {"all", "candidate", "confirmed", "dismissed"}:
        raise ValueError("审核状态无效")
    project_exists(store, project)
    session = int(values["session"]) if values.get("session") else None
    segment = values.get("segment")
    if session is not None and session < 1:
        raise ValueError("会话 ID 无效")
    # 最新追加的人工/规则操作决定有效审核状态，不改写模型原行。
    query = (
        "FROM claims c LEFT JOIN entities e USING(entity_id) "
        "LEFT JOIN review_actions a ON a.action_id=(SELECT max(action_id) "
        "FROM review_actions WHERE claim_id=c.claim_id) "
        "WHERE (? IS NULL OR e.project_id=?) AND (?='all' OR "
        "CASE WHEN a.action='confirm' THEN 'confirmed' "
        "WHEN a.action IS NOT NULL THEN 'dismissed' ELSE c.claim_state END=?) "
        "AND (? IS NULL OR EXISTS (SELECT 1 FROM claim_evidence ce "
        "JOIN evidence_spans es USING(span_id) JOIN raw_events re USING(event_id) "
        "LEFT JOIN coverage co ON co.event_id=re.event_id AND co.stage='pass2' "
        "WHERE ce.claim_id=c.claim_id AND re.session_pk=? "
        "AND (? IS NULL OR COALESCE(co.segment_id,'unknown')=?)))"
    )
    params = (project, project, state, state, session, session, segment, segment)
    total = store.db.execute("SELECT count(*) " + query, params).fetchone()[0]
    rows = store.db.execute(
        "SELECT c.* " + query + " ORDER BY c.claim_id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return {
        "revision": store.revision(),
        "total": total,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
        "claims": [_claim(store, row, comparisons=True) for row in rows],
    }


def _utf8_window(raw: bytes, start: int, end: int) -> tuple[int, int]:
    while start < end and raw[start] & 0xC0 == 0x80:
        start += 1
    while end < len(raw) and end > start and raw[end] & 0xC0 == 0x80:
        end -= 1
    return start, end


def _event(store: Store, event_id: int, start: int | None, end: int | None) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT r.*,f.path FROM raw_events r JOIN source_files f USING(file_instance_id) "
        "WHERE r.event_id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise NotFound("原始事件不存在")
    raw = store.raw(event_id)
    highlight = start is not None or end is not None
    if highlight:
        if start is None or end is None or not 0 <= start < end <= len(raw):
            raise ValueError("引用字节范围无效")
        raw[start:end].decode("utf-8")
        if end - start > MAX_WINDOW_BYTES:
            raise ValueError("引用过长，请选择更小的原文窗口")
        window_start, window_end = _utf8_window(
            raw, max(0, start - 2000), min(len(raw), end + 2000)
        )
        before, quote, after = (
            raw[window_start:start].decode("utf-8"),
            raw[start:end].decode("utf-8"),
            raw[end:window_end].decode("utf-8"),
        )
    else:
        window_start, window_end = _utf8_window(raw, 0, min(len(raw), MAX_WINDOW_BYTES))
        before, quote, after = raw[window_start:window_end].decode("utf-8"), "", ""
    return {
        "event_id": event_id,
        "session_pk": row["session_pk"],
        "seq": row["seq"],
        "kind": row["kind"],
        "role": row["role"],
        "path": row["path"],
        "source_byte_start": row["byte_start"],
        "source_byte_end": row["byte_end"],
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "exclude_reason": row["exclude_reason"],
        "total_bytes": len(raw),
        "window_start": window_start,
        "window_end": window_end,
        "window_truncated": window_start > 0 or window_end < len(raw),
        "before": before,
        "quote": quote,
        "after": after,
        "quote_sha256": digest(raw[start:end]) if highlight else None,
    }


def evidence(store: Store, event_id: int, values: dict[str, str]) -> dict[str, Any]:
    context = int(values.get("context", "2"))
    if not 0 <= context <= 5:
        raise ValueError("上下文条数为 0 到 5")
    start = int(values["start"]) if "start" in values else None
    end = int(values["end"]) if "end" in values else None
    event = _event(store, event_id, start, end)
    before = store.db.execute(
        "SELECT event_id FROM raw_events WHERE session_pk=? AND seq<? ORDER BY seq DESC LIMIT ?",
        (event["session_pk"], event["seq"], context),
    ).fetchall()
    after = store.db.execute(
        "SELECT event_id FROM raw_events WHERE session_pk=? AND seq>? ORDER BY seq LIMIT ?",
        (event["session_pk"], event["seq"], context),
    ).fetchall()
    versions = [
        dict(row)
        for row in store.db.execute(
            "SELECT version_id,path,algo,digest,size,source,observed_at,phase,basis,claim_state,"
            "representation FROM artifact_versions "
            "WHERE evidence_event_id=? ORDER BY observed_at,version_id LIMIT 40",
            (event_id,),
        )
    ]
    from rg.derive.views import evidence as l1_evidence

    facts = l1_evidence(store, event_id)
    available = any(item["diff"]["available"] for item in facts["edits"])
    return {
        "event": event,
        "before": [_event(store, row[0], None, None) for row in reversed(before)],
        "after": [_event(store, row[0], None, None) for row in after],
        "artifact_versions": versions,
        "artifact_versions_partial": store.db.execute(
            "SELECT count(*) FROM artifact_versions WHERE evidence_event_id=?", (event_id,)
        ).fetchone()[0]
        > 40,
        "artifact_diff": {
            "available": available,
            "reason": "逐条查看工具记录的候选文本版本或补丁，完整性见对应缺口"
            if available
            else "尚无可比较的版本内容快照",
        },
        "l1": facts,
    }


def search(store: Store, values: dict[str, str]) -> dict[str, Any]:
    text = values.get("q", "")
    project = values.get("project") or None
    if not text.strip() or len(text) > 500:
        raise ValueError("搜索文本需为 1 到 500 字符")
    project_exists(store, project)
    limit, offset = page(values, 100)
    query = (
        "FROM event_search x JOIN raw_events r USING(event_id) JOIN sessions s USING(session_pk) "
        "WHERE instr(x.text,?)>0 AND (? IS NULL OR s.project_id=?)"
    )
    params = (text, project, project)
    total = store.db.execute("SELECT count(*) " + query, params).fetchone()[0]
    rows = store.db.execute(
        "SELECT r.event_id,r.session_pk,r.occurred_at,x.text "
        + query
        + " ORDER BY r.event_id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    # instr 按字面量匹配；控制返回长度，但命中的文字始终在预览里。
    results = []
    for row in rows:
        position = row["text"].find(text)
        begin = max(0, position - 100)
        results.append(
            {
                "event_id": row["event_id"],
                "session_pk": row["session_pk"],
                "occurred_at": row["occurred_at"],
                "text": row["text"][begin : position + len(text) + 150],
            }
        )
    return {
        "results": results,
        "total": total,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def health(store: Store, values: dict[str, str], daily_budget: int) -> dict[str, Any]:
    limit, offset = page(values)
    pipeline_offset = int(values.get("pipeline_offset", "0"))
    if not 0 <= pipeline_offset <= 2147483647:
        raise ValueError("流水线偏移超出范围")
    project = values.get("project") or None
    project_exists(store, project)
    result = store.health(project, None, daily_budget, limit, offset, pipeline_offset)
    result["global_counts"] = True
    rows = store.db.execute(
        "SELECT f.*,s.tool,s.native_session_id,s.project_id FROM source_files f "
        "LEFT JOIN sessions s USING(session_pk) "
        "WHERE f.parser IN ('claude','codex') AND (? IS NULL OR s.project_id=?) "
        "ORDER BY f.file_instance_id",
        (project, project),
    ).fetchall()
    sources = []
    for row in rows:
        entry = {
            key: row[key]
            for key in (
                "file_instance_id",
                "path",
                "tool",
                "last_read",
                "committed_offset",
                "status",
            )
        }
        try:
            size = Path(row["path"]).stat().st_size
            entry.update(source_bytes=size, cursor_lag_bytes=max(0, size - row["committed_offset"]))
            entry["cleanup_risk"] = "source_truncated" if size < row["committed_offset"] else None
        except OSError:
            entry.update(source_bytes=None, cursor_lag_bytes=None, cleanup_risk="source_missing")
        sources.append(entry)
    result["sources"] = sources
    result["compression_points"] = store.db.execute(
        "SELECT count(*) FROM raw_events r JOIN sessions s USING(session_pk) "
        "WHERE r.kind='compact_boundary' AND (? IS NULL OR s.project_id=?)",
        (project, project),
    ).fetchone()[0]
    result["hook_failures"] = None
    result["hook_failures_reason"] = "尚未建立钩子失败账本"
    return result


def _human(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("human:") or not value[6:].strip():
        raise ValueError("复核需使用非空 human:人工身份")
    if len(value) > 120:
        raise ValueError("人工身份过长")
    return value


def _expected(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision 需为非负整数")
    return value


def review(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    actor, expected = _human(body.get("actor")), _expected(body.get("expected_revision"))
    ids, action = body.get("claim_ids"), body.get("action")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 2000
        or any(type(item) is not int or item < 1 for item in ids)
        or len(set(ids)) != len(ids)
        or action not in {"confirm", "dismiss"}
    ):
        raise ValueError("需提供不重复的记录 ID 与 confirm/dismiss 操作，最多 2000 条")
    with store.transaction() as db:
        if expected != store.revision():
            raise ConflictError("图版本已变化，请刷新后复核")
        for claim_id in ids:
            if not db.execute("SELECT 1 FROM claims WHERE claim_id=?", (claim_id,)).fetchone():
                raise NotFound("批次包含不存在的记录，未写入任何复核")
            if db.execute("SELECT 1 FROM claims WHERE replaces_claim=?", (claim_id,)).fetchone():
                raise ValueError("记录已有人工修改版，请复核最新记录")
            store.assert_reviewable(claim_id, action)
        for claim_id in ids:
            db.execute(
                "INSERT INTO review_actions "
                "(claim_id,action,actor,expected_revision,recorded_at) VALUES (?,?,?,?,?)",
                (claim_id, action, actor, expected, now()),
            )
        revision = store.revision()
    return {"revision": revision, "claim_ids": ids, "action": action}


def edit(store: Store, claim_id: int, body: dict[str, Any]) -> dict[str, Any]:
    actor, expected = _human(body.get("actor")), _expected(body.get("expected_revision"))
    payload, scope = body.get("payload"), body.get("scope")
    if not isinstance(payload, dict) or "scope" not in body:
        raise ValueError("修改需提供结构化 payload 与范围")
    with store.transaction() as db:
        if store.revision() != expected:
            raise ConflictError("图版本已变化，请刷新后修改")
        row = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise NotFound("记录不存在")
        if db.execute("SELECT 1 FROM claims WHERE replaces_claim=?", (claim_id,)).fetchone():
            raise ValueError("记录已有修改版，请打开最新记录")
        store.assert_reviewable(claim_id, "edit")
        original = json.loads(row["payload"])
        from rg.record.schema import validate_decision, validate_question, validate_scope

        manual_question = (
            row["basis"] == "manual"
            and row["actor"].startswith("human:")
            and row["claim_type"] == "entity_version"
            and original.get("kind") == "question"
        )
        manual_decision = (
            row["basis"] == "manual"
            and row["actor"].startswith("human:")
            and row["claim_type"] == "decision_event"
            and original.get("speaker") == "user"
            and original.get("explicitness") == "explicit"
            and original.get("referent_unique") is True
        )
        if manual_question or manual_decision:
            scope = validate_scope(scope)
        elif not isinstance(scope, dict):
            raise ValueError("修改需提供完整范围")
        payload = dict(payload)
        if "evidence" in payload:
            raise ValueError("修改不能自行替换原文引用")
        if "scope" in payload:
            payload["scope"] = scope
        immutable = {
            "claim_type",
            "kind",
            "temp_id",
            "source",
            "target",
            "relation",
            "inputs",
            "semantics",
            "selected",
        }
        if any(payload.get(key) != original.get(key) for key in immutable):
            raise ValueError("修改不能重定向对象或关系；请另建人工记录")
        spans = _spans(store, claim_id)
        evidence_items = []
        for span in spans:
            raw = store.raw(span["event_id"])
            quote = raw[span["byte_start"] : span["byte_end"]]
            if digest(quote) != span["quote_sha256"]:
                raise ValueError("原文引用校验失败，不能复用证据")
            evidence_items.append(
                {key: span[key] for key in ("event_id", "byte_start", "byte_end", "quote_sha256")}
                | {"quote": quote.decode("utf-8")}
            )
        if manual_question:
            validate_question(payload | {"scope": scope, "evidence": evidence_items})
        elif manual_decision:
            validate_decision(payload | {"scope": scope, "evidence": evidence_items})
        else:
            errors = list(
                Draft202012Validator(CLAIM_SCHEMA).iter_errors(
                    payload | {"scope": scope, "evidence": evidence_items}
                )
            )
            if errors:
                raise ValueError("修改不符合 claim schema，需保留有效结构和证据")
        cursor = db.execute(
            "INSERT INTO claims (claim_type,entity_id,payload,scope,basis,actor,claim_state, "
            "replaces_claim,occurred_at,recorded_at) VALUES (?,?,?,?,?,?,'confirmed',?,?,?)",
            (
                row["claim_type"],
                row["entity_id"],
                dumps(payload),
                dumps(scope) if scope is not None else None,
                "manual",
                actor,
                claim_id,
                row["occurred_at"],
                now(),
            ),
        )
        new_claim_id = cursor.lastrowid
        for span in spans:
            db.execute(
                "INSERT INTO claim_evidence (claim_id,span_id,role) VALUES (?,?,?)",
                (new_claim_id, span["span_id"], span["role"]),
            )
        db.execute(
            "INSERT INTO review_actions (claim_id,action,new_claim_id,reason,actor, "
            "expected_revision,recorded_at) VALUES (?,'edit',?,?,?,?,?)",
            (claim_id, new_claim_id, "人工修改；原文与旧记录保留", actor, expected, now()),
        )
        revision = store.revision()
    return {"revision": revision, "claim_id": new_claim_id, "replaces_claim": claim_id}


def graph(store: Store, project: str) -> dict[str, Any]:
    if not project:
        raise ValueError("研究图需指定项目")
    project_exists(store, project)
    rows = store.db.execute(
        "SELECT c.* FROM claims c JOIN entities e USING(entity_id) WHERE e.project_id=? "
        "ORDER BY c.claim_id LIMIT ?",
        (project, MAX_GRAPH_CLAIMS + 1),
    ).fetchall()
    entries = [_claim(store, row) for row in rows[:MAX_GRAPH_CLAIMS]]
    return {
        "revision": store.revision(),
        "claims": entries,
        "partial": len(rows) > MAX_GRAPH_CLAIMS,
        "limit": MAX_GRAPH_CLAIMS,
        "run_states": [
            dict(row)
            for row in store.db.execute(
                "SELECT run_id,state,exit_code,started_at,ended_at FROM runs WHERE project_id=?",
                (project,),
            )
        ],
        "capabilities": {"impact_propagation": False, "artifact_diff": False},
    }
