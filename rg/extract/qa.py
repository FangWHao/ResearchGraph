from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.monitor import set_status
from rg.extract.redact import model_input
from rg.extract.schemas import obj
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim
from rg.extract.worker import Worker, _permission
from rg.query.context import wrap
from rg.query.retrieval import retrieve
from rg.store.database import dumps
from rg.store.objects import digest

TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
CITATION = obj({"id": TEXT, "quote": TEXT}, ["id", "quote"])
STATEMENT = obj(
    {
        "text": TEXT,
        "citations": {"type": "array", "minItems": 1, "maxItems": 12, "items": CITATION},
    },
    ["text", "citations"],
)
QA_SCHEMA = obj(
    {
        "status": {"enum": ["answered", "insufficient"]},
        "statements": {"type": "array", "maxItems": 12, "items": STATEMENT},
        "caveats": {"type": "array", "minItems": 1, "maxItems": 12, "items": TEXT},
    },
    ["status", "statements", "caveats"],
)


def validate(output: Any, sent: dict[str, Any]) -> None:
    if list(Draft202012Validator(QA_SCHEMA).iter_errors(output)):
        raise InvalidClaim("问答结构不合法")
    if output["status"] == "answered" and not output["statements"]:
        raise InvalidClaim("回答没有带引用的陈述")
    known = {source["citation_id"]: source["text"] for source in sent["sources"]}
    for statement in output["statements"]:
        for citation in statement["citations"]:
            if (
                citation["id"] not in known
                or not citation["quote"].strip()
                or citation["quote"] not in known[citation["id"]]
            ):
                raise InvalidClaim("回答引用了未发送的证据或原文")
    if re.search(r"</?rg-context\b", dumps(output), re.I):
        raise InvalidClaim("问答不能嵌套防回流标记")


def prepare_input(packet: dict[str, Any], values: dict[str, Any] | None = None) -> dict[str, Any]:
    """预览和生成共用的遮盖输入；不访问提供方，不读取原生会话。"""
    sent = dict(packet)
    for field in ("occurred_until", "known_until"):
        if not values or values.get(field) is None:
            sent[field] = "current"
    sent = json.loads(model_input(dumps(sent), "qa"))
    sent["question_redacted"] = sent["question"] != packet["question"]
    for source, original in zip(sent["sources"], packet["sources"], strict=True):
        source["text_redacted"] = source["text"] != original["text"]
        source["records_redacted"] = source["records"] != original["records"]
    return sent


def ask(
    worker: Worker,
    project: str,
    question: str,
    k: int = 12,
    values: dict[str, Any] | None = None,
    max_bytes: int = 4000,
    *,
    include_provider: bool = True,
) -> str:
    packet = retrieve(worker.store, project, question, k, values, max_bytes)
    result = packet | {
        "layer": "qa",
        "model_interpretation": True,
        "run_id": None,
        "notice": "模型解释，仅供阅读，不作证据或其他提取阶段的输入；引用校验不证明解释正确。",
    }
    if not packet["sources"]:
        return wrap(
            result
            | {
                "status": "insufficient",
                "statements": [],
                "caveats": ["未找到可校验的原文证据；无法判断。"],
                "generation_calls": 0,
            }
        )
    _permission(worker.store, project, worker.provider)
    # 默认“当前”请求可复用同一资料集；显式截止保持精确值。返回仍报告本次实际读取截止。
    sent = prepare_input(packet, values)
    identity = digest(dumps(sent).encode())
    item = Segment(
        "qa:" + identity, [{"event_id": source["event_id"]} for source in sent["sources"]]
    )
    run = None
    try:
        run, output = worker.invoke("qa", item, project, dumps(sent), QA_SCHEMA, [])
        validate(output, sent)
        set_status(worker.store, run, "ok", attempt_id=worker.attempt_ids.get(run))
        return wrap(
            result
            | output
            | {
                "run_id": run,
                "model": worker.provider.model,
                "current_revision": worker.store.revision(),
                "records_changed_during_answer": worker.store.revision() != packet["revision"],
            }
            | ({"provider": worker.provider.provider} if include_provider else {})
        )
    except (ValueError, RuntimeError, OSError) as error:
        if run is not None:
            worker._status(run, "invalid", "问答引用校验失败，未发布回答")
        return wrap(
            result
            | {
                "status": "unavailable",
                "statements": [],
                "failure_type": type(error).__name__,
                "caveats": ["本次模型回答未通过执行或引用校验；仍可查看本地检索证据。"],
            }
        )
