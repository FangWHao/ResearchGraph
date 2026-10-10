"""离线运行清单的有限输入合同；字段是报告，不升级执行事实。"""

from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any, BinaryIO

from rg.clients.record import unique_pairs
from rg.ingest.common import RG_BLOCK
from rg.record.question import expected_revision, timestamp
from rg.record.schema import validate_scope
from rg.store.database import dumps

MAX_BYTES = 65536
ROLES = {"inputs": "in", "scripts": "in", "patches": "in", "environment": "in", "outputs": "out"}
FIELDS = set(ROLES) | {
    "request_id",
    "run_id",
    "attempt_id",
    "snapshot_id",
    "scope",
    "parameters",
    "seed",
    "started_at",
    "ended_at",
    "exit_code",
    "occurred_at",
    "expected_revision",
}


def read(stream: BinaryIO) -> dict[str, Any]:
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("运行清单最多 65536 字节")
    try:
        value = json.loads(raw.decode(), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("运行清单需为无重复字段的有限 UTF8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("运行清单需为 JSON 对象")
    return value


def identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 120 or "\x00" in value:
        raise ValueError(f"{label}需为非空 ID，最多 120 字符")
    return value


def parameters(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("参数嵌套最多八层")
    if isinstance(value, dict):
        if len(value) > 128 or any(not isinstance(k, str) or len(k) > 500 for k in value):
            raise ValueError("参数对象键非法或过多")
        for item in value.values():
            parameters(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 256:
            raise ValueError("参数列表最多 256 项")
        for item in value:
            parameters(item, depth + 1)
    elif not (
        value is None
        or isinstance(value, (str, bool, int))
        or isinstance(value, float)
        and math.isfinite(value)
    ):
        raise ValueError("参数需为有限 JSON 值")


def normalize(body: dict[str, Any]) -> tuple[str, dict[str, Any], int | None]:
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise ValueError("运行清单包含未知字段；项目和报告身份由入口固定")
    request_id = body.get("request_id")
    if not isinstance(request_id, str):
        raise ValueError("运行清单需提供标准 UUID request_id")
    try:
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError
    except ValueError as error:
        raise ValueError("运行清单需提供标准 UUID request_id") from error
    data = {
        "run_id": identity(body.get("run_id"), "运行"),
        "attempt_id": identity(body["attempt_id"], "尝试")
        if body.get("attempt_id") is not None
        else None,
        "snapshot_id": body.get("snapshot_id"),
        "scope": validate_scope(body.get("scope")),
        "parameters": body.get("parameters"),
        "seed": body.get("seed"),
        "exit_code": body.get("exit_code"),
    }
    if data["snapshot_id"] is not None and (
        type(data["snapshot_id"]) is not int or data["snapshot_id"] < 1
    ):
        raise ValueError("快照 ID 需为正整数")
    if data["exit_code"] is not None and (
        type(data["exit_code"]) is not int or not -(2**31) <= data["exit_code"] < 2**31
    ):
        raise ValueError("报告退出码需为 32 位整数或空")
    if data["seed"] is not None:
        seed = data["seed"]
        if not (
            type(seed) is int
            and seed.bit_length() <= 266
            or isinstance(seed, str)
            and re.fullmatch(r"-?[0-9]{1,80}", seed)
        ):
            raise ValueError("种子需为最多 80 位的十进制整数")
        if len(str(abs(int(seed)))) > 80:
            raise ValueError("种子需为最多 80 位的十进制整数")
        data["seed"] = str(int(data["seed"]))
    if data["parameters"] is not None:
        if not isinstance(data["parameters"], dict):
            raise ValueError("参数需为对象或空")
        parameters(data["parameters"])
    count = 0
    for role in ROLES:
        items = body.get(role)
        if items is not None and (
            not isinstance(items, list)
            or len(items) > 256
            or any(not isinstance(item, str) for item in items)
        ):
            raise ValueError("文件版本字段需为 ID 列表或空")
        data[role] = [identity(item, "文件版本") for item in items] if items is not None else None
        count += len(items or [])
    if count > 256:
        raise ValueError("运行清单文件版本合计最多 256 项")
    for field in ("started_at", "ended_at", "occurred_at"):
        data[field] = timestamp(body.get(field))
    if data["started_at"] and data["ended_at"] and data["started_at"] > data["ended_at"]:
        raise ValueError("报告结束时间早于开始时间")
    serialized = dumps(data)
    if len(serialized.encode()) > MAX_BYTES or RG_BLOCK.search(serialized):
        raise ValueError("运行清单过大或包含 ResearchGraph 注入内容")
    expected = expected_revision(body["expected_revision"]) if "expected_revision" in body else None
    return request_id, data, expected
