"""模型调用：Anthropic Messages 兼容接口（默认 DeepSeek），强制用工具返回 JSON。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from rg.store import Store, now

# (system, user, schema, max_tokens) -> 工具输入
Call = Callable[[str, str, dict[str, Any], int], dict[str, Any]]

TOOL_NAME = "submit"


class ModelError(RuntimeError):
    pass


class Truncated(ModelError):
    """输出达到 max_tokens 被截断，结果作废。"""


class Model:
    def __init__(
        self,
        base_url: str,
        model: str,
        key: str,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
    ):
        address = urlparse(base_url)
        if address.scheme != "https" and address.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("模型接口必须使用 HTTPS")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retries = retries
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
        return {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": TOOL_NAME, "description": "提交整理结果", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }

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
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.retries:
                    raise ModelError(f"模型接口返回 HTTP {response.status_code}")
            time.sleep(2 * (attempt + 1))
        raise ModelError("模型接口不可用")

    def __call__(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        data = self._post(self.body(system, user, schema, max_tokens))
        if data.get("stop_reason") == "max_tokens":
            raise Truncated("模型输出被截断")
        blocks = [
            b
            for b in data.get("content", [])
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == TOOL_NAME
        ]
        if len(blocks) != 1 or not isinstance(blocks[0].get("input"), dict):
            raise ModelError("模型没有按格式返回结果")
        return blocks[0]["input"]


def cached(store: Store, call: Call, identity: str) -> Call:
    """按请求内容缓存结果；重试和重新整理不重复调用。截断和错误不缓存。"""

    def run(system: str, user: str, schema: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        raw = json.dumps([identity, system, user, schema, max_tokens], ensure_ascii=False)
        key = hashlib.sha256(raw.encode()).hexdigest()
        row = store.one("SELECT response FROM model_cache WHERE key = ?", (key,))
        if row:
            return json.loads(row["response"])
        result = call(system, user, schema, max_tokens)
        with store.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO model_cache VALUES (?, ?, ?)",
                (key, json.dumps(result, ensure_ascii=False), now()),
            )
        return result

    return run
