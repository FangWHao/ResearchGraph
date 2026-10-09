from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rg.extract.schemas import STRING, obj
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim
from rg.extract.worker import Worker, _permission
from rg.slim.tokens import BudgetExceeded
from rg.store.database import Store, dumps
from rg.store.objects import atomic_write, digest

OVERVIEW_SCHEMA = obj(
    {
        "text": {"type": "string", "minLength": 1, "maxLength": 3000},
        "claim_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 1},
        },
        "caveats": {"type": "array", "items": STRING},
    },
    ["text", "claim_ids", "caveats"],
)


def records(
    store: Store, project: str, session: int | None = None, scope: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    if session is not None:
        owner = store.db.execute(
            "SELECT project_id FROM sessions WHERE session_pk=?", (session,)
        ).fetchone()
        if not owner or owner[0] != project:
            raise ValueError("会话不属于指定项目")
    rows = store.db.execute(
        "SELECT c.* FROM claims c WHERE EXISTS (SELECT 1 FROM entities e "
        "WHERE e.entity_id=c.entity_id AND e.project_id=?) OR EXISTS "
        "(SELECT 1 FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
        "JOIN raw_events r USING(event_id) JOIN sessions s USING(session_pk) "
        "WHERE ce.claim_id=c.claim_id AND s.project_id=?) ORDER BY c.claim_id",
        (project, project),
    ).fetchall()
    result = []
    for row in rows:
        current_scope = json.loads(row["scope"] or "{}")
        if scope is not None and current_scope != scope:
            continue
        if (
            session is not None
            and not store.db.execute(
                "SELECT 1 FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
                "JOIN raw_events r USING(event_id) WHERE ce.claim_id=? AND r.session_pk=? LIMIT 1",
                (row["claim_id"], session),
            ).fetchone()
        ):
            continue
        result.append(
            {
                "claim_id": row["claim_id"],
                "claim_type": row["claim_type"],
                "payload": json.loads(row["payload"]),
                "scope": current_scope,
                "claim_state": store.claim_state(row["claim_id"]),
                "basis": row["basis"],
                "occurred_at": row["occurred_at"] or "unknown",
                "recorded_at": row["recorded_at"],
            }
        )
    return result


def overview(
    worker: Worker,
    project: str,
    destination: Path,
    session: int | None = None,
    scope: dict[str, str] | None = None,
) -> dict[str, int]:
    store = worker.store
    _permission(store, project, worker.provider)
    source = records(store, project, session, scope)
    # 每页直接读 claims，不把任何已生成的概览作为下一页或其他阶段的输入。
    batches: list[list[dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []
    for row in source:
        if worker.provider.count_text(dumps([row])) > worker.budgets.content_tokens:
            raise BudgetExceeded("单条结构化记录超过概览预算，需人工处理")
        if (
            pending
            and worker.provider.count_text(dumps(pending + [row])) > worker.budgets.content_tokens
        ):
            batches.append(pending)
            pending = []
        pending.append(row)
    if pending:
        batches.append(pending)
    pages: list[dict[str, Any]] = []
    for batch in batches:
        identity = digest(dumps([project, session, scope, batch]).encode())
        item = Segment("overview:" + identity, [])
        run: int | None = None
        try:
            run, output = worker.invoke(
                "overview", item, project, dumps({"records": batch}), OVERVIEW_SCHEMA, []
            )
            known = {row["claim_id"] for row in batch}
            if not output["claim_ids"] or not set(output["claim_ids"]).issubset(known):
                raise InvalidClaim("概览引用了未知或其他页的记录")
            if re.search(r"</?rg-context\b", dumps(output), re.I):
                raise InvalidClaim("概览正文不能嵌套或关闭防回流标记")
            store.db.execute(
                "UPDATE extraction_runs SET status='ok' WHERE extraction_run_id=?", (run,)
            )
            pages.append({**output, "input_claim_ids": sorted(known), "run_id": run})
        except (ValueError, RuntimeError):
            if run is not None:
                worker._status(run, "invalid", "概览校验失败，未发布概览")
            raise
    lines = [
        '<rg-context origin="researchgraph" layer="overview">',
        "",
        "# 模型摘要 · 结构化研究记录概览",
        "",
        "仅供阅读；不作证据，不参与后续提取。候选与人工确认记录分别标注。",
        "",
        f"输入记录：{len(source)}；分页：{len(pages)}。",
        "",
    ]
    if not pages:
        lines.extend(["尚无结构化记录；未调用模型。", ""])
    for index, page in enumerate(pages, 1):
        lines.extend(
            [
                f"## 第 {index} 页",
                "",
                page["text"],
                "",
                "引用记录：" + "、".join(str(value) for value in page["claim_ids"]),
                "",
                "本页输入记录：" + "、".join(str(value) for value in page["input_claim_ids"]),
                "",
            ]
        )
        if page["caveats"]:
            lines.extend(["待澄清事项：", "", *["- " + text for text in page["caveats"]], ""])
    lines.append("</rg-context>")
    atomic_write(destination, "\n".join(lines).encode())
    return {"claims": len(source), "pages": len(pages)}
