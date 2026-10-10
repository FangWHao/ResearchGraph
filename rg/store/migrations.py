from __future__ import annotations

import sqlite3

LATEST_VERSION = 19
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
    8: (
        "ALTER TABLE runs ADD COLUMN request_event_id INTEGER REFERENCES raw_events",
        "ALTER TABLE runs ADD COLUMN root_id TEXT REFERENCES source_roots",
        "ALTER TABLE runs ADD COLUMN gap TEXT",
        "ALTER TABLE runs ADD COLUMN requested_at TEXT",
        "CREATE UNIQUE INDEX runs_request_event ON runs(request_event_id)",
        "CREATE TABLE run_observations ("
        "observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, "
        "event_id INTEGER NOT NULL REFERENCES raw_events, state TEXT NOT NULL "
        "CHECK(state IN ('requested','started','exited','unknown')), exit_code INTEGER, "
        "executor_session_id INTEGER, reason TEXT, details TEXT NOT NULL, "
        "occurred_at TEXT, recorded_at TEXT NOT NULL)",
        "CREATE INDEX run_observations_run ON run_observations(run_id,event_id)",
        "CREATE TRIGGER run_fact_no_update BEFORE UPDATE ON run_observations BEGIN "
        "SELECT RAISE(ABORT,'run observation is append-only'); END",
        "CREATE TRIGGER run_fact_no_delete BEFORE DELETE ON run_observations BEGIN "
        "SELECT RAISE(ABORT,'run observation is append-only'); END",
        "ALTER TABLE artifact_versions ADD COLUMN content_sha256 TEXT",
        "ALTER TABLE artifact_versions ADD COLUMN phase TEXT",
        "ALTER TABLE artifact_versions ADD COLUMN root_id TEXT REFERENCES source_roots",
        "ALTER TABLE artifact_versions ADD COLUMN basis TEXT",
        "ALTER TABLE artifact_versions ADD COLUMN claim_state TEXT "
        "CHECK(claim_state IS NULL OR claim_state IN ('candidate','confirmed','dismissed'))",
        "ALTER TABLE artifact_versions ADD COLUMN representation TEXT",
        "CREATE TABLE edit_records (edit_id TEXT PRIMARY KEY, project_id TEXT NOT NULL "
        "REFERENCES projects, session_pk INTEGER NOT NULL REFERENCES sessions, call_id TEXT, "
        "request_event_id INTEGER NOT NULL REFERENCES raw_events, "
        "result_event_id INTEGER NOT NULL REFERENCES raw_events, path TEXT, "
        "root_id TEXT REFERENCES source_roots, operation TEXT NOT NULL, patch_sha256 TEXT, "
        "before_version TEXT REFERENCES artifact_versions, "
        "after_version TEXT REFERENCES artifact_versions, gap TEXT, user_modified INTEGER, "
        "occurred_at TEXT, recorded_at TEXT NOT NULL)",
        "CREATE INDEX edit_records_events ON edit_records(request_event_id,result_event_id)",
        "CREATE TRIGGER edit_no_update BEFORE UPDATE ON edit_records BEGIN "
        "SELECT RAISE(ABORT,'edit record is append-only'); END",
        "CREATE TRIGGER edit_no_delete BEFORE DELETE ON edit_records BEGIN "
        "SELECT RAISE(ABORT,'edit record is append-only'); END",
        "CREATE TABLE l1_derivations (event_id INTEGER PRIMARY KEY REFERENCES raw_events, "
        "state TEXT NOT NULL CHECK(state IN ('queued','waiting','done','failed')), "
        "error TEXT, updated_at TEXT NOT NULL)",
        "CREATE INDEX l1_derivations_queue ON l1_derivations(state,updated_at,event_id)",
    ),
    9: (
        "CREATE TABLE explicit_records (request_id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL REFERENCES projects, "
        "kind TEXT NOT NULL CHECK(kind='question'), intent_sha256 TEXT NOT NULL, "
        "event_id INTEGER NOT NULL UNIQUE REFERENCES raw_events, "
        "claim_id INTEGER NOT NULL UNIQUE REFERENCES claims, recorded_at TEXT NOT NULL)",
        "CREATE TRIGGER explicit_no_update BEFORE UPDATE ON explicit_records BEGIN "
        "SELECT RAISE(ABORT,'explicit record is append-only'); END",
        "CREATE TRIGGER explicit_no_delete BEFORE DELETE ON explicit_records BEGIN "
        "SELECT RAISE(ABORT,'explicit record is append-only'); END",
    ),
    10: (
        "CREATE TABLE decision_requests (request_id TEXT PRIMARY KEY, "
        "project_id TEXT NOT NULL REFERENCES projects, intent_sha256 TEXT NOT NULL, "
        "event_id INTEGER NOT NULL UNIQUE REFERENCES raw_events, "
        "claim_id INTEGER NOT NULL UNIQUE REFERENCES claims, selector TEXT NOT NULL, "
        "target_id TEXT REFERENCES entities, recorded_at TEXT NOT NULL)",
        "CREATE TABLE decision_resolutions (request_id TEXT PRIMARY KEY, "
        "intent_sha256 TEXT NOT NULL, original_claim_id INTEGER NOT NULL UNIQUE REFERENCES claims, "
        "event_id INTEGER NOT NULL UNIQUE REFERENCES raw_events, "
        "claim_id INTEGER NOT NULL UNIQUE REFERENCES claims, "
        "target_id TEXT NOT NULL REFERENCES entities, "
        "recorded_at TEXT NOT NULL)",
        "CREATE TRIGGER decision_input_no_update BEFORE UPDATE ON decision_requests BEGIN "
        "SELECT RAISE(ABORT,'decision input is append-only'); END",
        "CREATE TRIGGER decision_input_no_delete BEFORE DELETE ON decision_requests BEGIN "
        "SELECT RAISE(ABORT,'decision input is append-only'); END",
        "CREATE TRIGGER decision_resolution_no_update BEFORE UPDATE ON decision_resolutions BEGIN "
        "SELECT RAISE(ABORT,'decision resolution is append-only'); END",
        "CREATE TRIGGER decision_resolution_no_delete BEFORE DELETE ON decision_resolutions BEGIN "
        "SELECT RAISE(ABORT,'decision resolution is append-only'); END",
        "CREATE TRIGGER unresolved_decision_no_confirm BEFORE INSERT ON review_actions "
        "WHEN NEW.action='confirm' AND EXISTS (SELECT 1 FROM claims c "
        "WHERE c.claim_id=NEW.claim_id AND c.claim_type='decision_event' "
        "AND json_extract(c.payload,'$.target') IS NULL) BEGIN "
        "SELECT RAISE(ABORT,'decision target must be resolved'); END",
        "CREATE TRIGGER unresolved_decision_candidate BEFORE INSERT ON claims "
        "WHEN NEW.claim_type='decision_event' AND NEW.claim_state='confirmed' "
        "AND json_extract(NEW.payload,'$.target') IS NULL BEGIN "
        "SELECT RAISE(ABORT,'unresolved decision must be candidate'); END",
    ),
    11: (
        "CREATE TABLE extraction_queue (queue_id INTEGER PRIMARY KEY, task_key "
        "TEXT NOT NULL UNIQUE, "
        "session_pk INTEGER NOT NULL REFERENCES sessions, project_id TEXT NOT "
        "NULL REFERENCES projects, "
        "config_key TEXT NOT NULL, input_key TEXT NOT NULL, max_event_id INTEGER "
        "NOT NULL REFERENCES raw_events, "
        "provider TEXT NOT NULL, model TEXT NOT NULL, scope TEXT, "
        "state TEXT NOT NULL CHECK(state IN "
        "('queued','running','done','partial','paused','blocked','cancelled')), "
        "owner_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, "
        "defer_reason TEXT, next_attempt_at TEXT, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)",
        "CREATE INDEX extraction_queue_ready ON "
        "extraction_queue(config_key,state,next_attempt_at,updated_at)",
        "CREATE TRIGGER extraction_queue_input_no_update BEFORE UPDATE OF task_key,session_pk,"
        "project_id,config_key,input_key,max_event_id,provider,model,scope,created_at "
        "ON extraction_queue BEGIN SELECT RAISE(ABORT,'queue input is immutable'); END",
        "CREATE TABLE extraction_queue_events (event_id INTEGER PRIMARY KEY, "
        "queue_id INTEGER NOT NULL REFERENCES extraction_queue, owner_id TEXT, kind TEXT NOT NULL, "
        "details TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE INDEX extraction_queue_history ON extraction_queue_events(queue_id,event_id)",
        "CREATE TRIGGER extraction_queue_event_no_update BEFORE UPDATE ON "
        "extraction_queue_events BEGIN "
        "SELECT RAISE(ABORT,'queue event is append-only'); END",
        "CREATE TRIGGER extraction_queue_event_no_delete BEFORE DELETE ON "
        "extraction_queue_events BEGIN "
        "SELECT RAISE(ABORT,'queue event is append-only'); END",
    ),
    12: (
        "CREATE TABLE pipeline_queue (queue_id INTEGER PRIMARY KEY, task_key TEXT NOT NULL UNIQUE, "
        "project_id TEXT NOT NULL REFERENCES projects, session_pk INTEGER REFERENCES sessions, "
        "stage TEXT NOT NULL CHECK(stage IN ('link','overview')), target_key TEXT NOT NULL, "
        "config_key TEXT NOT NULL, input_key TEXT NOT NULL, scope TEXT, "
        "state TEXT NOT NULL CHECK(state IN "
        "('queued','running','done','partial','paused','blocked','cancelled')), "
        "owner_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_plan TEXT, result TEXT, "
        "error TEXT, defer_reason TEXT, next_attempt_at TEXT, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)",
        "CREATE INDEX pipeline_queue_ready ON "
        "pipeline_queue(stage,state,next_attempt_at,updated_at)",
        "CREATE TRIGGER pipeline_queue_input_no_update BEFORE UPDATE OF task_key,project_id,"
        "session_pk,stage,target_key,config_key,input_key,scope,created_at ON pipeline_queue "
        "BEGIN SELECT RAISE(ABORT,'pipeline input is immutable'); END",
        "CREATE TABLE pipeline_queue_events (event_id INTEGER PRIMARY KEY, queue_id INTEGER "
        "NOT NULL REFERENCES pipeline_queue, owner_id TEXT, kind TEXT NOT NULL, details TEXT "
        "NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE INDEX pipeline_queue_history ON pipeline_queue_events(queue_id,event_id)",
        "CREATE TRIGGER pipeline_queue_event_no_update BEFORE UPDATE ON pipeline_queue_events "
        "BEGIN SELECT RAISE(ABORT,'pipeline event is append-only'); END",
        "CREATE TRIGGER pipeline_queue_event_no_delete BEFORE DELETE ON pipeline_queue_events "
        "BEGIN SELECT RAISE(ABORT,'pipeline event is append-only'); END",
    ),
    13: (
        "CREATE TABLE artifact_discovery_attempts(attempt_id INTEGER PRIMARY KEY,"
        "snapshot_id INTEGER NOT NULL REFERENCES workspace_snapshots,status TEXT NOT NULL,"
        "recorded_at TEXT NOT NULL)",
        "CREATE INDEX artifact_discovery_attempts_snapshot "
        "ON artifact_discovery_attempts(snapshot_id,attempt_id)",
        "CREATE TRIGGER artifact_discovery_attempt_no_update "
        "BEFORE UPDATE ON artifact_discovery_attempts "
        "BEGIN SELECT RAISE(ABORT,'artifact discovery attempt is append-only'); END",
        "CREATE TRIGGER artifact_discovery_attempt_no_delete "
        "BEFORE DELETE ON artifact_discovery_attempts "
        "BEGIN SELECT RAISE(ABORT,'artifact discovery attempt is append-only'); END",
        "CREATE TABLE artifact_discoveries(snapshot_id INTEGER PRIMARY KEY "
        "REFERENCES workspace_snapshots,status TEXT NOT NULL,details TEXT NOT NULL,"
        "recorded_at TEXT NOT NULL)",
        "CREATE TRIGGER artifact_discovery_no_update BEFORE UPDATE ON artifact_discoveries "
        "BEGIN SELECT RAISE(ABORT,'artifact discovery is append-only'); END",
        "CREATE TRIGGER artifact_discovery_no_delete BEFORE DELETE ON artifact_discoveries "
        "BEGIN SELECT RAISE(ABORT,'artifact discovery is append-only'); END",
        "CREATE TABLE artifact_jobs(job_id INTEGER PRIMARY KEY,job_key TEXT NOT NULL UNIQUE,"
        "project_id TEXT NOT NULL REFERENCES projects,root_id TEXT NOT NULL "
        "REFERENCES source_roots,"
        "snapshot_id INTEGER NOT NULL REFERENCES workspace_snapshots,kind TEXT NOT NULL "
        "CHECK(kind IN ('snapshot_blob','file_hash')),input_json TEXT NOT NULL,"
        "state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed','paused')),"
        "owner_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,result TEXT,"
        "next_attempt_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)",
        "CREATE INDEX artifact_jobs_ready ON artifact_jobs(state,next_attempt_at,updated_at)",
        "CREATE TRIGGER artifact_job_input_no_update BEFORE UPDATE OF job_key,project_id,"
        "root_id,snapshot_id,kind,input_json,created_at ON artifact_jobs "
        "BEGIN SELECT RAISE(ABORT,'artifact job input is immutable'); END",
        "CREATE TABLE artifact_job_events(event_id INTEGER PRIMARY KEY,job_id INTEGER "
        "NOT NULL REFERENCES artifact_jobs,kind TEXT NOT NULL,owner_id TEXT,"
        "details TEXT NOT NULL,recorded_at TEXT NOT NULL)",
        "CREATE TRIGGER artifact_job_event_no_update BEFORE UPDATE ON artifact_job_events "
        "BEGIN SELECT RAISE(ABORT,'artifact job event is append-only'); END",
        "CREATE TRIGGER artifact_job_event_no_delete BEFORE DELETE ON artifact_job_events "
        "BEGIN SELECT RAISE(ABORT,'artifact job event is append-only'); END",
        "CREATE TABLE artifact_observations(observation_id TEXT PRIMARY KEY,version_id TEXT "
        "NOT NULL REFERENCES artifact_versions,job_id INTEGER NOT NULL UNIQUE REFERENCES "
        "artifact_jobs,snapshot_id INTEGER REFERENCES workspace_snapshots,"
        "discovery_snapshot_id INTEGER NOT NULL REFERENCES workspace_snapshots,"
        "mode TEXT NOT NULL,signature TEXT,hash_started_at TEXT,hash_finished_at TEXT,"
        "cache_reused INTEGER NOT NULL CHECK(cache_reused IN (0,1)),"
        "cached_from TEXT REFERENCES artifact_observations,"
        "details TEXT NOT NULL,recorded_at TEXT NOT NULL)",
        "CREATE INDEX artifact_observations_version ON "
        "artifact_observations(version_id,recorded_at)",
        "CREATE TRIGGER artifact_observation_no_update BEFORE UPDATE ON artifact_observations "
        "BEGIN SELECT RAISE(ABORT,'artifact observation is append-only'); END",
        "CREATE TRIGGER artifact_observation_no_delete BEFORE DELETE ON artifact_observations "
        "BEGIN SELECT RAISE(ABORT,'artifact observation is append-only'); END",
        "CREATE TABLE file_hash_cache(cache_key TEXT PRIMARY KEY,observation_id TEXT NOT NULL "
        "REFERENCES artifact_observations)",
        "CREATE TRIGGER file_hash_cache_no_update BEFORE UPDATE ON file_hash_cache "
        "BEGIN SELECT RAISE(ABORT,'full hash cache is append-only'); END",
        "CREATE TRIGGER file_hash_cache_no_delete BEFORE DELETE ON file_hash_cache "
        "BEGIN SELECT RAISE(ABORT,'full hash cache is append-only'); END",
        "CREATE TRIGGER physical_version_no_update BEFORE UPDATE ON artifact_versions "
        "WHEN OLD.source IN ('shadow_snapshot','current_file') "
        "BEGIN SELECT RAISE(ABORT,'physical version is append-only'); END",
        "CREATE TRIGGER physical_version_no_delete BEFORE DELETE ON artifact_versions "
        "WHEN OLD.source IN ('shadow_snapshot','current_file') "
        "BEGIN SELECT RAISE(ABORT,'physical version is append-only'); END",
    ),
    14: (
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1 AND "
        "(EXISTS(SELECT 1 FROM artifact_versions) OR EXISTS(SELECT 1 FROM workspace_snapshots))",
        "CREATE TRIGGER artifact_version_revision AFTER INSERT ON artifact_versions BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        "CREATE TRIGGER artifact_observation_revision AFTER INSERT ON artifact_observations BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        "CREATE TRIGGER workspace_snapshot_revision AFTER INSERT ON workspace_snapshots BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
    ),
    15: (
        "CREATE TABLE run_manifests(request_id TEXT PRIMARY KEY,project_id TEXT NOT NULL "
        "REFERENCES projects,run_id TEXT NOT NULL,attempt_id TEXT REFERENCES "
        "entities,snapshot_id INTEGER REFERENCES workspace_snapshots,evidence_event_id INTEGER "
        "NOT NULL UNIQUE REFERENCES raw_events,intent_sha256 TEXT NOT NULL,scope TEXT,"
        "payload TEXT NOT NULL,occurred_at TEXT,recorded_at TEXT NOT NULL,"
        "claim_state TEXT NOT NULL CHECK(claim_state='candidate'),"
        "basis TEXT NOT NULL CHECK(basis='direct_record'))",
        "CREATE INDEX run_manifests_run ON run_manifests(run_id,recorded_at)",
        "CREATE TRIGGER run_manifest_no_update BEFORE UPDATE ON run_manifests BEGIN "
        "SELECT RAISE(ABORT,'run manifest is append-only'); END",
        "CREATE TRIGGER run_manifest_no_delete BEFORE DELETE ON run_manifests BEGIN "
        "SELECT RAISE(ABORT,'run manifest is append-only'); END",
        "CREATE TRIGGER run_manifest_revision AFTER INSERT ON run_manifests BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        "CREATE TRIGGER native_run_revision AFTER INSERT ON runs BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        "CREATE TRIGGER native_run_observation_revision AFTER INSERT ON run_observations BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
        "ALTER TABLE run_io ADD COLUMN io_id TEXT",
        "ALTER TABLE run_io ADD COLUMN manifest_id TEXT REFERENCES run_manifests(request_id)",
        "ALTER TABLE run_io ADD COLUMN requested_version_id TEXT",
        "ALTER TABLE run_io ADD COLUMN role TEXT",
        "ALTER TABLE run_io ADD COLUMN ordinal INTEGER",
        "ALTER TABLE run_io ADD COLUMN claim_state TEXT CHECK(claim_state IS NULL OR "
        "claim_state='candidate')",
        "ALTER TABLE run_io ADD COLUMN evidence_event_id INTEGER REFERENCES raw_events",
        "ALTER TABLE run_io ADD COLUMN occurred_at TEXT",
        "ALTER TABLE run_io ADD COLUMN recorded_at TEXT",
        "CREATE UNIQUE INDEX run_io_identity ON run_io(io_id) WHERE io_id IS NOT NULL",
        "CREATE UNIQUE INDEX run_io_manifest_position ON run_io(manifest_id,role,ordinal) "
        "WHERE manifest_id IS NOT NULL",
        "CREATE TRIGGER manifest_io_no_update BEFORE UPDATE ON run_io "
        "WHEN OLD.manifest_id IS NOT NULL BEGIN "
        "SELECT RAISE(ABORT,'manifest IO is append-only'); END",
        "CREATE TRIGGER manifest_io_no_delete BEFORE DELETE ON run_io "
        "WHEN OLD.manifest_id IS NOT NULL BEGIN "
        "SELECT RAISE(ABORT,'manifest IO is append-only'); END",
    ),
    16: (
        "CREATE TABLE project_privacy(rule_id INTEGER PRIMARY KEY,project_id TEXT NOT NULL "
        "REFERENCES projects,patterns TEXT NOT NULL,actor TEXT NOT NULL,recorded_at TEXT NOT NULL)",
        "CREATE INDEX project_privacy_latest ON project_privacy(project_id,rule_id)",
        "CREATE TRIGGER project_privacy_no_update BEFORE UPDATE ON project_privacy BEGIN "
        "SELECT RAISE(ABORT,'project privacy is append-only'); END",
        "CREATE TRIGGER project_privacy_no_delete BEFORE DELETE ON project_privacy BEGIN "
        "SELECT RAISE(ABORT,'project privacy is append-only'); END",
        "CREATE TRIGGER project_privacy_revision AFTER INSERT ON project_privacy BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
    ),
    17: (
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1 AND "
        "EXISTS(SELECT 1 FROM edit_records)",
        "CREATE TRIGGER edit_record_revision AFTER INSERT ON edit_records BEGIN "
        "UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
    ),
    18: (
        "ALTER TABLE source_roots ADD COLUMN git_metadata TEXT NOT NULL DEFAULT '{}'",
    ),
    19: (
        "CREATE TABLE parser_records(record_id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL "
        "UNIQUE REFERENCES raw_events,file_instance_id INTEGER NOT NULL REFERENCES source_files,"
        "parser_version TEXT NOT NULL,tool_version TEXT,version_basis TEXT NOT NULL "
        "CHECK(version_basis IN ('direct_record','file_context','unknown','invalid')),"
        "status TEXT NOT NULL CHECK(status IN "
        "('parsed','bad_json','invalid_record','parser_error')),"
        "types TEXT NOT NULL CHECK(json_valid(types)),unknown_events INTEGER NOT NULL "
        "CHECK(unknown_events>=0),unknown_types INTEGER NOT NULL CHECK(unknown_types>=0),"
        "recorded_at TEXT NOT NULL)",
        "CREATE INDEX parser_records_file ON parser_records(file_instance_id,record_id)",
        "CREATE TRIGGER parser_record_no_update BEFORE UPDATE ON parser_records BEGIN "
        "SELECT RAISE(ABORT,'parser record is append-only'); END",
        "CREATE TRIGGER parser_record_no_delete BEFORE DELETE ON parser_records BEGIN "
        "SELECT RAISE(ABORT,'parser record is append-only'); END",
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
