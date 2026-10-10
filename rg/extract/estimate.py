from __future__ import annotations

from typing import Any

from rg.extract.queue import Queue, sessions
from rg.extract.segmenter import segment
from rg.extract.worker import Worker
from rg.slim.slimmer import slim_session


def estimate_session(worker: Worker, session_id: int) -> dict[str, Any]:
    if (
        type(session_id) is not int
        or session_id < 1
        or not worker.store.db.execute(
            "SELECT 1 FROM sessions WHERE session_pk=?", (session_id,)
        ).fetchone()
    ):
        raise ValueError("会话不存在或 ID 非法")
    events = slim_session(worker.store, session_id, worker.provider)
    worker.provider.validate_budget(worker.input_budget, worker.output_budget)
    items = segment(events, worker.provider, worker.budgets.content_tokens)
    return {
        "measured_slim_tokens": sum(x["tokens"] for x in events),
        "segments": len(items),
        "model": worker.provider.model,
        "generation_calls": 0,
        "input_budget": worker.input_budget,
        "content_budget": worker.budgets.content_tokens,
    }


def estimate_batch(worker: Worker, project: str | None, limit: int) -> dict[str, Any]:
    Queue.validate_limit(limit)
    rows, skipped = sessions(worker, project)
    values = [
        {
            "session_pk": row["session_pk"],
            "project_id": row["project_id"],
            **estimate_session(worker, row["session_pk"]),
        }
        for row in rows[:limit]
    ]
    return {
        "sessions": values,
        "eligible_sessions": len(rows),
        "skipped": skipped,
        "limited": len(values) < len(rows),
        "generation_calls": 0,
    }
