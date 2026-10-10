"""令牌、同源与主机保护沿用本地服务；查询不创建或更新图缓存。"""

from __future__ import annotations

import json
from typing import Any

from rg.query.graph import query
from rg.store.database import Store


def semantic(store: Store, values: dict[str, str]) -> dict[str, Any]:
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
    return query(store, values.get("project", ""), arguments)
