from __future__ import annotations

import uuid
from typing import Any

from rg.record.events import evidence, original, previous, windows
from rg.record.question import expected_revision, human, timestamp
from rg.record.schema import validate_decision, validate_scope
from rg.record.targets import unique_target
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

ACTIONS = {"accept": "accepted", "defer": "deferred", "reject": "rejected", "withdraw": "withdrawn"}


def request_uuid(value: Any) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as error:
        raise ValueError("request_id 需为标准 UUID") from error
    return value


def intent(body: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
    if set(body) - {
        "project_id",
        "selector",
        "action",
        "why",
        "scope",
        "actor",
        "request_id",
        "expected_revision",
        "occurred_at",
    }:
        raise ValueError("人工决定请求包含未知字段")
    project, selector, why, action = (
        body.get(key) for key in ("project_id", "selector", "why", "action")
    )
    if not isinstance(project, str) or not project or len(project) > 120:
        raise ValueError("需明确指定项目 ID")
    if not isinstance(selector, str) or not selector.strip() or len(selector.encode()) > 4000:
        raise ValueError("对象需为非空文字或 ID，最多 4000 UTF8 字节")
    if not isinstance(why, str) or not why.strip() or len(why.encode()) > 16000:
        raise ValueError("理由需为非空文字，最多 16000 UTF8 字节")
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("决定操作需为 accept/defer/reject/withdraw")
    timestamp(body.get("occurred_at"))
    return (
        request_uuid(body.get("request_id")),
        {
            "project_id": project,
            "selector": selector,
            "action": action,
            "why": why,
            "scope": validate_scope(body.get("scope")),
            "actor": human(body.get("actor")),
            "input_occurred_at": body.get("occurred_at"),
        },
        expected_revision(body.get("expected_revision")),
    )


def receipt(store: Store, request_id: str, replayed: bool) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT * FROM decision_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("人工决定回执缺失")
    resolution = store.db.execute(
        "SELECT claim_id FROM decision_resolutions WHERE original_claim_id=?", (row["claim_id"],)
    ).fetchone()
    return {
        "revision": store.revision(),
        "request_id": request_id,
        "claim_id": row["claim_id"],
        "event_id": row["event_id"],
        "target_id": row["target_id"],
        "effective_state": store.claim_state(row["claim_id"]),
        "replayed": replayed,
        "resolved_claim_id": resolution[0] if resolution else None,
    }


def decide(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    request_id, data, expected = intent(body)
    intent_sha = digest(dumps(data).encode())
    with store.transaction() as db:
        if previous(store, request_id, "decide", intent_sha):
            return receipt(store, request_id, True)
        if expected != store.revision():
            raise ConflictError("图版本已变化，请刷新后再保存决定")
        if not db.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (data["project_id"],)
        ).fetchone():
            raise ValueError("项目不存在")
        target = unique_target(store, data["project_id"], data["selector"], data["scope"])
        recorded = now()
        occurred = timestamp(data["input_occurred_at"]) or recorded
        event_id, raw = original(
            store,
            request_id,
            data,
            "decide",
            occurred,
            recorded,
            data["selector"] + "\n" + data["why"],
        )
        carrier = target or str(uuid.uuid4())
        if target is None:
            # 仅承载项目归属；无 entity_version，不能伪造待采用的科学对象。
            db.execute(
                "INSERT INTO entities VALUES (?,?,'decision',NULL,?)",
                (carrier, data["project_id"], recorded),
            )
        payload = {
            "claim_type": "decision_event",
            "target": target,
            "action": ACTIONS[data["action"]],
            "reason": data["why"],
            "speaker": "user",
            "explicitness": "explicit",
            "referent_unique": target is not None,
            "scope": data["scope"],
        }
        spans = windows(raw, event_id, ["selector", "action", "why", "scope"])
        validate_decision(payload | {"evidence": spans})
        claim_id = db.execute(
            "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
            "occurred_at,recorded_at) "
            "VALUES ('decision_event',?,?,?,'manual',?,?,?,?)",
            (
                carrier,
                dumps(payload),
                dumps(data["scope"]) if data["scope"] is not None else None,
                data["actor"],
                "confirmed" if target else "candidate",
                occurred,
                recorded,
            ),
        ).lastrowid
        if claim_id is None:
            raise RuntimeError("人工决定写入失败")
        evidence(store, claim_id, spans)
        db.execute(
            "INSERT INTO decision_requests VALUES (?,?,?,?,?,?,?,?)",
            (
                request_id,
                data["project_id"],
                intent_sha,
                event_id,
                claim_id,
                data["selector"],
                target,
                recorded,
            ),
        )
        return receipt(store, request_id, False)
