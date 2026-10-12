"""在新记录提交时应用已授权策略，不回填历史、不确认科研内容。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from rg.ingest.common import Parsed
from rg.ingest.project_context import declaration, path_value
from rg.store.assignment_policy import MAX_ROOTS, policies, registry, signature
from rg.store.database import now


@dataclass(frozen=True)
class Route:
    session: int
    previous_session: int
    project: str | None
    policy: int | None
    scope: str
    context_event: int | None
    reason: str


def route(
    db: sqlite3.Connection,
    physical: int,
    logical: int,
    file_id: int,
    offset: int,
    tool: str,
    record: dict[str, Any],
    status: str,
    events: list[Parsed],
) -> Route | None:
    if db.execute("PRAGMA user_version").fetchone()[0] < 35:
        return None
    session = db.execute("SELECT * FROM sessions WHERE session_pk=?", (physical,)).fetchone()
    if session["project_id"] is not None:
        return None
    enabled = {p["project_id"]: p for p in policies(db) if p["enabled"]}
    if not enabled:
        return None
    db.execute(
        "UPDATE sessions SET project_basis='fragment_container' WHERE session_pk=?",
        (physical,),
    )
    context = declaration(tool, record, status)
    if status == "parsed" and any(event.kind == "unknown" for event in events):
        context.update(state="unavailable", reason="unknown_record")
    origin = None
    boundary = db.execute(
        "SELECT coalesce(max(byte_end),0) FROM source_record_skips "
        "WHERE file_instance_id=? AND byte_end<=?",
        (file_id, offset),
    ).fetchone()[0]
    if context["state"] == "absent":
        previous = db.execute(
            "SELECT o.*,e.event_id AS anchor,p.unknown_events FROM raw_events e "
            "LEFT JOIN project_context_observations o ON o.observation_id=("
            "SELECT max(n.observation_id) FROM project_context_observations n "
            "WHERE n.event_id=e.event_id) LEFT JOIN parser_records p ON p.event_id=e.event_id "
            "WHERE e.file_instance_id=? AND e.record_index=0 AND e.byte_start>=? "
            "AND e.byte_start<? AND (o.observation_id IS NULL OR o.state!='absent' "
            "OR p.unknown_events>0) ORDER BY e.byte_start DESC,"
            "o.observation_id DESC LIMIT 1",
            (file_id, boundary, offset),
        ).fetchone()
        if previous:
            context, origin = dict(previous), previous["anchor"]
            if previous["observation_id"] is None:
                context.update(state="unavailable", reason="metadata_not_observed")
            elif previous["unknown_events"]:
                context.update(state="unavailable", reason="unknown_record")
        elif boundary:
            context["reason"] = "privacy_gap"
    project, action = None, None
    reason = context["reason"]
    if context["state"] == "declared":
        rows, total = registry(db)
        path = path_value(context["cwd"])
        matches = [
            r
            for r in rows
            if r["host_id"] == "local"
            and (root := path_value(r["path"])) is not None
            and path is not None
            and type(path) is type(root)
            and path.is_relative_to(root)
        ]
        owners = {r["project_id"] for r in matches}
        if total > MAX_ROOTS:
            reason = "root_registry_limit"
        elif len(owners) > 1:
            reason = "ambiguous_path"
        elif len(owners) == 1 and any(r["registered"] for r in matches):
            candidate = next(iter(owners))
            if candidate in enabled:
                project = candidate
                action = enabled[candidate]["action_id"]
                reason = "registered_path_rule"
            else:
                reason = "policy_disabled"
        else:
            reason = "no_registered_path"
    # 工具结果仍属于可唯一定位的请求；换目录不改变请求的归属。
    # 未知或非法记录不借用工具键跨越缺失的上下文。
    if context["state"] in {"declared", "absent"} and reason != "privacy_gap":
        calls = {event.call_id for event in events if event.kind == "tool_result" and event.call_id}
        if calls:
            owners = set()
            for call in calls:
                found = db.execute(
                    "SELECT DISTINCT e.session_pk,s.project_id FROM effective_raw_events e "
                    "JOIN raw_events r USING(event_id) "
                    "JOIN sessions s ON s.session_pk=e.session_pk "
                    "WHERE r.session_pk=? AND r.file_instance_id=? AND r.byte_start>=? "
                    "AND e.call_id=? AND e.kind IN ('tool_call','file_edit') "
                    "AND e.exclude_reason IS NULL LIMIT 3",
                    (physical, file_id, boundary, call),
                ).fetchall()
                owners.update((r["session_pk"], r["project_id"]) for r in found)
                if len(found) != 1:
                    owners.add((None, None))
            if len(owners) == 1:
                target, owner = next(iter(owners))
                if target is not None and owner in enabled:
                    return Route(
                        target,
                        logical,
                        owner,
                        enabled[owner]["action_id"],
                        signature(db),
                        origin,
                        "call_owner",
                    )
                project, action, reason = None, None, "ambiguous_call_owner"
            else:
                project, action, reason = None, None, "ambiguous_call_owner"
    if project is None:
        return Route(logical, logical, None, None, signature(db), origin, reason)
    fragment = "auto:" + project
    target = db.execute(
        "SELECT session_pk FROM sessions WHERE physical_session_pk=? AND fragment_id=?",
        (physical, fragment),
    ).fetchone()
    if target is None:
        target = db.execute(
            "INSERT INTO sessions(tool,native_session_id,agent_id,project_id,project_basis,"
            "physical_session_pk,fragment_id,git_branch,tool_version) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session["tool"],
                session["native_session_id"],
                session["agent_id"],
                project,
                "path_rule",
                physical,
                fragment,
                session["git_branch"],
                session["tool_version"],
            ),
        ).lastrowid
    else:
        target = target[0]
    if target is None:
        raise RuntimeError("未获得自动归属会话编号")
    return Route(target, logical, project, action, signature(db), origin, reason)


def append(db: sqlite3.Connection, first: int, routed: Route) -> None:
    if routed.session != routed.previous_session:
        db.execute(
            "INSERT INTO source_project_history(first_event_id,request_id,previous_project_id,"
            "project_id,previous_session_pk,session_pk,recorded_at) VALUES (?,?,?,?,?,?,?)",
            (
                first,
                "auto:" + str(first),
                None,
                routed.project,
                routed.previous_session,
                routed.session,
                now(),
            ),
        )
    db.execute(
        "INSERT INTO automatic_assignments(first_event_id,policy_action_id,policy_sha256,"
        "context_event_id,reason,recorded_at) VALUES (?,?,?,?,?,?)",
        (first, routed.policy, routed.scope, routed.context_event, routed.reason, now()),
    )
