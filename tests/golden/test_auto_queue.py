from __future__ import annotations

import json
import select
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from rg.cli.main import parser, run
from rg.extract.estimate import estimate_batch
from rg.extract.queue import Queue, sessions
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.record.question import question
from rg.slim.tokens import CountingUnavailable
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.locking import TaskBusy, exclusive
from tests.golden.test_concurrency import OfflineProvider
from tests.golden.test_incremental import Interrupted, RecordingProvider, append, reply, setup
from tests.golden.test_ingestion import lines, record
from tests.test_worker import PipelineProvider


def add_session(store: Store, directory: Path, project: str | None, name: str) -> int:
    path = directory / (name + ".jsonl")
    lines(path, [record("采用方法甲，合成队列测试。", uuid=name, sessionId=name)])
    scan_file(store, path, "claude", project)
    return store.db.execute(
        "SELECT session_pk FROM sessions WHERE native_session_id=?", (name,)
    ).fetchone()[0]


def rows(store: Store):
    return store.db.execute("SELECT * FROM extraction_queue ORDER BY queue_id").fetchall()


def test_automatic_discovery_skips_unassigned_disabled_and_manual_before_count(
    store: Store, tmp_path: Path
):
    allowed = store.project("合成已授权", [])
    denied = store.project("合成未授权", [])
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (allowed,))
    add_session(store, tmp_path, allowed, "allowed")
    add_session(store, tmp_path, denied, "denied")
    add_session(store, tmp_path, None, "unassigned")
    question(
        store,
        {
            "project_id": allowed,
            "text": "合成人工问题",
            "actor": "human:test",
            "scope": None,
            "request_id": str(uuid4()),
            "expected_revision": store.revision(),
        },
    )
    provider = RecordingProvider()
    provider.remote = True
    queue = Queue(Worker(store, provider))
    result = queue.run()
    assert result["unassigned"] == result["remote_disabled"] == result["done"] == 1
    assert len(rows(store)) == 1
    assert {r["session_pk"] for r in rows(store)} == {1}
    assert {
        event["event_id"]
        for request in provider.requests
        if isinstance(request, list)
        for event in request
    } == {1}
    assert provider.calls == 2
    assert Queue(Worker(store, provider)).run().get("processed", 0) == 0


def test_append_is_incremental_and_review_alone_does_not_requeue(store: Store, tmp_path: Path):
    project, path = setup(store, tmp_path, [record("采用方法甲，原始回合。", uuid="first")])
    provider = RecordingProvider()
    queue = Queue(Worker(store, provider))
    assert queue.run()["claims"] == 1
    claim = store.db.execute("SELECT claim_id FROM claims").fetchone()[0]
    store.review(claim, "confirm", "human:test", store.revision())
    calls = provider.calls
    assert queue.run().get("processed", 0) == 0 and provider.calls == calls
    append(path, [reply()])
    scan_file(store, path, "claude", project)
    # 人工审核按既有合同改变工作集配置；追加仍不覆盖已确认事实。
    assert queue.run()["done"] == 1
    assert store.claim_state(claim) == "confirmed"
    assert len(rows(store)) == 2
    assert queue.run().get("processed", 0) == 0


def test_append_without_review_does_not_repeat_owned_content(store: Store, tmp_path: Path):
    project, path = setup(store, tmp_path, [record("采用方法甲，原始回合。", uuid="first")])
    provider = RecordingProvider()
    queue = Queue(Worker(store, provider))
    assert queue.run()["claims"] == 1
    provider.requests.clear()
    append(path, [record("采用方法甲，新增回合。", uuid="second")])
    scan_file(store, path, "claude", project)
    assert queue.run()["claims"] == 1
    inputs = [e for r in provider.requests if isinstance(r, list) for e in r]
    assert {e["event_id"] for e in inputs if not e.get("context_only")} == {2}
    assert all(e["context_only"] for e in inputs if e["event_id"] == 1)


