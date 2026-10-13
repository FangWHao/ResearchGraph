"""L1路径复核另存；候选正文首次补登时间不冒充原件入库时间。"""

MIGRATION = (
    "CREATE TABLE l1_path_checks(check_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "edit_id TEXT REFERENCES edit_records,run_id TEXT REFERENCES runs,"
    "input_signature TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN "
    "('matches','revised','unavailable')),payload TEXT NOT NULL,"
    "before_version TEXT REFERENCES artifact_versions,"
    "after_version TEXT REFERENCES artifact_versions,"
    "context_event_id INTEGER REFERENCES raw_events,recorded_at TEXT NOT NULL,"
    "CHECK((edit_id IS NOT NULL AND run_id IS NULL) OR "
    "(edit_id IS NULL AND run_id IS NOT NULL)))",
    "CREATE INDEX l1_path_edit ON l1_path_checks(edit_id,check_id)",
    "CREATE INDEX l1_path_run ON l1_path_checks(run_id,check_id)",
    "CREATE TABLE l1_repair_versions(version_id TEXT PRIMARY KEY REFERENCES artifact_versions,"
    "recorded_at TEXT NOT NULL)",
    *(
        statement
        for table in ("l1_path_checks", "l1_repair_versions")
        for statement in (
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'L1 path proof is append-only'); END",
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'L1 path proof is append-only'); END",
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} BEGIN "
            "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        )
    ),
)
