from __future__ import annotations

import json
import re
from typing import Any

from rg.extract.redact import redact
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim
from rg.ingest.spans import decoded_range
from rg.slim.tokens import TokenCounter
from rg.store.database import Store

CUE_PATTERNS = {
    "retraction": re.compile(r"不用了|先不|暂缓|暂停|放弃|撤回|回退|之前错了|revert|drop", re.I),
    "decision": re.compile(
        r"改用|换成|就用|采用|保留|选择|选\s*[A-Z]|switch to|instead|keep|go with", re.I
    ),
    "comparison": re.compile(r"对比|比较|compare", re.I),
    "finding": re.compile(r"发现|结果|错误|失败|error|exception|finding", re.I),
    "question": re.compile(r"为什么|假设|why|hypothesis", re.I),
}


def _safe_ranges(
    raw: bytes,
    start: int,
    end: int,
    private_ranges: tuple[tuple[int, int], ...] | None = None,
) -> list[tuple[int, int]]:
    intervals = [(start, end)]
    for private_start, private_end in (
        redact(raw).private_ranges if private_ranges is None else private_ranges
    ):
        remaining = []
        for a, b in intervals:
            if private_start >= b or private_end <= a:
                remaining.append((a, b))
            else:
                if a < private_start:
                    remaining.append((a, private_start))
                if private_end < b:
                    remaining.append((private_end, b))
        intervals = remaining
    return [(a, b) for a, b in intervals if a < b and raw[a:b].strip()]


def _bounded(raw: bytes, start: int, end: int, counter: TokenCounter) -> list[tuple[int, int]]:
    text = raw[start:end].decode()
    if counter.count_text(text) <= 2000:
        return [(start, end)]
    result = []
    while text:
        lo, hi = 0, len(text)
        while lo < hi:
            middle = (lo + hi + 1) // 2
            if counter.count_text(text[:middle]) <= 2000:
                lo = middle
            else:
                hi = middle - 1
        if not lo:
            raise InvalidClaim("单字符超过原文窗口预算")
        byte_end = start + len(text[:lo].encode())
        result.append((start, byte_end))
        start, text = byte_end, text[lo:]
    return result


def _display(raw: bytes, start: int, end: int, is_string: bool) -> str:
    text = raw[start:end].decode()
    if is_string:
        try:
            return json.loads('"' + text + '"')
        except ValueError:
            pass
    return text


def units(store: Store, item: Segment, counter: TokenCounter) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    raw_cache: dict[int, bytes] = {}
    private_cache: dict[int, tuple[tuple[int, int], ...]] = {}
    for event in item.input_events:
        event_id = event["event_id"]
        if event_id not in raw_cache:
            raw_cache[event_id] = store.raw(event_id)
            private_cache[event_id] = redact(raw_cache[event_id]).private_ranges
        raw = raw_cache[event_id]
        private = private_cache[event_id]
        start, end = event.get("raw_start", 0), event.get("raw_end", len(raw))
        pieces: list[tuple[int, int, str]] = []
        if event["kind"] in {"user_msg", "assistant_msg", "plan_update"}:
            for a, b in _safe_ranges(raw, start, end, private):
                source = raw[a:b].decode()
                for match in re.finditer(r".+?(?:[。！？；;!?]|\\n|$)", source, re.DOTALL):
                    first = a + len(source[: match.start()].encode())
                    last = a + len(source[: match.end()].encode())
                    for left, right in _bounded(raw, first, last, counter):
                        pieces.append(
                            (
                                left,
                                right,
                                _display(raw, left, right, event.get("raw_is_string", False)),
                            )
                        )
        else:
            if event["kind"] == "tool_result" and event.get("raw_is_string"):
                original_text = json.loads('"' + raw[start:end].decode() + '"')
                lines = original_text.splitlines(keepends=True)
                selected: list[tuple[int, int]] = []
                consumed = 0
                for index, line in enumerate(lines):
                    if (
                        index < 5
                        or index >= len(lines) - 5
                        or re.search(r"error|exception|错误|失败", line, re.I)
                    ):
                        selected.append((consumed, consumed + min(200, len(line))))
                        if len(line) > 200:
                            selected.append((consumed + len(line) - 200, consumed + len(line)))
                    consumed += len(line)
                for first, last in list(dict.fromkeys(selected))[:30]:
                    if first == last:
                        continue
                    a, b = decoded_range(raw, start, end, first, last)
                    for left, right in _safe_ranges(raw, a, b, private):
                        for first_byte, last_byte in _bounded(raw, left, right, counter):
                            pieces.append(
                                (first_byte, last_byte, _display(raw, first_byte, last_byte, True))
                            )
            else:
                # 非字符串工具值不推断内部位置：只给有限的首尾窗口与摘要定位。
                safe = _safe_ranges(raw, start, end, private)
                for a, b in safe[:1] + (safe[-1:] if len(safe) > 1 else []):
                    bounded = _bounded_prefix(raw, a, b, counter)
                    pieces.append((*bounded, event["text"]))
                if safe and end - start > 4000:
                    a, b = safe[-1]
                    tail = raw[a:b].decode()[-200:]
                    left = b - len(tail.encode())
                    pieces.append((left, b, event["text"] + "（原文尾部）"))
        for a, b, text in pieces:
            result.append(
                {
                    "event_id": event["event_id"],
                    "kind": event["kind"],
                    "call_id": event.get("call_id"),
                    "byte_range": [a, b],
                    "text": text,
                    "context_only": event["context_only"],
                }
            )
    return result


