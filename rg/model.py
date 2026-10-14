"""模型调用：Anthropic Messages 兼容接口（默认 DeepSeek），强制用工具返回 JSON。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, NamedTuple, Protocol
from urllib.parse import urlparse

import httpx

from rg.store import Store, now

TOOL_NAME = "submit"
# 打开思考时给思考预留的 token；思考和强制调用工具不能同时用，改成让模型自己调用 submit。
# 预留要宽：实测模型的思考常常超过 budget_tokens（提取时中位 5.7k、P90 1.1 万）
THINKING_BUDGET = {"low": 8_000, "high": 24_000, "max": 48_000}


class Usage(NamedTuple):
    """一次请求用掉的 token。输入按 Anthropic 的口径分成未命中、命中缓存、写入缓存三部分。"""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


# 每次调用后报告用量；None 表示命中了本地缓存，没有发请求。
Meter = Callable[[Usage | None], None]


class Call(Protocol):
    """(system, user, schema, max_tokens) -> 工具输入。"""

    def __call__(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        meter: Meter | None = None,
    ) -> dict[str, Any]: ...


class ModelError(RuntimeError):
    pass


class Truncated(ModelError):
    """输出达到 max_tokens 被截断，结果作废。"""


class AuthError(ModelError):
    """Key 无效或没有权限（HTTP 401/403）。重试没有意义，整个任务应当停下。"""


def _auth_error(status: int) -> AuthError:
    # 不带返回原文：有的接口会在 401 的说明里回显部分 Key。
    return AuthError(f"API Key 无效或没有权限（HTTP {status}）")


def read_usage(data: dict[str, Any], fallback_model: str) -> Usage:
    raw = data.get("usage")
    raw = raw if isinstance(raw, dict) else {}

    def count(name: str) -> int:
        value = raw.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    return Usage(
        str(data.get("model") or fallback_model),
        count("input_tokens"),
        count("output_tokens"),
        count("cache_read_input_tokens"),
        count("cache_creation_input_tokens"),
    )


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return f"：{' '.join(str(message).split())[:160]}" if message else ""


class Model:
    def __init__(
        self,
        base_url: str,
        model: str,
        key: str,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        thinking: str = "off",
    ):
        address = urlparse(base_url)
        if address.scheme != "https" and address.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("模型接口必须使用 HTTPS")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retries = retries
        self.thinking = thinking if thinking in THINKING_BUDGET else "off"
        self.client = httpx.Client(
            timeout=httpx.Timeout(300, connect=20),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={
                "x-api-key": key,
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
            },
        )

    def close(self) -> None:
        self.client.close()

    def body(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": TOOL_NAME, "description": "提交整理结果", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        if self.thinking != "off":
            budget = THINKING_BUDGET[self.thinking]
            body.update(
                tool_choice={"type": "auto"},
                max_tokens=max_tokens + budget,
                thinking={"type": "enabled", "budget_tokens": budget},
                output_config={"effort": self.thinking},
            )
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                response = self.client.post(self.base_url + "/v1/messages", json=body)
            except httpx.HTTPError as error:
                # 不带 URL、请求头和返回原文，避免日志泄露 Key 或会话内容。
                if attempt == self.retries:
                    raise ModelError(f"模型接口连接失败：{type(error).__name__}") from None
            else:
                if response.is_success:
                    return response.json()
                if response.status_code in {401, 403}:
                    raise _auth_error(response.status_code)
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.retries:
                    raise ModelError(f"模型接口返回 HTTP {response.status_code}")
            time.sleep(2 * (attempt + 1))
        raise ModelError("模型接口不可用")

    def check(self) -> str:
        """发一条极短的消息，确认 Key、接口地址和模型名都能用。返回接口报告的模型名。"""
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "thinking": {"type": "disabled"},
        }
        try:
            response = self.client.post(self.base_url + "/v1/messages", json=body)
        except httpx.HTTPError as error:
            raise ModelError(f"连不上模型接口：{type(error).__name__}") from None
        if response.status_code in {401, 403}:
            raise _auth_error(response.status_code)
        if not response.is_success:
            raise ModelError(f"模型接口返回 HTTP {response.status_code}{_detail(response)}")
        try:
            data = response.json()
        except ValueError:
            raise ModelError("模型接口返回的不是 JSON，接口地址可能不对") from None
        return str(data.get("model") or self.model) if isinstance(data, dict) else self.model

    def __call__(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        meter: Meter | None = None,
    ) -> dict[str, Any]:
        required = [k for k in schema.get("required", []) if isinstance(k, str)]
        for attempt in range(self.retries + 1):
            data = self._post(self.body(system, user, schema, max_tokens))
            if meter is not None:
                # 截断或格式不对的回复同样花了 token，先记账再校验。
                meter(read_usage(data, self.model))
            if data.get("stop_reason") == "max_tokens":
                raise Truncated("模型输出被截断")
            blocks = [
                b
                for b in data.get("content", [])
                if isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("name") == TOOL_NAME
            ]
            result = blocks[0].get("input") if len(blocks) == 1 else None
            # 偶尔会返回空的 {}：缺了必需的字段就重问，不能当成“什么都不用做”
            if isinstance(result, dict) and all(k in result for k in required):
                return result
            if attempt < self.retries:
                time.sleep(1)
        raise ModelError("模型没有按格式返回结果")


def list_models(
    base_url: str, key: str, transport: httpx.BaseTransport | None = None
) -> list[dict[str, Any]]:
    """接口上能用的模型。先试 Anthropic 的 /v1/models，不行再试同一服务的 OpenAI 式 /models
    （DeepSeek 的 /anthropic 下没有模型列表，但 /models 有，还带思考强度）。"""
    base = base_url.rstrip("/")
    urls = [base + "/v1/models"]
    if base.endswith("/anthropic"):
        urls.append(base[: -len("/anthropic")] + "/models")
    headers = {
        "x-api-key": key,
        "Authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
    }
    with httpx.Client(timeout=30, transport=transport, trust_env=False) as client:
        for url in urls:
            try:
                response = client.get(url, headers=headers)
            except httpx.HTTPError as error:
                raise ModelError(f"连不上模型接口：{type(error).__name__}") from None
            if response.status_code in {401, 403}:
                raise _auth_error(response.status_code)
            if not response.is_success:
                continue
            try:
                data = response.json().get("data")
            except (ValueError, AttributeError):
                continue
            if isinstance(data, list):
                return [_model_info(m) for m in data if isinstance(m, dict) and m.get("id")]
    raise ModelError("这个接口没有提供模型列表，可以直接填写模型名")


def _model_info(raw: dict[str, Any]) -> dict[str, Any]:
    effort: dict[str, Any] = raw["effort"] if isinstance(raw.get("effort"), dict) else {}
    levels = [lv for lv in effort.get("supported_levels") or [] if lv in THINKING_BUDGET]
    return {
        "id": str(raw["id"]),
        "name": str(raw.get("name") or raw.get("display_name") or raw["id"]),
        "context_window": raw.get("context_window"),
        "max_output_tokens": raw.get("max_output_tokens"),
        "thinking": levels,
        "default_thinking": effort.get("default_level")
        if effort.get("default_level") in levels
        else None,
    }


def cached(store: Store, call: Call, identity: str) -> Call:
    """按请求内容缓存结果；重试和重新整理不重复调用。截断和错误不缓存。"""

    def run(
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        meter: Meter | None = None,
    ) -> dict[str, Any]:
        raw = json.dumps([identity, system, user, schema, max_tokens], ensure_ascii=False)
        key = hashlib.sha256(raw.encode()).hexdigest()
        row = store.one("SELECT response FROM model_cache WHERE key = ?", (key,))
        if row:
            if meter is not None:
                meter(None)
            return json.loads(row["response"])
        result = call(system, user, schema, max_tokens, meter)
        with store.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO model_cache VALUES (?, ?, ?)",
                (key, json.dumps(result, ensure_ascii=False), now()),
            )
        return result

    return run


def metered(call: Call, sink: Meter) -> Call:
    """让每次调用都向 sink 报告用量。"""

    def run(
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        meter: Meter | None = None,
    ) -> dict[str, Any]:
        return call(system, user, schema, max_tokens, sink)

    return run
