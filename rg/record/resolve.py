from __future__ import annotations

import json
from typing import Any

from rg.record.decide import ACTIONS, request_uuid
from rg.record.events import evidence, original, previous, windows
from rg.record.question import expected_revision, human
from rg.record.schema import validate_decision
from rg.record.targets import active_objects
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest


def pending(store: Store, claim_id: int) -> dict[str, Any] | None:
    row = store.db.execute(
        "SELECT r.*,c.payload,c.scope FROM decision_requests r JOIN claims c USING(claim_id) "
        "WHERE r.claim_id=? AND r.target_id IS NULL",
        (claim_id,),
    ).fetchone()
    if row is None:
        return None
    resolution = store.db.execute(
        "SELECT claim_id FROM decision_resolutions WHERE original_claim_id=?", (claim_id,)
    ).fetchone()
    payload = json.loads(row["payload"])
    return {
        "request_id": row["request_id"],
        "selector": row["selector"],
        "action": payload["action"],
        "why": payload["reason"],
        "scope": json.loads(row["scope"]) if row["scope"] else None,
        "target_id": None,
        "requires_resolution": resolution is None and store.claim_state(claim_id) == "candidate",
        "resolved_claim_id": resolution[0] if resolution else None,
    }


def receipt(store: Store, request_id: str, replayed: bool) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT r.*,d.event_id AS original_event_id FROM decision_resolutions r "
        "JOIN decision_requests d ON d.claim_id=r.original_claim_id WHERE r.request_id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("对象选择回执缺失")
    return {
        "revision": store.revision(),
        "request_id": request_id,
        "claim_id": row["claim_id"],
        "event_id": row["event_id"],
        "original_claim_id": row["original_claim_id"],
        "original_event_id": row["original_event_id"],
        "replayed": replayed,
    }


def resolve(store: Store, claim_id: int, body: dict[str, Any]) -> dict[str, Any]:
    from rg.api.views import NotFound

    if type(claim_id) is not int or claim_id < 1:
        raise ValueError("待复核决定 ID 无效")
    if set(body) - {"target_id", "actor", "request_id", "expected_revision"}:
        raise ValueError("歧义复核只能选择对象，不能修改动作、理由或范围")
    target = body.get("target_id")
    if not isinstance(target, str) or not target or len(target) > 120:
        raise ValueError("需明确选择现有对象 ID")
    request_id = request_uuid(body.get("request_id"))
    actor, expected = human(body.get("actor")), expected_revision(body.get("expected_revision"))
    data = {"original_claim_id": claim_id, "target_id": target, "actor": actor}
    intent_sha = digest(dumps(data).encode())
    with store.transaction() as db:
        if previous(store, request_id, "resolve", intent_sha):
            return receipt(store, request_id, True)
        if store.revision() != expected:
            raise ConflictError("图版本已变化，请刷新后再选择对象")
        row = db.execute(
            "SELECT c.*,r.project_id,r.event_id,r.selector,r.target_id FROM claims c "
            "JOIN decision_requests r USING(claim_id) WHERE claim_id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise NotFound("待复核人工决定不存在")
        metadata = pending(store, claim_id)
        if metadata is None or not metadata["requires_resolution"]:
            raise ValueError("决定已复核或驳回，不能再次选择对象")
        if target not in {
            item["entity_id"]
            for item in active_objects(store, row["project_id"], metadata["scope"])
        }:
            raise ValueError("对象须为同项目、原完整范围内的有效对象")
        payload = json.loads(row["payload"])
        raw = store.raw(row["event_id"])
        input_data = json.loads(raw)
        if (
            row["basis"] != "manual"
            or not row["actor"].startswith("human:")
            or payload["target"] is not None
            or payload["referent_unique"] is not False
            or input_data.get("selector") != row["selector"]
            or input_data.get("why") != payload["reason"]
            or ACTIONS.get(input_data.get("action")) != payload["action"]
            or input_data.get("scope") != metadata["scope"]
            or payload.get("scope") != metadata["scope"]
            or input_data.get("actor") != row["actor"]
            or input_data.get("project_id") != row["project_id"]
        ):
            raise ValueError("人工决定与原文不一致，不能解析对象")
        # 验证每一原文窗口，再为人的选择追加独立原件；两次事件分别保留双时间。
        old_spans = db.execute(
            "SELECT s.*,e.role FROM evidence_spans s JOIN claim_evidence e USING(span_id) "
            "WHERE e.claim_id=?",
            (claim_id,),
        ).fetchall()
        quotes = []
        for span in old_spans:
            quote = store.raw(span["event_id"])[span["byte_start"] : span["byte_end"]]
            if digest(quote) != span["quote_sha256"]:
                raise ValueError("原文引用校验失败，不能复用决定")
            quotes.append(
                {key: span[key] for key in ("event_id", "byte_start", "byte_end", "quote_sha256")}
                | {"quote": quote.decode()}
            )
        validate_decision(payload | {"evidence": quotes})
        recorded = now()
        event_id, selection_raw = original(
            store,
            request_id,
            data | {"project_id": row["project_id"]},
            "resolve",
            recorded,
            recorded,
            f"选择对象 {target}；原人工决定 {claim_id}",
        )
        new_spans = windows(selection_raw, event_id, ["target_id", "original_claim_id"])
        payload = payload | {"target": target, "referent_unique": True}
        validate_decision(payload | {"evidence": quotes + new_spans})
        new_claim = db.execute(
            "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
            "replaces_claim,occurred_at,recorded_at) "
            "VALUES ('decision_event',?,?,?,'manual',?,'confirmed',?,?,?)",
            (target, dumps(payload), row["scope"], actor, claim_id, row["occurred_at"], recorded),
        ).lastrowid
        if new_claim is None:
            raise RuntimeError("人工对象复核写入失败")
        for span in old_spans:
            db.execute(
                "INSERT INTO claim_evidence VALUES (?,?,?)",
                (new_claim, span["span_id"], span["role"]),
            )
        evidence(store, new_claim, new_spans)
        db.execute(
            "INSERT INTO review_actions(claim_id,action,new_claim_id,reason,actor,"
            "expected_revision,recorded_at) "
            "VALUES (?,'edit',?,'人工选择对象；原动作、理由、范围和发生时间保留',?,?,?)",
            (claim_id, new_claim, actor, expected, recorded),
        )
        db.execute(
            "INSERT INTO decision_resolutions VALUES (?,?,?,?,?,?,?)",
            (request_id, intent_sha, claim_id, event_id, new_claim, target, recorded),
        )
        return receipt(store, request_id, False)
