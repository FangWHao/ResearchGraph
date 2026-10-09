from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Redacted:
    data: bytes
    original: bytes
    private_ranges: tuple[tuple[int, int], ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.data):
            raise ValueError("引用范围非法")
        if any(start < b and end > a for a, b in self.private_ranges):
            raise ValueError("引用覆盖遮盖区域，需人工复核")
        self.data[:start].decode("utf-8")
        self.data[:end].decode("utf-8")
        return start, end


DEFAULT_PATTERNS = (
    r"<rg-context\b[^>]*>.*?(?:</rg-context>|$)",
    r"(?<!\d)\d{17}[\dXx](?!\d)",
    r"(?<!\d)1[3-9]\d{9}(?!\d)",
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"\bsk-[A-Za-z0-9_-]{8,}",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{12,}",
    r"(?:Bearer\s+)[A-Za-z0-9._-]{12,}",
    r'(?<=/home/)[^/\s"\\]+',
    r'(?<=/Users/)[^/\s"\\]+',
    r'(?<=/mnt/c/Users/)[^/\s"\\]+',
)


def redact(raw: bytes, custom: tuple[str, ...] = ()) -> Redacted:
    text = raw.decode("utf-8")
    ranges: list[tuple[int, int]] = []
    for pattern in DEFAULT_PATTERNS + custom:
        for match in re.finditer(pattern, text, re.DOTALL):
            ranges.append((len(text[: match.start()].encode()), len(text[: match.end()].encode())))
    # JSON 转义后的隐私与 rg 标记同样遮盖，映射回原始字符串的字节位置。
    from rg.ingest.spans import decoded_range

    for token in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
        if "\\" not in token[0]:
            continue
        try:
            decoded = json.loads(token[0])
        except ValueError:
            continue
        start = len(text[: token.start() + 1].encode())
        end = len(text[: token.end() - 1].encode())
        for pattern in DEFAULT_PATTERNS + custom:
            for match in re.finditer(pattern, decoded, re.DOTALL):
                ranges.append(decoded_range(raw, start, end, match.start(), match.end()))
    data = bytearray(raw)
    for start, end in ranges:
        data[start:end] = b"*" * (end - start)
    # 等长 UTF-8 字节遮盖使正反映射保持恒等；不允许引用被遮盖区域。
    return Redacted(bytes(data), raw, tuple(sorted(set(ranges))))


def model_input(content: str, stage: str) -> str:
    """按程序输入层次保留生成的标识符；原文和自由文本不享有此例外。"""
    from rg.store.database import dumps

    identities = {
        "link": {("pair_id",), ("cards", "*", "id")},
        "pass2": {("segment_id",), ("working_set", "*", "id")},
        "overview": {
            ("records", "*", "payload", field)
            for field in ("entity_id", "source", "target", "selected")
        }
        | {("records", "*", "payload", "inputs", "*", "ref")},
    }.get(stage, set())

    def walk(value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, str):
            if path in identities:
                return value
            # pass2 的 slim 是程序生成的 JSON 字符串，按 pass1 层次处理其中的正文。
            if stage == "pass2" and path == ("slim",):
                return model_input(value, "pass1")
            return redact(value.encode()).data.decode()
        if isinstance(value, list):
            return [walk(item, (*path, "*")) for item in value]
        if isinstance(value, dict):
            return {
                redact(key.encode()).data.decode(): walk(item, (*path, key))
                for key, item in value.items()
            }
        return value

    try:
        parsed = json.loads(content)
    except ValueError:
        return redact(content.encode()).data.decode()
    return dumps(walk(parsed, ()))
