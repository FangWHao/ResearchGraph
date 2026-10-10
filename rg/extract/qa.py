from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.monitor import set_status
from rg.extract.redact import model_input, redact
from rg.extract.schemas import obj
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim
from rg.extract.worker import Worker, _permission
from rg.query.context import wrap
from rg.query.retrieval import retrieve
from rg.store.database import Store, dumps
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


def prepare_input(
    packet: dict[str, Any],
    values: dict[str, Any] | None = None,
    custom: tuple[str, ...] = (),
    store: Store | None = None,
) -> dict[str, Any]:
    """预览和生成共用的遮盖输入；不访问提供方，不读取原生会话。"""
    sent = json.loads(dumps(packet))
    if store is not None:
        # 在完整原件上定位敏感串，再取既有窗口；边界中间不能漏出编号前缀。
        for source in sent["sources"]:
            raw = store.raw(source["event_id"])
            if digest(raw) != source["object_sha256"]:
                raise InvalidClaim("问答原件摘要已变化")
            safe = redact(raw, custom).data
            source["text"] = safe[source["window_start"] : source["window_end"]].decode()
            for record in source["records"]:
                row = store.db.execute(
                    "SELECT payload FROM claims WHERE claim_id=?", (record["claim_id"],)
                ).fetchone()
                if not row:
                    raise InvalidClaim("问答记录已缺失")
                payload = dumps(json.loads(row[0])).encode()
                size = len(record["payload_preview"].encode())
                record["payload_preview"] = redact(payload, custom).data[:size].decode()
    for field in ("occurred_until", "known_until"):
        if not values or values.get(field) is None:
            sent[field] = "current"
    sent = json.loads(model_input(dumps(sent), "qa", custom))
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
    counter = worker.project_counter(project)
    sent = prepare_input(packet, values, counter.policy.patterns, worker.store)
    sent["privacy_policy_id"] = counter.policy.identity
    identity = digest(dumps(sent).encode())
    item = Segment(
        "qa:" + identity, [{"event_id": source["event_id"]} for source in sent["sources"]]
    )
    run = None
    try:
        with worker.project_context(project):
            if worker.project_counter(project).policy != counter.policy:
                raise InvalidClaim("问答遮盖配置已变化，请重新准备输入")
            run, output = worker.invoke("qa", item, project, dumps(sent), QA_SCHEMA, [])
        counter.policy.check(worker.store, worker.provider.remote)
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
