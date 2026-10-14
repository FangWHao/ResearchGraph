from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rg.model import Meter, Usage
from rg.organize import CONSOLIDATE_SYSTEM, EVENT_SYSTEM, REVIEW_SYSTEM
from rg.store import Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"
LINE = re.compile(r"^#(\d+) \[[^\]]*\] 用户：(.*)$", re.M)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db = Store(tmp_path / "timeline.db")
    yield db
    db.close()


@pytest.fixture
def copy_fixture(tmp_path: Path) -> Callable[[str], Path]:
    def copy(name: str) -> Path:
        target = tmp_path / "sessions" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / name, target)
        return target

    return copy


class FakeModel:
    """事件提取：每条用户消息生成一个“尝试”事件；归类：交给测试给定的函数。"""

    def __init__(
        self,
        place: Callable[[str], dict[str, Any]] | None = None,
        review: Callable[[str], dict[str, Any]] | None = None,
        consolidate: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.place = place or (lambda user: {"placements": []})
        self.review = review or (lambda user: {"milestones": [], "summary": "", "status": "active"})
        self.consolidate = consolidate or (lambda user: {"groups": []})
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        meter: Meter | None = None,
    ) -> dict[str, Any]:
        kind = {
            EVENT_SYSTEM: "events",
            REVIEW_SYSTEM: "review",
            CONSOLIDATE_SYSTEM: "consolidate",
        }.get(system, "threads")
        self.calls.append((kind, user))
        if meter is not None:
            meter(Usage("fake", len(user), 10, cache_read_tokens=5))
        if kind == "events":
            return {
                "events": [
                    {"kind": "attempt", "title": text[:10], "summary": text, "refs": [int(idx)]}
                    for idx, text in LINE.findall(user)
                ]
            }
        if kind == "review":
            return self.review(user)
        if kind == "consolidate":
            return self.consolidate(user)
        return self.place(user)

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)
