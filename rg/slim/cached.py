from __future__ import annotations

from typing import Any

from rg.slim.slimmer import cache_count
from rg.slim.tokens import TokenCounter
from rg.store.database import Store


class CachedCounter:
    """复用同提供方/模型/正文的实测分量计数；完整生成请求仍每次实测。"""

    def __init__(self, store: Store, counter: TokenCounter):
        self.store, self.counter = store, counter
        self.provider, self.model = counter.provider, counter.model

    def count_text(self, text: str) -> int:
        return cache_count(self.store, self.counter, text)

    def count_request(self, request: dict[str, Any]) -> int:
        return self.counter.count_request(request)
