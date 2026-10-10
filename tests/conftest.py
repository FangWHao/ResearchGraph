from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rg.extract.provider import ModelResult
from rg.store.database import Store


@pytest.fixture
def store(tmp_path: Path):
    value = Store(tmp_path / "data")
    yield value
    value.close()


class FakeProvider:
    """模拟提供方计数，不用于真实 token 计数或任何生产路径。"""

    provider = "test-service"
    model = "test-model"
    remote = False

    def __init__(self, results: list[ModelResult] | None = None, count: int | None = None):
        self.results = list(results or [])
        self.calls = 0
        self.count = count
        self.counts = 0

    def count_text(self, text: str) -> int:
        self.counts += 1
        return len(text.encode())

    def count_request(self, request: dict[str, Any]) -> int:
        self.counts += 1
        return self.count if self.count is not None else len(json.dumps(request).encode())

    def request(
        self, instructions: str, content: str, schema: dict[str, Any], output_budget: int = 4000
    ) -> dict[str, Any]:
        return {
            "instructions": instructions,
            "input": content,
            "schema": schema,
            "max_tokens": output_budget,
        }

    def generate(self, request: dict[str, Any]) -> ModelResult:
        self.calls += 1
        if not self.results:
            raise RuntimeError("模拟结果耗尽")
        return self.results.pop(0)
