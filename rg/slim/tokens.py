from __future__ import annotations

from typing import Any, Protocol


class TokenCounter(Protocol):
    provider: str
    model: str

    def count_text(self, text: str) -> int: ...

    def count_request(self, request: dict[str, Any]) -> int: ...


class CountingUnavailable(RuntimeError):
    pass


class BudgetExceeded(ValueError):
    pass
