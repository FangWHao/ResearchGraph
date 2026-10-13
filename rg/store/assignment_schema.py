"""未来记录的登记目录策略与归属回执；不改变原件或科研审核。"""

MIGRATION = (
    "CREATE TABLE project_assignment_actions (action_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "project_id TEXT NOT NULL REFERENCES projects,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),"
    "actor TEXT NOT NULL,after_event_id INTEGER NOT NULL,recorded_at TEXT NOT NULL)",
    "CREATE INDEX assignment_policy_project ON project_assignment_actions(project_id,action_id)",
    "CREATE TABLE automatic_assignments (first_event_id INTEGER PRIMARY KEY REFERENCES raw_events,"
    "policy_action_id INTEGER,policy_sha256 TEXT NOT NULL,context_event_id INTEGER,"
    "reason TEXT NOT NULL,recorded_at TEXT NOT NULL)",
    *(
        statement
        for table in ("project_assignment_actions", "automatic_assignments")
        for statement in (
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'assignment history is append-only'); END",
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'assignment history is append-only'); END",
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} BEGIN "
            "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        )
    ),
)
