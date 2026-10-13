from __future__ import annotations

import json
import re

from rg.ingest.common import RG_BLOCK


def value_span(raw: bytes, path: tuple[str | int, ...]) -> tuple[int, int, bool] | None:
    """按 JSON 路径取原始值，重复文字不会误指向另一个内容块。"""
    source = raw.decode()
    decoder = json.JSONDecoder()

    def white(position: int) -> int:
        while position < len(source) and source[position].isspace():
            position += 1
        return position

    def walk(position: int, remaining: tuple[str | int, ...]) -> tuple[int, int, bool] | None:
        position = white(position)
        if not remaining:
            value, end = decoder.raw_decode(source, position)
            is_string = isinstance(value, str)
            start = position + int(is_string)
            stop = end - int(is_string)
            return len(source[:start].encode()), len(source[:stop].encode()), is_string
        head, *tail = remaining
        if source[position] == "{" and isinstance(head, str):
            position = white(position + 1)
            located = None
            while source[position] != "}":
                key, position = decoder.raw_decode(source, position)
                position = white(position)
                position = white(position + 1)
                if key == head:
                    located = walk(position, tuple(tail))
                _, position = decoder.raw_decode(source, position)
                position = white(position)
                if source[position] == ",":
                    position = white(position + 1)
            return located
        if source[position] == "[" and isinstance(head, int):
            position = white(position + 1)
            index = 0
            while source[position] != "]":
                if index == head:
                    return walk(position, tuple(tail))
                _, position = decoder.raw_decode(source, position)
                index += 1
                position = white(position)
                if source[position] == ",":
                    position = white(position + 1)
        return None

    return walk(0, path)


def event_span(raw: bytes, tool: str, record_index: int) -> tuple[int, int, bool] | None:
    record = json.loads(raw)
    if tool == "claude":
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            path = ("message", "content")
        elif isinstance(content, list) and record_index < len(content):
            block = content[record_index]
            if not isinstance(block, dict):
                return None
            field = {"text": "text", "tool_use": "input", "tool_result": "content"}.get(
                str(block.get("type", ""))
            )
            if not field:
                return None
            path = ("message", "content", record_index, field)
        else:
            return None
    else:
        payload = record.get("payload") or {}
        kind = payload.get("type")
        if record.get("type") == "event_msg":
            path = ("payload", "message")
        elif kind == "message":
            path = ("payload", "content", record_index, "text")
        elif kind in {"function_call", "custom_tool_call"}:
            field = "arguments" if payload.get("arguments") else "input"
            path = ("payload", field)
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            path = ("payload", "output")
        else:
            return None
    return value_span(raw, path)


def decoded_range(raw: bytes, start: int, end: int, first: int, last: int) -> tuple[int, int]:
    """将 JSON 字符串的解码字符范围映射回原始字节，包含转义与代理对。"""
    source = raw[start:end].decode()
    position = decoded = consumed = 0
    left: int | None = None
    while position < len(source):
        if decoded == first:
            left = start + consumed
        length = 1
        if source[position] == "\\":
            length = 6 if source[position + 1] == "u" else 2
            if length == 6 and 0xD800 <= int(source[position + 2 : position + 6], 16) <= 0xDBFF:
                next_part = source[position + 6 : position + 12]
                if next_part.startswith("\\u") and 0xDC00 <= int(next_part[2:], 16) <= 0xDFFF:
                    length = 12
        consumed += len(source[position : position + length].encode())
        position += length
        decoded += 1
        if decoded == last and left is not None:
            return left, start + consumed
    raise ValueError("解码文字位置越界")


def text_span(raw: bytes, text: str) -> tuple[int, int] | None:
    """找回 JSON 字符串值的原始字节范围，保留转义及 UTF-8 位置。"""
    source = raw.decode()
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', source):
        try:
            value = json.loads(match[0])
        except ValueError:
            continue
        if value == text or (RG_BLOCK.search(value) and RG_BLOCK.sub("", value).strip() == text):
            return (
                len(source[: match.start() + 1].encode()),
                len(source[: match.end() - 1].encode()),
            )
    return None
