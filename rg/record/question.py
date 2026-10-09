from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from rg.ingest.spans import value_span
from rg.record.schema import validate_question, validate_scope
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

MAX_TEXT_BYTES = 16000
MAX_RECORD_BYTES = 65536
SPAN_BYTES = 8000


def human(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("human:")
        or not value[6:].strip()
        or len(value) > 120
    ):
        raise ValueError("人工记录需使用非空 human:人工身份，最多 120 字符")
    return value


def expected_revision(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision 需为非负整数")
    return value


def timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("发生时间需为带时区的 ISO 时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("发生时间需为带时区的 ISO 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("发生时间需明确时区")
    return parsed.astimezone(UTC).isoformat()


def intent(body: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
    allowed = {
        "project_id",
        "text",
        "scope",
        "actor",
        "request_id",
        "expected_revision",
        "occurred_at",
    }
    if set(body) - allowed:
        raise ValueError("人工问题请求包含未知字段")
    request_id = body.get("request_id")
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError
    except (ValueError, AttributeError) as error:
        raise ValueError("request_id 需为标准 UUID") from error
    project, text = body.get("project_id"), body.get("text")
    if not isinstance(project, str) or not project or len(project) > 120:
        raise ValueError("需明确指定项目 ID")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("问题需为非空文字，最多 16000 UTF8 字节")
    timestamp(body.get("occurred_at"))
    data = {
        "project_id": project,
        "text": text,
        "scope": validate_scope(body.get("scope")),
        "actor": human(body.get("actor")),
        "input_occurred_at": body.get("occurred_at"),
    }
    return request_id, data, expected_revision(body.get("expected_revision"))


def receipt(store: Store, request_id: str, replayed: bool) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT r.*,c.entity_id FROM explicit_records r JOIN claims c USING(claim_id) "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("显式记录回执缺失")
    return {
        "revision": store.revision(),
        "request_id": request_id,
        "claim_id": row["claim_id"],
        "entity_id": row["entity_id"],
        "event_id": row["event_id"],
        "replayed": replayed,
    }


def question(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    """可信本地人工入口；不提供给 Agent 的 MCP 写工具。"""
    request_id, data, expected = intent(body)
    intent_sha = digest(dumps(data).encode())
    with store.transaction() as db:
        previous = db.execute(
            "SELECT intent_sha256 FROM explicit_records WHERE request_id=?", (request_id,)
        ).fetchone()
        if previous:
            if previous[0] != intent_sha:
                raise ConflictError("请求编号已用于不同内容，请核对原记录")
            return receipt(store, request_id, True)
        if store.revision() != expected:
            raise ConflictError("图版本已变化，请刷新后再保存问题")
        if not db.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (data["project_id"],)
        ).fetchone():
            raise ValueError("项目不存在")
        recorded = now()
        occurred = timestamp(data["input_occurred_at"]) or recorded
        event_id, raw = _original(store, request_id, data, occurred, recorded)
        claim_id = _question_claim(store, data, event_id, raw, occurred, recorded)
        db.execute(
            "INSERT INTO explicit_records VALUES (?,?,'question',?,?,?,?)",
            (request_id, data["project_id"], intent_sha, event_id, claim_id, recorded),
        )
        return receipt(store, request_id, False)


def _original(
    store: Store, request_id: str, data: dict[str, Any], occurred: str, recorded: str
) -> tuple[int, bytes]:
    db = store.db
    raw = dumps(
        data
        | {
            "type": "rg_question",
            "request_id": request_id,
            "occurred_at": occurred,
            "recorded_at": recorded,
        }
    ).encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("完整人工记录超过 65536 字节")
    sha = store.objects.put(raw)
    session = db.execute(
        "SELECT session_pk FROM sessions WHERE tool='rg' AND native_session_id=? AND agent_id=''",
        (f"manual:{data['project_id']}",),
    ).fetchone()
    if session:
        session_id = session[0]
    else:
        session_id = db.execute(
            "INSERT INTO sessions(tool,native_session_id,project_id,project_basis,first_at) "
            "VALUES ('rg',?,?,'manual',?)",
            (f"manual:{data['project_id']}", data["project_id"], recorded),
        ).lastrowid
    file_id = db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,committed_offset,parser, "
        "parser_version,status,first_seen,last_read) VALUES (?,?,?,?,'rg','1','virtual',?,?)",
        (session_id, f"rg-record://{request_id}", sha, len(raw), recorded, recorded),
    ).lastrowid
    seq = db.execute(
        "SELECT COALESCE(max(seq),0)+1 FROM raw_events WHERE session_pk=?", (session_id,)
    ).fetchone()[0]
    event_id = db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end, "
        "object_sha256,native_id,seq,kind,role,occurred_at,recorded_at,line_sha256, "
        "exclude_reason) VALUES (?,?,0,0,?,?,?,?, 'explicit_question','user',?,?,?, "
        "'explicit_recorded')",
        (session_id, file_id, len(raw), sha, request_id, seq, occurred, recorded, sha),
    ).lastrowid
    if event_id is None:
        raise RuntimeError("人工原文写入失败")
    db.execute("INSERT INTO event_search VALUES (?,?)", (event_id, data["text"]))
    db.execute(
        "INSERT INTO coverage(event_id,stage,status) "
        "VALUES (?,'pass2','excluded:explicit_recorded')",
        (event_id,),
    )
    db.execute("UPDATE sessions SET last_at=? WHERE session_pk=?", (recorded, session_id))
    return event_id, raw


def _question_claim(
    store: Store, data: dict[str, Any], event_id: int, raw: bytes, occurred: str, recorded: str
) -> int:
    db = store.db
    entity_id = str(uuid.uuid4())
    payload = {
        "claim_type": "entity_version",
        "temp_id": entity_id,
        "kind": "question",
        "label": data["text"],
        "content": data["text"],
        "scope": data["scope"],
    }
    position = value_span(raw, ("text",))
    if position is None:
        raise RuntimeError("人工问题原文位置缺失")
    spans = []
    start, stop, _ = position
    while start < stop:
        end = min(stop, start + SPAN_BYTES)
        while end < stop and raw[end] & 0xC0 == 0x80:
            end -= 1
        quote = raw[start:end]
        spans.append(
            {
                "event_id": event_id,
                "byte_start": start,
                "byte_end": end,
                "quote": quote.decode(),
                "quote_sha256": digest(quote),
            }
        )
        start = end
    validate_question(payload | {"evidence": spans})
    db.execute(
        "INSERT INTO entities VALUES (?,?,'question',NULL,?)",
        (entity_id, data["project_id"], recorded),
    )
    claim_id = db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state, "
        "occurred_at,recorded_at) VALUES ('entity_version',?,?,?,'manual',?,'confirmed',?,?)",
        (
            entity_id,
            dumps(payload),
            dumps(data["scope"]) if data["scope"] else None,
            data["actor"],
            occurred,
            recorded,
        ),
    ).lastrowid
    for span in spans:
        span_id = db.execute(
            "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
            "VALUES (?,?,?,?)",
            (event_id, span["byte_start"], span["byte_end"], span["quote_sha256"]),
        ).lastrowid
        db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim_id, span_id))
    if claim_id is None:
        raise RuntimeError("人工问题记录写入失败")
    return claim_id
