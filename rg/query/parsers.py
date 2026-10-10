"""只读分页查看解析器遇到的协议类型；不回读会话或发模型请求。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from rg.store.database import ConflictError, Store, dumps

BASE = (
    "WITH records AS (SELECT r.event_id,r.file_instance_id,s.session_pk,s.project_id,"
    "f.parser AS tool,p.record_id,p.parser_version,p.tool_version,p.version_basis,p.status,"
    "p.types,p.unknown_types,p.recorded_at FROM raw_events r "
    "JOIN source_files f USING(file_instance_id) JOIN sessions s ON s.session_pk=r.session_pk "
    "LEFT JOIN parser_records p ON p.event_id=r.event_id "
    "WHERE r.record_index=0 AND f.parser IN ('claude','codex') "
    "AND (? IS NULL OR s.project_id=?) AND (p.record_id IS NULL OR p.record_id<=?)) "
)
GROUPS = (
    "SELECT tool,parser_version,tool_version,version_basis,"
    "json_extract(j.value,'$.category') AS category,"
    "json_extract(j.value,'$.type_name') AS type_name,"
    "json_extract(j.value,'$.recognized') AS recognized,"
    "sum(json_extract(j.value,'$.count')) AS occurrences,count(*) AS records,"
    "count(DISTINCT file_instance_id) AS source_files,"
    "min(json_extract(j.value,'$.event_id')) AS first_event_id,"
    "max(json_extract(j.value,'$.last_event_id')) AS last_event_id,"
    "max(recorded_at) AS last_recorded_at FROM records,json_each(records.types) j "
    "WHERE record_id IS NOT NULL "
    "GROUP BY tool,parser_version,tool_version,version_basis,category,type_name,recognized"
)
ALERTS = (
    ("unknown_types", "records_with_unknown_types", "存在尚未识别的类型，原文已保留，请查看来源"),
    ("bad_json", "bad_json_records", "存在无法解码的日志行，原始字节已保留"),
    ("invalid_record", "invalid_record_records", "存在非对象记录，原始字节已保留"),
    ("parser_error", "parser_error_records", "存在解析失败记录，原文已保留，不能当作已解析"),
    ("unknown_version", "records_unknown_version", "部分记录没有可验证的工具版本"),
    ("invalid_version", "records_invalid_version", "部分声明的工具版本格式无效，未借用前一版本"),
    ("unobserved", "records_unobserved", "旧记录没有解析观测账本，未补造类型或版本"),
)


def query(store: Store, values: dict[str, str]) -> dict[str, Any]:
    from rg.api.views import project_exists

    if set(values) - {"project", "limit", "offset", "snapshot", "expected_scope_key"}:
        raise ValueError("解析器查询包含未知参数")
    project = values.get("project") or None
    project_exists(store, project)
    limit, offset = int(values.get("limit", "20")), int(values.get("offset", "0"))
    if not 1 <= limit <= 200 or not 0 <= offset <= 2147483647:
        raise ValueError("解析器页长或偏移超出范围")
    snapshot = int(values["snapshot"]) if "snapshot" in values else None
    expected = values.get("expected_scope_key")
    if (snapshot is None) != (expected is None):
        raise ValueError("续页需要同时提供观测截止和归属摘要")
    if expected is not None and re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("归属摘要必须为规范SHA256")
    if offset and snapshot is None:
        raise ValueError("续页需要第一页面的观测截止和归属摘要")
    with store.snapshot():
        if store.db.execute("PRAGMA user_version").fetchone()[0] < 19:
            return {"available": False, "reason": "legacy_schema"}
        maximum = store.db.execute(
            "SELECT coalesce(max(record_id),0) FROM parser_records"
        ).fetchone()[0]
        if snapshot is not None and not 0 <= snapshot <= maximum:
            raise ValueError("观测截止超出范围")
        return _query(
            store,
            project,
            limit,
            offset,
            maximum if snapshot is None else snapshot,
            expected,
        )


def _query(
    store: Store, project: str | None, limit: int, offset: int, snapshot: int, expected: str | None
) -> dict[str, Any]:
    params = project, project, snapshot
    # 只对冻结的物理记录归属做摘要；后续追加不挤动分页，迟到项目登记则拒绝拼页。
    identity = hashlib.sha256(dumps([project, snapshot]).encode())
    for row in store.db.execute(
        BASE + "SELECT file_instance_id,session_pk,project_id,count(*),min(event_id),max(event_id) "
        "FROM records GROUP BY file_instance_id,session_pk,project_id ORDER BY file_instance_id",
        params,
    ):
        identity.update(dumps(list(row)).encode())
        identity.update(b"\n")
    scope = identity.hexdigest()
    if expected is not None and expected != scope:
        raise ConflictError("解析器记录的项目归属已变化，请重新读取第一页")
    result = dict(
        store.db.execute(
            BASE + "SELECT count(*) AS records_total,count(record_id) AS records_observed,"
            "count(*)-count(record_id) AS records_unobserved,"
            "coalesce(sum(unknown_types>0),0) AS records_with_unknown_types,"
            "coalesce(sum(status='bad_json'),0) AS bad_json_records,"
            "coalesce(sum(status='invalid_record'),0) AS invalid_record_records,"
            "coalesce(sum(status='parser_error'),0) AS parser_error_records,"
            "coalesce(sum(record_id IS NOT NULL AND tool_version IS NULL),0) "
            "AS records_unknown_version,"
            "coalesce(sum(version_basis='invalid'),0) AS records_invalid_version FROM records",
            params,
        ).fetchone()
    )
    result["known_version_count"] = store.db.execute(
        BASE + "SELECT count(*) FROM (SELECT DISTINCT tool,tool_version FROM records "
        "WHERE tool_version IS NOT NULL)",
        params,
    ).fetchone()[0]
    total = store.db.execute(BASE + "SELECT count(*) FROM (" + GROUPS + ")", params).fetchone()[0]
    rows = store.db.execute(
        BASE + GROUPS + " ORDER BY tool,parser_version,coalesce(tool_version,''),version_basis,"
        "category,type_name,recognized LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    groups = []
    for row in rows:
        item = dict(row)
        item["recognized"] = bool(item["recognized"])
        groups.append(item)
    result.update(
        available=True,
        type_groups_total=total,
        snapshot_id=snapshot,
        scope_key=scope,
        offset=offset,
        next_offset=offset + limit if offset + limit < total else None,
        types=groups,
        alerts=[
            {"code": code, "count": result[key], "message": message}
            for code, key, message in ALERTS
            if result[key]
        ],
    )
    return result
