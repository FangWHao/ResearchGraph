from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse

import httpx

from rg.slim.tokens import CountingUnavailable


def load_key(path: Path | None = None) -> str:
    value = os.environ.get("RG_API_KEY", "")
    if not value and path:
        value = path.read_text().strip()
    match = re.search(r"\bsk-[A-Za-z0-9_-]+", value)
    if match:
        return match[0]
    value = value.strip().strip("\"'")
    if not value or any(x.isspace() for x in value):
        raise ValueError("凭据格式不正确，请使用 RG_API_KEY 或独立凭据文件")
    return value


@dataclass
class ModelResult:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    contaminated: bool = False


def validate_base_url(base_url: str) -> ParseResult:
    address = urlparse(base_url)
    if address.scheme != "https" and address.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("远程接口必须使用 HTTPS")
    if address.username or address.password or address.query or address.fragment:
        raise ValueError("接口地址不得含凭据、查询参数或片段")
    return address


class Provider:
    def __init__(
        self, base_url: str, model: str, key: str, transport: httpx.BaseTransport | None = None
    ):
        address = validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = self.base_url
        self.deepseek = address.hostname == "api.deepseek.com"
        self.remote = address.hostname not in {"127.0.0.1", "localhost"}
        self._limits: tuple[int, int] | None = None
        self.client = httpx.Client(
            timeout=60,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {key}",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )

    def close(self) -> None:
        self.client.close()

    @property
    def context_window(self) -> int | None:
        return self._limits[0] if self._limits else None

    def validate_budget(self, input_budget: int, output_budget: int) -> None:
        # 只读模型元数据，不发送会话内容；必须由已通过项目外发许可的 worker 调用。
        if not self.deepseek:
            return
        if self._limits is None:
            try:
                response = self.client.get(self.base_url + "/models")
                if not response.is_success:
                    raise RuntimeError("无法读取模型窗口，未发送生成请求")
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise RuntimeError("无法读取模型窗口，未发送生成请求") from None
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("模型未返回合法窗口信息，未发送生成请求")
            row = next((x for x in rows if isinstance(x, dict) and x.get("id") == self.model), None)
            context = row.get("context_window") if row else None
            output = row.get("max_output_tokens") if row else None
            if type(context) is not int or type(output) is not int or context < 1 or output < 1:
                raise RuntimeError("模型未返回合法窗口信息，未发送生成请求")
            self._limits = context, output
        context, output = self._limits
        if input_budget + output_budget > context or output_budget > output:
            raise ValueError("所设输入与输出预算超过模型窗口或输出上限")

    def request(
        self, instructions: str, content: str, schema: dict[str, Any], output_budget: int = 4000
    ) -> dict[str, Any]:
        if self.deepseek:
            # 同一个消息请求用于计数和生成；schema 作为强制工具定义参与计数。
            return {
                "model": self.model,
                "system": instructions,
                "messages": [{"role": "user", "content": content}],
                "tools": [
                    {
                        "name": "submit_claims",
                        "description": "提交 JSON 提取结果",
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": "submit_claims"},
                "max_tokens": output_budget,
                "thinking": {"type": "disabled"},
            }
        return {
            "model": self.model,
            "instructions": instructions,
            "input": content,
            "text": {"format": {"type": "json_schema", "name": "claims", "schema": schema}},
            "max_output_tokens": output_budget,
            "truncation": "disabled",
            "store": False,
        }

    def _post(self, path: str, body: dict[str, Any], counting: bool = False) -> dict[str, Any]:
        try:
            response = self.client.post(self.base_url + path, json=body)
            if not response.is_success:
                message = f"接口 {path} 返回 HTTP {response.status_code}"
                if counting:
                    raise CountingUnavailable(message + "；未发送生成请求")
                raise RuntimeError(message)
            return response.json()
        except httpx.HTTPError as error:
            # 不包含 URL、头部或服务器返回的原文，避免异常日志泄露凭据/输入。
            if counting:
                raise CountingUnavailable("计数接口连接失败；未发送生成请求") from None
            raise RuntimeError(f"模型接口连接失败：{type(error).__name__}") from None

    def count_request(self, request: dict[str, Any]) -> int:
        if self.deepseek:
            payload = {
                k: v
                for k, v in request.items()
                if k not in {"max_tokens", "thinking", "tool_choice"}
            }
            result = self._post("/anthropic/v1/messages/count_tokens", payload, counting=True)
        else:
            payload = {k: v for k, v in request.items() if k not in {"max_output_tokens", "store"}}
            result = self._post("/responses/input_tokens", payload, counting=True)
        tokens = result.get("input_tokens")
        if type(tokens) is not int or tokens < 0:
            raise CountingUnavailable("计数接口没有返回合法 input_tokens；未发送生成请求")
        return tokens

    def count_text(self, text: str) -> int:
        from rg.extract.redact import redact

        text = redact(text.encode()).data.decode()
        if self.deepseek:
            return self.count_request(
                {"model": self.model, "messages": [{"role": "user", "content": text}]}
            )
        return self.count_request({"model": self.model, "input": text})

    def generate(self, request: dict[str, Any]) -> ModelResult:
        if self.deepseek:
            data = self._post("/anthropic/v1/messages", request)
            blocks = [
                x
                for x in data.get("content", [])
                if x.get("type") == "tool_use" and x.get("name") == "submit_claims"
            ]
            from rg.store.database import dumps

            text = dumps(blocks[0].get("input")) if len(blocks) == 1 else ""
            usage = data.get("usage", {})
            # Anthropic 协议将命中缓存的输入分列，input_tokens 本身只包含未命中部分。
            full_input = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            )
            return ModelResult(
                text,
                full_input,
                usage.get("output_tokens", 0),
                data.get("stop_reason", "unknown"),
            )
        data = self._post("/responses", request)
        text = "".join(
            block.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for block in item.get("content", [])
            if block.get("type") == "output_text"
        )
        usage = data.get("usage", {})
        return ModelResult(
            text,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            "stop"
            if data.get("status") == "completed"
            else (data.get("incomplete_details") or {}).get("reason", "failed"),
        )
