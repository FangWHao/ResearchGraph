from __future__ import annotations

import sqlite3

LATEST_VERSION = 7
MIGRATIONS = {
    2: (
        "CREATE TABLE candidate_locations ("
        "extraction_run_id INTEGER NOT NULL REFERENCES extraction_runs, "
        "event_id INTEGER NOT NULL REFERENCES raw_events, "
        "byte_start INTEGER NOT NULL CHECK(byte_start >= 0), "
        "byte_end INTEGER NOT NULL CHECK(byte_end > byte_start), "
        "cue TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('rule', 'model')), "
        "PRIMARY KEY(extraction_run_id, event_id, byte_start, byte_end, cue, source))",
        "CREATE INDEX candidate_locations_event ON candidate_locations(event_id)",
    ),
    3: (
        "CREATE TABLE link_progress ("
        "job_key TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects, "
        "pair_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, "
        "source_cards TEXT NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ('pending','done','failed')), "
        "attempts INTEGER NOT NULL DEFAULT 0, "
        "extraction_run_id INTEGER REFERENCES extraction_runs, "
        "review_job_id INTEGER REFERENCES jobs, error TEXT, updated_at TEXT NOT NULL)",
        "CREATE INDEX link_progress_project ON link_progress(project_id, state)",
    ),
    4: (
        "CREATE TABLE model_attempts ("
        "attempt_id INTEGER PRIMARY KEY, "
        "extraction_run_id INTEGER NOT NULL REFERENCES extraction_runs, "
        "project_id TEXT NOT NULL REFERENCES projects, stage TEXT NOT NULL, "
        "provider TEXT NOT NULL, model TEXT NOT NULL, segment_id TEXT NOT NULL, "
        "input_budget INTEGER NOT NULL, output_budget INTEGER NOT NULL, "
        "measured_input_tokens INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
        "sent INTEGER NOT NULL DEFAULT 0 CHECK(sent IN (0,1)), "
        "status TEXT NOT NULL, failure_kind TEXT, stop_reason TEXT, usage_day TEXT, "
        "created_at TEXT NOT NULL, finished_at TEXT)",
        "CREATE INDEX model_attempts_run ON model_attempts(extraction_run_id, attempt_id)",
        "CREATE INDEX model_attempts_day ON model_attempts(created_at, project_id)",
    ),
    5: (
        "CREATE TABLE extraction_plans ("
        "config_key TEXT NOT NULL, segment_id TEXT NOT NULL, "
        "session_pk INTEGER NOT NULL REFERENCES sessions, event_ids TEXT NOT NULL, "
        "segment_json TEXT, state TEXT NOT NULL "
        "CHECK(state IN ('pending','split','manual','done')), outcome TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY(config_key,segment_id))",
        "CREATE INDEX extraction_plans_session ON extraction_plans(session_pk,config_key,state)",
    ),
    6: (
        "CREATE TABLE ingest_sources ("
        "path TEXT PRIMARY KEY, tool TEXT NOT NULL CHECK(tool IN ('claude','codex')), "
        "project_id TEXT REFERENCES projects, "
        "kind TEXT NOT NULL CHECK(kind IN ('file','directory')), "
        "registered_at TEXT NOT NULL)",
        "CREATE TABLE spool_receipts ("
        "receipt_id TEXT PRIMARY KEY, filename TEXT NOT NULL, object_sha256 TEXT NOT NULL, "
        "tool TEXT, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs, recorded_at TEXT NOT NULL)",
        "CREATE INDEX jobs_spool_queue ON jobs(kind,state,updated_at)",
        "CREATE TRIGGER spool_no_update BEFORE UPDATE ON spool_receipts BEGIN "
        "SELECT RAISE(ABORT,'spool receipt is append-only'); END",
        "CREATE TRIGGER spool_no_delete BEFORE DELETE ON spool_receipts BEGIN "
        "SELECT RAISE(ABORT,'spool receipt is append-only'); END",
    ),
    7: (
        "ALTER TABLE workspace_snapshots ADD COLUMN snapshot_key TEXT",
        "ALTER TABLE workspace_snapshots ADD COLUMN record_sha256 TEXT",
        "ALTER TABLE workspace_snapshots ADD COLUMN async_race INTEGER",
        "ALTER TABLE workspace_snapshots ADD COLUMN metadata TEXT",
        "ALTER TABLE workspace_snapshots ADD COLUMN recorded_at TEXT",
        "CREATE UNIQUE INDEX workspace_snapshots_key ON workspace_snapshots(snapshot_key)",
        "CREATE TRIGGER snapshot_no_update BEFORE UPDATE ON workspace_snapshots BEGIN "
        "SELECT RAISE(ABORT,'snapshot is append-only'); END",
        "CREATE TRIGGER snapshot_no_delete BEFORE DELETE ON workspace_snapshots BEGIN "
        "SELECT RAISE(ABORT,'snapshot is append-only'); END",
    ),
}


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > LATEST_VERSION:
        raise ValueError("数据库版本高于当前程序，不能降级打开")
    while version < LATEST_VERSION:
        db.execute("BEGIN IMMEDIATE")
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= LATEST_VERSION:
                db.commit()
                return
            for statement in MIGRATIONS[version + 1]:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {version + 1}")
            db.commit()
            version += 1
        except BaseException:
            db.rollback()
            raise
