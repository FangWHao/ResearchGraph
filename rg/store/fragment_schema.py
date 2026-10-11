"""物理记录不变，正式归属通过逻辑会话投影。"""

SESSION_COLUMNS = (
    "session_pk,tool,native_session_id,agent_id,parent_session_pk,cwd,git_branch,"
    "tool_version,first_at,last_at,project_id,project_basis"
)
EVENT_COLUMNS = (
    "event_id,file_instance_id,record_index,byte_start,byte_end,object_sha256,native_id,"
    "seq,kind,role,tool_name,call_id,model,occurred_at,recorded_at,line_sha256"
).split(",")

MIGRATION = (
    "DROP VIEW IF EXISTS effective_dedupe_links",
    "DROP VIEW IF EXISTS effective_raw_events",
    "CREATE TABLE sessions_new (session_pk INTEGER PRIMARY KEY AUTOINCREMENT,"
    "tool TEXT NOT NULL CHECK(tool IN ('claude','codex','rg')),native_session_id TEXT NOT NULL,"
    "agent_id TEXT NOT NULL DEFAULT '',parent_session_pk INTEGER REFERENCES sessions,"
    "cwd TEXT,git_branch TEXT,tool_version TEXT,first_at TEXT,last_at TEXT,"
    "project_id TEXT REFERENCES projects,project_basis TEXT NOT NULL DEFAULT 'unassigned',"
    "physical_session_pk INTEGER REFERENCES sessions,fragment_id TEXT NOT NULL DEFAULT '',"
    "UNIQUE(tool,native_session_id,agent_id,fragment_id))",
    f"INSERT INTO sessions_new({SESSION_COLUMNS}) SELECT {SESSION_COLUMNS} FROM sessions",
    "DROP TABLE sessions",
    "ALTER TABLE sessions_new RENAME TO sessions",
    "CREATE TABLE fragment_assignments (assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "request_id TEXT NOT NULL UNIQUE,intent_sha256 TEXT NOT NULL,"
    "physical_session_pk INTEGER NOT NULL REFERENCES sessions,"
    "file_instance_id INTEGER NOT NULL REFERENCES source_files,"
    "first_event_id INTEGER NOT NULL,last_event_id INTEGER NOT NULL,"
    "project_id TEXT REFERENCES projects,actor TEXT NOT NULL,"
    "response_json TEXT NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE TABLE fragment_partitions (partition_id TEXT PRIMARY KEY,"
    "physical_session_pk INTEGER NOT NULL REFERENCES sessions,"
    "logical_session_pk INTEGER NOT NULL UNIQUE REFERENCES sessions,"
    "file_instance_id INTEGER NOT NULL REFERENCES source_files,"
    "project_id TEXT REFERENCES projects,basis TEXT NOT NULL "
    "CHECK(basis IN ('manual','inherited_manual','unassigned')),"
    "first_event_id INTEGER NOT NULL,last_event_id INTEGER NOT NULL,"
    "byte_start INTEGER NOT NULL,byte_end INTEGER NOT NULL,"
    "request_id TEXT NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE TABLE fragment_record_maps (map_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "first_event_id INTEGER NOT NULL REFERENCES raw_events,"
    "partition_id TEXT NOT NULL REFERENCES fragment_partitions,"
    "request_id TEXT NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE INDEX fragment_map_event ON fragment_record_maps(first_event_id,map_id)",
    "CREATE INDEX fragment_partition_origin ON fragment_partitions(physical_session_pk)",
    "CREATE VIEW effective_raw_events AS WITH assigned AS NOT MATERIALIZED ("
    "SELECT m.first_event_id,p.logical_session_pk FROM fragment_record_maps m "
    "JOIN fragment_partitions p USING(partition_id) WHERE m.map_id=("
    "SELECT max(n.map_id) FROM fragment_record_maps n WHERE n.first_event_id=m.first_event_id)) "
    ", owned AS NOT MATERIALIZED (SELECT " + ",".join(f"r.{c}" for c in EVENT_COLUMNS) + ","
    "coalesce(a.logical_session_pk,r.session_pk) AS session_pk,"
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
    "CREATE VIEW effective_dedupe_links AS SELECT d.* FROM dedupe_links d "
    "JOIN effective_raw_events a ON a.event_id=d.alias_id "
    "JOIN effective_raw_events c ON c.event_id=d.canonical_id "
    "JOIN sessions sa ON sa.session_pk=a.session_pk JOIN sessions sc ON sc.session_pk=c.session_pk "
    "WHERE a.session_pk=c.session_pk OR "
    "(sa.physical_session_pk IS NULL AND sc.physical_session_pk IS NULL)",
    *(
        statement
        for table in ("fragment_assignments", "fragment_partitions", "fragment_record_maps")
        for statement in (
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'fragment assignments are append-only'); END",
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'fragment assignments are append-only'); END",
        )
    ),
)
