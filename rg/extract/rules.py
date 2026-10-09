from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from rg.ingest.common import parse
from rg.ingest.spans import event_span
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

RULE_VERSION = "explicit-user-v1"
UNKNOWN = {"unknown", "未知", "未确定", "不详", "unspecified", "?"}
VERBS = {
    "accepted": r"(?:采用|选用|使用|选择|保留|adopt |accept |choose |use |retain )",
    "withdrawn": r"(?:撤回|停止使用|不再使用|弃用|withdraw |stop using |abandon )",
    "rejected": r"(?:拒绝|否决|不采用|reject |do not adopt )",
    "refuted": r"(?:否定|证伪|refute )",
}
REASONS = {
    "accepted": {"用户明确采用", "用户明确选择", "用户明确使用", "用户明确保留"},
    "withdrawn": {"用户明确撤回", "用户明确停止使用", "用户明确弃用"},
    "rejected": {"用户明确拒绝", "用户明确否决", "用户明确不采用"},
    "refuted": {"用户明确否定", "用户明确证伪"},
}
SUBJECT = r"(?:(?:我|我们)(?:现在)?(?:决定|确认)?|请|决定|确认|I |we )?"


def _plain(text: str) -> str:
    return text.strip().rstrip("。.!！").strip()


def _reference(text: str) -> str:
    text = text.strip()
    for left, right in [("[", "]"), ("`", "`"), ("“", "”"), ("「", "」"), ('"', '"')]:
        if text.startswith(left) and text.endswith(right):
            return text[len(left) : -len(right)].strip()
    return text


