from __future__ import annotations

import json
from pathlib import Path

from rg.extract.differences import confirmed_differences
from rg.store.database import Store
from rg.store.objects import atomic_write


def report(store: Store, session_id: int, destination: Path) -> None:
    lines = [
        f"# 会话 {session_id} 的候选提取报告",
        "",
        "本报告供人工复核。候选不代表已采用、已确认或科学结论成立。",
        "",
    ]
    claims = store.db.execute(
        "SELECT DISTINCT c.* FROM claims c JOIN claim_evidence ce USING(claim_id) "
        "JOIN evidence_spans es USING(span_id) JOIN raw_events r USING(event_id) "
        "WHERE r.session_pk = ? ORDER BY c.claim_id",
        (session_id,),
    ).fetchall()
    for item in claims:
        review = store.db.execute(
            "SELECT actor,reason FROM review_actions WHERE claim_id=? "
            "ORDER BY action_id DESC LIMIT 1",
            (item["claim_id"],),
        ).fetchone()
        state = store.claim_state(item["claim_id"])
        reviewer = (
            review["actor"]
            if review
            else (
                item["actor"]
                if state == "confirmed" and item["actor"].startswith("human:")
                else "尚未复核"
            )
        )
        lines.extend(
            [
                f"## 记录 {item['claim_id']} · {item['claim_type']}",
                "",
                f"审核状态：{state}",
                f"审核来源：{reviewer}",
                "",
                "```json",
                json.dumps(json.loads(item["payload"]), ensure_ascii=False, indent=2),
                "```",
                "",
                "原文引用：",
                "",
            ]
        )
        if review and review["actor"].startswith("rule:"):
            lines.extend(["独立规则核对：", "", "```json", review["reason"], "```", ""])
        for previous in confirmed_differences(store, item["claim_id"]):
            fields = "、".join(previous["differing_fields"]) or "内容一致"
            lines.extend(
                [
                    f"已有人工确认 #{previous['claim_id']}；对照字段：{fields}。新记录仍待复核。",
                    "",
                    "```json",
                    json.dumps(previous["payload"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        spans = store.db.execute(
            "SELECT es.* FROM evidence_spans es JOIN claim_evidence ce "
            "USING(span_id) WHERE claim_id = ?",
            (item["claim_id"],),
        ).fetchall()
        for span in spans:
            quote = store.raw(span["event_id"])[span["byte_start"] : span["byte_end"]].decode()
            lines.extend(
                [
                    f"- 事件 {span['event_id']}，UTF-8 字节 "
                    f"{span['byte_start']}–{span['byte_end']}",
                    "",
                    *["> " + line for line in quote.splitlines()],
                    "",
                ]
            )
    pending = store.db.execute(
        "SELECT count(*) FROM coverage c JOIN raw_events r USING(event_id) "
        "WHERE session_pk = ? AND status = 'pending'",
        (session_id,),
    ).fetchone()[0]
    lines.extend(
        [
            "## 覆盖与验收",
            "",
            f"- 待处理阶段记录：{pending}。",
            "- 人工参考决定对照：尚未提供；找回率、错误条数与复核耗时未测。",
            "- 不能据此报告宣布 M1 通过。",
            "",
        ]
    )
    atomic_write(destination, "\n".join(lines).encode())
