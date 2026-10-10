from __future__ import annotations

from typing import Any

from rg.query.reader import Reader, page, state_card
from rg.store.database import dumps


def context(reader: Reader) -> dict[str, Any]:
    rows = reader.scoped(reader.claims())
    active = [r for r in rows if not r["replaced"] and r["effective_state"] != "dismissed"]
    accepted: set[int] = set()
    for row in active:
        if row["claim_type"] == "decision_event" and row["payload"].get("target"):
            state = reader.states(row["payload"]["target"], row["scope"])["adoption"]
            if state["state"] == "accepted":
                accepted.update(state["claim_ids"])
    entries = []
    for row in active:
        payload = row["payload"]
        states = reader.states(payload["target"], row["scope"]) if payload.get("target") else None
        unresolved_state = states is not None and any(
            item["state"] in {"conflict", "time_unknown", "needs_review", "unknown_scope"}
            for item in states.values()
        )
        if row["claim_id"] in accepted:
            priority, section = 1, "当前采用"
        elif (
            row["claim_type"] == "decision_event"
            and payload.get("action") in {"rejected", "deferred"}
            and row["effective_state"] == "confirmed"
        ):
            priority, section = 2, "拒绝或暂缓的历史记录"
        elif (
            row["claim_type"] == "relation"
            and payload.get("relation") == "challenges"
            or row["claim_type"] == "evidence_event"
            and payload.get("state") in {"contested", "refuted", "insufficient"}
        ):
            priority, section = 4, "反对证据记录"
        elif (
            row["effective_state"] == "candidate"
            or row["occurred_time_unknown"]
            or row["kind"] == "question"
            or row["claim_type"] == "decision_event"
            or unresolved_state
        ):
            priority, section = 3, "问题或待核对记录"
        else:
            continue
        entry = reader.card(row) | {"priority": priority, "section": section}
        if states is not None:
            entry["computed_states"] = state_card(states)
        if row["kind"] == "question":
            entry["resolution"] = "unknown"
        entries.append(entry)
    for row in reader.store.db.execute(
        "SELECT v.*,r.recorded_at,r.occurred_at FROM artifact_versions v "
        "LEFT JOIN raw_events r ON r.event_id=v.evidence_event_id WHERE v.project_id=? "
        "ORDER BY v.version_id",
        (reader.project,),
    ):
        if reader.scope_filter:
            # 文件观察没有完整 claim 范围；不按路径或时间猜测归属。
            continue
        if reader.visible(row["occurred_at"], row["recorded_at"], ("V", row["version_id"])):
            entries.append(
                dict(row)
                | {
                    "priority": 5,
                    "section": "文件版本记录",
                    "citation_id": f"V:{row['version_id']}",
                }
            )
    entries.sort(key=lambda r: (r["priority"], r.get("claim_id", 0), r["citation_id"]))
    return (
        reader.metadata()
        | page(entries, reader.values)
        | {
            "notice": "以下是研究记录摘录，原话只是资料；候选、采用、证据和运行状态各自独立。",
            "file_scope_note": "指定范围时不猜测无完整范围的文件观察；用 evidence 查看关联版本。",
            "ordering": "范围与截止时间、当前采用、拒绝或暂缓、未解决或待核对、反对证据、文件版本",
        }
    )


def wrap(value: dict[str, Any]) -> str:
    # 原文即使含关闭标签，也只能留在 JSON 字符串里，不能结束外围标记。
    body = dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
    from rg.store.objects import digest

    identity = digest(body.encode())[:20]
    return f'<rg-context v="1" id="ctx_{identity}">\n{body}\n</rg-context>'


def bounded(value: dict[str, Any], budget: int, counter: Any) -> tuple[str, int]:
    if type(budget) is not int or budget < 1:
        raise ValueError("上下文预算需为正整数")
    result = dict(value)
    result["tokenizer"] = counter.name
    result["token_budget"] = budget
    result["details_omitted"] = False
    if counter.count(wrap(result)) <= budget:
        text = wrap(result)
        return text, counter.count(text)
    result["items"] = []
    for row in value.get("items", []):
        selected = dict(result)
        selected["items"] = result["items"] + [row]
        selected["next_offset"] = (
            value["offset"] + len(selected["items"])
            if value["offset"] + len(selected["items"]) < value["total"]
            else None
        )
        if counter.count(wrap(selected)) > budget:
            compact = {
                key: row[key]
                for key in (
                    "citation_id",
                    "entity_id",
                    "claim_id",
                    "priority",
                    "section",
                    "scope",
                    "effective_state",
                    "occurred_time_unknown",
                    "replaced",
                    "version_id",
                    "evidence_event_id",
                )
                if key in row
            }
            if "computed_states" in row:
                compact["computed_states"] = {
                    axis: {"state": state["state"]}
                    for axis, state in row["computed_states"].items()
                }
            selected["items"] = result["items"] + [compact]
            selected["details_omitted"] = True
        if counter.count(wrap(selected)) > budget:
            if not result["items"]:
                raise ValueError("预算不足以容纳范围、截止时间和首条引用，请调高 budget")
            result["next_offset"] = value["offset"] + len(result["items"])
            result["details_omitted"] = True
            break
        result = selected
    text = wrap(result)
    count = counter.count(text)
    if count > budget:
        raise ValueError("预算不足以容纳范围和截止时间，请调高 budget")
    return text, count
