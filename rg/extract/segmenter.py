from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rg.extract.budgets import Budgets
from rg.slim.tokens import TokenCounter
from rg.store.database import dumps
from rg.store.objects import digest


@dataclass
class Segment:
    segment_id: str
    events: list[dict[str, Any]]
    context_events: list[dict[str, Any]] = field(default_factory=list)
    context_gaps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return [event["event_id"] for event in self.events]

    @property
    def input_events(self) -> list[dict[str, Any]]:
        return [
            *({**event, "context_only": True} for event in self.context_events),
            *({**event, "context_only": False} for event in self.events),
        ]

    @property
    def input_ids(self) -> list[int]:
        return list(dict.fromkeys(e["event_id"] for e in self.input_events))

    def content(self) -> str:
        return render(self.input_events)


def render(events: list[dict[str, Any]]) -> str:
    return dumps(
        [
            {
                "event_id": e["event_id"],
                "kind": e["kind"],
                "text": e["text"],
                "raw_start": e.get("raw_start"),
                "raw_end": e.get("raw_end"),
                **({"context_only": e["context_only"]} if "context_only" in e else {}),
            }
            for e in events
        ]
    )


def segment(
    events: list[dict[str, Any]],
    counter: TokenCounter,
    budget: int = Budgets().content_tokens,
) -> list[Segment]:
    if budget < 1:
        raise ValueError("片段预算必须为正整数")
    prepared = []
    for event in events:
        source = event.get("raw_text", event["text"])
        if event["kind"] not in {"user_msg", "assistant_msg"} or counter.count_text(source) <= 2000:
            prepared.append(event)
            continue
        consumed = 0
        position = 0
        while position < len(source):
            lo, hi = position, len(source)
            while lo < hi:
                middle = (lo + hi + 1) // 2
                if counter.count_text(source[position:middle]) <= 2000:
                    lo = middle
                else:
                    hi = middle - 1
            if lo == position:
                raise ValueError("单个字符的计数超过窗口预算")
            part = source[position:lo]
            base = event.get("raw_start", 0)
            prepared.append(
                {
                    **event,
                    "text": part,
                    "raw_start": base + consumed,
                    "raw_end": base + consumed + len(part.encode()),
                    "raw_text": part,
                }
            )
            consumed += len(part.encode())
            position = lo
    events = prepared
    # 调用与结果之间的事件保持为一个不可拆组，缺少结果的调用仍然保留。
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events):
        if event.get("call_id") and event["kind"] != "tool_result":
            end = next(
                (
                    j
                    for j in range(index + 1, len(events))
                    if events[j]["kind"] == "tool_result"
                    and events[j].get("call_id") == event["call_id"]
                ),
                index,
            )
            intervals.append((index, end))
    groups: list[list[dict[str, Any]]] = []
    start = 0
    while start < len(events):
        end = max((b for a, b in intervals if a == start), default=start)
        while True:
            expanded = max((b for a, b in intervals if start <= a <= end), default=end)
            if expanded == end:
                break
            end = expanded
        groups.append(events[start : end + 1])
        start = end + 1
    segments = []
    pending: list[dict[str, Any]] = []
    for group in groups:
        user_boundary = (
            pending
            and group[0]["kind"] == "user_msg"
            and group[0]["event_id"] != pending[-1]["event_id"]
        )
        # 为相邻片段的真实原文重叠预留空间，完整请求仍由 worker 实测。
        owned_budget = max(1, budget // 2)
        over = counter.count_text(render(pending + group)) > owned_budget
        if pending and (user_boundary or over):
            segments.append(Segment(digest(render(pending).encode())[:24], pending))
            pending = []
        if counter.count_text(render(group)) > owned_budget:
            # 超长且不能拆分的工具组交给 worker 的预算检查及人工队列。
            segments.append(Segment(digest(render(group).encode())[:24], group))
        else:
            pending.extend(group)
    if pending:
        segments.append(Segment(digest(render(pending).encode())[:24], pending))
    # 全文回合以原始 user event_id 为界；同一长正文的多个窗口是延续片段。
    history: list[dict[str, Any]] = []
    current_turn: list[dict[str, Any]] = []
    previous_turn: list[dict[str, Any]] = []
    user_id: int | None = None
    for item in segments:
        first = item.events[0]
        new_turn = first["kind"] == "user_msg" and first["event_id"] != user_id
        if new_turn:
            previous_turn = current_turn
            current_turn = []
            user_id = first["event_id"]
            item.context_events = list(previous_turn)
        elif history:
            # 延续片段携带最近不可拆组；省略部分有账可查，绝不成为“已覆盖”。
            last_group = next((g for g in reversed(groups) if g[-1] is history[-1]), [])
            item.context_events = list(last_group)
            omitted = current_turn[: max(0, len(current_turn) - len(last_group))]
            item.context_gaps = [
                {
                    "event_id": e["event_id"],
                    "byte_start": e.get("raw_start"),
                    "byte_end": e.get("raw_end"),
                    "reason": "continuation_window",
                }
                for e in omitted
            ]
        identity = dumps([item.content(), item.context_gaps])
        item.segment_id = digest(identity.encode())[:24]
        current_turn.extend(item.events)
        history.extend(item.events)
    return segments


def split(item: Segment) -> list[Segment]:
    if len(item.events) < 2:
        if not item.events or item.events[0]["kind"] not in {"user_msg", "assistant_msg"}:
            return []
        event = item.events[0]
        source = event.get("raw_text", event["text"])
        if len(source) < 2:
            return []
        middle = len(source) // 2
        base = event.get("raw_start", 0)
        boundary = base + len(source[:middle].encode())
        return _retry_overlap(
            [
                Segment(
                    item.segment_id + ":a",
                    [
                        {
                            **event,
                            "text": source[:middle],
                            "raw_text": source[:middle],
                            "raw_start": base,
                            "raw_end": boundary,
                        }
                    ],
                    list(item.context_events),
                    list(item.context_gaps),
                ),
                Segment(
                    item.segment_id + ":b",
                    [
                        {
                            **event,
                            "text": source[middle:],
                            "raw_text": source[middle:],
                            "raw_start": boundary,
                            "raw_end": base + len(source.encode()),
                        }
                    ],
                    list(item.context_events),
                    list(item.context_gaps),
                ),
            ],
        )
    middle = len(item.events) // 2
    # 不切断调用与对应结果；无法拆分的单调用组转人工队列。
    while True:
        previous_middle = middle
        for event in item.events[:middle]:
            if event.get("call_id") and event["kind"] != "tool_result":
                result = next(
                    (
                        i
                        for i in range(middle, len(item.events))
                        if item.events[i].get("call_id") == event["call_id"]
                        and item.events[i]["kind"] == "tool_result"
                    ),
                    None,
                )
                if result is not None:
                    middle = result + 1
        if middle == previous_middle:
            break
    if middle == len(item.events):
        return []
    return _retry_overlap(
        [
            Segment(
                item.segment_id + ":a",
                item.events[:middle],
                list(item.context_events),
                list(item.context_gaps),
            ),
            Segment(
                item.segment_id + ":b",
                item.events[middle:],
                list(item.context_events),
                list(item.context_gaps),
            ),
        ],
    )


def _retry_overlap(children: list[Segment]) -> list[Segment]:
    left, right = children
    start = len(left.events) - 1
    while True:
        previous_start = start
        for i, event in enumerate(left.events[:start]):
            if (
                event.get("call_id")
                and event["kind"] != "tool_result"
                and any(
                    result["kind"] == "tool_result" and result.get("call_id") == event["call_id"]
                    for result in left.events[start:]
                )
            ):
                start = i
                break
        if start == previous_start:
            break
    right.context_events.extend(left.events[start:])
    right.context_gaps.extend(
        {
            "event_id": event["event_id"],
            "byte_start": event.get("raw_start"),
            "byte_end": event.get("raw_end"),
            "reason": "retry_window",
        }
        for event in left.events[:start]
    )
    # 父 ID 隐含其完整上下文；子窗口与重叠的变化仍纳入请求缓存键。
    return children
