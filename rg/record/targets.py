from __future__ import annotations

import json
from typing import Any

from rg.record.schema import validate_scope
from rg.store.database import Store, dumps


def active_objects(
    store: Store, project: str, scope: dict[str, str] | None
) -> list[dict[str, Any]]:
    """遍历全项目，审核状态和完整范围确定版本；不依赖界面图的返回上限。"""
    rows = store.db.execute(
        "SELECT c.claim_id,c.payload,c.scope,c.entity_id,e.kind FROM claims c "
        "JOIN entities e USING(entity_id) WHERE e.project_id=? AND c.claim_type='entity_version' "
        "AND NOT EXISTS (SELECT 1 FROM claims n WHERE n.replaces_claim=c.claim_id) "
        "ORDER BY c.claim_id DESC",
        (project,),
    ).fetchall()
    versions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        state = store.claim_state(row["claim_id"])
        if state == "dismissed":
            continue
        row_scope = json.loads(row["scope"]) if row["scope"] else None
        if scope is not None and scope != row_scope:
            continue
        key = (row["entity_id"], dumps(row_scope))
        if key in versions and (versions[key]["state"] == "confirmed" or state != "confirmed"):
            continue
        versions[key] = {
            "entity_id": row["entity_id"],
            "kind": row["kind"],
            "claim_id": row["claim_id"],
            "label": json.loads(row["payload"])["label"],
            "scope": row_scope,
            "state": state,
        }
    return list(versions.values())


def unique_target(
    store: Store, project: str, selector: str, scope: dict[str, str] | None
) -> str | None:
    objects = active_objects(store, project, scope)
    by_id = {row["entity_id"] for row in objects if row["entity_id"] == selector}
    matches = by_id or {row["entity_id"] for row in objects if row["label"] == selector}
    return next(iter(matches)) if len(matches) == 1 else None


def targets(store: Store, values: dict[str, str]) -> dict[str, Any]:
    from rg.api.views import page, project_exists

    project = values.get("project", "")
    if not project:
        raise ValueError("对象检索需明确项目")
    project_exists(store, project)
    query = values.get("q", "")
    if len(query.encode()) > 4000:
        raise ValueError("对象搜索最多 4000 UTF8 字节")
    scope = validate_scope(json.loads(values["scope"])) if "scope" in values else None
    limit, offset = page(values)
    by_entity: dict[str, dict[str, Any]] = {}
    for row in sorted(active_objects(store, project, scope), key=lambda item: -item["claim_id"]):
        if query not in row["label"] and query not in row["entity_id"]:
            continue
        if row["entity_id"] not in by_entity:
            by_entity[row["entity_id"]] = row
    entries = sorted(by_entity.values(), key=lambda row: (row["label"], row["entity_id"]))
    result = []
    for row in entries[offset : offset + limit]:
        raw = row["label"].encode()
        result.append(
            {key: value for key, value in row.items() if key not in {"label", "state"}}
            | {
                "label": raw[:8000].decode(errors="ignore"),
                "label_truncated": len(raw) > 8000,
                "label_total_bytes": len(raw),
            }
        )
    return {
        "revision": store.revision(),
        "total": len(entries),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(entries) else None,
        "targets": result,
    }
