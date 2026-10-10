from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rg.store.migrations import LATEST_VERSION, migrate
from rg.store.objects import ObjectStore


def now() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, root: Path, *, readonly: bool = False):
        self.root = root
        self.readonly = readonly
        if readonly:
            self.objects = ObjectStore(root / "objects", readonly=True)
            self.db = sqlite3.connect(
                (root / "rg.db").resolve().as_uri() + "?mode=ro",
                uri=True,
                isolation_level=None,
                timeout=30,
            )
            self.db.row_factory = sqlite3.Row
            try:
                self.db.execute("PRAGMA query_only = ON")
                self.db.execute("PRAGMA foreign_keys = ON")
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version != LATEST_VERSION:
                    raise ValueError("只读入口要求当前数据库版本；请先使用写入入口升级")
            except BaseException:
                self.db.close()
                raise
            return
        root.mkdir(parents=True, exist_ok=True)
        self.objects = ObjectStore(root / "objects")
        self.db = sqlite3.connect(root / "rg.db", isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = FULL")
        if self.db.execute("PRAGMA user_version").fetchone()[0] == 0:
            # executescript 会隐式提交；逐个执行完整语句，确保建库中断也整体回滚。
            with self.transaction() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    pending = ""
                    for line in Path(__file__).with_name("schema.sql").read_text().splitlines():
                        pending += line + "\n"
                        if sqlite3.complete_statement(pending):
                            db.execute(pending)
                            pending = ""
                    if pending.strip():
                        raise ValueError("数据库 schema 存在未完成语句")
        migrate(self.db)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise PermissionError("只读数据库不能开始写事务")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """一个读取结果共享快照；已有事务的生命周期仍由调用方管理。"""
        if self.db.in_transaction:
            yield self.db
            return
        self.db.execute("BEGIN")
        try:
            yield self.db
        finally:
            self.db.rollback()

    def project(self, name: str, roots: list[Path]) -> str:
        project_id = str(uuid.uuid4())
        with self.transaction() as db:
            db.execute("INSERT INTO projects VALUES (?, ?, 0, ?)", (project_id, name, now()))
            for path in roots:
                db.execute(
                    "INSERT INTO source_roots "
                    "(root_id, project_id, host_id, path, kind) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), project_id, "local", str(path.resolve()), "repo"),
                )
        return project_id

    def raw(self, event_id: int) -> bytes:
        row = self.db.execute(
            "SELECT object_sha256 FROM raw_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise ValueError("事件不存在")
        return self.objects.get(row[0])

    def revision(self) -> int:
        return self.db.execute("SELECT revision FROM graph_clock WHERE id = 1").fetchone()[0]

    def review(self, claim_id: int, action: str, actor: str, expected: int) -> int:
        if (
            action not in {"confirm", "dismiss"}
            or not isinstance(actor, str)
            or not actor.startswith("human:")
            or not actor[6:].strip()
            or len(actor) > 120
            or type(expected) is not int
            or expected < 0
        ):
            raise ValueError("复核必须使用人工身份及 confirm/dismiss")
        with self.transaction() as db:
            if expected != self.revision():
                raise ConflictError("图版本已变化，请刷新后复核")
            self.assert_reviewable(claim_id, action)
            db.execute(
                "INSERT INTO review_actions "
                "(claim_id, action, actor, expected_revision, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (claim_id, action, actor, expected, now()),
            )
        return self.revision()

    def assert_reviewable(self, claim_id: int, action: str) -> None:
        row = self.db.execute(
            "SELECT claim_type,payload FROM claims WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if row is None:
            raise ValueError("记录不存在")
        if self.db.execute("SELECT 1 FROM claims WHERE replaces_claim=?", (claim_id,)).fetchone():
            raise ValueError("记录已有修改版，请复核最新记录")
        if (
            action in {"confirm", "edit"}
            and row["claim_type"] == "decision_event"
            and json.loads(row["payload"]).get("target") is None
        ):
            raise ValueError("对象尚未确定，请使用选择对象入口；普通确认或修改不能解析歧义")

    def claim_state(self, claim_id: int) -> str:
        row = self.db.execute(
            "SELECT action FROM review_actions WHERE claim_id = ? ORDER BY action_id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row:
            return "confirmed" if row[0] == "confirm" else "dismissed"
        claim = self.db.execute(
            "SELECT claim_state FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if not claim:
            raise ValueError("候选不存在")
        return claim[0]

    def search(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        if not text or not 1 <= limit <= 100:
            raise ValueError("搜索文本不能为空，页长为 1 到 100")
        if len(text) < 3:
            rows = self.db.execute(
                "SELECT event_id, text FROM event_search WHERE instr(text, ?) > 0 LIMIT ?",
                (text, limit),
            )
        else:
            literal = '"' + text.replace('"', '""') + '"'
            rows = self.db.execute(
                "SELECT rowid AS event_id, text FROM event_fts WHERE event_fts MATCH ? LIMIT ?",
                (literal, limit),
            )
        return [dict(row) for row in rows]

    def health(
        self,
        project: str | None = None,
        day: str | None = None,
        daily_budget: int = 500000,
        limit: int = 50,
        offset: int = 0,
        pipeline_offset: int = 0,
    ) -> dict[str, Any]:
        from rg.extract.monitor import monitor

        result: dict[str, Any] = {}
        for name, query in {
            "events": "SELECT count(*) FROM raw_events",
            "unknown": "SELECT count(*) FROM raw_events WHERE kind = 'unknown'",
            "bad_lines": "SELECT count(*) FROM raw_events WHERE exclude_reason = 'bad_json'",
            "unassigned_sessions": "SELECT count(*) FROM sessions WHERE project_id IS NULL",
            "pending": "SELECT count(*) FROM coverage WHERE status = 'pending'",
            "manual_jobs": "SELECT count(*) FROM jobs WHERE state = 'failed'",
        }.items():
            result[name] = self.db.execute(query).fetchone()[0]
        result["revision"] = self.revision()
        result["ingest"] = {
            "registered_sources": self.db.execute("SELECT count(*) FROM ingest_sources").fetchone()[
                0
            ],
            "known_source_paths": self.db.execute(
                "SELECT count(DISTINCT path) FROM source_files WHERE parser IN ('claude','codex')"
            ).fetchone()[0],
            "spool_receipts": self.db.execute("SELECT count(*) FROM spool_receipts").fetchone()[0],
            "spool_unfinished": self.db.execute(
                "SELECT count(*) FROM jobs WHERE kind='spool_hint' "
                "AND state IN ('queued','running')"
            ).fetchone()[0],
            "spool_failed": self.db.execute(
                "SELECT count(*) FROM jobs WHERE kind='spool_hint' AND state='failed'"
            ).fetchone()[0],
        }
        result["extraction"] = monitor(self, project, day, daily_budget, limit, offset)
        from rg.extract.queue_health import health as queue_health

        result["extraction_queue"] = queue_health(self, project, limit, offset)
        from rg.extract.queue_health import pipeline_health

        result["pipeline_queue"] = pipeline_health(self, project, limit, pipeline_offset)
        from rg.artifacts.views import health as artifact_health

        result["artifacts"] = artifact_health(self, project)
        result["snapshots"] = dict(
            self.db.execute(
                "SELECT count(*) AS total, "
                "count(CASE WHEN skipped IS NOT NULL THEN 1 END) AS skipped, "
                "count(CASE WHEN async_race=1 THEN 1 END) AS async_race, "
                "count(CASE WHEN json_array_length(metadata,'$.omitted_files')>0 "
                "THEN 1 END) AS partial, "
                "count(CASE WHEN metadata IS NULL THEN 1 END) AS metadata_unknown "
                "FROM workspace_snapshots WHERE (? IS NULL OR project_id=?)",
                (project, project),
            ).fetchone()
        )
        result["l1"] = {
            "runs": self.db.execute(
                "SELECT count(*) FROM runs WHERE (? IS NULL OR project_id=?)", (project, project)
            ).fetchone()[0],
            "edit_records": self.db.execute(
                "SELECT count(*) FROM edit_records WHERE (? IS NULL OR project_id=?)",
                (project, project),
            ).fetchone()[0],
            "waiting": self.db.execute(
                "SELECT count(*) FROM l1_derivations WHERE state='waiting'"
            ).fetchone()[0],
            "failed": self.db.execute(
                "SELECT count(*) FROM l1_derivations WHERE state='failed'"
            ).fetchone()[0],
            "queue_scope": "all_projects",
        }
        return result
