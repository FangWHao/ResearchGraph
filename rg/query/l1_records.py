"""完整遍历已记录的运行和版本；与历史导出共用双时间和范围合同。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rg.query.artifacts import version_record
from rg.query.reader import Reader
from rg.query.runs import manifests, native


def all_pages(fetch: Callable[[int], dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    offset = 0
    while True:
        value = fetch(offset)
        result.extend(value["items"])
        offset = value["next_offset"]
        if offset is None:
            return result


def run_records(reader: Reader) -> tuple[list[dict[str, Any]], set[int], set[str]]:
    identities = {
        r[0]
        for r in reader.store.db.execute(
            "SELECT run_id FROM runs WHERE project_id=? UNION "
            "SELECT run_id FROM run_manifests WHERE project_id=?",
            (reader.project, reader.project),
        )
    }
    result, events, versions = [], set(), set()
    saved = reader.values
    try:
        for identity in sorted(identities):
            reader.values = saved | {"limit": 100, "offset": 0, "io_offset": 0}
            reports = all_pages(
                lambda offset, identity=identity: manifests(
                    reader, identity, values={"limit": 100, "offset": offset}
                )
            )
            if reader.scope_filter and not reports:
                continue
            execution = native(reader, identity)
            if execution:
                facts = execution["observations"]
                offset = execution["observations_pagination"]["next_offset"]
                while offset is not None:
                    reader.values = saved | {"limit": 100, "offset": offset}
                    continued = native(reader, identity)
                    assert continued is not None
                    facts.extend(continued["observations"])
                    offset = continued["observations_pagination"]["next_offset"]
                execution["observations"] = facts
                execution.pop("observations_pagination")
                execution["observations_partial"] = False
                events.add(execution["request_event_id"])
                events.update(r["event_id"] for r in facts)
            for report in reports:
                offset = report["io"]["next_offset"]
                entries = report["io"]["items"]
                while offset is not None:
                    from rg.query.runs import io

                    reader.values = saved | {"io_offset": offset}
                    continued_io = io(reader, report)
                    entries.extend(continued_io["items"])
                    offset = continued_io["next_offset"]
                report["io"] = {"items": entries, "total": len(entries), "partial": False}
                versions.update(r["requested_version_id"] for r in entries)
                events.add(report["evidence_event_id"])
            if execution or reports:
                result.append(
                    {
                        "run_id": identity,
                        "native": execution,
                        "manifests": reports,
                        "scope_basis": "reported_only" if reader.scope_filter else "unfiltered",
                        "actual_io_completeness": "unknown",
                    }
                )
    finally:
        reader.values = saved
    return result, events, versions


def artifacts(reader: Reader, requested: set[str]) -> tuple[list[dict[str, Any]], set[int]]:
    result, events = [], set()
    for row in reader.store.db.execute(
        "SELECT * FROM artifact_versions WHERE project_id=? ORDER BY version_id", (reader.project,)
    ):
        if reader.scope_filter and row["version_id"] not in requested:
            continue
        value = version_record(reader, dict(row))
        if value is None:
            continue
        card, observations = value
        result.append(
            {
                "version": card,
                "observations": observations,
                "scope_basis": "reported_only" if reader.scope_filter else "unassigned",
            }
        )
        if card.get("evidence_event_id") is not None:
            events.add(card["evidence_event_id"])
    return result, events
