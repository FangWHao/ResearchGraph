from __future__ import annotations

import uuid
from typing import Any

from rg.record.events import evidence, original, previous, windows
from rg.record.question import intent, timestamp
from rg.record.schema import validate_note
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

PROPOSAL_FIELDS = {"text", "about", "scope", "request_id", "expected_revision", "occurred_at"}


def note(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    """用户亲自输入的本地笔记；保留原文，使用统一的人工复核合同。"""
    return _write(store, body, model_generated=False)


def propose_note(store: Store, project: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Agent 入口固定项目和模型身份，不能选择 basis 或确认状态。"""
    if set(arguments) - PROPOSAL_FIELDS:
        raise ValueError("候选笔记包含未知字段，项目和模型身份由服务固定")
    body = arguments | {"project_id": project, "actor": "human:内部字段校验"}
    return _write(store, body, model_generated=True)


def _receipt(store: Store, request_id: str, replayed: bool) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT r.*,c.entity_id,c.claim_state FROM effective_note_requests "
        "r JOIN claims c USING(claim_id) "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("笔记回执缺失")
    return {
        "revision": store.revision(),
        "request_id": request_id,
        "claim_id": row["claim_id"],
        "entity_id": row["entity_id"],
        "event_id": row["event_id"],
        "replayed": replayed,
        "claim_state": row["claim_state"],
        "effective_state": store.claim_state(row["claim_id"]),
    }


def _write(store: Store, body: dict[str, Any], *, model_generated: bool) -> dict[str, Any]:
    request_id, data, expected = intent({k: v for k, v in body.items() if k != "about"})
    about = body.get("about")
    if about is not None and (not isinstance(about, str) or not about.strip() or len(about) > 120):
        raise ValueError("笔记关联需为本项目对象 ID，未关联时使用 null")
    data["about"] = about
    if model_generated:
        data["actor"] = "model:mcp"
    intent_sha = digest(dumps(data).encode())
    with store.transaction() as db:
        if previous(store, request_id, "note", intent_sha):
            return _receipt(store, request_id, True)
        if store.revision() != expected:
            raise ConflictError("图版本已变化，请刷新后再保存笔记")
        if not db.execute(
            "SELECT 1 FROM active_projects WHERE project_id=?", (data["project_id"],)
        ).fetchone():
            raise ValueError("项目不存在")
        if (
            about is not None
            and not db.execute(
                "SELECT 1 FROM effective_entities WHERE entity_id=? AND project_id=?",
                (about, data["project_id"]),
            ).fetchone()
        ):
            raise ValueError("笔记关联对象不存在或不属于本项目")
        recorded = now()
        occurred = timestamp(data["input_occurred_at"]) or recorded
        event_id, raw = original(
            store,
            request_id,
            data,
            "note",
            occurred,
            recorded,
            data["text"],
            model_generated=model_generated,
        )
        entity_id = str(uuid.uuid4())
        payload = {
            "claim_type": "entity_version",
            "temp_id": entity_id,
            "kind": "note",
            "label": data["text"],
            "content": data["text"],
            "about": about,
            "scope": data["scope"],
        }
        spans = windows(raw, event_id, ["text"])
        validate_note(payload | {"evidence": spans})
        db.execute(
            "INSERT INTO entities VALUES (?,?,'note',NULL,?)",
            (entity_id, data["project_id"], recorded),
        )
        claim_id = db.execute(
            "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state, "
            "occurred_at,recorded_at) VALUES ('entity_version',?,?,?,?,?,?,?,?)",
            (
                entity_id,
                dumps(payload),
                dumps(data["scope"]) if data["scope"] else None,
                "model_inference" if model_generated else "manual",
                data["actor"],
                "candidate" if model_generated else "confirmed",
                occurred,
                recorded,
            ),
        ).lastrowid
        if claim_id is None:
            raise RuntimeError("笔记记录写入失败")
        evidence(store, claim_id, spans)
        db.execute(
            "INSERT INTO note_requests VALUES (?,?,?,?,?,?)",
            (request_id, data["project_id"], intent_sha, event_id, claim_id, recorded),
        )
        return _receipt(store, request_id, False)
