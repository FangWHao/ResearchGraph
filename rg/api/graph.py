"""令牌、同源与主机保护沿用本地服务；查询不创建或更新图缓存。"""

from __future__ import annotations

import json
from typing import Any

from rg.query.graph import query
from rg.store.database import ConflictError, Store


def parameters(values: dict[str, str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for key, value in values.items():
        if key == "project":
            continue
        if key in {"limit", "offset", "expected_revision"}:
            if not value.isascii() or not value.isdecimal() or len(value) > 20:
                raise ValueError("图分页参数需为有限非负整数")
            arguments[key] = int(value)
        elif key == "scope":

            def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for k, v in items:
                    if k in result:
                        raise ValueError("范围字段不得重复")
                    result[k] = v
                return result

            arguments[key] = json.loads(value, object_pairs_hook=pairs)
        else:
            arguments[key] = value
    return arguments


def semantic(store: Store, values: dict[str, str]) -> dict[str, Any]:
    return query(store, values.get("project", ""), parameters(values))


def l1(store: Store, values: dict[str, str]) -> dict[str, Any]:
    from rg.query.l1 import query as l1_query

    return l1_query(store, values.get("project", ""), parameters(values))


def source(store: Store, values: dict[str, str]) -> dict[str, Any]:
    from rg.query.reader import Reader

    if (
        set(values)
        - {
            "project",
            "event_id",
            "occurred_until",
            "known_until",
            "expected_revision",
            "byte_offset",
            "max_bytes",
        }
        or not values.get("project")
        or not values.get("event_id")
    ):
        raise ValueError("L1 原文读取需明确项目和事件，使用双截止与字节窗口")
    arguments: dict[str, Any] = {}
    for key, value in values.items():
        if key == "project":
            continue
        if key in {"event_id", "expected_revision", "byte_offset", "max_bytes"}:
            if not value.isascii() or not value.isdecimal() or len(value) > 20:
                raise ValueError("原文事件和字节参数需为有限非负整数")
            arguments[key] = int(value)
        else:
            arguments[key] = value
    with store.snapshot():
        reader = Reader(store, values["project"], arguments)
        if arguments.get("expected_revision", store.revision()) != store.revision():
            raise ConflictError("L1 图已变化，请重新读取后打开原文")
        result = reader.evidence()
        result.pop("artifact_versions", None)
        return result
