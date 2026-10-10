"""文件观察的双时间视图；只读登记记录，不打开工作区文件。"""

from __future__ import annotations

import json
from typing import Any

from rg.query.reader import Reader, instant


def snapshot(reader: Reader, identity: int | None, root: str | None) -> dict[str, Any] | None:
    row = reader.store.db.execute(
        "SELECT snapshot_id,root_id,trigger,prompt_id,head_commit,branch,dirty,shadow_commit,"
        "skipped,taken_at,async_race,recorded_at FROM workspace_snapshots "
        "WHERE snapshot_id=? AND project_id=? AND root_id IS ?",
        (identity, reader.project, root),
    ).fetchone()
    if row is None or not reader.visible(row["taken_at"], row["recorded_at"], ("W", identity)):
        return None
    return dict(row)


def observations(reader: Reader, version: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in reader.store.db.execute(
        "SELECT * FROM artifact_observations WHERE version_id=?", (version["version_id"],)
    ):
        value = dict(row)
        # 先核对入库时间；查询不能通过后来追加的观察回填旧视图。
        if not reader.visible(None, value["recorded_at"], ("O", value["observation_id"])):
            continue
        archived = version["source"] == "shadow_snapshot"
        captured = snapshot(reader, value["snapshot_id"], version["root_id"]) if archived else None
        if archived and captured is None:
            continue
        occurred = captured["taken_at"] if captured else value["hash_finished_at"]
        warnings = []
        if not archived and value["snapshot_id"] is not None:
            warnings.append("当前文件观察含快照绑定矛盾；不能证明旧快照字节")
        if archived and value["snapshot_id"] != value["discovery_snapshot_id"]:
            warnings.append("捕获与发现快照不同；不能视为同次快照")
        if not archived:
            start, finish = instant(value["hash_started_at"]), instant(value["hash_finished_at"])
            if start is None or finish is None or start > finish:
                warnings.append("完整读取窗口缺失或矛盾")
                occurred = None
        if captured and (captured["skipped"] or not captured["shadow_commit"]):
            warnings.append("捕获快照缺少有效提交")
        if not reader.visible(occurred, value["recorded_at"], ("O", value["observation_id"])):
            continue
        value["signature"] = json.loads(value["signature"]) if value["signature"] else None
        value["details"] = json.loads(value["details"])
        value["cache_reused"] = bool(value["cache_reused"])
        if captured:
            value["catalog_recorded_at"] = value["recorded_at"]
            times = [instant(value["recorded_at"]), instant(captured["recorded_at"])]
            value["recorded_at"] = max(t for t in times if t is not None).isoformat()
        value |= {
            "citation_id": f"V:{version['version_id']}",
            "occurred_at": occurred,
            "occurred_time_unknown": instant(occurred) is None,
            "snapshot": captured,
            "discovery_snapshot": snapshot(
                reader, value["discovery_snapshot_id"], version["root_id"]
            ),
            "provenance_warnings": warnings,
        }
        result.append(value)
    # 按实际时刻排序；不同 UTC 偏移的 ISO 字符串不能直接排序。
    result.sort(key=lambda r: (instant(r["recorded_at"]), r["observation_id"]), reverse=True)
    return result


def version_record(
    reader: Reader, version: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    identity = version["version_id"]
    if version["source"] not in {"shadow_snapshot", "current_file"}:
        event = reader.store.db.execute(
            "SELECT r.occurred_at,r.recorded_at FROM raw_events r "
            "JOIN sessions s USING(session_pk) "
            "WHERE r.event_id=? AND s.project_id=?",
            (version["evidence_event_id"], reader.project),
        ).fetchone()
        if event is None or not reader.visible(
            event["occurred_at"], event["recorded_at"], ("V", identity)
        ):
            return None
        return version | dict(event) | {"citation_id": f"V:{identity}"}, []
    observed = observations(reader, version)
    if not observed:
        return None
    occurred = [instant(r["occurred_at"]) for r in observed]
    known = [instant(r["recorded_at"]) for r in observed]
    first = min(t for t in occurred if t is not None) if all(occurred) else None
    # observed_at 原列只记首次后台插入，不用于推定任何后来观察的发生时间。
    # 摘要时间全部从当前双截止视图内的观察计算，不能泄露被排除的观察。
    card = version | {
        "citation_id": f"V:{identity}",
        "observed_at": first.isoformat() if first else None,
        "occurred_at": first.isoformat() if first else None,
        "occurred_time_unknown": first is None,
        "recorded_at": min(t for t in known if t is not None).isoformat(),
        "observations_total": len(observed),
        "provenance_warnings": sorted(
            {warning for r in observed for warning in r["provenance_warnings"]}
        ),
        "observation_time_basis": (
            "saved_snapshot" if version["source"] == "shadow_snapshot" else "complete_read_window"
        ),
        "details": "research.evidence",
    }
    return card, observed
