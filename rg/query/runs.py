"""原生运行事实与显式清单的双时间读取；不把报告合并为执行事实。"""

from __future__ import annotations

import json
from typing import Any

from rg.api.views import NotFound
from rg.derive.runtime import EXEC_TOOLS
from rg.query.artifacts import snapshot, version_record
from rg.query.reader import Reader, instant, page


def unique_call(reader: Reader, run: dict[str, Any]) -> bool:
    for condition in (
        "kind IN ('tool_call','file_edit') AND exclude_reason IS NULL",
        "kind='meta' AND tool_name='exec_command' AND exclude_reason='execution_metadata'",
    ):
        rows = reader.store.db.execute(
            "SELECT event_id,occurred_at,recorded_at FROM raw_events WHERE session_pk=? "
            "AND call_id=? AND alias_of IS NULL AND " + condition,
            (run["session_pk"], run["call_id"]),
        )
        visible = [
            r
            for r in rows
            if reader.visible(r["occurred_at"], r["recorded_at"], ("E", r["event_id"]))
        ]
        if visible:
            return len(visible) == 1
    return False


def native(reader: Reader, identity: str) -> dict[str, Any] | None:
    row = reader.store.db.execute(
        "SELECT * FROM runs WHERE run_id=? AND project_id=?",
        (identity, reader.project),
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    request = reader.store.db.execute(
        "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
        "WHERE event_id=? AND s.project_id=?",
        (run["request_event_id"], reader.project),
    ).fetchone()
    if (
        request is None
        or request["tool_name"] not in EXEC_TOOLS
        or request["alias_of"] is not None
        or not (
            request["kind"] in {"tool_call", "file_edit"}
            and request["exclude_reason"] is None
            or request["kind"] == "meta"
            and request["exclude_reason"] == "execution_metadata"
        )
        or not reader.visible(
            request["occurred_at"], request["recorded_at"], ("E", request["event_id"])
        )
    ):
        return None
    facts = []
    for observed in reader.store.db.execute(
        "SELECT o.*,r.occurred_at AS source_occurred_at,r.recorded_at AS source_recorded_at "
        "FROM run_observations o JOIN raw_events r USING(event_id) "
        "JOIN sessions s USING(session_pk) "
        "WHERE o.run_id=? AND s.project_id=? ORDER BY o.event_id,o.observation_id",
        (identity, reader.project),
    ):
        item = dict(observed)
        if reader.visible(
            item["occurred_at"], item["recorded_at"], ("N", item["observation_id"])
        ) and reader.visible(
            item["source_occurred_at"], item["source_recorded_at"], ("E", item["event_id"])
        ):
            item["details"] = json.loads(item["details"])
            facts.append(item)
    if not facts:
        return None
    exits = {r["exit_code"] for r in facts if r["state"] == "exited"}
    invalid = any(
        r["reason"] in {"invalid_exit_code", "conflicting_exit_code", "invalid_executor_session"}
        for r in facts
    )
    ambiguous = not unique_call(reader, run)
    state = (
        "unknown"
        if ambiguous or invalid or len(exits) > 1
        else (
            "exited"
            if exits
            else "started"
            if any(r["state"] == "started" for r in facts)
            else "unknown"
            if any(r["state"] == "unknown" for r in facts)
            else "requested"
        )
    )
    command = run["command"] or ""
    selected = page(facts, reader.values)
    return run | {
        "state": state,
        "exit_code": next(iter(exits)) if state == "exited" else None,
        "started_at": None,
        "ended_at": next((r["occurred_at"] for r in facts if r["state"] == "exited"), None)
        if state == "exited"
        else None,
        "gap": "ambiguous_call_id"
        if ambiguous
        else "conflicting_execution_facts"
        if invalid or len(exits) > 1
        else next((r["reason"] for r in facts if r["state"] == "unknown"), None)
        if state == "unknown"
        else None
        if run["root_id"]
        else "cwd_root_unknown",
        "snapshot_id": None,
        "command": command.encode()[:8000].decode(errors="ignore"),
        "command_total_bytes": len(command.encode()),
        "command_truncated": len(command.encode()) > 8000,
        "observations": selected["items"],
        "observations_pagination": {k: v for k, v in selected.items() if k != "items"},
        "observations_partial": reader.values.get("offset", 0) > 0
        or selected["next_offset"] is not None,
        "binding_state": "native_request_ambiguous" if ambiguous else "native_request",
        "citation_id": "R:" + identity,
    }


def io(reader: Reader, manifest: dict[str, Any]) -> dict[str, Any]:
    rows = [
        dict(r)
        for r in reader.store.db.execute(
            "SELECT * FROM run_io WHERE manifest_id=? ORDER BY role,ordinal,io_id",
            (manifest["request_id"],),
        )
    ]
    selected = page(rows, {"limit": 100, "offset": reader.values.get("io_offset", 0)})
    result = []
    for value in selected["items"]:
        version = reader.store.db.execute(
            "SELECT * FROM artifact_versions WHERE version_id=? AND project_id=?",
            (value["requested_version_id"], reader.project),
        ).fetchone()
        record = version_record(reader, dict(version)) if version is not None else None
        # 只有明确 ID 匹配可以在后来入库后显示版本；不修改原观察或猜路径。
        result.append(
            value
            | {
                "version": record[0] if record else None,
                "resolution": "visible_version" if record else "version_unknown_or_not_visible",
                "resolved_version_id": record[0]["version_id"] if record else None,
                "association_state": "reported_only",
            }
        )
    return selected | {
        "items": result,
        "partial": selected["offset"] > 0 or selected["next_offset"] is not None,
    }


def manifests(
    reader: Reader,
    identity: str,
    *,
    values: dict[str, Any] | None = None,
    attempt: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    result = []
    run = native(reader, identity)
    for row in reader.store.db.execute(
        "SELECT m.*,r.occurred_at AS source_occurred_at,r.recorded_at AS source_recorded_at "
        "FROM run_manifests m JOIN raw_events r ON r.event_id=m.evidence_event_id "
        "JOIN sessions s USING(session_pk) WHERE m.run_id=? AND m.project_id=? AND s.project_id=? "
        "AND (? IS NULL OR m.attempt_id=?) AND (? IS NULL OR EXISTS(SELECT 1 FROM run_io i "
        "WHERE i.manifest_id=m.request_id AND i.requested_version_id=?)) "
        "AND (? IS NULL OR m.request_id=?)",
        (
            identity,
            reader.project,
            reader.project,
            attempt,
            attempt,
            version,
            version,
            reader.values.get("manifest_id"),
            reader.values.get("manifest_id"),
        ),
    ):
        item = dict(row)
        scope = json.loads(item["scope"]) if item["scope"] else None
        if reader.scope_filter and scope != reader.scope:
            continue
        if not reader.visible(
            item["occurred_at"], item["recorded_at"], ("M", item["request_id"])
        ) or not reader.visible(
            item["source_occurred_at"], item["source_recorded_at"], ("E", item["evidence_event_id"])
        ):
            continue
        item["scope"] = scope
        result.append(item)
    result.sort(key=lambda r: (instant(r["recorded_at"]), r["request_id"]), reverse=True)
    selected = page(result, values if values is not None else reader.values)
    for item in selected["items"]:
        payload = json.loads(item.pop("payload"))
        captured = reader.store.db.execute(
            "SELECT root_id FROM workspace_snapshots WHERE snapshot_id=? AND project_id=?",
            (item["snapshot_id"], reader.project),
        ).fetchone()
        item |= {
            "reported": payload,
            "io": io(reader, item),
            "binding_state": run["binding_state"] if run else "native_run_unknown_or_not_visible",
            "citation_id": "E" + str(item["evidence_event_id"]),
            "declared_snapshot": snapshot(reader, item["snapshot_id"], captured[0])
            if captured
            else None,
            "reported_exit_conflicts_with_native": bool(
                run
                and run["state"] == "exited"
                and payload["exit_code"] is not None
                and payload["exit_code"] != run["exit_code"]
            ),
            "unknown_fields": [k for k, value in payload.items() if value is None],
            "actual_io_completeness": "unknown",
        }
    return selected


def evidence(reader: Reader, identity: str) -> dict[str, Any]:
    run = native(reader, identity)
    reports = manifests(reader, identity)
    if reports["total"] == 0 and (run is None or "manifest_id" in reader.values):
        raise NotFound("当前查询中没有这个运行或清单")
    return reader.metadata() | {
        "citation_id": "R:" + identity,
        "run_id": identity,
        "run": run,
        "manifests": reports,
        "notice": "原生执行状态与候选清单独立。清单仅报告使用或输出版本，"
        "不证明实际读取、完整 I/O、环境复现或运行成功；不执行历史命令。",
    }


def associations(
    reader: Reader, *, attempt: str | None = None, version: str | None = None
) -> dict[str, Any]:
    if attempt is not None:
        rows = reader.store.db.execute(
            "SELECT DISTINCT run_id FROM run_manifests WHERE project_id=? AND attempt_id=?",
            (reader.project, attempt),
        )
    else:
        rows = reader.store.db.execute(
            "SELECT DISTINCT m.run_id FROM run_manifests m JOIN run_io i "
            "ON i.manifest_id=m.request_id WHERE m.project_id=? AND i.requested_version_id=?",
            (reader.project, version),
        )
    result = []
    for row in rows:
        eligible = manifests(reader, row[0], values={"limit": 20}, attempt=attempt, version=version)
        items = eligible["items"]
        if items:
            result.append(
                {
                    "run_id": row[0],
                    "citation_id": "R:" + row[0],
                    "basis": "direct_record",
                    "claim_state": "candidate",
                    "association_state": "reported_only",
                    "manifest_ids": [r["request_id"] for r in items[:20]],
                    "manifest_ids_partial": len(items) > 20 or eligible["next_offset"] is not None,
                }
            )
    result.sort(key=lambda r: r["run_id"])
    return page(result, reader.values)
