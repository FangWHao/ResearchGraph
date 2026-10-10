"""研究图的完整记录页及同一双时间下的只读详情和原文。"""

from __future__ import annotations

from typing import Any

from rg.api.views import NotFound, _event, _spans
from rg.query.reader import Reader, page
from rg.query.time import instant
from rg.store.database import ConflictError, Store


def record(reader: Reader, item: dict[str, Any]) -> dict[str, Any]:
    """沿用历史审核，不把当前 _claim 的状态拼入旧图。"""
    references, offset = [], 0
    while True:
        found = reader.references(item["claim_id"], {"limit": 100, "offset": offset})
        references.extend(found["items"])
        offset = found["next_offset"]
        if offset is None:
            break
    visible = {span["span_id"] for span in references}
    spans = [s for s in _spans(reader.store, item["claim_id"]) if s["span_id"] in visible]
    actor = item["review"]["actor"] if item["review"] else item["actor"]
    return item | {
        "evidence": spans,
        "citation_id": f"C{item['claim_id']}",
        "confirmation_source": (
            "human" if actor.startswith("human:") else "rule" if actor.startswith("rule:") else None
        )
        if item["effective_state"] == "confirmed"
        else None,
        # 历史图只登记可见原件的会话；不把晚到的派生片段计划归入旧视图。
        "groups": [
            {"session_pk": identity, "segment_id": None}
            for identity in sorted({s["session_pk"] for s in spans})
        ],
        "groups_basis": "visible_source_sessions",
    }


def records(reader: Reader, values: dict[str, Any]) -> dict[str, Any]:
    rows = reader.scoped(reader.claims())
    result = page(rows, values)
    result["items"] = [record(reader, item) for item in result["items"]]
    return (
        reader.metadata()
        | result
        | {
            "collection": "claims",
            "claims_total": len(rows),
            "layer": "L2",
            "projection": False,
            "semantic_graph_complete": True,
            "content_version_selection_is_view_only": True,
            "l1_dependencies_complete": False,
        }
    )


def _reader(store: Store, project: str, values: dict[str, Any]) -> Reader:
    expected = values.get("expected_revision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision 需为非负整数")
    reader = Reader(store, project, values)
    if expected is not None and expected != store.revision():
        raise ConflictError("图已变化，请固定双时间后重新读取")
    return reader


def detail(store: Store, project: str, claim_id: int, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - {"occurred_until", "known_until", "expected_revision", "scope"}:
        raise ValueError("未知历史记录详情参数")
    with store.snapshot():
        reader = _reader(store, project, values)
        visible = reader.scoped(reader.claims())
        item = next((r for r in visible if r["claim_id"] == claim_id), None)
        if item is None:
            raise NotFound("当前查询中没有这个记录")
        result = record(reader, item)
        result["review_history"] = [
            dict(row)
            for row in store.db.execute(
                "SELECT * FROM review_actions WHERE claim_id=? ORDER BY action_id", (claim_id,)
            )
            if (stamp := instant(row["recorded_at"])) is not None and stamp <= reader.known_cutoff
        ]
        if item["replaces_claim"]:
            original = next((r for r in visible if r["claim_id"] == item["replaces_claim"]), None)
            result["replacement_original_visible"] = original is not None
            if original is not None:
                result["replaces"] = {
                    key: original[key] for key in ("claim_id", "payload", "scope")
                }
        return reader.metadata() | {"claim": result, "history_context": True}


def evidence(store: Store, project: str, event_id: int, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - {
        "occurred_until",
        "known_until",
        "expected_revision",
        "start",
        "end",
        "context",
    }:
        raise ValueError("未知历史原文参数")
    context = values.get("context", 2)
    if type(context) is not int or not 0 <= context <= 5:
        raise ValueError("上下文条数为 0 到 5")
    start, end = values.get("start"), values.get("end")
    if any(value is not None and (type(value) is not int or value < 0) for value in (start, end)):
        raise ValueError("原文字节范围无效")
    with store.snapshot():
        reader = _reader(store, project, values)
        row = store.db.execute(
            "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE r.event_id=? AND s.project_id=?",
            (event_id, project),
        ).fetchone()
        if row is None or not reader.visible(
            row["occurred_at"], row["recorded_at"], ("E", event_id)
        ):
            raise NotFound("当前查询中没有这个原文事件")

        def adjacent(before: bool) -> list[dict[str, Any]]:
            if not context:
                return []
            # 运算符和排序是固定程序语法，所有数据仍参数绑定。
            comparison, order = ("<", "DESC") if before else (">", "ASC")
            found = []
            for candidate in store.db.execute(
                "SELECT * FROM raw_events WHERE session_pk=? AND seq "
                + comparison
                + " ? ORDER BY seq "
                + order
                + ",event_id "
                + order,
                (row["session_pk"], row["seq"]),
            ):
                if reader.visible(
                    candidate["occurred_at"], candidate["recorded_at"], ("E", candidate["event_id"])
                ):
                    found.append(candidate["event_id"])
                    if len(found) == context:
                        break
            if before:
                found.reverse()
            return [_event(store, identity, None, None) for identity in found]

        event = _event(store, event_id, start, end)
        before, after = adjacent(True), adjacent(False)
        return reader.metadata() | {
            "history_context": True,
            "event": event,
            "before": before,
            "after": after,
            "derived_context_loaded": False,
        }
