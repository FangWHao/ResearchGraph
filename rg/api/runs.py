"""受既有认证保护的运行证据读取；与 MCP 共用双时间查询。"""

from __future__ import annotations

import json
from typing import Any

from rg.mcp.tools import ToolService
from rg.store.database import Store


def evidence(store: Store, values: dict[str, str]) -> dict[str, Any]:
    if not values.get("project") or not values.get("run_id"):
        raise ValueError("运行读取需明确项目和运行 ID")
    arguments: dict[str, Any] = {}
    for key, value in values.items():
        if key == "project":
            continue
        if key in {"limit", "offset", "io_offset", "expected_revision"}:
            if not value.isascii() or not value.isdecimal() or len(value) > 20:
                raise ValueError("运行分页参数需为有限非负整数")
            arguments[key] = int(value)
        elif key == "scope":
            arguments[key] = json.loads(value)
        else:
            arguments[key] = value
    response = ToolService(store, values["project"]).call("research.evidence", arguments)
    text = response["content"][0]["text"]
    return json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
