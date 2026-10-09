from __future__ import annotations

import sqlite3

LATEST_VERSION = 4
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
