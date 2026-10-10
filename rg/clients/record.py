from __future__ import annotations

import json
from typing import Any, BinaryIO

from rg.record.decide import decide
from rg.record.question import question
from rg.store.database import Store

MAX_REQUEST_BYTES = 65536
FIELDS = {
    "question": {"text", "scope", "request_id", "occurred_at", "expected_revision"},
    "decide": {
        "selector",
        "action",
        "why",
        "scope",
        "request_id",
        "occurred_at",
        "expected_revision",
    },
}


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("请求不能包含重复字段")
        result[key] = value
    return result


def read_request(stream: BinaryIO, kind: str) -> dict[str, Any]:
    if kind not in FIELDS:
        raise ValueError("客户端只支持人工 question/decide")
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("客户端请求最多 65536 字节")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("客户端请求需为 UTF8 JSON") from error
    if not isinstance(body, dict) or set(body) - FIELDS[kind]:
        raise ValueError("客户端请求字段非法，项目和身份由入口固定")
    if "request_id" not in body:
        raise ValueError("客户端请求需提供 request_id，重试复用同一 UUID")
    return body


def record(store: Store, project: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in FIELDS or set(body) - FIELDS[kind]:
        raise ValueError("客户端请求字段非法")
    values = {
        "expected_revision": store.revision(),
        **body,
        "project_id": project,
        "actor": "human:本机用户",
    }
    return question(store, values) if kind == "question" else decide(store, values)
