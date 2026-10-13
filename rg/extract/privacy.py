from __future__ import annotations

from typing import Any

from rg.extract.redact import redact
from rg.slim.cached import CachedCounter
from rg.slim.tokens import TokenCounter
from rg.store.database import Store
from rg.store.privacy import Policy, read


class ProjectCounter:
    """项目配置固定到本次处理；先遮盖再缓存、计数，发送与配置写入互斥。"""

    def __init__(self, store: Store, project: str, counter: TokenCounter):
        self.store = store
        self.policy: Policy = read(store, project)
        self.provider, self.model = counter.provider, counter.model
        self.remote = bool(getattr(counter, "remote", False))
        self.counter = CachedCounter(store, counter)

    def count_text(self, text: str) -> int:
        with self.policy.sending(self.store, self.remote):
            safe = redact(text.encode(), self.policy.patterns).data.decode()
            return self.counter.count_text(safe)

    def count_request(self, request: dict[str, Any]) -> int:
        # 完整请求由 Worker 用同一配置准备；每次仍调用提供方实测。
        with self.policy.sending(self.store, self.remote):
            return self.counter.count_request(request)

    def safe_slice(self, text: str, start: int, end: int) -> str:
        safe = redact(text.encode(), self.policy.patterns).data
        a, b = len(text[:start].encode()), len(text[:end].encode())
        return safe[a:b].decode()

    def count_slice(self, text: str, start: int, end: int) -> int:
        return self.count_text(self.safe_slice(text, start, end))
