"""模型记录的保存来源资格；派生诊断只追加，读取只核对元数据。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from typing import Any

import zstandard

from rg.ingest.exclusion import current_exclusion, saved_parse
from rg.ingest.scanner import PARSER_VERSION
from rg.slim.slimmer import SLIM_VERSION
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from rg.store.ownership import entity_owner, event_owner, historical_source, read_table

POLICY_VERSION = "1"


def policy() -> dict[str, str]:
    return {"claim_sources": POLICY_VERSION, "parser": PARSER_VERSION, "slim": SLIM_VERSION}


def modeled(row: Mapping[str, Any] | sqlite3.Row) -> bool:
    return bool(
        row["extraction_run_id"] is not None
        or row["basis"] == "model_inference"
        or row["actor"].startswith("model:")
    )


def inputs(
    store: Store, claim_id: int, known_at: str | None = None
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    claim = store.db.execute(
        f"SELECT c.*,e.project_id FROM claims c LEFT JOIN "
        f"{read_table(store.db, 'entities')} e USING(entity_id) "
        "WHERE c.claim_id=?",
        (claim_id,),
    ).fetchone()
    if claim is None:
        raise ValueError("来源资格记录不存在")
    rows = []
    for saved in store.db.execute(
        "SELECT ce.span_id,ce.role,es.byte_start,es.byte_end,es.quote_sha256,r.*,f.parser,"
        "se.project_id AS source_project,"
        "(SELECT canonical_id FROM effective_dedupe_links WHERE alias_id=r.event_id) AS duplicate "
        "FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
        "JOIN effective_raw_events r USING(event_id) JOIN source_files f USING(file_instance_id) "
        "JOIN sessions se USING(session_pk) WHERE ce.claim_id=? ORDER BY ce.span_id,ce.role",
        (claim_id,),
    ):
        row = dict(saved)
        if known_at is not None:
            row = historical_source(store.db, row, known_at)
            row["source_project"] = event_owner(store.db, row["event_id"], known_at)
        row["calls"] = []
        if row["kind"] == "tool_result" and row["call_id"]:
            for saved_call in store.db.execute(
                "SELECT r.*,f.parser FROM effective_raw_events r JOIN source_files f "
                "USING(file_instance_id) WHERE r.call_id=? "
                "AND r.kind IN ('tool_call','file_edit','plan_update') ORDER BY r.event_id",
                (row["call_id"],),
            ):
                call = dict(saved_call)
                if known_at is not None:
                    call = historical_source(store.db, call, known_at)
                if call["session_pk"] == row["session_pk"] and call["alias_of"] is None:
                    row["calls"].append(call)
                    if len(row["calls"]) == 2:
                        break
        rows.append(row)
    value = dict(claim)
    if known_at is not None:
        value["project_id"] = entity_owner(store.db, value["entity_id"], known_at)
    # 正文、作者及路径只进入本地摘要，不复制到诊断载荷或模型请求。
    signature = digest(dumps([policy(), value, rows]).encode())
    return signature, value, rows


def reference(store: Store, project: str | None, row: dict[str, Any]) -> dict[str, Any]:
    result = {k: row[k] for k in ("span_id", "event_id", "occurred_at", "recorded_at")}
    reason = (
        "source_project_mismatch"
        if row["source_project"] != project
        else "mirror_event"
        if row["alias_of"] is not None
        else "duplicate_event"
        if row["duplicate"] is not None
        else row["exclude_reason"]
    )
    if reason:
        return result | {"status": "excluded", "reason": reason}
    try:
        raw = store.raw(row["event_id"])
        reason = current_exclusion(store, row, raw=raw, call_visible=lambda _: False)
        if reason:
            return result | {"status": "excluded", "reason": reason}
        left, right = row["byte_start"], row["byte_end"]
        if not 0 <= left < right <= len(raw):
            raise ValueError("引用范围超出原件")
        quote = raw[left:right]
        quote.decode("utf-8")
        if digest(quote) != row["quote_sha256"]:
            raise ValueError("引用摘要不一致")
    except (OSError, ValueError, IndexError, KeyError, TypeError, zstandard.ZstdError) as error:
        return result | {
            "status": "unavailable",
            "reason": "source_unavailable",
            "error_class": type(error).__name__,
        }
    result |= {"status": "not_excluded", "reason": None}
    if len(row["calls"]) == 1:
        call = row["calls"][0]
        associated = {k: call[k] for k in ("event_id", "occurred_at", "recorded_at")}
        try:
            reason = saved_parse(store, call).excluded
            if reason in {"injected_by_rg", "unknown_tool_namespace"}:
                result["associated_call"] = associated | {"status": "excluded", "reason": reason}
        except (OSError, ValueError, IndexError, KeyError, TypeError, zstandard.ZstdError) as error:
            result["associated_call"] = associated | {
                "status": "unavailable",
                "reason": "source_unavailable",
                "error_class": type(error).__name__,
            }
    return result


def projection(
    payload: dict[str, Any], visible: Callable[[dict[str, Any]], bool] | None = None
) -> dict[str, Any]:
    refs = []
    for original in payload["references"]:
        row = {k: v for k, v in original.items() if k != "associated_call"}
        if visible is not None and not visible(row):
            row |= {"status": "unavailable", "reason": "source_not_visible"}
            refs.append(row)
            continue
        associated = original.get("associated_call")
        if associated and (visible is None or visible(associated)):
            row |= {k: associated[k] for k in ("status", "reason")}
            row["associated_event_id"] = associated["event_id"]
        refs.append(row)
    statuses = {r["status"] for r in refs}
    status = (
        "excluded"
        if "excluded" in statuses
        else "unavailable"
        if not refs or "unavailable" in statuses
        else "not_excluded"
    )
    return {
        "status": status,
        "input_eligible": status == "not_excluded",
        "policy": policy(),
        "references": refs,
        "reason": "no_evidence" if not refs else None,
    }


def recorded(
    store: Store,
    claim_id: int,
    visible: Callable[[dict[str, Any]], bool] | None = None,
    *,
    known_at: str | None = None,
) -> dict[str, Any]:
    signature, claim, _ = inputs(store, claim_id, known_at)
    if not modeled(claim):
        return {"status": "not_required", "input_eligible": True, "policy": policy()}
    row = store.db.execute(
        f"SELECT payload,recorded_at FROM {read_table(store.db, 'claim_source_checks')} "
        "WHERE claim_id=? AND input_signature=? ORDER BY check_id DESC LIMIT 1",
        (claim_id, signature),
    ).fetchone()
    if row:
        return projection(json.loads(row["payload"]), visible) | {"checked_at": row["recorded_at"]}
    return {"status": "pending", "input_eligible": False, "policy": policy()}


def assess(store: Store, claim_id: int, *, refresh: bool = False) -> dict[str, Any]:
    with (
        nullcontext(store.db) if store.db.in_transaction or store.readonly else store.transaction()
    ):
        signature, claim, rows = inputs(store, claim_id)
        if not modeled(claim):
            return {"status": "not_required", "input_eligible": True, "policy": policy()}
        previous = store.db.execute(
            f"SELECT payload FROM {read_table(store.db, 'claim_source_checks')} "
            f"WHERE claim_id=? AND input_signature=? "
            "ORDER BY check_id DESC LIMIT 1",
            (claim_id, signature),
        ).fetchone()
        if previous and not refresh:
            return projection(json.loads(previous[0]))
        payload = {"references": [reference(store, claim["project_id"], row) for row in rows]}
        encoded = dumps(payload)
        if not store.readonly and claim["project_id"] and (not previous or previous[0] != encoded):
            store.db.execute(
                "INSERT INTO claim_source_checks"
                "(claim_id,project_id,input_signature,payload,recorded_at) "
                "VALUES (?,?,?,?,?)",
                (claim_id, claim["project_id"], signature, encoded, now()),
            )
        return projection(payload)


def check_batch(store: Store, limit: int = 100) -> dict[str, int]:
    """持久轮转；旧不可用项自动重查，不长期占满新记录的批次。"""
    row = store.db.execute(
        f"SELECT job_id,payload FROM {read_table(store.db, 'jobs')} "
        f"WHERE kind=? ORDER BY job_id LIMIT 1",
        ("claim_source_cursor",),
    ).fetchone()
    cursor = json.loads(row["payload"])["last_claim"] if row else 0
    ids = [
        r[0]
        for r in store.db.execute(
            "SELECT claim_id FROM claims WHERE claim_id>? AND (extraction_run_id IS NOT NULL "
            "OR basis='model_inference' OR substr(actor,1,6)='model:') ORDER BY claim_id LIMIT ?",
            (cursor, limit),
        )
    ]
    if not ids and cursor:
        cursor = 0
        ids = [
            r[0]
            for r in store.db.execute(
                "SELECT claim_id FROM claims WHERE extraction_run_id IS NOT NULL "
                "OR basis='model_inference' OR substr(actor,1,6)='model:' "
                "ORDER BY claim_id LIMIT ?",
                (limit,),
            )
        ]
    count = 0
    for claim_id in ids:
        previous = recorded(store, claim_id)
        result = assess(store, claim_id, refresh=True)
        count += result["status"] != previous["status"]
    if ids:
        with store.transaction() as db:
            payload = dumps({"last_claim": ids[-1]})
            if row:
                db.execute(
                    "UPDATE jobs SET payload=?,updated_at=? WHERE job_id=?",
                    (payload, now(), row["job_id"]),
                )
            else:
                db.execute(
                    "INSERT INTO jobs(kind,payload,state,updated_at) VALUES (?,?,'done',?)",
                    ("claim_source_cursor", payload, now()),
                )
    return {"claim_sources_checked": len(ids), "claim_sources_changed": count}
