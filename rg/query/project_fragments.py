"""只读检查跨项目片段的物理边界和目录线索；不发布项目归属。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from rg.ingest.project_context import path_value
from rg.store.database import ConflictError, Store, dumps

MAX_ROOTS = 2000
MAX_CANDIDATES = 20
PARAMETERS = {
    "session",
    "source",
    "project",
    "limit",
    "after_event",
    "snapshot",
    "observation_snapshot",
    "expected_scope_key",
}


def _number(values: dict[str, str], name: str, default: str | None = None) -> int:
    value = values.get(name, default)
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("片段编号与分页参数必须为非负整数")
    result = int(value)
    if result > 9223372036854775807:
        raise ValueError("片段编号与分页参数超出范围")
    return result


def query(store: Store, values: dict[str, str]) -> dict[str, Any]:
    if set(values) - PARAMETERS or not {"session", "source"} <= set(values):
        raise ValueError("片段查询需要 session 和 source，不接受未知参数")
    session, source = _number(values, "session"), _number(values, "source")
    limit, after = _number(values, "limit", "20"), _number(values, "after_event", "0")
    if session < 1 or source < 1 or not 1 <= limit <= 50:
        raise ValueError("会话、来源或页长超出范围")
    frozen = {"snapshot", "observation_snapshot", "expected_scope_key"}
    if (set(values) & frozen) not in (set(), frozen) or (after and not frozen <= set(values)):
        raise ValueError("续页必须带齐原文截止、观测截止及归属线索摘要")
    expected = values.get("expected_scope_key")
    if expected is not None and re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("归属线索摘要必须为规范SHA256")
    with store.snapshot():
        file = store.db.execute(
            "SELECT f.parser,s.project_id FROM source_files f JOIN sessions s USING(session_pk) "
            "WHERE f.file_instance_id=? AND s.session_pk=?",
            (source, session),
        ).fetchone()
        if file is None or ("project" in values and values["project"] != file["project_id"]):
            raise ValueError("来源不属于该会话或指定项目")
        if file["parser"] not in {"claude", "codex"}:
            raise ValueError("当前来源没有受支持的原生会话格式")
        if store.db.execute("PRAGMA user_version").fetchone()[0] < 30:
            return {"available": False, "reason": "legacy_schema", "assignments_applied": False}
        return _query(store, values, session, source, limit, after, file["project_id"])


def _hint(context: dict[str, Any], roots: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": "unassigned",
        "basis": "path_hint",
        "projects": [],
        "matching_root_ids": [],
        "candidates_partial": False,
    }
    if context["state"] != "declared":
        result["reason"] = context["state"]
        return result
    if len(roots) > MAX_ROOTS:
        result["reason"] = "root_registry_limit"
        return result
    path = path_value(context["cwd"])
    matches = []
    for root in roots:
        registered = path_value(root["path"])
        if path is not None and registered is not None and type(path) is type(registered):
            if path.is_relative_to(registered):
                matches.append(root)
    projects = sorted({root["project_id"] for root in matches})
    result.update(
        state="candidate_path"
        if len(projects) == 1
        else "ambiguous_path"
        if projects
        else "unassigned",
        reason="registered_path_hint" if projects else "no_registered_path",
        projects=projects[:MAX_CANDIDATES],
        matching_root_ids=[r["root_id"] for r in matches[:MAX_CANDIDATES]],
        candidates_partial=len(projects) > MAX_CANDIDATES or len(matches) > MAX_CANDIDATES,
    )
    return result


def _query(
    store: Store,
    values: dict[str, str],
    session: int,
    source: int,
    limit: int,
    after: int,
    project: str | None,
) -> dict[str, Any]:
    db = store.db
    maximum = db.execute(
        "SELECT coalesce(max(event_id),0) FROM raw_events WHERE file_instance_id=? "
        "AND record_index=0",
        (source,),
    ).fetchone()[0]
    observed = db.execute(
        "SELECT coalesce(max(observation_id),0) FROM project_context_observations",
    ).fetchone()[0]
    snapshot = _number(values, "snapshot", str(maximum))
    observation_snapshot = _number(values, "observation_snapshot", str(observed))
    if snapshot > maximum or observation_snapshot > observed:
        raise ValueError("分页截止超出当前原文或观测范围")
    start = -1
    if after:
        row = db.execute(
            "SELECT byte_start FROM raw_events WHERE event_id=? AND file_instance_id=? "
            "AND record_index=0 AND event_id<=?",
            (after, source, snapshot),
        ).fetchone()
        if row is None:
            raise ValueError("续页锚点必须是当前来源截止内的物理记录首事件")
        start = row[0]
    roots = [
        dict(row)
        for row in db.execute(
            "SELECT root_id,project_id,path,kind FROM source_roots WHERE host_id='local' "
            "ORDER BY root_id LIMIT ?",
            (MAX_ROOTS + 1,),
        )
    ]
    scope_key = hashlib.sha256(
        dumps([session, source, project, snapshot, observation_snapshot, roots]).encode()
    ).hexdigest()
    if values.get("expected_scope_key", scope_key) != scope_key:
        raise ConflictError("会话归属或根目录线索已变化，请重新读取第一页")
    base = (
        "FROM raw_events r LEFT JOIN project_context_observations o ON o.observation_id=("
        "SELECT max(c.observation_id) FROM project_context_observations c "
        "WHERE c.event_id=r.event_id AND c.observation_id<=?) "
        "WHERE r.file_instance_id=? AND r.record_index=0 "
        "AND r.event_id<=? "
    )
    params = observation_snapshot, source, snapshot
    totals = db.execute(
        "SELECT count(*) AS records_total,count(o.observation_id) AS records_observed,"
        "coalesce(sum(o.state='unavailable'),0) AS records_unavailable " + base,
        params,
    ).fetchone()
    rows = db.execute(
        "SELECT r.event_id,r.byte_start,r.byte_end,r.object_sha256,r.seq,o.state,o.source,"
        "o.cwd,o.cwd_sha256,o.reason,o.recorded_at "
        + base
        + "AND r.byte_start>? ORDER BY r.byte_start LIMIT ?",
        (*params, start, limit + 1),
    ).fetchall()
    context: dict[str, Any] = {
        "state": "unobserved",
        "source": "none",
        "cwd": None,
        "cwd_sha256": None,
        "reason": "no_prior_context",
        "origin_event_id": None,
    }
    if rows:
        previous = db.execute(
            "SELECT r.event_id,o.state,o.source,o.cwd,o.cwd_sha256,o.reason "
            + base
            + "AND r.byte_start<? AND (o.observation_id IS NULL OR o.state!='absent') "
            "ORDER BY r.byte_start DESC LIMIT 1",
            (*params, rows[0]["byte_start"]),
        ).fetchone()
        if previous:
            context = _context(dict(previous))
    items = []
    for row in rows[:limit]:
        if row["state"] != "absent":
            context = _context(dict(row))
        siblings = db.execute(
            "SELECT min(seq) AS first_seq,max(seq) AS last_seq,count(*) AS events,"
            "coalesce(sum(kind='user_msg' AND exclude_reason IS NULL),0) AS user_events "
            "FROM raw_events WHERE file_instance_id=? AND byte_start=?",
            (source, row["byte_start"]),
        ).fetchone()
        items.append(
            {
                "first_event_id": row["event_id"],
                "byte_start": row["byte_start"],
                "byte_end": row["byte_end"],
                "object_sha256": row["object_sha256"],
                "first_seq": siblings["first_seq"],
                "last_seq": siblings["last_seq"],
                "event_count": siblings["events"],
                "has_user_turn": bool(siblings["user_events"]),
                "observation_state": row["state"] or "unobserved",
                "observed_at": row["recorded_at"],
                "context": dict(context),
                "project_hint": _hint(context, roots),
            }
        )
    return {
        "available": True,
        "session_pk": session,
        "file_instance_id": source,
        "current_session_project": project,
        "assignments_applied": False,
        "unit": "physical_record",
        "snapshot": snapshot,
        "observation_snapshot": observation_snapshot,
        "scope_key": scope_key,
        "records_total": totals["records_total"],
        "records_observed": totals["records_observed"],
        "records_unavailable": totals["records_unavailable"],
        "observations_complete": totals["records_total"] == totals["records_observed"],
        "after_event": after,
        "next_after_event": items[-1]["first_event_id"] if len(rows) > limit else None,
        "records": items,
    }


def _context(row: dict[str, Any]) -> dict[str, Any]:
    state = row["state"] or "unobserved"
    return {
        "state": state,
        "source": row["source"] or "none",
        "cwd": row["cwd"],
        "cwd_sha256": row["cwd_sha256"],
        "reason": row["reason"] or "metadata_not_observed",
        "origin_event_id": row["event_id"],
    }
