from __future__ import annotations

import os
import select
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from rg.cli.main import parser, run
from rg.ingest.scanner import scan_file
from rg.ingest.sources import register
from rg.ingest.spool import MAX_BYTES, enqueue
from rg.ingest.watch import cycle, watch
from rg.store.backup import backup
from rg.store.database import Store, dumps
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest
from tests.golden.test_ingestion import lines, record


def hint(path: Path, **extra) -> bytes:
    return dumps(
        {"hook_event_name": "Stop", "session_id": "demo", "transcript_path": str(path), **extra}
    ).encode()


def test_daemon_absent_queue_and_recursive_scan_recover_without_duplicates(
    store: Store, tmp_path: Path
):
    logs = tmp_path / "logs"
    path = logs / "subagents" / "agent.jsonl"
    path.parent.mkdir(parents=True)
    lines(path, [record(uuid="child", agentId="agent")])
    project = store.project("合成采集项目", [tmp_path])
    register(store, logs, "claude", project)
    raw = hint(path, command="只当资料，不执行", unknown_field={"keep": True})
    queued = enqueue(store.root, "claude", raw)
    assert queued.read_bytes() == raw and store.health()["events"] == 0
    result = cycle(store)
    assert result["events"] == 1 and result["spool_done"] == 1
    assert not queued.exists()
    receipt = store.db.execute("SELECT * FROM spool_receipts").fetchone()
    assert store.objects.get(receipt["object_sha256"]) == raw
    assert store.db.execute("SELECT project_id FROM sessions").fetchone()[0] == project
    assert cycle(store).get("events", 0) == 0
    parent = logs / "parent.jsonl"
    lines(parent, [record(uuid="parent")])
    assert cycle(store)["events"] == 1
    assert (
        store.db.execute(
            "SELECT parent_session_pk FROM sessions WHERE agent_id='agent'"
        ).fetchone()[0]
        is not None
    )


