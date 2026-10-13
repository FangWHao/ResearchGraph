"""失效的原生别名保留历史编号，不再强制保留已清除的原件。"""

from __future__ import annotations

MIGRATION = (
    "CREATE TABLE raw_events_alias_migration ("
    "event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "session_pk INTEGER NOT NULL REFERENCES sessions,"
    "file_instance_id INTEGER NOT NULL REFERENCES source_files,"
    "record_index INTEGER NOT NULL DEFAULT 0,byte_start INTEGER NOT NULL,"
    "byte_end INTEGER NOT NULL,object_sha256 TEXT NOT NULL,native_id TEXT,"
    "seq INTEGER NOT NULL,kind TEXT NOT NULL,role TEXT,tool_name TEXT,call_id TEXT,model TEXT,"
    "occurred_at TEXT,recorded_at TEXT NOT NULL,line_sha256 TEXT NOT NULL,"
    "alias_of INTEGER,exclude_reason TEXT,"
    "UNIQUE(file_instance_id,byte_start,record_index))",
    "INSERT INTO raw_events_alias_migration SELECT * FROM raw_events",
    "DROP TABLE raw_events",
    "ALTER TABLE raw_events_alias_migration RENAME TO raw_events",
    "CREATE TRIGGER raw_alias_target BEFORE INSERT ON raw_events "
    "WHEN NEW.alias_of IS NOT NULL AND NOT EXISTS "
    "(SELECT 1 FROM raw_events WHERE event_id=NEW.alias_of) BEGIN "
    "SELECT RAISE(ABORT,'alias target is missing'); END",
)
