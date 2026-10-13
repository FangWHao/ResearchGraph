from __future__ import annotations

from typing import Any

UNKNOWN = {"unknown", "未知", "未确定", "不详", "unspecified", "?"}


def known_scope(scope: Any) -> bool:
    return (
        isinstance(scope, dict)
        and bool(scope)
        and all(
            isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in UNKNOWN
            for value in scope.values()
        )
    )
