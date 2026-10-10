from __future__ import annotations

from typing import Any

from rg.extract.queue import STATES
from rg.store.database import Store


def health(store: Store, project: str | None, limit: int, offset: int) -> dict[str, Any]:
    counts = dict.fromkeys(STATES, 0)
    counts.update(
        dict(
            store.db.execute(
                "SELECT state,count(*) FROM extraction_queue WHERE (? IS NULL OR project_id=?) "
                "GROUP BY state",
                (project, project),
            ).fetchall()
        )
    )
    total = sum(counts.values())
    rows = store.db.execute(
        "SELECT queue_id,session_pk,project_id,model,max_event_id,state,attempts,error,"
        "defer_reason,next_attempt_at,created_at,updated_at FROM extraction_queue "
        "WHERE (? IS NULL OR project_id=?) ORDER BY updated_at DESC,queue_id DESC LIMIT ? OFFSET ?",
        (project, project, limit, offset),
    ).fetchall()
    return {
        "scope": "project" if project is not None else "all_projects",
        "total": total,
        "counts": counts,
        "tasks": [dict(row) for row in rows],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }
