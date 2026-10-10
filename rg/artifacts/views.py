from __future__ import annotations

import json
from typing import Any

from rg.api.views import page, project_exists
from rg.store.database import Store


def health(store: Store, project: str | None) -> dict[str, Any]:
    jobs = {state: 0 for state in ("queued", "running", "paused", "failed", "done")}
    jobs.update(
        {
            row[0]: row[1]
            for row in store.db.execute(
                "SELECT state,count(*) FROM artifact_jobs WHERE (? IS NULL OR "
                "project_id=?) GROUP BY state",
                (project, project),
            )
        }
    )
    discoveries = {state: 0 for state in ("pending", "unknown", "partial", "done")}
    discoveries.update(
        {
            row[0]: row[1]
            for row in store.db.execute(
                "SELECT coalesce(d.status,'pending'),count(*) FROM workspace_snapshots s "
                "LEFT JOIN artifact_discoveries d USING(snapshot_id) "
                "WHERE (? IS NULL OR s.project_id=?) GROUP BY coalesce(d.status,'pending')",
                (project, project),
            )
        }
    )
    versions = {
        row[0]: row[1]
        for row in store.db.execute(
            "SELECT source,count(*) FROM artifact_versions WHERE (? IS NULL OR "
            "project_id=?) GROUP BY source",
            (project, project),
        )
    }
    reused = store.db.execute(
        "SELECT count(*) FROM artifact_observations o JOIN artifact_versions v USING(version_id) "
        "WHERE o.cache_reused=1 AND (? IS NULL OR v.project_id=?)",
        (project, project),
    ).fetchone()[0]
    return {
        "versions": sum(versions.values()),
        "archived": versions.get("shadow_snapshot", 0),
        "current_hashed": versions.get("current_file", 0),
        "cache_reused": reused,
        "jobs": jobs,
        "discovery": discoveries,
    }


def versions(store: Store, values: dict[str, str]) -> dict[str, Any]:
    if set(values) - {"project", "limit", "offset", "path"}:
        raise ValueError("未知文件版本查询参数")
    project = values.get("project") or None
    project_exists(store, project)
    limit, offset = page(values)
    if offset > 2147483647:
        raise ValueError("版本偏移超出范围")
    path = values.get("path")
    if path is not None and (not path or len(path.encode()) > 4096):
        raise ValueError("版本路径应为非空且不超过 4096 字节")
    where = "WHERE (? IS NULL OR project_id=?) AND (? IS NULL OR path=?)"
    parameters = (project, project, path, path)
    total = store.db.execute(
        "SELECT count(*) FROM artifact_versions " + where, parameters
    ).fetchone()[0]
    rows = store.db.execute(
        "SELECT * FROM artifact_versions "
        + where
        + " ORDER BY observed_at DESC,version_id LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    result = []
    for row in rows:
        observations = []
        for observed in store.db.execute(
            "SELECT * FROM artifact_observations WHERE version_id=? ORDER BY "
            "recorded_at DESC,observation_id DESC LIMIT 3",
            (row["version_id"],),
        ):
            value = dict(observed)
            value["signature"] = (
                json.loads(observed["signature"]) if observed["signature"] else None
            )
            value["details"] = json.loads(observed["details"])
            value["cache_reused"] = bool(observed["cache_reused"])
            observations.append(value)
        count = store.db.execute(
            "SELECT count(*) FROM artifact_observations WHERE version_id=?", (row["version_id"],)
        ).fetchone()[0]
        result.append(
            dict(row)
            | {
                "observations": observations,
                "observations_total": count,
                "observations_partial": count > len(observations),
            }
        )
    return {
        "revision": store.revision(),
        "versions": result,
        "total": total,
        "limit": limit,
        "offset": offset,
        "partial": offset > 0 or offset + len(rows) < total,
    }
