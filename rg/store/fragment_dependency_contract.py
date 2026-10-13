"""片段依赖预检的科研边界；管理归属与正文索引不作为科研连接。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rg.store.clear_selection import identifier as ident
from rg.store.database import dumps
from rg.store.objects import digest

SCIENCE = frozenset(
    """
raw_events entities claims claim_evidence evidence_spans review_actions
extraction_runs extraction_plans model_attempts candidate_locations link_progress jobs
extraction_queue extraction_queue_events pipeline_queue pipeline_queue_events
artifact_versions runs run_io run_observations edit_records workspace_snapshots run_manifests
artifact_observations artifact_jobs artifact_job_events artifact_discoveries
artifact_discovery_attempts claim_source_checks rule_reference_checks edit_request_checks
l1_path_checks l1_repair_versions
explicit_records note_requests decision_requests decision_resolutions
""".split()
)
SCIENCE_SCHEMA_SHA256 = "e0e92f238e862bf41ca7167f90d1ef74ba293f51c44fbc646d76e193335d8dae"


def schema_sha256(db: sqlite3.Connection) -> str:
    """固定科研列与外键合同；新布局必须先明确新增引用的语义。"""
    layout = {
        table: {
            "columns": [
                list(r) for r in db.execute("SELECT * FROM pragma_table_info(?)", (table,))
            ],
            "foreign": [
                list(r) for r in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,))
            ],
        }
        for table in sorted(SCIENCE)
    }
    return digest(dumps(layout).encode())


# 没有声明外键的原生记录，仍是完整依赖；不存在的请求版本只保留诊断。
PLAIN_LINKS = (
    ("runs", "snapshot_id", "workspace_snapshots", "snapshot_id"),
    ("run_io", "run_id", "runs", "run_id"),
    ("run_io", "version_id", "artifact_versions", "version_id"),
    ("run_io", "requested_version_id", "artifact_versions", "version_id"),
    ("run_manifests", "run_id", "runs", "run_id"),
)


@dataclass(frozen=True)
class JsonLink:
    table: str
    column: str
    path: str
    parent: str
    key: str
    value_type: str
    array: bool = True
    member: str | None = None
    optional_member: bool = False


def json_sql(link: JsonLink, max_bytes: int) -> tuple[str, str, str]:
    """固定合同中的 JSON 引用表达式；所有外部值另用参数绑定。"""
    column = "c." + ident(link.column)
    safe = f"CASE WHEN length(CAST({column} AS BLOB))<={max_bytes} "
    safe += f"AND json_valid({column}) THEN {column} ELSE 'null' END"
    container = f"json_extract({safe},'{link.path}')"
    if link.array:
        each = f"json_each(CASE WHEN json_type({safe},'{link.path}')='array' "
        each += f"THEN {container} ELSE '[]' END) j"
    else:
        each = f"json_each(json_array({container})) j"
    if link.member:
        obj = "CASE WHEN j.type='object' THEN j.value ELSE '{}' END"
        value = f"json_extract({obj},'$.{link.member}')"
        kind = f"json_type({obj},'$.{link.member}')"
    elif not link.array:
        value, kind = "j.value", f"json_type({safe},'{link.path}')"
    else:
        value, kind = "j.value", "j.type"
    return each, value, kind


def reference_issues(db: sqlite3.Connection, link: JsonLink, max_bytes: int) -> set[str]:
    issues: set[str] = set()
    column = ident(link.column)
    if db.execute(
        f"SELECT 1 FROM {ident(link.table)} WHERE {column} IS NOT NULL "
        f"AND (length(CAST({column} AS BLOB))>? OR NOT json_valid({column})) LIMIT 1",
        (max_bytes,),
    ).fetchone():
        issues.add("unverifiable_json_reference")
    safe = f"CASE WHEN length(CAST({column} AS BLOB))<={max_bytes} "
    safe += f"AND json_valid({column}) THEN {column} ELSE 'null' END"
    root_type = "array" if link.path == "$" else "object"
    if db.execute(
        f"SELECT 1 FROM {ident(link.table)} WHERE {column} IS NOT NULL "
        f"AND json_type({safe})!=? LIMIT 1",
        (root_type,),
    ).fetchone():
        issues.add("invalid_reference_container")
    if (
        link.array
        and db.execute(
            f"SELECT 1 FROM {ident(link.table)} WHERE "
            f"json_type({safe},'{link.path}') IS NOT NULL AND "
            f"json_type({safe},'{link.path}') NOT IN ('array','null') LIMIT 1"
        ).fetchone()
    ):
        issues.add("invalid_reference_container")
    each, value, kind = json_sql(link, max_bytes)
    condition = "" if link.array else f"{kind} IS NOT NULL AND {kind}!='null' AND "
    condition += f"({kind} IS NULL OR {kind}!=? OR {value} IS NULL OR typeof({value})!=? OR "
    condition += f"{value}<=0)" if link.value_type == "integer" else f"{value}='')"
    if link.optional_member:
        condition = f"j.type!='object' OR ({kind} IS NOT NULL AND ({condition}))"
    if db.execute(
        f"SELECT 1 FROM {ident(link.table)} c,{each} WHERE {condition} LIMIT 1",
        (link.value_type, link.value_type),
    ).fetchone():
        issues.add("invalid_reference_type")
    return issues


JSON_LINKS = (
    JsonLink("extraction_runs", "input_event_ids", "$", "raw_events", "event_id", "integer"),
    JsonLink("extraction_runs", "working_set_ids", "$", "entities", "entity_id", "text"),
    JsonLink("extraction_plans", "event_ids", "$", "raw_events", "event_id", "integer"),
    *(
        JsonLink(
            "extraction_plans",
            "segment_json",
            path,
            "raw_events",
            "event_id",
            "integer",
            member="event_id",
        )
        for path in ("$.events", "$.context_events")
    ),
    *(
        JsonLink("jobs", "payload", path, parent, key, kind)
        for path, parent, key, kind in (
            ("$.event_ids", "raw_events", "event_id", "integer"),
            ("$.context_event_ids", "raw_events", "event_id", "integer"),
            ("$.entity_ids", "entities", "entity_id", "text"),
        )
    ),
    JsonLink(
        "jobs", "payload", "$.segment_id", "extraction_plans", "segment_id", "text", array=False
    ),
    *(
        JsonLink("claims", "payload", path, "entities", "entity_id", "text", array=False)
        for path in ("$.source", "$.target", "$.selected", "$.about")
    ),
    JsonLink("claims", "payload", "$.inputs", "entities", "entity_id", "text", member="ref"),
    JsonLink("link_progress", "source_cards", "$", "entities", "entity_id", "text", member="id"),
    JsonLink(
        "link_progress", "source_cards", "$", "claims", "claim_id", "integer", member="claim_id"
    ),
    JsonLink(
        "claim_source_checks",
        "payload",
        "$.references",
        "raw_events",
        "event_id",
        "integer",
        member="event_id",
    ),
    JsonLink(
        "claim_source_checks",
        "payload",
        "$.references",
        "raw_events",
        "event_id",
        "integer",
        member="associated_call.event_id",
        optional_member=True,
    ),
)