def _objects(
    store: Store, project: str, scope: dict[str, str], explicit_scope: bool
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    other_labels: set[str] = set()
    rows = store.db.execute(
        "SELECT e.entity_id,e.kind,c.claim_id,c.payload,c.scope FROM entities e JOIN claims c "
        "USING(entity_id) WHERE e.project_id=? AND c.claim_type='entity_version' "
        "ORDER BY c.claim_id DESC",
        (project,),
    )
    for row in rows:
        if row["entity_id"] in result or store.claim_state(row["claim_id"]) == "dismissed":
            continue
        data = json.loads(row["payload"])
        if json.loads(row["scope"] or "{}") == scope:
            result[row["entity_id"]] = {"kind": row["kind"], **data}
        elif not explicit_scope:
            other_labels.add(data.get("label", ""))
    for data in result.values():
        data["ambiguous_label"] = data.get("label") in other_labels
    return result


def _resolve(token: str, objects: dict[str, dict[str, Any]], allowed: set[str]) -> str | None:
    token = _reference(token)
    if token in allowed and token in objects:
        return token
    matches = [entity for entity, data in objects.items() if data.get("label") == token]
    return (
        matches[0]
        if (
            len(matches) == 1
            and matches[0] in allowed
            and not objects[matches[0]]["ambiguous_label"]
        )
        else None
    )


def _statement(
    text: str, payload: dict[str, Any], objects: dict[str, dict[str, Any]], allowed: set[str]
) -> tuple[list[str], str] | None:
    text = _plain(text)
    kind = payload["claim_type"]
    target = payload["target"]
    if target not in allowed or target not in objects:
        return None
    if kind == "evidence_event" and objects[target]["kind"] != "finding":
        return None
    if kind == "join_ports":
        if payload["semantics"] != "all_required" or objects[target]["kind"] != "join":
            return None
        match = re.fullmatch(
            r"(?:将)?(.+?)共同输入(.+?)，(?:所有输入|全部输入)(?:都)?(?:必需|必不可少)", text
        )
        if not match or _resolve(match[2], objects, allowed) != target:
            return None
        declared = {item["ref"]: item["port"] for item in payload["inputs"]}
        found: set[str] = set()
        for item in re.split(r"、|和|与", match[1]):
            named = re.fullmatch(r"(.+?)（端口[：:]?(.+?)）", item.strip())
            ref = _resolve(named[1] if named else item, objects, allowed)
            if ref is None or ref in found or ref not in declared:
                return None
            port = named[2] if named else _reference(item)
            if port != declared[ref]:
                return None
            found.add(ref)
        if len(found) < 2 or found != set(declared):
            return None
        return [target, *sorted(found)], "explicit-inputs"
    action = payload.get("action") if kind == "decision_event" else payload.get("state")
    if action not in VERBS or (kind == "evidence_event" and action != "refuted"):
        return None
    if kind == "decision_event" and (
        payload.get("speaker") != "user"
        or payload.get("explicitness") != "explicit"
        or payload.get("referent_unique") is not True
    ):
        return None
    match = re.fullmatch(SUBJECT + VERBS[action] + r"(.+)", text, re.I)
    if not match:
        return None
    parts = re.split(r"，因为|，理由是| because ", match[1], maxsplit=1)
    if _resolve(parts[0], objects, allowed) != target:
        return None
    reason = _plain(payload.get("reason", ""))
    if len(parts) == 2:
        if re.search(r"[\n；;？?]|但是|不过|然而|算了|取消|撤回|停止|暂缓", parts[1]):
            return None
    if reason == text:
        return [target], "literal-statement"
    if len(parts) == 2:
        if reason == _plain(parts[1]):
            return [target], "literal-reason"
    if reason in REASONS[action]:
        return [target], "verified-action-description"
    return None


def _human_protected(store: Store, claim: sqlite3.Row) -> bool:
    for other in store.db.execute(
        "SELECT claim_id,actor,scope FROM claims WHERE entity_id=? AND claim_type=? "
        "AND claim_id!=?",
        (claim["entity_id"], claim["claim_type"], claim["claim_id"]),
    ):
        if json.loads(other["scope"] or "{}") != json.loads(claim["scope"] or "{}"):
            continue
        review = store.db.execute(
            "SELECT actor FROM review_actions WHERE claim_id=? ORDER BY action_id DESC LIMIT 1",
            (other["claim_id"],),
        ).fetchone()
        actor = review[0] if review else other["actor"]
        if actor.startswith("human:") and store.claim_state(other["claim_id"]) == "confirmed":
            return True
    return False


def _proof(
    store: Store,
    claim: sqlite3.Row,
    project: str,
    allowed: set[str],
    owned: dict[int, list[tuple[int, int]]] | None,
    scope_filter: dict[str, str] | None,
) -> dict[str, Any] | None:
    scope = json.loads(claim["scope"] or "{}")
    if not scope or any(not value or value.strip().lower() in UNKNOWN for value in scope.values()):
        return None
    if scope_filter is not None and scope_filter != scope:
        return None
    payload = {"claim_type": claim["claim_type"], **json.loads(claim["payload"])}
    if claim["claim_type"] not in {"decision_event", "evidence_event", "join_ports"}:
        return None
    objects = _objects(store, project, scope, scope_filter is not None)
    spans = store.db.execute(
        "SELECT es.*,ce.role,r.role AS speaker,r.kind,r.record_index,r.exclude_reason,"
        "s.project_id,s.tool FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
        "JOIN raw_events r USING(event_id) JOIN sessions s USING(session_pk) WHERE ce.claim_id=?",
        (claim["claim_id"],),
    ).fetchall()
    evidence = []
    target_ids: list[str] = []
    reason_source = ""
    for span in spans:
        if span["role"] != "support":
            continue
        if (
            span["speaker"] != "user"
            or span["kind"] != "user_msg"
            or span["exclude_reason"]
            or span["project_id"] != project
            or store.db.execute(
                "SELECT 1 FROM dedupe_links WHERE alias_id=?", (span["event_id"],)
            ).fetchone()
        ):
            return None
        raw = store.raw(span["event_id"])
        parsed = parse(span["tool"], json.loads(raw))
        index = span["record_index"]
        bounds = event_span(raw, span["tool"], index)
        if (
            not bounds
            or not bounds[2]
            or index >= len(parsed)
            or parsed[index].excluded
            or parsed[index].role != "user"
            or parsed[index].kind != "user_msg"
        ):
            return None
        a, b, _ = bounds
        if not (span["byte_start"] <= a < b <= span["byte_end"]):
            return None
        if digest(raw[span["byte_start"] : span["byte_end"]]) != span["quote_sha256"]:
            return None
        if owned is not None and not any(
            x <= a < b <= y for x, y in owned.get(span["event_id"], [])
        ):
            return None
        text = json.loads(b'"' + raw[a:b] + b'"')
        if (
            re.search(r"</?rg-context\b", text, re.I)
            or len([p for p in parsed if p.kind == "user_msg" and p.text]) != 1
        ):
            return None
        matched = _statement(text, payload, objects, allowed)
        if not matched:
            return None
        target_ids, reason_source = matched
        evidence.append(
            {
                "event_id": span["event_id"],
                "byte_start": a,
                "byte_end": b,
                "quote_sha256": digest(raw[a:b]),
            }
        )
    if not evidence:
        return None
    return {
        "rule_version": RULE_VERSION,
        "basis": "direct_record",
        "scope": scope,
        "target_ids": target_ids,
        "reason_source": reason_source,
        **evidence[0],
        "evidence": evidence,
    }


def confirm_explicit(
    store: Store,
    claim_ids: list[int],
    project: str,
    allowed_ids: set[str],
    owned_windows: dict[int, list[tuple[int, int]]] | None = None,
    scope_filter: dict[str, str] | None = None,
) -> int:
    """独立核对完整用户原话；模型字段与解释不能充当确认依据。"""

    def apply() -> int:
        confirmed = 0
        for claim_id in claim_ids:
            claim = store.db.execute(
                "SELECT * FROM claims WHERE claim_id=?", (claim_id,)
            ).fetchone()
            if (
                not claim
                or not claim["actor"].startswith("model:")
                or claim["claim_state"] != "candidate"
                or store.db.execute(
                    "SELECT 1 FROM review_actions WHERE claim_id=?", (claim_id,)
                ).fetchone()
                or _human_protected(store, claim)
            ):
                continue
            proof = _proof(store, claim, project, allowed_ids, owned_windows, scope_filter)
            if proof is not None:
                store.db.execute(
                    "INSERT INTO review_actions (claim_id,action,reason,actor,expected_revision,"
                    "recorded_at) "
                    "VALUES (?,'confirm',?,?,?,?)",
                    (claim_id, dumps(proof), "rule:" + RULE_VERSION, store.revision(), now()),
                )
                confirmed += 1
        return confirmed

    if store.db.in_transaction:
        return apply()
    with store.transaction():
        return apply()
