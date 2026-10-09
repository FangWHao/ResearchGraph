PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE projects (
  project_id            TEXT PRIMARY KEY,
  name                  TEXT NOT NULL,
  remote_model_allowed  INTEGER NOT NULL DEFAULT 0,   -- 默认不发外部模型
  created_at            TEXT NOT NULL
);

CREATE TABLE source_roots (
  root_id         TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects,
  host_id         TEXT NOT NULL,
  path            TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('repo','worktree','data','alias')),
  git_common_dir  TEXT,
  UNIQUE (host_id, path)
);

CREATE TABLE sessions (
  session_pk         INTEGER PRIMARY KEY,
  tool               TEXT NOT NULL CHECK (tool IN ('claude','codex','rg')),
  native_session_id  TEXT NOT NULL,
  agent_id           TEXT NOT NULL DEFAULT '',          -- 子 Agent；主会话为空串
  parent_session_pk  INTEGER REFERENCES sessions,
  cwd TEXT, git_branch TEXT, tool_version TEXT,
  first_at TEXT, last_at TEXT,
  project_id         TEXT REFERENCES projects,
  project_basis      TEXT NOT NULL DEFAULT 'unassigned', -- path_rule / manual / unassigned
  UNIQUE (tool, native_session_id, agent_id)
);

CREATE TABLE source_files (
  file_instance_id  INTEGER PRIMARY KEY,
  session_pk        INTEGER REFERENCES sessions,
  path              TEXT NOT NULL,
  prefix_sha256     TEXT NOT NULL,             -- 前 4 KB 的摘要，用于识别轮换和改写
  committed_offset  INTEGER NOT NULL DEFAULT 0,
  tail_fragment     BLOB,                      -- 末行未写完的残片
  parser            TEXT NOT NULL,
  parser_version    TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'active', -- active / rotated / truncated / deleted_at_source
  first_seen TEXT NOT NULL, last_read TEXT
);

CREATE TABLE raw_events (
  event_id          INTEGER PRIMARY KEY,
  session_pk        INTEGER NOT NULL REFERENCES sessions,
  file_instance_id  INTEGER NOT NULL REFERENCES source_files,
  record_index      INTEGER NOT NULL DEFAULT 0,
  byte_start        INTEGER NOT NULL,
  byte_end          INTEGER NOT NULL,
  object_sha256     TEXT NOT NULL,             -- 原文所在对象（文件分块）
  native_id         TEXT,                      -- Claude uuid；Codex call_id 或序号
  seq               INTEGER NOT NULL,          -- 会话内顺序
  kind              TEXT NOT NULL,             -- 见 §6.4
  role TEXT, tool_name TEXT, call_id TEXT, model TEXT,
  occurred_at TEXT, recorded_at TEXT NOT NULL,
  line_sha256       TEXT NOT NULL,
  alias_of          INTEGER REFERENCES raw_events,   -- 镜像或重复副本
  exclude_reason    TEXT,                      -- injected_by_rg / compaction_replay / mirror / encrypted / NULL
  UNIQUE (file_instance_id, byte_start, record_index)
);
CREATE INDEX raw_events_seq    ON raw_events (session_pk, seq);
CREATE INDEX raw_events_native ON raw_events (native_id);
CREATE INDEX raw_events_call   ON raw_events (call_id);