def _bounded_prefix(raw: bytes, start: int, end: int, counter: TokenCounter) -> tuple[int, int]:
    text = raw[start:end].decode()
    if counter.count_text(text) <= 2000:
        return start, end
    lo, hi = 0, len(text)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if counter.count_text(text[:middle]) <= 2000:
            lo = middle
        else:
            hi = middle - 1
    if not lo:
        raise InvalidClaim("单字符超过原文窗口预算")
    return start, start + len(text[:lo].encode())


def positions(
    input_units: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    allowed = {(u["event_id"], *u["byte_range"]): u for u in input_units}
    result = []
    for candidate in candidates:
        key = (candidate["event_id"], *candidate["byte_range"])
        if key not in allowed:
            raise InvalidClaim("pass1 字节位置不是已给出的原文窗口")
        if not allowed[key]["context_only"]:
            result.append({**candidate, "source": "model"})
    for unit in input_units:
        if unit["context_only"]:
            continue
        for cue, pattern in CUE_PATTERNS.items():
            if pattern.search(unit["text"]):
                result.append(
                    {
                        "event_id": unit["event_id"],
                        "byte_range": unit["byte_range"],
                        "cue": cue,
                        "source": "rule",
                    }
                )
    return result


def windows(
    store: Store, item: Segment, input_units: list[dict[str, Any]], located: list[dict[str, Any]]
) -> tuple[
    list[dict[str, Any]], dict[int, list[tuple[int, int]]], dict[int, list[tuple[int, int]]]
]:
    selected = {(x["event_id"], *x["byte_range"]) for x in located}
    tools = {
        e["event_id"] for e in item.events if e["kind"] in {"tool_call", "tool_result", "file_edit"}
    }
    context = {u["event_id"]: u for u in input_units if u["context_only"]}
    output = []
    ranges: dict[int, list[tuple[int, int]]] = {}
    owned: dict[int, list[tuple[int, int]]] = {}
    seen = set()
    raw_cache: dict[int, bytes] = {}
    for unit in input_units:
        event_id = unit["event_id"]
        a, b = unit["byte_range"]
        key = (event_id, a, b, unit["context_only"])
        take = (
            (context.get(event_id) is unit)
            if unit["context_only"]
            else (event_id, a, b) in selected or event_id in tools
        )
        if not take or key in seen:
            continue
        seen.add(key)
        if event_id not in raw_cache:
            raw_cache[event_id] = store.raw(event_id)
        quote = raw_cache[event_id][a:b].decode()
        output.append(
            {
                "event_id": event_id,
                "byte_start": a,
                "byte_end": b,
                "raw": quote,
                "context_only": unit["context_only"],
            }
        )
        ranges.setdefault(event_id, []).append((a, b))
        if not unit["context_only"]:
            owned.setdefault(event_id, []).append((a, b))
    return output, ranges, owned
