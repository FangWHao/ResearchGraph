"""独立原话规则的历史资格；原审核保持不变，诊断只追加。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

import zstandard

from rg.derive import claim_sources
from rg.ingest.common import parse
from rg.ingest.spans import event_span
from rg.query.time import instant
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from rg.store.ownership import entity_owner, read_table
from rg.store.scopes import known_scope

POLICY_VERSION = "1"
SUPPORTED = {"explicit-user-v1", "explicit-user-v2", "explicit-user-v3"}


def policy() -> dict[str, str]:
    return {"rule_references": POLICY_VERSION, **claim_sources.policy()}


def required(review: Mapping[str, Any] | sqlite3.Row | None) -> bool:
    return bool(review and review["action"] == "confirm" and review["actor"].startswith("rule:"))


def latest(store: Store, claim: int) -> dict[str, Any] | None:
    row = store.db.execute(
        "SELECT * FROM review_actions WHERE claim_id=? ORDER BY action_id DESC LIMIT 1", (claim,)
    ).fetchone()
    return dict(row) if row else None


def _known(value: Any, cutoff: Any) -> bool:
    when = instant(value)
    return when is not None and cutoff is not None and when <= cutoff


def _sources(store: Store, claim: int, cutoff: Any) -> list[dict[str, Any]]:
    _, _, rows = claim_sources.inputs(store, claim, cutoff.isoformat() if cutoff else None)
    for row in rows:
        row["calls"] = [c for c in row["calls"] if _known(c["recorded_at"], cutoff)]
    return rows


def inputs(store: Store, review: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    from rg.store.ownership import entity_owner

    _, claim, _ = claim_sources.inputs(store, review["claim_id"], review["recorded_at"])
    cutoff = instant(review["recorded_at"])
    rows = [
        dict(row)
        for row in store.db.execute(
            f"SELECT c.*,e.kind FROM claims c JOIN {read_table(store.db, 'entities')} "
            f"e USING(entity_id) "
            "ORDER BY c.claim_id DESC",
        )
        if _known(row["recorded_at"], cutoff)
        and entity_owner(store.db, row["entity_id"], review["recorded_at"]) == claim["project_id"]
    ]
    reviews: dict[int, dict[str, Any]] = {}
    for row in store.db.execute(
        "SELECT a.*,c.entity_id AS owner_entity FROM review_actions "
        "a JOIN claims c USING(claim_id) "
        "ORDER BY a.action_id",
    ):
        if (
            _known(row["recorded_at"], cutoff)
            and entity_owner(store.db, row["owner_entity"], review["recorded_at"])
            == claim["project_id"]
        ):
            saved = dict(row)
            saved.pop("owner_entity")
            reviews[row["claim_id"]] = saved
    replaced = {r["replaces_claim"] for r in rows if r["replaces_claim"] is not None}
    definitions = []
    for row in rows:
        if row["claim_type"] != "entity_version":
            continue
        row["review"] = reviews.get(row["claim_id"])
        row["replaced"] = row["claim_id"] in replaced
        row["sources"] = _sources(store, row["claim_id"], cutoff)
        definitions.append(row)
    data = {
        "claim": claim,
        "review": dict(review),
        "definitions": definitions,
        "sources": _sources(store, claim["claim_id"], cutoff),
    }
    run = store.db.execute(
        "SELECT working_set_ids,created_at FROM extraction_runs WHERE extraction_run_id=?",
        (claim["extraction_run_id"],),
    ).fetchone()
    data["working_set"] = dict(run) if run and _known(run["created_at"], cutoff) else None
    return digest(dumps([policy(), data]).encode()), data


def _projection(payload: dict[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    return payload | {
        "input_eligible": payload["status"] == "verified",
        "policy": policy(),
        "review_action_id": review["action_id"],
        "proof_known_at": review["recorded_at"],
    }


def recorded(
    store: Store, claim: int, review: Mapping[str, Any] | None = None, *, selected: bool = False
) -> dict[str, Any]:
    review = review if selected else latest(store, claim)
    if not required(review):
        return {"status": "not_required", "input_eligible": True, "policy": policy()}
    assert review is not None
    signature, _ = inputs(store, review)
    saved = store.db.execute(
        f"SELECT payload,recorded_at FROM "
        f"{read_table(store.db, 'rule_reference_checks')} WHERE review_action_id=? "
        "AND input_signature=? ORDER BY check_id DESC LIMIT 1",
        (review["action_id"], signature),
    ).fetchone()
    if saved:
        return _projection(json.loads(saved["payload"]), review) | {
            "checked_at": saved["recorded_at"]
        }
    return _projection({"status": "pending", "reasons": ["rule_check_pending"]}, review)


def _qualified(
    store: Store, claim: dict[str, Any], sources: list[dict[str, Any]], cutoff: Any
) -> bool:
    if not claim_sources.modeled(claim):
        return True
    payload = {
        "references": [claim_sources.reference(store, claim.get("project_id"), r) for r in sources]
    }
    return claim_sources.projection(payload, lambda r: _known(r["recorded_at"], cutoff))[
        "input_eligible"
    ]


def _objects(
    store: Store, data: dict[str, Any], explicit: bool
) -> tuple[dict[str, Any], list[int]]:
    scope = json.loads(data["claim"]["scope"] or "{}")
    cutoff = instant(data["review"]["recorded_at"])
    objects: dict[str, Any] = {}
    seen = set()
    other_labels, other_ids = set(), set()
    ids = []
    for row in data["definitions"]:
        review = row["review"]
        state = (
            ("confirmed" if review["action"] == "confirm" else "dismissed")
            if review
            else row["claim_state"]
        )
        if state == "dismissed" or row["replaced"]:
            continue
        if not _qualified(
            store, row | {"project_id": data["claim"]["project_id"]}, row["sources"], cutoff
        ):
            continue
        row_scope = json.loads(row["scope"] or "{}")
        key = (row["entity_id"], dumps(row_scope))
        if key in seen:
            continue
        seen.add(key)
        payload = json.loads(row["payload"])
        if row_scope == scope:
            objects[row["entity_id"]] = {"kind": row["kind"], **payload}
            ids.append(row["claim_id"])
        elif not explicit:
            other_labels.add(payload.get("label", ""))
            other_ids.add(row["entity_id"])
    for entity, row in objects.items():
        row["ambiguous_label"] = row.get("label") in other_labels
        row["ambiguous_scope"] = entity in other_ids
    return objects, sorted(ids)


def _verify(store: Store, data: dict[str, Any]) -> dict[str, Any]:
    from rg.extract.rules import _statement

    claim, review = data["claim"], data["review"]
    cutoff = instant(review["recorded_at"])
    if cutoff is None or not _known(claim["recorded_at"], cutoff):
        return {"status": "unverifiable", "reasons": ["proof_time_unknown"]}
    try:
        proof = json.loads(review["reason"])
    except (ValueError, TypeError):
        return {"status": "invalid", "reasons": ["proof_json_invalid"]}
    if not isinstance(proof, dict):
        return {"status": "invalid", "reasons": ["proof_json_invalid"]}
    version = proof.get("rule_version")
    if not isinstance(version, str) or version not in SUPPORTED:
        return {"status": "unverifiable", "reasons": ["rule_version_unknown"]}
    scope = json.loads(claim["scope"] or "{}")
    if (
        review["actor"] != "rule:" + version
        or proof.get("basis") != "direct_record"
        or proof.get("scope") != scope
        or not known_scope(scope)
        or claim["claim_type"] not in {"decision_event", "evidence_event", "join_ports"}
    ):
        return {"status": "invalid", "reasons": ["proof_claim_mismatch"]}
    explicit = proof.get("scope_filter") is not None
    if "scope_filter" in proof and explicit and proof["scope_filter"] != scope:
        return {"status": "invalid", "reasons": ["scope_filter_mismatch"]}
    if (
        version == "explicit-user-v3"
        and not {"scope_filter", "definition_ids", "evidence", "allowed_ids"} <= proof.keys()
    ):
        return {"status": "unverifiable", "reasons": ["proof_context_missing"]}
    objects, ids = _objects(store, data, explicit)
    if "definition_ids" in proof and proof["definition_ids"] != ids:
        return {"status": "invalid", "reasons": ["definition_context_mismatch"]}
    if "allowed_ids" in proof:
        allowed = proof["allowed_ids"]
    else:
        working = data["working_set"]
        allowed = (
            json.loads(working["working_set_ids"])
            if working and working["working_set_ids"]
            else None
        )
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        return {"status": "unverifiable", "reasons": ["working_set_not_recorded"]}
    refs = [r for r in data["sources"] if r["role"] == "support"]
    if not refs:
        return {"status": "unverifiable", "reasons": ["proof_evidence_missing"]}
    evidence, outcomes = [], []
    payload = {"claim_type": claim["claim_type"], **json.loads(claim["payload"])}
    for row in refs:
        if not _known(row["recorded_at"], cutoff):
            return {"status": "unverifiable", "reasons": ["proof_source_not_known"]}
        if (
            row["role"] != "support"
            or row["kind"] != "user_msg"
            or row["exclude_reason"]
            or row["alias_of"] is not None
            or row["duplicate"] is not None
            or row["source_project"] != claim["project_id"]
        ):
            return {"status": "invalid", "reasons": ["proof_not_user_source"]}
        checked = claim_sources.reference(store, claim["project_id"], row)
        if checked["status"] != "not_excluded":
            return {
                "status": "unavailable" if checked["status"] == "unavailable" else "invalid",
                "reasons": ["proof_source_" + checked["status"]],
            }
        raw = store.raw(row["event_id"])
        tool = store.db.execute(
            "SELECT tool FROM sessions WHERE session_pk=?", (row["session_pk"],)
        ).fetchone()[0]
        parsed = parse(tool, json.loads(raw))
        bounds = event_span(raw, tool, row["record_index"])
        if not bounds or not bounds[2] or row["record_index"] >= len(parsed):
            return {"status": "invalid", "reasons": ["proof_body_missing"]}
        a, b, _ = bounds
        body = parsed[row["record_index"]]
        if (
            body.role != "user"
            or body.kind != "user_msg"
            or body.excluded
            or not row["byte_start"] <= a < b <= row["byte_end"]
            or len([p for p in parsed if p.kind == "user_msg" and p.text]) != 1
        ):
            return {"status": "invalid", "reasons": ["proof_not_full_user_body"]}
        text = json.loads(b'"' + raw[a:b] + b'"')
        if re.search(r"</?rg-context\b", text, re.I):
            return {"status": "invalid", "reasons": ["proof_injected_body"]}
        matched = _statement(text, payload, objects, set(allowed))
        if not matched:
            # 旧证明没有显式范围记录，保留歧义而不推断其调用模式。
            if "scope_filter" not in proof and _statement(
                text, payload, _objects(store, data, True)[0], set(allowed)
            ):
                return {"status": "unverifiable", "reasons": ["scope_filter_not_recorded"]}
            return {"status": "invalid", "reasons": ["statement_not_proven"]}
        outcomes.append(matched)
        evidence.append(
            {
                "event_id": row["event_id"],
                "byte_start": a,
                "byte_end": b,
                "quote_sha256": digest(raw[a:b]),
            }
        )
    if (
        any(item != outcomes[0] for item in outcomes)
        or proof.get("target_ids") != outcomes[0][0]
        or proof.get("reason_source") != outcomes[0][1]
        or any(proof.get(k) != v for k, v in evidence[0].items())
        or ("evidence" in proof and proof["evidence"] != evidence)
    ):
        return {"status": "invalid", "reasons": ["proof_statement_mismatch"]}
    return {"status": "verified", "reasons": []}


def assess(
    store: Store,
    claim: int,
    *,
    refresh: bool = False,
    review: Mapping[str, Any] | None = None,
    selected: bool = False,
) -> dict[str, Any]:
    review = review if selected else latest(store, claim)
    if not required(review):
        return {"status": "not_required", "input_eligible": True, "policy": policy()}
    assert review is not None
    with (
        nullcontext(store.db) if store.db.in_transaction or store.readonly else store.transaction()
    ):
        signature, data = inputs(store, review)
        previous = store.db.execute(
            f"SELECT payload FROM {read_table(store.db, 'rule_reference_checks')} "
            f"WHERE review_action_id=? "
            "AND input_signature=? ORDER BY check_id DESC LIMIT 1",
            (review["action_id"], signature),
        ).fetchone()
        if previous and not refresh:
            return _projection(json.loads(previous[0]), review)
        try:
            payload = _verify(store, data)
        except (OSError, ValueError, IndexError, KeyError, TypeError, zstandard.ZstdError):
            payload = {"status": "unavailable", "reasons": ["rule_check_unavailable"]}
        encoded = dumps(payload)
        if (
            not store.readonly
            and data["claim"]["project_id"]
            and (not previous or previous[0] != encoded)
        ):
            store.db.execute(
                "INSERT INTO rule_reference_checks(review_action_id,claim_id,project_id,"
                "input_signature,payload,recorded_at) VALUES(?,?,?,?,?,?)",
                (
                    review["action_id"],
                    claim,
                    entity_owner(store.db, data["claim"]["entity_id"]),
                    signature,
                    encoded,
                    now(),
                ),
            )
        return _projection(payload, review)


def check_batch(store: Store, limit: int = 100) -> dict[str, int]:
    row = store.db.execute(
        f"SELECT job_id,payload FROM {read_table(store.db, 'jobs')} "
        f"WHERE kind=? ORDER BY job_id LIMIT 1",
        ("rule_reference_cursor",),
    ).fetchone()
    cursor = json.loads(row["payload"])["last_review"] if row else 0

    def batch(after: int) -> list[tuple[int, int]]:
        return [
            tuple(r)
            for r in store.db.execute(
                "SELECT action_id,claim_id FROM review_actions WHERE action_id>? "
                "AND action='confirm' AND substr(actor,1,5)='rule:' ORDER BY action_id LIMIT ?",
                (after, limit),
            )
        ]

    ids = batch(cursor) or (batch(0) if cursor else [])
    changed = 0
    for action, claim in ids:
        review = dict(
            store.db.execute("SELECT * FROM review_actions WHERE action_id=?", (action,)).fetchone()
        )
        before = recorded(store, claim, review, selected=True)
        result = assess(store, claim, refresh=True, review=review, selected=True)
        changed += result["status"] != before["status"]
    if ids:
        with store.transaction() as db:
            payload = dumps({"last_review": ids[-1][0]})
            if row:
                db.execute(
                    "UPDATE jobs SET payload=?,updated_at=? WHERE job_id=?",
                    (payload, now(), row["job_id"]),
                )
            else:
                db.execute(
                    "INSERT INTO jobs(kind,payload,state,updated_at) VALUES (?,?,'done',?)",
                    ("rule_reference_cursor", payload, now()),
                )
    return {"rule_references_checked": len(ids), "rule_references_changed": changed}
