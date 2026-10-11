"""清除缺口只留摘要和边界；续接身份另存，不改写 L0。"""

from rg.store.fragment_schema import EVENT_COLUMNS
from rg.store.fragment_schema import MIGRATION as FRAGMENT_MIGRATION

MIGRATION = (
    "CREATE TABLE source_record_skips (skip_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "file_instance_id INTEGER NOT NULL REFERENCES source_files,"
    "byte_start INTEGER NOT NULL CHECK(byte_start>=0),"
    "byte_end INTEGER NOT NULL CHECK(byte_end>byte_start),"
    "record_key TEXT NOT NULL CHECK(length(record_key)=64 "
    "AND record_key NOT GLOB '*[^a-f0-9]*'),recorded_at TEXT NOT NULL,"
    "UNIQUE(file_instance_id,byte_start))",
    # 身份是不可复用的本库整数，清除共享来源的一部分时可保留无正文的旧边界。
    "CREATE TABLE source_continuations (continuation_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "file_instance_id INTEGER NOT NULL REFERENCES source_files,"
    "from_offset INTEGER NOT NULL CHECK(from_offset>=0),"
    "logical_session_pk INTEGER NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE INDEX source_continuation_offset ON "
    "source_continuations(file_instance_id,from_offset,continuation_id)",
    *(
        statement
        for table in ("source_record_skips", "source_continuations")
        for statement in (
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'privacy continuation is append-only'); END",
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'privacy continuation is append-only'); END",
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} BEGIN "
            "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        )
    ),
    "DROP VIEW effective_dedupe_links",
    "DROP VIEW effective_raw_events",
    "CREATE VIEW effective_raw_events AS WITH assigned AS NOT MATERIALIZED ("
    "SELECT m.first_event_id,p.logical_session_pk FROM fragment_record_maps m "
    "JOIN fragment_partitions p USING(partition_id) WHERE m.map_id=("
    "SELECT max(n.map_id) FROM fragment_record_maps n WHERE n.first_event_id=m.first_event_id)) "
    ", owned AS NOT MATERIALIZED (SELECT " + ",".join(f"r.{c}" for c in EVENT_COLUMNS) + ","
    "coalesce(a.logical_session_pk,(SELECT gs.session_pk FROM source_continuations c "
    "LEFT JOIN sessions gs ON gs.session_pk=c.logical_session_pk "
    "WHERE c.file_instance_id=r.file_instance_id AND c.from_offset<=r.byte_start "
    "ORDER BY c.from_offset DESC,c.continuation_id DESC LIMIT 1),r.session_pk) AS session_pk,"
    "r.session_pk AS physical_session_pk,r.alias_of,r.exclude_reason "
    "FROM raw_events r LEFT JOIN raw_events first ON "
    "first.file_instance_id=r.file_instance_id AND first.byte_start=r.byte_start "
    "AND first.record_index=0 LEFT JOIN assigned a ON a.first_event_id=first.event_id) "
    "SELECT " + ",".join(f"o.{c}" for c in EVENT_COLUMNS) + ",o.session_pk,"
    "CASE WHEN c.session_pk=o.session_pk OR (o.session_pk=o.physical_session_pk "
    "AND c.session_pk=c.physical_session_pk) THEN o.alias_of END AS alias_of,"
    "CASE WHEN o.alias_of IS NOT NULL AND coalesce(c.session_pk,-1)!=o.session_pk "
    "AND NOT (o.session_pk=o.physical_session_pk AND c.session_pk=c.physical_session_pk) "
    "AND o.exclude_reason='duplicate' THEN NULL ELSE o.exclude_reason END AS exclude_reason "
    "FROM owned o LEFT JOIN owned c ON c.event_id=o.alias_of",
    next(s for s in FRAGMENT_MIGRATION if s.startswith("CREATE VIEW effective_dedupe_links")),
)
