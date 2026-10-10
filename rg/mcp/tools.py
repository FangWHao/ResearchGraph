from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from rg.query.context import bounded, context, wrap
from rg.query.reader import Reader
from rg.query.tokenizer import LocalCounter
from rg.store.database import ConflictError, Store

COMMON = {
    "scope": {
        "type": ["object", "null"],
        "minProperties": 1,
        "maxProperties": 32,
        "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "occurred_until": {"type": "string", "maxLength": 80},
    "known_until": {"type": "string", "maxLength": 80},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647},
    "expected_revision": {"type": "integer", "minimum": 0},
}


def definition(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": "research." + name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": COMMON | properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


TOOLS = [
    definition(
        "search",
        "当前启动项目内按字面量检索。候选保持候选；事件预览不是原文字节窗。",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "source": {"enum": ["claims", "events"]},
        },
        ["query"],
    ),
    definition(
        "node",
        "查看对象版本和全历史计算的独立状态；分页不决定当前采用。",
        {
            "entity_id": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        ["entity_id"],
    ),
    definition(
        "evidence",
        "引用 C=记录、S=片段、E=事件、V:=版本；片段偏移相对引用，事件偏移相对原件。",
        {
            "claim_id": {"type": "integer", "minimum": 1},
            "span_id": {"type": "integer", "minimum": 1},
            "event_id": {"type": "integer", "minimum": 1},
            "version_id": {"type": "string", "minLength": 1, "maxLength": 120},
            "byte_offset": {"type": "integer", "minimum": 0},
            "max_bytes": {"type": "integer", "minimum": 4, "maximum": 24000},
        },
        [],
    ),
    definition(
        "history",
        "保留修改前后及审核状态；双截止时间分别约束发生与当时已记录。",
        {
            "entity_id": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        ["entity_id"],
    ),
    definition(
        "context",
        "按优先级生成状态卡，默认 2000 本地编码 token，可调高；超额给引用。",
        {
            "budget": {"type": "integer", "minimum": 1},
        },
        [],
    ),
]
TOOLS[2]["inputSchema"]["oneOf"] = [
    {
        "required": [key],
        "not": {
            "anyOf": [
                {"required": [other]}
                for other in ("claim_id", "span_id", "event_id", "version_id")
                if other != key
            ]
        },
    }
    for key in ("claim_id", "span_id", "event_id", "version_id")
]


class ToolService:
    def __init__(
        self,
        store: Store,
        project: str,
        encoding: str = "cl100k_base",
        tokenizer_dir: Path | None = None,
    ):
        self.store, self.project = store, project
        self.encoding, self.tokenizer_dir = encoding, tokenizer_dir
        self.counter: LocalCounter | None = None

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        definitions = [item for item in TOOLS if item["name"] == name]
        if not definitions:
            raise ValueError("只提供五个 research 只读工具")
        if not isinstance(arguments, dict) or list(
            Draft202012Validator(definitions[0]["inputSchema"]).iter_errors(arguments)
        ):
            raise ValueError("工具参数不符合 schema")
        # jsonschema 将 1.0 视作整数；入口坚持实际整数，禁止 bool/浮点混入 ID。
        for key in (
            "claim_id",
            "span_id",
            "event_id",
            "byte_offset",
            "max_bytes",
            "limit",
            "offset",
            "budget",
            "expected_revision",
        ):
            if key in arguments and type(arguments[key]) is not int:
                raise ValueError("ID、分页、窗口和预算需为整数")
        with self.store.snapshot():
            if "expected_revision" in arguments and arguments["expected_revision"] != (
                self.store.revision()
            ):
                raise ConflictError("图版本已变化，请重新开始分页读取")
            reader = Reader(self.store, self.project, arguments)
            if name == "research.search":
                value = reader.search(arguments["query"])
            elif name == "research.node":
                value = reader.node(arguments["entity_id"])
            elif name == "research.history":
                value = reader.history(arguments["entity_id"])
            elif name == "research.evidence":
                value = reader.evidence()
            else:
                if self.counter is None:
                    self.counter = LocalCounter(self.encoding, self.tokenizer_dir)
                text, measured = bounded(
                    context(reader), arguments.get("budget", 2000), self.counter
                )
                return {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                    "_meta": {
                        "researchgraph/tokenizer": self.encoding,
                        "researchgraph/textTokens": measured,
                    },
                }
            return {"content": [{"type": "text", "text": wrap(value)}], "isError": False}
