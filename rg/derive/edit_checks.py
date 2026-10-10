"""后台追加多步编辑诊断；研究图仅读取与原始引用匹配的计算缓存。"""

from __future__ import annotations

import json
from typing import Any

from rg.derive.multiedit import project_edit
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

ALGORITHM = "multiedit-sequential-v1"
FIELDS = ("after_version", "reported_after_version", "gap", "request_validation")


def signature(store: Store, row: dict[str, Any]) -> str:
    # 只取原行及已存元数据，排除视图临时字段；不会打开原文或候选正文。
    original = store.db.execute(
        "SELECT * FROM edit_records WHERE edit_id=?", (row["edit_id"],)
    ).fetchone()
    request = store.db.execute(
        "SELECT r.*,s.project_id,s.tool FROM raw_events r JOIN sessions s USING(session_pk) "
        "WHERE r.event_id=?",
        (row["request_event_id"],),
    ).fetchone()
    versions = [
        store.db.execute(
            "SELECT * FROM artifact_versions WHERE version_id=?", (row[key],)
        ).fetchone()
        for key in ("before_version", "after_version")
    ]
    return digest(
        dumps(
            [ALGORITHM, *[dict(r) if r else None for r in (original, request, *versions)]]
        ).encode()
    )


def recorded_edit(store: Store, record: dict[str, Any]) -> dict[str, Any]:
    if record["operation"] != "multiedit":
        return record
    row = dict(record)
    checked = store.db.execute(
        "SELECT payload FROM edit_request_checks WHERE edit_id=? AND input_signature=? "
        "ORDER BY check_id DESC LIMIT 1",
        (row["edit_id"], signature(store, row)),
    ).fetchone()
    if checked:
        payload = json.loads(checked[0])
        return row | {key: payload[key] for key in FIELDS}
    return row | {
        "after_version": None,
        "reported_after_version": row["after_version"],
        "gap": row["gap"] or "multiedit_validation_pending",
        "request_validation": {
            "status": "unavailable",
            "basis": "saved_request_and_reported_versions",
        },
    }


def validate_edits(store: Store, limit: int = 100, *, retry_unavailable: bool = False) -> int:
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 25:
        return 0
    count = 0
    attempted: set[str] = set()
    # 新记录与旧版本缺核验的记录优先；显式重试的旧不可用项不能长期占满批次。
    for retrying in (False, True) if retry_unavailable else (False,):
        for saved in store.db.execute(
            "SELECT * FROM edit_records WHERE operation='multiedit' ORDER BY edit_id"
        ):
            row = dict(saved)
            if row["edit_id"] in attempted:
                continue
            with store.transaction() as db:
                fingerprint = signature(store, row)
                previous = db.execute(
                    "SELECT payload FROM edit_request_checks WHERE edit_id=? AND input_signature=? "
                    "ORDER BY check_id DESC LIMIT 1",
                    (row["edit_id"], fingerprint),
                ).fetchone()
                if (
                    bool(previous) != retrying
                    or previous
                    and (json.loads(previous[0])["request_validation"]["status"] != "unavailable")
                ):
                    continue
                result = project_edit(store, row)
                db.execute(
                    "INSERT OR IGNORE INTO edit_request_checks "
                    "(edit_id,project_id,input_signature,payload,recorded_at) VALUES (?,?,?,?,?)",
                    (
                        row["edit_id"],
                        row["project_id"],
                        fingerprint,
                        dumps({key: result[key] for key in FIELDS}),
                        now(),
                    ),
                )
                count += 1
                attempted.add(row["edit_id"])
            if count >= limit:
                return count
    return count