def test_claimed_input_cannot_consume_later_append(store: Store, tmp_path: Path):
    project, path = setup(store, tmp_path, [record("采用方法甲，原始回合。", uuid="first")])
    provider = RecordingProvider()
    worker = Worker(store, provider)
    queue = Queue(worker)
    queue._discover(False)
    task, owner = queue._claim(rows(store)[0]["queue_id"])
    append(path, [record("采用方法甲，随后追加。", uuid="second")])
    scan_file(store, path, "claude", project)
    result = worker.process(task["session_pk"], max_event_id=task["max_event_id"])
    queue._finish(task["queue_id"], owner, "done", result)
    assert result["claims"] == 1 and store.health()["pending"] > 0
    assert {e["event_id"] for r in provider.requests if isinstance(r, list) for e in r} == {1}
    assert queue.run()["claims"] == 1
    assert store.health()["pending"] == 0


@pytest.mark.parametrize("completed", [False, True])
def test_bound_does_not_split_existing_plan_or_repeat_completed_claim(
    store: Store, tmp_path: Path, monkeypatch, completed: bool
):
    setup(store, tmp_path, [record("采用方法甲，原始回合。"), reply()])
    provider = PipelineProvider()
    worker = Worker(store, provider)
    if completed:
        assert worker.process(1)["claims"] == 1
    else:

        def stop(*args):
            raise Interrupted()

        monkeypatch.setattr(worker, "_process_item", stop)
        with pytest.raises(Interrupted):
            worker.process(1)
    before = provider.calls
    assert worker.process(1, max_event_id=1)["busy"] == 1
    assert provider.calls == before and worker.max_event_id is None


def test_partial_is_not_automatically_resent_but_explicit_retry_recovers(
    store: Store, tmp_path: Path
):
    setup(store, tmp_path, [record("采用方法甲，合成失败。")])

    class CounterUnavailable(PipelineProvider):
        def count_request(self, request):
            if self.fail_always:
                raise CountingUnavailable("合成计数服务暂不可用")
            return super().count_request(request)

    provider = CounterUnavailable(fail_always=True)
    queue = Queue(Worker(store, provider))
    assert queue.run()["partial"] == 1
    assert provider.calls == 0 and store.health()["pending"] > 0
    assert queue.run().get("processed", 0) == 0 and provider.calls == 0
    provider.fail_always = False
    assert queue.run(retry_failed=True)["claims"] == 1
    assert len(rows(store)) == 1 and rows(store)[0]["attempts"] == 2
    assert store.health()["pending"] == 0
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 3


def test_busy_session_yields_to_other_sessions(store: Store, tmp_path: Path, monkeypatch):
    project = store.project("合成公平排队", [])
    first = add_session(store, tmp_path, project, "first")
    second = add_session(store, tmp_path, project, "second")
    provider = PipelineProvider()
    worker = Worker(store, provider)
    original = worker.process

    def busy(session, *args, **kwargs):
        if session == first:
            raise TaskBusy("合成忙碌")
        return original(session, *args, **kwargs)

    monkeypatch.setattr(worker, "process", busy)
    queue = Queue(worker)
    assert queue.run(limit=1)["queued"] == 1 and provider.calls == 0
    assert queue.run(limit=1)["done"] == 1
    assert rows(store)[1]["session_pk"] == second
    assert rows(store)[0]["state"] == "queued"


def test_daily_budget_wait_is_inherited_and_resumes_next_utc_day(
    store: Store, tmp_path: Path, monkeypatch
):
    timestamp = "2026-10-10T23:59:00+00:00"
    monkeypatch.setattr("rg.extract.queue.now", lambda: timestamp)
    monkeypatch.setattr("rg.extract.worker.now", lambda: timestamp)
    project, path = setup(store, tmp_path, [record("采用方法甲，合成额度。")])
    store.db.execute("INSERT INTO daily_usage VALUES ('2026-10-10',1000)")
    provider = PipelineProvider()
    queue = Queue(Worker(store, provider, daily_budget=5000))
    assert queue.run()["paused"] == 1 and provider.calls == 0
    assert rows(store)[0]["next_attempt_at"] == "2026-10-11T00:00:00+00:00"
    assert queue.run().get("processed", 0) == 0
    append(path, [reply()])
    scan_file(store, path, "claude", project)
    assert queue.run().get("processed", 0) == 0
    assert [r["state"] for r in rows(store)] == ["cancelled", "paused"]
    assert provider.calls == 0 and rows(store)[1]["attempts"] == 0
    timestamp = "2026-10-11T00:00:01+00:00"
    assert queue.run()["done"] == 1 and provider.calls == 3
    assert (
        store.db.execute(
            "SELECT reserved_tokens FROM daily_usage WHERE day='2026-10-10'"
        ).fetchone()[0]
        == 1000
    )
    assert store.health()["pending"] == 0


