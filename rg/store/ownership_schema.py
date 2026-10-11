"""归属变更另存；科学记录、原生身份与历史审核不改写。"""

import sqlite3

PROJECT_TABLES = (
    "artifact_jobs artifact_versions claim_source_checks decision_requests edit_records "
    "edit_request_checks entities explicit_records extraction_queue link_progress "
    "model_attempts note_requests pipeline_queue rule_reference_checks run_manifests "
    "runs workspace_snapshots"
).split()
SESSION_TABLES = (
    "edit_records",
    "extraction_queue",
    "pipeline_queue",
    "runs",
    "workspace_snapshots",
    "extraction_plans",
)
OWNED_TABLES = tuple(sorted(set(PROJECT_TABLES) | set(SESSION_TABLES) | {"jobs"}))

MIGRATION = (
    "CREATE TABLE science_migrations (migration_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "request_id TEXT NOT NULL UNIQUE,intent_sha256 TEXT NOT NULL,plan_sha256 TEXT NOT NULL,"
    "project_id TEXT NOT NULL,actor TEXT NOT NULL,response_json "
    "TEXT NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE TABLE science_ownership (ownership_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "migration_id INTEGER NOT NULL REFERENCES science_migrations,table_name TEXT NOT NULL,"
    "record_rowid INTEGER NOT NULL,project_id TEXT NOT "
    "NULL,session_pk INTEGER,recorded_at TEXT NOT NULL,"
    "UNIQUE(migration_id,table_name,record_rowid))",
    "CREATE INDEX science_owner_record ON science_ownership(table_name,record_rowid,ownership_id)",
    "CREATE TABLE source_project_history (history_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "first_event_id INTEGER NOT NULL REFERENCES raw_events,request_id TEXT NOT NULL,"
    "previous_project_id TEXT,project_id TEXT,previous_session_pk INTEGER NOT NULL,"
    "session_pk INTEGER NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE INDEX source_project_event ON source_project_history(first_event_id,history_id)",
    "CREATE TABLE project_tombstones (project_id TEXT PRIMARY KEY REFERENCES projects,"
    "recorded_at TEXT NOT NULL)",
    "CREATE VIEW active_projects AS SELECT p.* FROM projects p WHERE NOT EXISTS "
    "(SELECT 1 FROM project_tombstones t WHERE t.project_id=p.project_id)",
    *(
        statement
        for table in (
            "science_migrations",
            "science_ownership",
            "source_project_history",
            "project_tombstones",
        )
        for statement in (
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'ownership history is append-only'); END",
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'ownership history is append-only'); END",
        )
    ),
)


def views(db: sqlite3.Connection) -> None:
    """逐列投影，不给 SELECT * 增添隐式字段，也不拦截数据库连接。"""
    for table in OWNED_TABLES:
        columns = [r[1] for r in db.execute("SELECT * FROM pragma_table_info(?)", (table,))]
        expressions = []
        for column in columns:
            if table == "jobs" and column == "payload":
                expressions.append(
                    "CASE WHEN o.ownership_id IS NULL THEN r.payload "
                    "WHEN o.session_pk IS NULL THEN "
                    "json_set(r.payload,'$.project_id',o.project_id) "
                    "ELSE json_set(r.payload,'$.project_id',o.project_id,"
                    "'$.session_pk',o.session_pk) "
                    "END AS payload"
                )
            elif column == "project_id":
                expressions.append(
                    "CASE WHEN o.ownership_id IS NULL THEN r.project_id ELSE "
                    "o.project_id END AS project_id"
                )
            elif column == "session_pk":
                anchor = {"runs": "request_event_id", "edit_records": "request_event_id"}.get(table)
                expressions.append(
                    f"coalesce((SELECT a.session_pk FROM effective_raw_events "
                    f"a WHERE a.event_id=r.{anchor}),o.session_pk,r.session_pk) AS session_pk"
                    if anchor
                    else "coalesce(o.session_pk,r.session_pk) AS session_pk"
                )
            else:
                expressions.append(f'r."{column}"')
        db.execute(
            f"CREATE VIEW effective_{table} AS SELECT {','.join(expressions)} FROM {table} r "
            "LEFT JOIN science_ownership o ON o.ownership_id=(SELECT max(n.ownership_id) "
            f"FROM science_ownership n WHERE n.table_name='{table}' AND n.record_rowid=r.rowid)"
        )
    root_tables = (
        "artifact_jobs",
        "artifact_versions",
        "edit_records",
        "runs",
        "workspace_snapshots",
    )
    borrowed = " UNION ".join(
        f"SELECT r.root_id,o.project_id FROM {table} r JOIN science_ownership o ON "
        "o.ownership_id=(SELECT max(n.ownership_id) FROM science_ownership n WHERE "
        f"n.table_name='{table}' AND n.record_rowid=r.rowid) WHERE r.root_id IS NOT NULL"
        for table in root_tables
    )
    db.execute(
        "CREATE VIEW effective_source_roots AS WITH membership AS ("
        "SELECT root_id,project_id FROM source_roots UNION " + borrowed + ") "
        "SELECT r.root_id,m.project_id,r.host_id,r.path,r.kind,r.git_common_dir,r.git_metadata "
        "FROM membership m JOIN source_roots r USING(root_id) JOIN active_projects p "
        "ON p.project_id=m.project_id"
    )
    raw = db.execute("SELECT sql FROM sqlite_schema WHERE name='effective_raw_events'").fetchone()[
        0
    ]
    db.execute("DROP VIEW effective_dedupe_links")
    db.execute("DROP VIEW effective_raw_events")
    raw = raw.replace(
        "coalesce(a.logical_session_pk,",
        "coalesce((SELECT h.session_pk FROM source_project_history h WHERE "
        "h.first_event_id=first.event_id ORDER BY h.history_id DESC LIMIT 1),a.logical_session_pk,",
        1,
    )
    db.execute(raw)
    from rg.store.fragment_schema import MIGRATION as FRAGMENTS

    db.execute(next(s for s in FRAGMENTS if s.startswith("CREATE VIEW effective_dedupe_links")))
