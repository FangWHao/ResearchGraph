"""工作集按完整范围分组；未指定范围时不替会话推断一个范围。"""

from __future__ import annotations

from typing import Any

from rg.store.database import dumps
from rg.store.scopes import UNKNOWN, known_scope


def policy() -> dict[str, Any]:
    return {"working_scope_version": 1, "versions": "per_complete_scope", "references": "exact"}


def scope_shape(scope: Any) -> bool:
    return (
        isinstance(scope, dict)
        and bool(scope)
        and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            and bool(value.strip())
            for key, value in scope.items()
        )
    )


def groups(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        scope = card["scope"]
        key = dumps(scope)
        group = grouped.setdefault(
            key,
            {
                "scope": scope,
                "known": scope_shape(scope) and known_scope(scope),
                "ids": [],
            },
        )
        if card["id"] not in group["ids"]:
            group["ids"].append(card["id"])
    return list(grouped.values())


def context(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {"working_set": cards, "working_scope_groups": groups(cards)}


def matches(scope: dict[str, str], reference_scope: Any) -> bool:
    if scope_shape(reference_scope):
        return scope == reference_scope
    # 缺失或非法旧范围只是提示；它不能为新候选提供已知范围。
    return all(value.strip().lower() in UNKNOWN for value in scope.values())
