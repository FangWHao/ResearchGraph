"""只读钩子错误观测；只计算已保存报告，不推断未记录的失败。"""

from __future__ import annotations

from typing import Any

from rg.store.database import Store


def query(store: Store, values: dict[str, str]) -> dict[str, Any]:
    from rg.api.views import project_exists

    if set(values) - {"project", "limit", "offset", "snapshot"}:
        raise ValueError("钩子错误查询包含未知参数")
    project_exists(store, values.get("project") or None)
    limit, offset = int(values.get("limit", "20")), int(values.get("offset", "0"))
    if not 1 <= limit <= 200 or not 0 <= offset <= 2147483647:
        raise ValueError("钩子错误页长或偏移超出范围")
    snapshot = int(values["snapshot"]) if "snapshot" in values else None
    if offset and snapshot is None:
        raise ValueError("续页需要第一页面的观测截止")
    with store.snapshot():
        if store.db.execute("PRAGMA user_version").fetchone()[0] < 20:
            return {"available": False, "reason": "legacy_schema", "scope": "data_directory"}
        maximum = store.db.execute(
            "SELECT coalesce(max(check_id),0) FROM hook_error_checks"
        ).fetchone()[0]
        snapshot = maximum if snapshot is None else snapshot
        if not 0 <= snapshot <= maximum:
            raise ValueError("钩子错误观测截止不存在")
        check = store.db.execute(
            "SELECT * FROM hook_error_checks WHERE check_id=?", (snapshot,),
        ).fetchone()
        highwater = check["report_highwater"] if check is not None else 0
        counts = dict(store.db.execute(
            "SELECT count(*) AS records_total,"
            "count(CASE WHEN status='reported' THEN 1 END) AS reported_failures,"
            "count(CASE WHEN status='unknown_record' THEN 1 END) AS unknown_records,"
            "count(CASE WHEN exception_class_sha256 IS NOT NULL THEN 1 END) "
            "AS unknown_exception_classes FROM hook_error_reports WHERE report_id<=?",
            (highwater,),
        ).fetchone())
        instances = store.db.execute(
            "SELECT count(DISTINCT source_id) FROM hook_error_checks WHERE check_id<=?",
            (snapshot,),
        ).fetchone()[0]
        rows = [dict(row) for row in store.db.execute(
            "SELECT * FROM hook_error_reports WHERE report_id<=? ORDER BY report_id DESC "
            "LIMIT ? OFFSET ?", (highwater, limit, offset),
        )]
        available = instances > 0
        return {
            "available": available, "reason": None if available else "not_observed",
            "scope": "data_directory", "snapshot_id": snapshot,
            "source_instances": instances,
            "observation": dict(check) if check is not None else None,
            "counts": counts if available else None, "reports": rows,
            "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < counts["records_total"] else None,
            "complete_failure_history": False,
        }


def health(store: Store) -> dict[str, Any]:
    result = query(store, {"limit": "1"})
    available = result["available"]
    return {
        "hook_failures": result["counts"]["reported_failures"] if available else None,
        "hook_failures_reason": (
            "全库已采集的钩子失败报告数；不含未记录或尚未采集的失败，完整性未知"
            if available else "尚未观测到可读取的钩子错误日志；失败次数未知"
        ),
        "hook_errors": {key: value for key, value in result.items() if key != "reports"},
    }
