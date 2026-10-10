from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from rg.extract.budgets import Budgets
from rg.extract.provider import Provider, validate_base_url
from rg.extract.qa import ask, prepare_input
from rg.extract.worker import Worker
from rg.query.context import wrap
from rg.query.reader import Reader
from rg.query.retrieval import retrieve
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class QAConfig:
    base_url: str
    model: str
    key: str = field(repr=False)
    input_budget: int = 128000
    transport: httpx.BaseTransport | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.key.strip():
            raise ValueError("问答模型配置无效")
        Budgets(input_tokens=self.input_budget)
        validate_base_url(self.base_url)

    @property
    def remote(self) -> bool:
        return urlparse(self.base_url).hostname not in {"127.0.0.1", "localhost"}

    def connect(self) -> Provider:
        return Provider(self.base_url, self.model, self.key, self.transport)

    def identity(self) -> list[Any]:
        return [self.base_url.rstrip("/"), self.model, self.input_budget, 4000]


CORE = {
    "project_id",
    "question",
    "k",
    "max_bytes",
    "scope",
    "occurred_until",
    "known_until",
    "expected_revision",
}


def request(body: dict[str, Any], extra: set[str] | None = None) -> tuple[str, dict[str, Any]]:
    if set(body) - CORE - (extra or set()):
        raise ValueError("问答请求不接受额外字段")
    project = body.get("project_id")
    if not isinstance(project, str) or not project.strip() or len(project) > 1000:
        raise ValueError("请指定项目")
    values = {
        key: body[key]
        for key in (
            "scope",
            "occurred_until",
            "known_until",
            "expected_revision",
        )
        if key in body
    }
    return project, values


def packet(store: Store, body: dict[str, Any], extra: set[str] | None = None) -> dict[str, Any]:
    project, values = request(body, extra)
    question = body.get("question")
    if not isinstance(question, str):
        raise ValueError("问题需为 1 到 2000 字符")
    return retrieve(
        store, project, question, body.get("k", 12), values, body.get("max_bytes", 4000)
    )


def options(store: Store, project: str, config: QAConfig | None) -> dict[str, Any]:
    Reader(store, project, {})
    allowed = store.db.execute(
        "SELECT remote_model_allowed FROM projects WHERE project_id=?",
        (project,),
    ).fetchone()[0]
    return {
        "project_id": project,
        "revision": store.revision(),
        "configured": config is not None,
        "remote": config.remote if config else None,
        "remote_allowed": bool(allowed),
        "input_budget": config.input_budget if config else 128000,
        "output_budget": 4000,
        "model": config.model if config else None,
    }


def preview(
    store: Store,
    body: dict[str, Any],
    config: QAConfig | None,
    *,
    granting: bool = False,
) -> dict[str, Any]:
    data = packet(store, body, {"allow", "preview_sha"} if granting else None)
    _, values = request(body, {"allow", "preview_sha"} if granting else None)
    sent = prepare_input(data, values)
    identity = digest(dumps(["qa-preview1", config.identity() if config else None, sent]).encode())
    return {
        "project_id": data["project_id"],
        "revision": data["revision"],
        "preview_sha": identity,
        "input": sent,
        "source_count": len(sent["sources"]),
        "remote": config.remote if config else None,
        "notice": "以下为本次将发送的问题、记录和原文窗口；已有隐私模式先遮盖。"
        "开启许可适用于本项目的远程计数、自动提取、问答及后续新增资料。",
    }


def allow(store: Store, body: dict[str, Any], config: QAConfig | None) -> dict[str, Any]:
    if config is None or not config.remote:
        raise ModelUnavailable("当前没有配置远程问答模型；仍可本地检索")
    if body.get("allow") is not True:
        raise ValueError("需明确允许本项目使用远程模型")
    expected = body.get("expected_revision")
    if type(expected) is not int or expected < 0:
        raise ValueError("开启许可需提供预览中的记录版本")
    acknowledgement = body.get("preview_sha")
    if not isinstance(acknowledgement, str) or not re.fullmatch("[0-9a-f]{64}", acknowledgement):
        raise ValueError("请先查看本次发送预览")
    with store.transaction():
        sample = preview(store, body, config, granting=True)
        if not sample["source_count"]:
            raise ValueError("没有有效原文样例；先完成本地检索")
        if not hmac.compare_digest(acknowledgement, sample["preview_sha"]):
            raise ConflictError("发送预览或模型配置已变化，请重新查看预览")
        # 外发许可和已有样例回执均按项目记录，摘要绑定本次实际发送样例。
        store.db.execute(
            "INSERT OR IGNORE INTO remote_previews VALUES (?,?,?)",
            (sample["project_id"], acknowledgement, now()),
        )
        store.db.execute(
            "UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (sample["project_id"],)
        )
    return {
        "project_id": sample["project_id"],
        "revision": store.revision(),
        "remote_allowed": True,
    }


def disable(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    if set(body) != {"project_id", "expected_revision"}:
        raise ValueError("撤回许可仅需项目和记录版本")
    project, values = request(body)
    expected = values["expected_revision"]
    if type(expected) is not int or expected < 0:
        raise ValueError("撤回许可需提供记录版本")
    with store.transaction():
        Reader(store, project, {})
        if store.revision() != expected:
            raise ConflictError("研究记录已变化，请刷新后撤回许可")
        store.db.execute(
            "UPDATE projects SET remote_model_allowed=0 WHERE project_id=?", (project,)
        )
    return {"project_id": project, "revision": store.revision(), "remote_allowed": False}


def answer(store: Store, body: dict[str, Any], config: QAConfig | None, daily_budget: int) -> str:
    project, values = request(body)
    # 先验证研究请求和来源，未配置模型时仍保证本地查询错误口径一致。
    data = packet(store, body)
    if not data["sources"]:
        return wrap(
            data
            | {
                "layer": "qa",
                "model_interpretation": True,
                "run_id": None,
                "status": "insufficient",
                "statements": [],
                "generation_calls": 0,
                "caveats": ["未找到可校验原文；无法判断。"],
            }
        )
    if config is None:
        raise ModelUnavailable("没有配置问答模型；仍可本地检索")
    provider = config.connect()
    try:
        worker = Worker(
            store, provider, input_budget=config.input_budget, daily_budget=daily_budget
        )
        return ask(
            worker,
            project,
            body["question"],
            body.get("k", 12),
            values,
            body.get("max_bytes", 4000),
            include_provider=False,
        )
    finally:
        provider.close()
