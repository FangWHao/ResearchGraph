from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from rg.ingest.scanner import scan_file
from rg.ingest.sources import Source, authorized
from rg.store.database import Store, dumps, now
from rg.store.locking import TaskBusy
from rg.store.objects import atomic_stream, atomic_write, digest

MAX_BYTES = 4 * 1024 * 1024
EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SubagentStop",
    "Stop",
    "SessionEnd",
}


def enqueue(root: Path, tool: str, raw: bytes) -> Path:
    """只持久写入提示；不联网、不调模型、不读日志、不执行输入中的命令。"""
    if tool not in {"claude", "codex"}:
        raise ValueError("spool 来源必须为 claude 或 codex")
    path = root / "spool" / f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}-{tool}.json"
    atomic_write(path, raw)
    return path


def enqueue_stream(root: Path, tool: str, stream: BinaryIO, prefix: bytes) -> Path:
    if tool not in {"claude", "codex"}:
        raise ValueError("spool 来源必须为 claude 或 codex")
    path = root / "spool" / f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}-{tool}.json"
    atomic_stream(path, stream, prefix)
    return path


def register(store: Store) -> Counter[str]:
    counts: Counter[str] = Counter()
    paths = [
        *(store.root / "spool").glob("*.json"),
        *(store.root / "snapshots" / "pending").glob("*.json"),
    ]
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            counts["spool_held_files"] += 1
            continue
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            counts["spool_held_files"] += 1
            continue
        sha = store.objects.put(raw)
        key = digest((path.name + "\0" + sha).encode())
        previous = store.db.execute(
            "SELECT r.*,j.state FROM spool_receipts r JOIN jobs j USING(job_id) WHERE receipt_id=?",
            (key,),
        ).fetchone()
        if previous:
            if previous["state"] == "done":
                _ack(store, dict(previous))
            continue
        match = re.fullmatch(r"[0-9]+-[0-9]+-[a-f0-9]{32}-(claude|codex)\.json", path.name)
        tool = match[1] if match else None
        with store.transaction() as db:
            job = db.execute(
                "INSERT INTO jobs (kind,payload,state,attempts,updated_at) "
                "VALUES ('spool_hint',?,'queued',0,?)",
                (dumps({"receipt_id": key}), now()),
            ).lastrowid
            db.execute(
                "INSERT INTO spool_receipts VALUES (?,?,?,?,?,?)",
                (key, path.name, sha, tool, job, now()),
            )
        counts["spool_registered"] += 1
    return counts


def _paths(store: Store, tool: str, payload: dict[str, Any]) -> list[Path]:
    hints = [payload.get("transcript_path"), payload.get("agent_transcript_path")]
    if any(hint is not None and (not isinstance(hint, str) or not hint) for hint in hints):
        raise ValueError("spool 日志路径格式非法")
    paths = [Path(hint) for hint in hints if hint]
    if any(not path.is_absolute() for path in paths):
        raise ValueError("spool 日志路径必须是绝对路径")
    if paths:
        return paths
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return []
    return [
        Path(row[0])
        for row in store.db.execute(
            "SELECT DISTINCT f.path FROM source_files f JOIN sessions s USING(session_pk) "
            "WHERE s.tool=? AND s.native_session_id=?",
            (tool, session),
        )
    ]


def _ack(store: Store, row: dict[str, Any]) -> None:
    for directory in [store.root / "spool", store.root / "snapshots" / "pending"]:
        _ack_path(directory / row["filename"], row["object_sha256"])


def _ack_path(path: Path, sha: str) -> None:
    # 原件已经在对象库及不可变回执中；不同内容或符号链接不能被当作旧提示删除。
    if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) <= MAX_BYTES and digest(raw) == sha:
            path.unlink()


def consume(
    store: Store,
    values: list[Source],
    retry_failed: bool = False,
    limit: int = 100,
    fault: Callable[[], None] | None = None,
) -> Counter[str]:
    if type(limit) is not int or limit < 1:
        raise ValueError("spool 消费页长必须为正整数")
    counts: Counter[str] = Counter()
    rows = store.db.execute(
        "SELECT r.*,j.state FROM spool_receipts r JOIN jobs j USING(job_id) "
        "WHERE j.kind='spool_hint' AND "
        "(j.state IN ('queued','running') OR (? AND j.state='failed')) "
        "ORDER BY j.updated_at,r.recorded_at,r.receipt_id LIMIT ?",
        (retry_failed, limit),
    ).fetchall()
    for row in rows:
        state = _consume_one(store, dict(row), values, fault)
        if state == "done":
            counts["spool_done"] += 1
    for state, count in store.db.execute(
        "SELECT state,count(*) FROM jobs WHERE kind='spool_hint' "
        "AND state IN ('queued','running','failed') GROUP BY state"
    ):
        counts["spool_failed" if state == "failed" else "spool_waiting"] += count
    return counts


def _consume_one(
    store: Store, row: dict[str, Any], values: list[Source], fault: Callable[[], None] | None
) -> str:
    store.db.execute(
        "UPDATE jobs SET state='running',attempts=attempts+1,updated_at=? WHERE job_id=?",
        (now(), row["job_id"]),
    )
    state, error = "queued", None
    try:
        payload = json.loads(store.objects.get(row["object_sha256"]))
        if isinstance(payload, dict) and payload.get("rg_record_type") == "workspace_snapshot":
            from rg.snapshot.records import consume_record

            if row["tool"] is None:
                raise ValueError("快照来源未知")
            consume_record(store, payload, row["tool"], row["object_sha256"])
            if fault:
                fault()
            store.db.execute(
                "UPDATE jobs SET state='done',error=NULL,updated_at=? WHERE job_id=?",
                (now(), row["job_id"]),
            )
            _ack(store, row)
            return "done"
        if (
            not isinstance(payload, dict)
            or row["tool"] is None
            or payload.get("hook_event_name") not in EVENTS
        ):
            raise ValueError("spool 类型或事件未知；原文已保留")
        paths = _paths(store, row["tool"], payload)
        if not paths:
            error = "session_or_transcript_unknown"
        else:
            try:
                # 先核对全部路径，不能先读取一条、再发现另一条未获授权。
                chosen = [(path, authorized(path, values, row["tool"])) for path in paths]
            except ValueError:
                chosen = []
                error = "source_unassigned_or_ambiguous"
            if chosen and all(path.is_file() for path, _ in chosen):
                for path, source in chosen:
                    scan_file(store, path, source.tool, source.project)
                if fault:
                    fault()
                state = "done"
            elif chosen:
                error = "transcript_not_ready"
    except TaskBusy:
        error = "source_busy"
    except (OSError, ValueError, TypeError, RuntimeError) as failure:
        state, error = "failed", type(failure).__name__
    store.db.execute(
        "UPDATE jobs SET state=?,error=?,updated_at=? WHERE job_id=?",
        (state, error, now(), row["job_id"]),
    )
    if state == "done":
        _ack(store, row)
    return state
