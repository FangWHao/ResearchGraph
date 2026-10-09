from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.schemas import CLAIM_SCHEMA

# 问题本身可以尚无分析范围；此例外不进入模型 schema 或决定采用计算。
QUESTION_SCHEMA = deepcopy(CLAIM_SCHEMA["oneOf"][0])
QUESTION_SCHEMA["properties"]["kind"] = {"const": "question"}
QUESTION_SCHEMA["properties"]["scope"] = {
    "anyOf": [QUESTION_SCHEMA["properties"]["scope"], {"type": "null"}]
}


def validate_question(payload: dict[str, Any]) -> None:
    if list(Draft202012Validator(QUESTION_SCHEMA).iter_errors(payload)):
        raise ValueError("人工问题不符合记录 schema")


def validate_scope(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not 1 <= len(value) <= 32
        or any(
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 100
            or not isinstance(item, str)
            or not item.strip()
            or len(item) > 500
            for key, item in value.items()
        )
    ):
        raise ValueError("范围需为非空字段和值，最多 32 项；未知范围请使用 null")
    return dict(value)
