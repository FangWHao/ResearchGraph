"""分页复核模型记录的保存引用，不改原件、审核或研究状态。"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

import zstandard

from rg.api.views import NotFound
from rg.ingest.exclusion import current_exclusion
from rg.ingest.scanner import PARSER_VERSION
from rg.query.reader import pagination
from rg.slim.slimmer import SLIM_VERSION
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

POLICY_VERSION = "1"
REFERENCES = (
    "SELECT c.claim_id,c.claim_type,c.claim_state,c.extraction_run_id,ce.span_id,"
    "s.event_id,s.byte_start,s.byte_end,s.quote_sha256,r.session_pk,r.kind,r.record_index,"
    "r.object_sha256,r.call_id,r.exclude_reason,r.alias_of,se.project_id AS source_project,"
    "f.parser,(SELECT action FROM review_actions a WHERE a.claim_id=c.claim_id "
    "ORDER BY a.action_id DESC LIMIT 1) AS review_action,"
    "EXISTS(SELECT 1 FROM effective_dedupe_links d WHERE d.alias_id=r.event_id) AS duplicate "
    "FROM claims c JOIN effective_entities e USING(entity_id) "
    "LEFT JOIN claim_evidence ce USING(claim_id) LEFT JOIN evidence_spans s USING(span_id) "
    "LEFT JOIN effective_raw_events r USING(event_id) LEFT JOIN sessions se USING(session_pk) "
    "LEFT JOIN source_files f USING(file_instance_id) "
    "WHERE e.project_id=? AND (c.extraction_run_id IS NOT NULL OR substr(c.actor,1,6)=?) "
    "ORDER BY c.claim_id,coalesce(ce.span_id,0)"
)


def _diagnose(store: Store, project: str, row: sqlite3.Row) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in (
            "claim_id",
            "claim_type",
            "claim_state",
            "extraction_run_id",
            "span_id",
            "event_id",
            "byte_start",
            "byte_end",
            "exclude_reason",
        )
    }
    result["effective_state"] = (
        ("confirmed" if row["review_action"] == "confirm" else "dismissed")
        if row["review_action"]
        else row["claim_state"]
    )
    reason = (
        "no_evidence"
        if row["span_id"] is None
        else "source_project_mismatch"
        if row["source_project"] != project
        else "mirror_event"
        if row["alias_of"] is not None
        else "duplicate_event"
        if row["duplicate"]
        else row["exclude_reason"]
    )
    status = "unavailable" if reason == "no_evidence" else "excluded"
    if reason is None:
        try:
            raw = store.raw(row["event_id"])
            reason = current_exclusion(store, row, raw=raw)
            if reason is None:
                left, right = row["byte_start"], row["byte_end"]
                if not 0 <= left < right <= len(raw):
                    raise ValueError("引用范围超出原件")
                quote = raw[left:right]
                quote.decode("utf-8")
                if digest(quote) != row["quote_sha256"]:
                    raise ValueError("引用摘要不一致")
                status = "not_excluded"
        except (OSError, ValueError, IndexError, KeyError, TypeError, zstandard.ZstdError) as error:
            status, reason = "unavailable", "source_unavailable"
            result["error_class"] = type(error).__name__
    return result | {"status": status, "input_exclude_reason": reason}


def query(store: Store, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - {"project", "limit", "offset", "expected_audit"}:
        raise ValueError("未知来源审计参数")
    project = values.get("project")
    if (
        not isinstance(project, str)
        or not store.db.execute(
            "SELECT 1 FROM active_projects WHERE project_id=?", (project,)
        ).fetchone()
    ):
        raise NotFound("项目不存在")
    limit, offset = pagination(values)
    expected = values.get("expected_audit")
    if expected is not None and (
        not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
    ):
        raise ValueError("expected_audit 需为首页面的审计摘要")
    started = now()
    policy = {"audit": POLICY_VERSION, "parser": PARSER_VERSION, "slim": SLIM_VERSION}
    with store.snapshot():
        revision = store.revision()
        fingerprint = hashlib.sha256(dumps([project, revision, policy]).encode())
        selected, total = [], 0
        # 只遍历元数据算摘要；原件只为当前页打开，不加载完整会话或记录正文。
        for row in store.db.execute(REFERENCES, (project, "model:")):
            fingerprint.update(dumps(dict(row)).encode() + b"\n")
            if offset <= total < offset + limit:
                selected.append(row)
            total += 1
        for row in store.db.execute(
            "SELECT r.event_id,r.session_pk,r.kind,r.call_id,r.alias_of,r.exclude_reason,"
            "r.object_sha256,r.record_index,r.occurred_at,r.recorded_at,f.parser,"
            "d.canonical_id FROM effective_raw_events r JOIN sessions se USING(session_pk) "
            "JOIN source_files f USING(file_instance_id) "
            "LEFT JOIN effective_dedupe_links d ON d.alias_id=r.event_id "
            "WHERE se.project_id=? ORDER BY r.event_id",
            (project,),
        ):
            fingerprint.update(dumps(dict(row)).encode() + b"\n")
        audit_id = fingerprint.hexdigest()
        if expected is not None and expected != audit_id:
            raise ConflictError("来源元数据或校验版本已变化，请重新审计，不能拼接旧页")
        items = [_diagnose(store, project, row) for row in selected]
    return {
        "project_id": project,
        "revision": revision,
        "audit_id": audit_id,
        "policy": policy,
        "items": items,
        "total_references": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
        "page_counts": {
            state: sum(item["status"] == state for item in items)
            for state in ("excluded", "not_excluded", "unavailable")
        },
        "started_at": started,
        "finished_at": now(),
        "notice": "审计摘要只固定数据库元数据及校验版本；对象可用性按本次读取报告。"
        "未发现排除原因不证明断言语义有效；不改变原记录、人工审核或采用状态。",
    }
