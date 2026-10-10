from __future__ import annotations

import json
from typing import Any

from rg.derive.edits import diff
from rg.derive.records import calls, event
from rg.store.database import Store


def evidence(store: Store, event_id: int) -> dict[str, Any]:
    runs = [
        dict(row)
        for row in store.db.execute(
            "SELECT DISTINCT r.run_id,r.project_id,r.session_pk,r.call_id,r.cwd,r.snapshot_id,"
            "r.exit_code,r.state,r.started_at,r.ended_at,r.request_event_id,r.root_id,r.gap,"
            "r.requested_at,substr(r.command,1,8000) AS command,"
            "coalesce(length(cast(r.command AS BLOB)),0) AS command_total_bytes "
            "FROM runs r LEFT JOIN run_observations o USING(run_id) "
            "WHERE r.request_event_id=? OR o.event_id=? OR EXISTS(SELECT 1 FROM run_manifests m "
            "WHERE m.run_id=r.run_id AND m.project_id=r.project_id AND m.evidence_event_id=?) "
            "ORDER BY r.run_id LIMIT 20",
            (event_id, event_id, event_id),
        )
    ]
    for run in runs:
        from rg.query.reader import Reader
        from rg.query.runs import manifests

        run["manifests"] = manifests(
            Reader(store, run["project_id"], {}), run["run_id"], values={"limit": 20}
        )
        if isinstance(run["command"], str):
            run["command"] = run["command"].encode()[:8000].decode("utf-8", errors="ignore")
        run["command_truncated"] = len((run["command"] or "").encode()) < run["command_total_bytes"]
        run["observations"] = [
            dict(row)
            for row in store.db.execute(
                "SELECT event_id,state,exit_code,executor_session_id,reason,details,"
                "occurred_at,recorded_at "
                "FROM run_observations WHERE run_id=? ORDER BY event_id LIMIT 100",
                (run["run_id"],),
            )
        ]
        for observation in run["observations"]:
            observation["details"] = json.loads(observation["details"])
        run["observations_partial"] = (
            store.db.execute(
                "SELECT count(*) FROM run_observations WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
            > 100
        )
    edits = [
        dict(row)
        for row in store.db.execute(
            "SELECT * FROM edit_records WHERE request_event_id=? OR result_event_id=? "
            "ORDER BY edit_id LIMIT 20",
            (event_id, event_id),
        )
    ]
    remaining = 64_000
    for edited in edits:
        if len(calls(store, event(store, edited["result_event_id"]))) > 1:
            edited["association_gap"] = "ambiguous_call_id"
        edited["diff"] = diff(store, edited)
        size = len(edited["diff"].get("text", "").encode())
        if size > remaining:
            edited["diff"] = {
                "available": False,
                "reason": "本事件差异正文总量超过展示上限",
                "gap": edited["gap"],
            }
        else:
            remaining -= size
    total = store.db.execute(
        "SELECT count(*) FROM edit_records WHERE request_event_id=? OR result_event_id=?",
        (event_id, event_id),
    ).fetchone()[0]
    run_total = store.db.execute(
        "SELECT count(DISTINCT r.run_id) FROM runs r "
        "LEFT JOIN run_observations o USING(run_id) "
        "WHERE r.request_event_id=? OR o.event_id=? OR EXISTS(SELECT 1 FROM run_manifests m "
        "WHERE m.run_id=r.run_id AND m.project_id=r.project_id AND m.evidence_event_id=?)",
        (event_id, event_id, event_id),
    ).fetchone()[0]
    derivation = store.db.execute(
        "SELECT state,error,updated_at FROM l1_derivations WHERE event_id=?", (event_id,)
    ).fetchone()
    return {
        "runs": runs,
        "runs_partial": run_total > 20,
        "edits": edits,
        "edits_partial": total > 20,
        "derivation": dict(derivation) if derivation else None,
    }
