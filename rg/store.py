"""SQLite 存储。一个连接加一把锁，服务线程和整理线程共用。"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog(
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL, meta TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, workspace TEXT,
  follow INTEGER NOT NULL DEFAULT 0, model_allowed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, organized_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  tool TEXT NOT NULL, path TEXT NOT NULL, native_id TEXT, cwd TEXT, title TEXT,
  started_at TEXT, ended_at TEXT,
  parsed_size INTEGER NOT NULL DEFAULT -1, parsed_mtime REAL NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0, extracted_upto INTEGER NOT NULL DEFAULT 0,
  copied INTEGER NOT NULL DEFAULT 0, fork_of INTEGER, fork_branch INTEGER,
  missing INTEGER NOT NULL DEFAULT 0, unknown TEXT NOT NULL DEFAULT '{}',
  UNIQUE(project_id, path));
CREATE TABLE IF NOT EXISTS messages(
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, role TEXT NOT NULL, ts TEXT, text TEXT NOT NULL,
  PRIMARY KEY(session_id, idx));
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  edited INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS branches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
  from_event INTEGER, into_event INTEGER, edited INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  ts TEXT, anchor INTEGER NOT NULL, refs TEXT NOT NULL DEFAULT '[]',
  edited INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS events_project ON events(project_id);
CREATE TABLE IF NOT EXISTS model_cache(
  key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  stage TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cached INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_at ON usage(at);
CREATE TABLE IF NOT EXISTS excluded(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path TEXT NOT NULL, PRIMARY KEY(project_id, path));
CREATE TABLE IF NOT EXISTS briefs(
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  signature TEXT NOT NULL DEFAULT '', content TEXT, model TEXT NOT NULL DEFAULT '',
  created_at TEXT, error TEXT);
"""


# 已有表的改动按顺序写在这里，库里用 PRAGMA user_version 记到第几步。
# 上面的 SCHEMA 只能加新表，不能改已有的表。
MIGRATIONS: list[list[str]] = [
    # 1：事件改为 尝试（带成败）/ 决定 / 结果，并带重要性；会话记下用哪一版解析器读的
    [
        "ALTER TABLE events ADD COLUMN outcome TEXT",
        "ALTER TABLE events ADD COLUMN importance INTEGER NOT NULL DEFAULT 2",
        "UPDATE events SET kind = 'result' WHERE kind IN ('finding', 'output')",
        "ALTER TABLE sessions ADD COLUMN parser INTEGER NOT NULL DEFAULT 0",
    ],
    # 2：研究线复审——状态（active / done / dropped）和上次复审时的事件签名
    [
        "ALTER TABLE threads ADD COLUMN status TEXT",
        "ALTER TABLE threads ADD COLUMN reviewed TEXT",
    ],
]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self.migrate()

    def migrate(self) -> None:
        with self.tx() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            for number, steps in enumerate(MIGRATIONS[version:], start=version + 1):
                for sql in steps:
                    db.execute(sql)
                db.execute(f"PRAGMA user_version = {number}")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def q(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
