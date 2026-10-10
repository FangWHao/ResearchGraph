from __future__ import annotations

from typing import Any

from rg.ingest.spans import value_span
from rg.store.database import ConflictError, Store, dumps
from rg.store.objects import digest

MAX_RECORD_BYTES = 65536
SPAN_BYTES = 8000
RECEIPTS = {
    "question": "explicit_records",
    "decide": "decision_requests",
    "resolve": "decision_resolutions",
    "manifest": "run_manifests",
}


def previous(store: Store, request_id: str, kind: str, intent_sha: str) -> bool:
    for candidate, table in RECEIPTS.items():
        row = store.db.execute(
            f"SELECT intent_sha256 FROM {table} WHERE request_id=?", (request_id,)
        ).fetchone()
        if row:
            if candidate != kind or row[0] != intent_sha:
                raise ConflictError("请求编号已用于不同内容或操作，请核对原记录")
            return True
    return False


def original(
    store: Store,
    request_id: str,
    data: dict[str, Any],
    kind: str,
    occurred: str,
    recorded: str,
    search_text: str,
) -> tuple[int, bytes]:
    """由调用方事务同时提交原件、结构化记录和回执。"""
    db = store.db
    raw = dumps(
        data
        | {
            "type": f"rg_{kind}",
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
    session_id = (
        session[0]
        if session
        else db.execute(
            "INSERT INTO sessions(tool,native_session_id,project_id,project_basis,first_at) "
            "VALUES ('rg',?,?,'manual',?)",
            (f"manual:{data['project_id']}", data["project_id"], recorded),
        ).lastrowid
    )
    file_id = db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,prefix_length,committed_offset, "
        "parser,parser_version,status,first_seen,last_read) "
        "VALUES (?,?,?,?,?,'rg','1','virtual',?,?)",
        (
            session_id,
            f"rg-record://{request_id}",
            digest(raw[:4096]),
            min(len(raw), 4096),
            len(raw),
            recorded,
            recorded,
        ),
    ).lastrowid
    seq = db.execute(
        "SELECT COALESCE(max(seq),0)+1 FROM raw_events WHERE session_pk=?", (session_id,)
    ).fetchone()[0]
    event_id = db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end, "
        "object_sha256,native_id,seq,kind,role,occurred_at,recorded_at,line_sha256,exclude_reason) "
        "VALUES (?,?,0,0,?,?,?,?,?,'user',?,?,?,'explicit_recorded')",
        (
            session_id,
            file_id,
            len(raw),
            sha,
            request_id,
            seq,
            f"explicit_{kind}",
            occurred,
            recorded,
            sha,
        ),
    ).lastrowid
    if event_id is None:
        raise RuntimeError("人工原文写入失败")
    db.execute("INSERT INTO event_search VALUES (?,?)", (event_id, search_text))
    db.execute(
        "INSERT INTO coverage(event_id,stage,status) "
        "VALUES (?,'pass2','excluded:explicit_recorded')",
        (event_id,),
    )
    db.execute("UPDATE sessions SET last_at=? WHERE session_pk=?", (recorded, session_id))
    return event_id, raw


def windows(raw: bytes, event_id: int, fields: list[str]) -> list[dict[str, Any]]:
    spans = []
    for field in fields:
        position = value_span(raw, (field,))
        if position is None:
            raise RuntimeError("人工输入原文位置缺失")
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
    return spans


def evidence(store: Store, claim_id: int, spans: list[dict[str, Any]]) -> None:
    for span in spans:
        span_id = store.db.execute(
            "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
            "VALUES (?,?,?,?)",
            (span["event_id"], span["byte_start"], span["byte_end"], span["quote_sha256"]),
        ).lastrowid
        store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim_id, span_id))
