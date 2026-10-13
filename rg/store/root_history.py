"""按双截止读取已登记根身份，不打开工作区或借用当前 Git 配置。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from rg.store.ownership import record_owner

ROOT_RECORDS = {
    "artifact_jobs": "job_id",
    "artifact_versions": "version_id",
    "edit_records": "edit_id",
    "runs": "run_id",
    "workspace_snapshots": "snapshot_id",
}


def _instant(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(value) if value else None
        return result if result and result.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def observation(
    db: sqlite3.Connection,
    root: str | None,
    *,
    occurred_until: str | None = None,
    known_until: str | None = None,
) -> dict[str, Any]:
    unknown = {"root_id": root, "state": "unknown", "reason": "registration_not_known"}
    if root is None:
        return unknown | {"reason": "root_not_recorded"}
    happened, known = _instant(occurred_until), _instant(known_until)
    if (occurred_until is not None and happened is None) or (
        known_until is not None and known is None
    ):
        raise ValueError("根目录身份截止时间需为带时区的 ISO 时间")
    if db.execute("PRAGMA user_version").fetchone()[0] < 37:
        return unknown | {"reason": "registration_time_not_recorded"}
    for row in db.execute(
        "SELECT * FROM source_root_observations WHERE root_id=? ORDER BY observation_id DESC",
        (root,),
    ):
        saved, occurred = _instant(row["recorded_at"]), _instant(row["occurred_at"])
        if saved is None or (known is not None and saved > known):
            continue
        if happened is not None and occurred is not None and occurred > happened:
            continue
        value = json.loads(row["payload"])
        if not isinstance(value, dict):
            return unknown | {"reason": "invalid_registration"}
        try:
            metadata = json.loads(value.get("git_metadata") or "null")
        except (ValueError, TypeError):
            metadata = None
        value["git_metadata"] = (
            metadata if isinstance(metadata, dict) else {"status": "not_recorded"}
        )
        return {
            "root_id": root,
            "state": "recorded",
            "observation_id": row["observation_id"],
            "basis": row["basis"],
            "occurred_at": row["occurred_at"],
            "occurred_time_unknown": occurred is None,
            "recorded_at": row["recorded_at"],
            "declaration": value,
        }
    return unknown


def authorized(
    db: sqlite3.Connection, project: str, proof: dict[str, Any], known_until: str | None
) -> bool:
    declaration = proof.get("declaration")
    if declaration is None:
        return False
    if declaration["project_id"] == project:
        return True
    # 归属账本由本机UTC写入；带偏移的调用方截止先规范成同一时区。
    cutoff = _instant(known_until)
    at = cutoff.astimezone(UTC).isoformat() if cutoff is not None else None
    for table, key in ROOT_RECORDS.items():
        for row in db.execute(f"SELECT {key} FROM {table} WHERE root_id=?", (proof["root_id"],)):
            if record_owner(db, table, key, row[0], at) == project:
                return True
    return False