CREATE TABLE slim_events (
  event_id       INTEGER PRIMARY KEY REFERENCES raw_events,
  text           TEXT NOT NULL,
  tokens         INTEGER NOT NULL,             -- 用提供方计数接口实测
  slim_version   TEXT NOT NULL,
  dropped_bytes  INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE slim_fts USING fts5 (
  text, content = 'slim_events', content_rowid = 'event_id', tokenize = 'trigram'
);

CREATE TABLE evidence_spans (
  span_id            INTEGER PRIMARY KEY,
  event_id           INTEGER NOT NULL REFERENCES raw_events,
  byte_start         INTEGER NOT NULL,         -- 相对该事件原文，单位 utf8 字节
  byte_end           INTEGER NOT NULL,
  quote_sha256       TEXT NOT NULL,
  redaction_version  TEXT
);

CREATE TABLE entities (
  entity_id   TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES projects,
  kind        TEXT NOT NULL CHECK (kind IN ('question','approach','attempt','finding','decision','join')),
  subtype     TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE extraction_runs (
  extraction_run_id  INTEGER PRIMARY KEY,
  job_key            TEXT NOT NULL UNIQUE,     -- hash(输入事件, 工作集, 模型, 提示版本, schema 版本)
  stage              TEXT NOT NULL,            -- pass1 / pass2 / link / overview / qa
  provider TEXT, model TEXT, prompt_version TEXT, schema_version INTEGER,
  input_event_ids    TEXT NOT NULL,            -- JSON 数组
  working_set_ids    TEXT,
  input_tokens INTEGER, output_tokens INTEGER, budget_tokens INTEGER,
  stop_reason        TEXT,
  status             TEXT NOT NULL,            -- ok / truncated / invalid / over_budget / contaminated / failed
  error TEXT, created_at TEXT NOT NULL
);

CREATE TABLE claims (
  claim_id           INTEGER PRIMARY KEY,
  claim_type         TEXT NOT NULL CHECK (claim_type IN
                       ('entity_version','relation','decision_event','evidence_event','join_ports','merge')),
  entity_id          TEXT REFERENCES entities,
  payload            TEXT NOT NULL,            -- JSON，按 claim_type 校验，见附录 A
  scope              TEXT,                     -- JSON
  basis              TEXT NOT NULL CHECK (basis IN ('direct_record','manual','model_inference','time_match')),
  actor              TEXT NOT NULL,            -- human:<name> / model:<extraction_run_id> / rule:<name> / rg_command
  claim_state        TEXT NOT NULL DEFAULT 'candidate' CHECK (claim_state IN ('candidate','confirmed','dismissed')),
  replaces_claim     INTEGER REFERENCES claims,
  occurred_at TEXT, recorded_at TEXT NOT NULL,
  extraction_run_id  INTEGER REFERENCES extraction_runs
);
CREATE TABLE claim_evidence (
  claim_id  INTEGER NOT NULL REFERENCES claims,
  span_id   INTEGER NOT NULL REFERENCES evidence_spans,
  role      TEXT NOT NULL DEFAULT 'support',   -- support / against / context
  PRIMARY KEY (claim_id, span_id)
);

CREATE TABLE review_actions (                   -- 人工操作，只追加
  action_id          INTEGER PRIMARY KEY,
  claim_id           INTEGER NOT NULL REFERENCES claims,
  action             TEXT NOT NULL CHECK (action IN ('confirm','dismiss','edit','rescope','merge')),
  new_claim_id       INTEGER REFERENCES claims,
  reason TEXT, actor TEXT NOT NULL,
  expected_revision  INTEGER NOT NULL,          -- 不一致时返回 409
  recorded_at        TEXT NOT NULL
);

CREATE TABLE coverage (                         -- 覆盖账本：每个事件在每个阶段的去向
  event_id    INTEGER NOT NULL REFERENCES raw_events,
  stage       TEXT NOT NULL,
  segment_id  TEXT,
  status      TEXT NOT NULL,                    -- covered / excluded:<reason> / pending
  PRIMARY KEY (event_id, stage)
);

CREATE TABLE artifact_versions (
  version_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, path TEXT NOT NULL,
  algo TEXT NOT NULL, digest TEXT NOT NULL,      -- 保留原工具算法标签，如 dvc-md5、git-sha1、sha256
  size INTEGER, source TEXT NOT NULL,            -- git_commit / shadow_snapshot / agent_edit / watcher / dvc / mlflow / current_file
  evidence_event_id INTEGER REFERENCES raw_events, observed_at TEXT
);
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_pk INTEGER, call_id TEXT,
  command TEXT, cwd TEXT, snapshot_id INTEGER, exit_code INTEGER,
  state TEXT NOT NULL DEFAULT 'unknown', started_at TEXT, ended_at TEXT
);
CREATE TABLE run_io (run_id TEXT, version_id TEXT, direction TEXT CHECK (direction IN ('in','out')), basis TEXT NOT NULL);

CREATE TABLE workspace_snapshots (
  snapshot_id INTEGER PRIMARY KEY, project_id TEXT, root_id TEXT, session_pk INTEGER,
  trigger TEXT, prompt_id TEXT, head_commit TEXT, branch TEXT, dirty INTEGER,
  shadow_commit TEXT, skipped TEXT, duration_ms INTEGER, taken_at TEXT NOT NULL
);

CREATE TABLE jobs (
  job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
  state TEXT NOT NULL,                            -- queued / running / done / failed / cancelled
  attempts INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE view_states (user TEXT, project_id TEXT, payload TEXT, graph_revision INTEGER, updated_at TEXT);

ALTER TABLE source_files ADD COLUMN prefix_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_files ADD COLUMN source_token TEXT;
CREATE TABLE graph_clock (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL);
INSERT INTO graph_clock VALUES (1, 0);
CREATE TRIGGER claims_revision AFTER INSERT ON claims BEGIN
  UPDATE graph_clock SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER review_revision AFTER INSERT ON review_actions BEGIN
  UPDATE graph_clock SET revision = revision + 1 WHERE id = 1;
END;
CREATE TRIGGER raw_no_update BEFORE UPDATE ON raw_events BEGIN
  SELECT RAISE(ABORT, 'L0 is append-only');
END;
CREATE TRIGGER raw_no_delete BEFORE DELETE ON raw_events BEGIN
  SELECT RAISE(ABORT, 'L0 is append-only');
END;
CREATE TRIGGER claims_no_update BEFORE UPDATE ON claims BEGIN
  SELECT RAISE(ABORT, 'claims are append-only');
END;
CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims BEGIN
  SELECT RAISE(ABORT, 'claims are append-only');
END;
CREATE TRIGGER review_no_update BEFORE UPDATE ON review_actions BEGIN
  SELECT RAISE(ABORT, 'reviews are append-only');
END;
CREATE TRIGGER review_no_delete BEFORE DELETE ON review_actions BEGIN
  SELECT RAISE(ABORT, 'reviews are append-only');
END;
CREATE TRIGGER model_candidate BEFORE INSERT ON claims
WHEN NEW.actor LIKE 'model:%' AND NEW.claim_state != 'candidate' BEGIN
  SELECT RAISE(ABORT, 'model claims must be candidates');
END;
CREATE TRIGGER slim_insert AFTER INSERT ON slim_events BEGIN
  INSERT INTO slim_fts(rowid, text) VALUES (NEW.event_id, NEW.text);
END;
CREATE TABLE dedupe_links (
  alias_id INTEGER PRIMARY KEY REFERENCES raw_events,
  canonical_id INTEGER NOT NULL REFERENCES raw_events,
  reason TEXT NOT NULL
);
CREATE TABLE token_cache (
  cache_key TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
  tokens INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE message_fingerprints (
  event_id INTEGER PRIMARY KEY REFERENCES raw_events, session_pk INTEGER NOT NULL,
  signature TEXT NOT NULL, source TEXT NOT NULL, paired INTEGER NOT NULL
);
CREATE TRIGGER slim_delete AFTER DELETE ON slim_events BEGIN
  INSERT INTO slim_fts(slim_fts, rowid, text) VALUES ('delete', OLD.event_id, OLD.text);
END;
CREATE TABLE daily_usage (
  day TEXT PRIMARY KEY, reserved_tokens INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE extraction_runs ADD COLUMN output_json TEXT;
ALTER TABLE extraction_runs ADD COLUMN measured_input_tokens INTEGER;
CREATE TABLE segment_coverage (
  segment_id TEXT NOT NULL, event_id INTEGER NOT NULL REFERENCES raw_events,
  stage TEXT NOT NULL, status TEXT NOT NULL,
  PRIMARY KEY(segment_id, event_id, stage)
);
CREATE TABLE remote_previews (
  project_id TEXT NOT NULL REFERENCES projects, preview_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(project_id, preview_sha256)
);
CREATE TABLE event_search (event_id INTEGER PRIMARY KEY REFERENCES raw_events, text TEXT NOT NULL);
CREATE TABLE session_results (
  cache_key TEXT PRIMARY KEY, session_pk INTEGER NOT NULL REFERENCES sessions,
  segments INTEGER NOT NULL, completed_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE event_fts USING fts5 (
  text, content = 'event_search', content_rowid = 'event_id', tokenize = 'trigram'
);
CREATE TRIGGER event_search_insert AFTER INSERT ON event_search BEGIN
  INSERT INTO event_fts(rowid, text) VALUES (NEW.event_id, NEW.text);
END;
CREATE TRIGGER event_search_delete AFTER DELETE ON event_search BEGIN
  INSERT INTO event_fts(event_fts, rowid, text) VALUES ('delete', OLD.event_id, OLD.text);
END;
PRAGMA user_version = 1;