def test_polling_without_any_hint_finds_new_file_and_append(store: Store, tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    register(store, logs, "claude")
    iterator = watch(store, interval=0.001)
    assert next(iterator).get("events", 0) == 0
    path = logs / "late.jsonl"
    lines(path, [record()])
    assert next(iterator)["events"] == 1
    with path.open("ab") as stream:
        stream.write(dumps(record("新增决定", uuid="two")).encode() + b"\n")
    assert next(iterator)["events"] == 1
    assert next(iterator).get("events", 0) == 0
    assert store.db.execute("SELECT project_id,project_basis FROM sessions").fetchone()[:] == (
        None,
        "unassigned",
    )


def test_unknown_or_bad_spool_keeps_original_and_does_not_repeat_failed_jobs(
    store: Store, tmp_path: Path
):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    raw = hint(path, hook_event_name="FutureHook")
    first = enqueue(store.root, "claude", raw)
    second = enqueue(store.root, "claude", b"broken JSON")
    assert cycle(store)["spool_failed"] == 2
    assert first.read_bytes() == raw and second.exists()
    attempts = store.db.execute(
        "SELECT sum(attempts) FROM jobs WHERE kind='spool_hint'"
    ).fetchone()[0]
    cycle(store)
    assert (
        store.db.execute("SELECT sum(attempts) FROM jobs WHERE kind='spool_hint'").fetchone()[0]
        == attempts
    )
    cycle(store, retry_failed=True)
    assert store.db.execute("SELECT count(*) FROM spool_receipts").fetchone()[0] == 2
    assert (
        store.db.execute("SELECT sum(attempts) FROM jobs WHERE kind='spool_hint'").fetchone()[0]
        == attempts + 2
    )
    for sql in ["DELETE FROM spool_receipts", "UPDATE spool_receipts SET filename='changed'"]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(sql)


def test_unregistered_hint_waits_until_explicit_source_registration(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    queued = enqueue(store.root, "claude", hint(path))
    assert cycle(store)["spool_waiting"] == 1
    assert store.health()["events"] == 0 and queued.exists()
    register(store, path, "claude")
    assert cycle(store)["spool_done"] == 1 and store.health()["events"] == 1


def test_known_import_path_can_be_rescanned_and_project_assigned_explicitly(
    store: Store, tmp_path: Path
):
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "log.jsonl"
    lines(path, [record()])
    scan_file(store, path, "claude")
    project = store.project("显式归属", [tmp_path])
    register(store, logs, "claude", project)
    assert cycle(store).get("errors", 0) == 0
    assert store.db.execute("SELECT project_id FROM sessions").fetchone()[0] == project
    other = store.project("另一项目", [tmp_path / "other"])
    with pytest.raises(ValueError, match="其他项目"):
        register(store, logs, "claude", other)


def test_ambiguous_roots_and_symlink_escape_are_not_read(store: Store, tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    register(store, logs, "claude")
    inner = logs / "inner"
    inner.mkdir()
    project = store.project("另一范围", [inner])
    register(store, inner, "claude", project)
    path = inner / "log.jsonl"
    lines(path, [record()])
    outside = tmp_path / "outside.jsonl"
    lines(outside, [record(uuid="outside")])
    (logs / "escape.jsonl").symlink_to(outside)
    queued = enqueue(store.root, "claude", hint(path))
    result = cycle(store)
    assert result["errors"] == 2 and result["spool_waiting"] == 1
    assert store.health()["events"] == 0 and queued.exists()


def test_temporary_oversized_and_symlink_spools_are_held(store: Store, tmp_path: Path):
    directory = store.root / "spool"
    directory.mkdir()
    (directory / "unfinished.json.tmp").write_bytes(b"partial")
    huge = directory / "huge.json"
    with huge.open("wb") as stream:
        stream.truncate(MAX_BYTES + 1)
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret-shaped synthetic data")
    (directory / "alias.json").symlink_to(outside)
    assert cycle(store)["spool_held_files"] == 2
    assert store.db.execute("SELECT count(*) FROM spool_receipts").fetchone()[0] == 0
    assert huge.exists() and (directory / "unfinished.json.tmp").exists()


def test_spool_commit_interruption_and_backup_recover_even_without_queue_file(
    store: Store, tmp_path: Path
):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    raw = hint(path)
    queued = enqueue(store.root, "claude", raw)

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cycle(store, fault=interrupt)
    assert store.health()["events"] == 1 and queued.exists()
    queued.unlink()  # 回执与原件已提交；恢复不能仅依赖外部队列文件。
    destination = tmp_path / "backup"
    result = backup(store, destination)
    assert result == {"objects": 2, "events": 1}
    restored = Store(destination)
    try:
        assert cycle(restored)["spool_done"] == 1
        assert restored.health()["events"] == 1
        assert restored.objects.get(digest(raw)) == raw
    finally:
        restored.close()


def test_file_and_consumer_locks_do_not_advance_cursor_when_busy(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    lock = store.root / "locks" / "sources" / (digest(str(path.resolve()).encode()) + ".lock")
    with exclusive(lock):
        assert cycle(store)["busy"] == 1
        assert store.health()["events"] == 0
    with exclusive(store.root / "locks" / "scan.lock"):
        with pytest.raises(TaskBusy):
            cycle(store)
    assert cycle(store)["events"] == 1


@pytest.mark.parametrize(
    "first", [[], {"type": "future", "payload": "invalid", "sessionId": {"bad": "id"}}]
)
def test_bad_first_record_and_tail_remain_recoverable(store: Store, tmp_path: Path, first):
    path = tmp_path / "log.jsonl"
    good = dumps(record()).encode()
    path.write_bytes(dumps(first).encode() + b"\n" + good)
    register(store, path, "claude")
    first = cycle(store)
    assert first["unknown"] == 1 and first["tail_fragments"] == 1
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert cycle(store)["events"] == 1
    assert store.health()["events"] == 2


def test_cli_scan_validates_source_and_interval(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    with pytest.raises(ValueError, match="--tool"):
        run(parser().parse_args(["scan", "--path", str(path)]), store)
    with pytest.raises(ValueError, match="--path"):
        run(parser().parse_args(["scan", "--tool", "claude"]), store)
    assert (
        run(parser().parse_args(["scan", "--path", str(path), "--tool", "claude"]), store)["events"]
        == 1
    )
    for interval in [0, -1, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="有限正数"):
            next(watch(store, interval))


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL 为 Linux 的实际进程恢复验收")
def test_real_sigkill_after_log_commit_recovers_receipt_and_new_event(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    queued = enqueue(store.root, "claude", hint(path))
    code = """import sys
from pathlib import Path
from rg.store.database import Store
from rg.ingest.watch import cycle
store = Store(Path(sys.argv[1]))
def interrupted():
    print('READY',flush=True)
    sys.stdin.readline()
cycle(store,fault=interrupted)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(store.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline() == b"READY\n"
        os.kill(child.pid, signal.SIGKILL)
        assert child.wait(timeout=5) == -signal.SIGKILL
        assert queued.exists() and store.health()["events"] == 1
        assert (
            store.db.execute("SELECT state FROM jobs WHERE kind='spool_hint'").fetchone()[0]
            == "running"
        )
        with path.open("ab") as stream:
            stream.write(dumps(record("退出后新增", uuid="two")).encode() + b"\n")
        result = cycle(store)
        assert result["events"] == 1 and result["spool_done"] == 1
        assert store.health()["events"] == 2 and not queued.exists()
        assert cycle(store).get("events", 0) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_queued_unassigned_hints_do_not_starve_later_ready_page(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    for i in range(100):
        enqueue(store.root, "claude", hint(tmp_path / f"unknown-{i}.jsonl"))
    ready = enqueue(store.root, "claude", hint(path))
    assert cycle(store)["spool_waiting"] == 101
    assert cycle(store)["spool_done"] == 1
    assert not ready.exists() and store.health()["events"] == 1


def test_original_spool_is_atomic_and_done_ack_can_recover(
    store: Store, tmp_path: Path, monkeypatch
):
    from rg.ingest import spool
    from rg.store import objects

    with monkeypatch.context() as change:

        def failed_replace(*args):
            raise OSError("合成改名中断")

        change.setattr(objects.os, "replace", failed_replace)
        with pytest.raises(OSError):
            enqueue(store.root, "claude", b"incomplete")
    assert not list((store.root / "spool").iterdir())
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    queued = enqueue(store.root, "claude", hint(path))
    with monkeypatch.context() as change:

        def interrupted(*args):
            raise KeyboardInterrupt

        change.setattr(spool, "_ack", interrupted)
        with pytest.raises(KeyboardInterrupt):
            cycle(store)
    assert (
        store.db.execute("SELECT state FROM jobs WHERE kind='spool_hint'").fetchone()[0] == "done"
    )
    assert queued.exists()
    cycle(store)
    assert not queued.exists() and store.health()["events"] == 1


def test_watch_lifetime_lock_and_health_counts(store: Store, tmp_path: Path):
    path = tmp_path / "log.jsonl"
    lines(path, [record()])
    register(store, path, "claude")
    iterator = watch(store, 0.001)
    next(iterator)
    contender = watch(store, 0.001)
    with pytest.raises(TaskBusy):
        next(contender)
    iterator.close()
    recovered = watch(store, 0.001)
    try:
        assert next(recovered).get("events", 0) == 0
    finally:
        recovered.close()
    enqueue(store.root, "claude", hint(tmp_path / "unassigned.jsonl"))
    cycle(store)
    ingest = store.health()["ingest"]
    assert (
        ingest["registered_sources"] == ingest["spool_receipts"] == ingest["spool_unfinished"] == 1
    )
    assert ingest["spool_failed"] == 0