def test_configuration_return_can_resume_cancelled_task(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲，合成配置切换。")])
    first = Queue(Worker(store, PipelineProvider(), input_budget=128000))
    second = Queue(Worker(store, PipelineProvider(), input_budget=256000))
    first._discover(False)
    second._discover(False)
    assert [r["state"] for r in rows(store)] == ["cancelled", "queued"]
    assert first.run()["done"] == 1
    assert [r["state"] for r in rows(store)] == ["done", "cancelled"]


def test_restoring_cancelled_configuration_keeps_its_budget_wait(
    store: Store, tmp_path: Path, monkeypatch
):
    timestamp = "2026-10-10T12:00:00+00:00"
    monkeypatch.setattr("rg.extract.queue.now", lambda: timestamp)
    monkeypatch.setattr("rg.extract.worker.now", lambda: timestamp)
    setup(store, tmp_path, [record("采用方法甲，合成恢复等待。")])
    store.db.execute("INSERT INTO daily_usage VALUES ('2026-10-10',5000)")
    provider = PipelineProvider()
    first = Queue(Worker(store, provider, daily_budget=5000))
    second = Queue(Worker(store, provider, daily_budget=5000, input_budget=256000))
    assert first.run()["paused"] == 1
    second._discover(False)
    assert first.run().get("processed", 0) == 0
    assert rows(store)[0]["state"] == "paused"
    assert rows(store)[0]["next_attempt_at"] == "2026-10-11T00:00:00+00:00"
    assert provider.calls == 0


def test_revoked_permission_blocks_claim_and_grant_resumes(store: Store, tmp_path: Path):
    project, _ = setup(store, tmp_path, [record("采用方法甲，合成权限。")])
    provider = PipelineProvider()
    provider.remote = True
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (project,))
    queue = Queue(Worker(store, provider))
    queue._discover(False)
    store.db.execute("UPDATE projects SET remote_model_allowed=0 WHERE project_id=?", (project,))
    assert queue._claim(rows(store)[0]["queue_id"]) is None
    assert rows(store)[0]["state"] == "blocked" and provider.calls == provider.counts == 0
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (project,))
    assert queue.run()["done"] == 1 and len(rows(store)) == 1


