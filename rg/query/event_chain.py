"""按来源上下文核对Claude父UUID，正文副本不替代来源父链。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rg.store.database import Store

ANCESTRY_LIMIT = 64
REFERENCES_LIMIT = 20


def _groups(
    db: sqlite3.Connection, identity: str, project: str | None, file_id: int | None = None
) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT min(c.event_id) AS event_id,count(*) AS copies,"
        "count(DISTINCT c.file_instance_id) AS source_contexts FROM claude_chain_records c "
        "JOIN raw_events r ON r.event_id=c.event_id JOIN sessions s USING(session_pk) "
        "JOIN source_files f ON f.file_instance_id=c.file_instance_id "
        "WHERE s.tool='claude' AND f.parser='claude' AND c.native_uuid=? "
        "AND c.uuid_state='valid' AND s.project_id IS ? "
        "AND (? IS NULL OR c.file_instance_id=?) "
        "GROUP BY c.parent_uuid,c.parent_state,c.sidechain,c.sidechain_state,c.body_key "
        "ORDER BY event_id LIMIT 2",
        (identity, project, file_id, file_id),
    ).fetchall()


def _record(db: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    return db.execute("SELECT * FROM claude_chain_records WHERE event_id=?", (event_id,)).fetchone()


def _parent(
    db: sqlite3.Connection, node: sqlite3.Row, project: str | None
) -> tuple[str, sqlite3.Row | None, str, int, int]:
    if node["uuid_state"] != "valid":
        return "uuid_unavailable", None, "none", 0, 0
    if db.execute(
        "SELECT 1 FROM source_files WHERE file_instance_id=? "
        "AND chain_observed_offset<committed_offset",
        (node["file_instance_id"],),
    ).fetchone():
        return "metadata_incomplete", None, "none", 0, 0
    if len(_groups(db, node["native_uuid"], project, node["file_instance_id"])) > 1:
        return "conflicting_record", None, "none", 0, 0
    if node["parent_state"] != "declared":
        return (
            {"null": "null_parent", "missing": "parent_not_declared", "invalid": "invalid_parent"}[
                node["parent_state"]
            ],
            None,
            "none",
            0,
            0,
        )
    local = _groups(db, node["parent_uuid"], project, node["file_instance_id"])
    if (
        not local
        and db.execute(
            "SELECT 1 FROM source_files f JOIN sessions s USING(session_pk) "
            "WHERE f.parser='claude' AND s.tool='claude' AND s.project_id IS ? "
            "AND f.chain_observed_offset<f.committed_offset LIMIT 1",
            (project,),
        ).fetchone()
    ):
        return "metadata_incomplete", None, "project_uuid", 0, 0
    groups = local or _groups(db, node["parent_uuid"], project)
    scope = "source_file" if local else "project_uuid"
    if len(groups) > 1:
        return "ambiguous_parent", None, scope, 0, 0
    if not groups:
        elsewhere = db.execute(
            "SELECT 1 FROM claude_chain_records c JOIN raw_events r ON r.event_id=c.event_id "
            "JOIN sessions s USING(session_pk) JOIN source_files f "
            "ON f.file_instance_id=c.file_instance_id WHERE c.native_uuid=? "
            "AND c.uuid_state='valid' AND s.tool='claude' AND f.parser='claude' "
            "AND s.project_id IS NOT ? LIMIT 1",
            (node["parent_uuid"], project),
        ).fetchone()
        return "outside_project" if elsewhere else "missing_parent", None, scope, 0, 0
    group = groups[0]
    return (
        "linked",
        _record(db, group["event_id"]),
        scope,
        group["copies"],
        group["source_contexts"],
    )


def _references(
    db: sqlite3.Connection, node: sqlite3.Row, parent: sqlite3.Row, project: str | None, scope: str
) -> list[dict[str, int]]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT c.event_id,c.file_instance_id FROM claude_chain_records c "
            "JOIN raw_events r ON r.event_id=c.event_id JOIN sessions s USING(session_pk) "
            "JOIN source_files f ON f.file_instance_id=c.file_instance_id "
            "WHERE c.native_uuid=? AND c.uuid_state='valid' AND s.project_id IS ? "
            "AND s.tool='claude' AND f.parser='claude' AND (? IS NULL OR c.file_instance_id=?) "
            "AND c.parent_uuid IS ? AND c.parent_state=? AND c.sidechain IS ? "
            "AND c.sidechain_state=? AND c.body_key=? ORDER BY c.event_id LIMIT ?",
            (
                node["parent_uuid"],
                project,
                node["file_instance_id"] if scope == "source_file" else None,
                node["file_instance_id"] if scope == "source_file" else None,
                parent["parent_uuid"],
                parent["parent_state"],
                parent["sidechain"],
                parent["sidechain_state"],
                parent["body_key"],
                REFERENCES_LIMIT,
            ),
        )
    ]


def _ancestry(
    db: sqlite3.Connection, first: sqlite3.Row, project: str | None
) -> tuple[str, int, bool]:
    seen: set[tuple[int, str]] = set()
    node = first
    for step in range(ANCESTRY_LIMIT):
        identity = (node["file_instance_id"], node["native_uuid"])
        if node["native_uuid"] is not None and identity in seen:
            return "cycle", step, identity == (first["file_instance_id"], first["native_uuid"])
        if node["native_uuid"] is not None:
            seen.add(identity)
        state, parent, _, _, contexts = _parent(db, node, project)
        if state != "linked" or parent is None:
            return state, step, False
        if contexts > 1:
            return "multiple_source_contexts", step + 1, False
        node = parent
    return "depth_limit", ANCESTRY_LIMIT, False


def query(store: Store, values: dict[str, str]) -> dict[str, Any]:
    if set(values) - {"event", "project"} or "event" not in values:
        raise ValueError("事件父链需要 event，可选 project")
    try:
        event = int(values["event"])
    except ValueError as exc:
        raise ValueError("事件编号必须为正整数") from exc
    if not 0 < event <= 9223372036854775807:
        raise ValueError("事件编号超出范围")
    with store.snapshot():
        source = store.db.execute(
            "SELECT r.session_pk,r.file_instance_id,r.byte_start,s.tool,s.project_id,f.parser,"
            "f.committed_offset,f.chain_observed_offset FROM raw_events r JOIN sessions s "
            "USING(session_pk) JOIN source_files f USING(file_instance_id) WHERE r.event_id=?",
            (event,),
        ).fetchone()
        if source is None or ("project" in values and values["project"] != source["project_id"]):
            raise ValueError("事件不存在或不属于指定项目")
        view: dict[str, Any] = {
            "event_id": event,
            "session_pk": source["session_pk"],
            "file_instance_id": source["file_instance_id"],
            "tool": source["tool"],
            "state": "unsupported",
            "record_event_id": None,
            "native_uuid": None,
            "parent_uuid": None,
            "uuid_state": "missing",
            "parent_state": "missing",
            "is_sidechain": None,
            "sidechain_state": "missing",
            "recorded_at": None,
            "basis": None,
            "resolution_scope": "none",
            "parent_references": [],
            "parent_references_total": 0,
            "parent_references_partial": False,
            "parent_source_contexts": 0,
            "ancestry_state": "unsupported",
            "ancestry_steps": 0,
            "ancestry_limit": ANCESTRY_LIMIT,
            "source_metadata_complete": False,
            "scope_metadata_incomplete": False,
        }
        if source["tool"] != "claude" or source["parser"] != "claude":
            return view
        view["state"] = view["ancestry_state"] = "unobserved"
        view["source_metadata_complete"] = (
            source["chain_observed_offset"] == source["committed_offset"]
        )
        view["scope_metadata_incomplete"] = bool(
            store.db.execute(
                "SELECT 1 FROM source_files f JOIN sessions s USING(session_pk) "
                "WHERE f.parser='claude' AND s.tool='claude' AND s.project_id IS ? "
                "AND f.chain_observed_offset<f.committed_offset "
                "LIMIT 1",
                (source["project_id"],),
            ).fetchone()
        )
        node = store.db.execute(
            "SELECT * FROM claude_chain_records WHERE file_instance_id=? AND byte_start=?",
            (source["file_instance_id"], source["byte_start"]),
        ).fetchone()
        if node is None:
            return view
        view.update(
            {
                k: node[k]
                for k in (
                    "native_uuid",
                    "parent_uuid",
                    "uuid_state",
                    "parent_state",
                    "sidechain_state",
                    "recorded_at",
                )
            }
        )
        view.update(
            record_event_id=node["event_id"],
            is_sidechain=(bool(node["sidechain"]) if node["sidechain"] is not None else None),
            basis="direct_record",
        )
        state, parent, scope, copies, contexts = _parent(store.db, node, source["project_id"])
        ancestry, steps, own_cycle = _ancestry(store.db, node, source["project_id"])
        view.update(
            state="cycle" if own_cycle else state,
            resolution_scope=scope,
            ancestry_state=ancestry,
            ancestry_steps=steps,
            parent_source_contexts=contexts,
        )
        if parent is not None and not own_cycle:
            view["parent_references"] = _references(
                store.db, node, parent, source["project_id"], scope
            )
            view["parent_references_total"] = copies
            view["parent_references_partial"] = copies > REFERENCES_LIMIT
        return view