def test_response_and_candidate_commit_survive_queue_finish_interruption(
    store: Store, tmp_path: Path, monkeypatch
):
    setup(store, tmp_path, [record("采用方法甲，合成恢复。")])
    queue = Queue(Worker(store, PipelineProvider()))

    def stop(*args):
        raise Interrupted()

    monkeypatch.setattr(queue, "_finish", stop)
    with pytest.raises(Interrupted):
        queue.run()
    assert rows(store)[0]["state"] == "running"
    result = Queue(Worker(store, OfflineProvider())).run()
    assert result["recovered"] == result["done"] == 1 and result["claims"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    recovered = store.db.execute(
        "SELECT details FROM extraction_queue_events WHERE kind='recovered'"
    ).fetchone()[0]
    assert json.loads(recovered)["proof"] == "exclusive_process_lock_acquired"


def test_scheduler_lock_rejects_contender_before_recovery_or_model(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲，合成互斥。")])
    provider = PipelineProvider()
    queue = Queue(Worker(store, provider))
    with exclusive(store.root / "locks" / "extract-queue.lock"):
        with pytest.raises(TaskBusy):
            queue.run()
    assert not rows(store) and provider.counts == provider.calls == 0
    assert queue.run()["done"] == 1


def test_validated_response_recovers_without_model_and_without_duplicate_candidate(
    store: Store, tmp_path: Path, monkeypatch
):
    from rg.extract.validate import persist

    setup(store, tmp_path, [record("采用方法甲，合成响应恢复。")])
    original = store.raw(1)

    def stop(*args, **kwargs):
        raise Interrupted()

    monkeypatch.setattr("rg.extract.worker.persist", stop)
    with pytest.raises(Interrupted):
        Queue(Worker(store, PipelineProvider())).run()
    assert rows(store)[0]["state"] == "running"
    assert (
        store.db.execute(
            "SELECT status FROM model_attempts ORDER BY attempt_id DESC LIMIT 1"
        ).fetchone()[0]
        == "validated"
    )
    monkeypatch.setattr("rg.extract.worker.persist", persist)
    result = Queue(Worker(store, OfflineProvider())).run()
    assert result["recovered"] == result["claims"] == result["done"] == 1
    assert store.raw(1) == original and store.health()["pending"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1


def test_other_connection_can_write_during_generation(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲，合成事务边界。")])

    class Writer(PipelineProvider):
        def generate(self, request):
            other = Store(store.root)
            try:
                other.project("合成并发采集写入", [])
            finally:
                other.close()
            return super().generate(request)

    assert Queue(Worker(store, Writer())).run()["done"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程终止验收")
def test_killed_scheduler_releases_lock_and_keeps_unknown_usage(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲，合成进程终止。")])
    code = """
import signal, sys
from pathlib import Path
from rg.store.database import Store
from rg.extract.queue import Queue
from rg.extract.worker import Worker
from tests.test_worker import PipelineProvider
class Blocked(PipelineProvider):
    def generate(self, request):
        print('sent', flush=True)
        signal.pause()
value=Store(Path(sys.argv[1]))
Queue(Worker(value, Blocked())).run()
"""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(store.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        readable, _, _ = select.select([child.stdout], [], [], 10)
        assert readable and child.stdout.readline().strip() == "sent"
        assert rows(store)[0]["state"] == "running"
        with pytest.raises(TaskBusy):
            Queue(Worker(store, PipelineProvider())).run()
    finally:
        child.kill()
        child.communicate(timeout=5)
    before = store.db.execute("SELECT sum(reserved_tokens) FROM daily_usage").fetchone()[0]
    assert before == 4010
    result = Queue(Worker(store, PipelineProvider())).run()
    assert result["recovered"] == result["done"] == result["claims"] == 1
    assert (
        store.db.execute("SELECT sum(reserved_tokens) FROM daily_usage").fetchone()[0]
        == before + 60
    )
    assert (
        store.db.execute(
            "SELECT count(*) FROM model_attempts WHERE sent=1 AND input_tokens IS NULL"
        ).fetchone()[0]
        == 1
    )


def test_stale_owner_and_journal_mutation_are_rejected(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲，合成归属。")])
    queue = Queue(Worker(store, PipelineProvider()))
    queue._discover(False)
    task, owner = queue._claim(rows(store)[0]["queue_id"])
    with pytest.raises(RuntimeError, match="归属"):
        queue._finish(task["queue_id"], str(uuid4()), "done", {})
    assert rows(store)[0]["owner_id"] == owner
    for sql in (
        "UPDATE extraction_queue SET max_event_id=1",
        "DELETE FROM extraction_queue_events",
        "UPDATE extraction_queue_events SET kind='finished'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(sql)
    assert rows(store)[0]["state"] == "running"


def test_health_pagination_project_filter_and_backup_recovery(store: Store, tmp_path: Path):
    first = store.project("合成项目甲", [])
    second = store.project("合成项目乙", [])
    add_session(store, tmp_path, first, "first")
    add_session(store, tmp_path, second, "second")
    queue = Queue(Worker(store, PipelineProvider()))
    queue._discover(False)
    one = store.health(project=first, limit=1)["extraction_queue"]
    all_projects = store.health(limit=1)["extraction_queue"]
    assert one["scope"] == "project" and one["total"] == one["counts"]["queued"] == 1
    assert all_projects["scope"] == "all_projects" and all_projects["total"] == 2
    assert all_projects["next_offset"] == 1
    assert store.health(limit=1, offset=1)["extraction_queue"]["next_offset"] is None
    assert len(sessions(queue.worker, first)[0]) == 1
    destination = tmp_path / "backup"
    backup(store, destination)
    original = store.root
    store.close()
    shutil.rmtree(original)
    restored = Store(destination)
    try:
        assert Queue(Worker(restored, PipelineProvider())).run()["claims"] == 2
        assert restored.health()["pending"] == 0
    finally:
        restored.close()


def test_watch_scans_appends_and_retries_failed_only_on_start(
    store: Store, tmp_path: Path, monkeypatch
):
    _, path = setup(store, tmp_path, [record("采用方法甲，合成持续扫描。", uuid="first")])
    provider = PipelineProvider(fail_always=True)
    queue = Queue(Worker(store, provider))
    assert queue.run()["partial"] == 1
    monkeypatch.setattr("rg.extract.queue.time.sleep", lambda seconds: None)
    stream = queue.watch(retry_failed=True)
    try:
        assert next(stream)["extract"]["partial"] == 1
        calls = provider.calls
        assert next(stream)["extract"].get("processed", 0) == 0 and provider.calls == calls
        provider.fail_always = False
        append(path, [record("采用方法甲，新增内容。", uuid="second")])
        assert next(stream)["extract"]["claims"] == 1
        assert store.health()["pending"] > 0
    finally:
        stream.close()


@pytest.mark.parametrize("value", [0, -1, 201, True])
def test_invalid_limit_does_not_scan_enqueue_or_count(store: Store, tmp_path: Path, value):
    setup(store, tmp_path, [record("采用方法甲，合成参数。")])
    provider = PipelineProvider()
    queue = Queue(Worker(store, provider))
    with pytest.raises(ValueError):
        next(queue.watch(limit=value))
    assert not rows(store) and provider.counts == provider.calls == 0


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_interval_does_not_scan_or_enqueue(store: Store, tmp_path: Path, interval):
    setup(store, tmp_path, [record("采用方法甲，合成间隔。")])
    with pytest.raises(ValueError):
        next(Queue(Worker(store, PipelineProvider())).watch(interval=interval))
    assert not rows(store)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--watch", "--session", "1"],
        ["--report", "unused.md"],
        ["--watch", "--interval", "nan"],
        ["--daily-budget", "0"],
        ["--limit", "201"],
    ],
)
def test_invalid_cli_flags_are_rejected_before_provider(store: Store, arguments):
    with pytest.raises(ValueError):
        run(parser().parse_args(["extract", *arguments]), store)
    assert not rows(store)


def test_batch_estimate_never_generates_or_enqueues_and_limits_authorized_sessions(
    store: Store, tmp_path: Path
):
    project = store.project("合成估算", [])
    add_session(store, tmp_path, project, "first")
    add_session(store, tmp_path, project, "second")

    class Counter(PipelineProvider):
        def validate_budget(self, *args):
            pass

    provider = Counter()
    value = estimate_batch(Worker(store, provider), project, 1)
    assert value["eligible_sessions"] == 2 and value["limited"]
    assert len(value["sessions"]) == 1 and value["generation_calls"] == provider.calls == 0
    assert provider.counts > 0 and not rows(store)


def test_bulk_and_single_cli_preserve_results_and_close_provider(
    store: Store, tmp_path: Path, monkeypatch
):
    setup(store, tmp_path, [record("采用方法甲，合成命令行。")])
    provider = PipelineProvider()
    closed = []
    provider.close = lambda: closed.append(True)
    monkeypatch.setattr("rg.cli.main.Provider", lambda *args: provider)
    monkeypatch.setattr("rg.cli.main.load_key", lambda *args: "synthetic-only")
    args = ["extract", "--base-url", "http://127.0.0.1", "--model", "synthetic", "--extract-only"]
    assert run(parser().parse_args(args), store)["claims"] == 1
    assert run(parser().parse_args([*args, "--session", "1"]), store)["claims"] == 0
    assert closed == [True, True]
