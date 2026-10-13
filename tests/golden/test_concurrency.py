from __future__ import annotations

import copy
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from rg.extract.overview import overview
from rg.extract.provider import ModelResult
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim, persist
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from rg.store.locking import TaskBusy
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup
from tests.golden.test_ingestion import lines, record
from tests.golden.test_links_overview import DerivedProvider, add_object
from tests.test_worker import PipelineProvider


class OfflineProvider(FakeProvider):
    def validate_budget(self, *args):
        raise RuntimeError("模拟服务离线")

    def count_text(self, text):
        raise AssertionError("缓存不应重新计数")

    def count_request(self, request):
        raise AssertionError("缓存不应重新计数")

    def generate(self, request):
        raise AssertionError("缓存不应重新生成")


def invoke(worker: Worker, project: str):
    return worker.invoke(
        "pass2",
        Segment("test-segment", [{"event_id": 1}]),
        project,
        "合成并发输入",
        {"type": "object"},
        [],
    )


def test_same_job_busy_response_recovery_and_atomic_persistence(store: Store, tmp_path: Path):
    project, output, _ = setup(store, tmp_path)
    entered, release = Event(), Event()

    class Blocked(FakeProvider):
        def generate(self, request):
            entered.set()
            assert release.wait(10)
            return super().generate(request)

    provider = Blocked([ModelResult(dumps(output), 10, 20, "stop")], count=10)

    def generate():
        local = Store(store.root)
        try:
            return invoke(Worker(local, provider), project)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(generate)
        try:
            assert entered.wait(5)
            contender = FakeProvider(count=10)
            with pytest.raises(TaskBusy):
                invoke(Worker(store, contender), project)
            assert contender.counts == contender.calls == 0
            assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 1
            assert store.db.execute("SELECT status FROM model_attempts").fetchone()[0] == "pending"
        finally:
            release.set()
        run, response = first.result(timeout=5)
        assert response == output and provider.calls == 1

        # 模拟收到完整响应后退出，另一个连接恢复，不能再访问提供方。
        recovered = Store(store.root)
        try:
            worker = Worker(recovered, OfflineProvider())
            cached_run, cached = invoke(worker, project)
            assert cached_run == run and cached == output
            attempt = worker.attempt_ids[run]
        finally:
            recovered.close()

        ready = Barrier(2)

        def save():
            local = Store(store.root)
            try:
                ready.wait(timeout=5)
                return persist(
                    local, output, project, run, set(), {1}, "test-segment", attempt_id=attempt
                )
            finally:
                local.close()

        a, b = pool.submit(save), pool.submit(save)
        batches = [a.result(timeout=5), b.result(timeout=5)]
        assert sorted(map(len, batches)) == [0, 2]

    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM entities").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM evidence_spans").fetchone()[0] == 2
    assert store.db.execute("SELECT status FROM model_attempts").fetchone()[0] == "ok"
    assert store.db.execute("SELECT reserved_tokens FROM daily_usage").fetchone()[0] == 30
    assert all(store.claim_state(i) == "candidate" for batch in batches for i in batch)
    revision = store.revision()
    changed = copy.deepcopy(output)
    changed["claims"][0]["label"] = "另一条有效候选"
    with pytest.raises(InvalidClaim, match="不同输出"):
        persist(store, changed, project, run, set(), {1}, "test-segment")
    changed["claims"][0]["evidence"][0]["quote"] = "伪造引用"
    with pytest.raises(InvalidClaim):
        persist(store, changed, project, run, set(), {1}, "test-segment")
    assert store.revision() == revision
    assert (
        store.db.execute(
            "SELECT status,output_json FROM extraction_runs WHERE extraction_run_id=?", (run,)
        ).fetchone()[0]
        == "ok"
    )
    assert invoke(Worker(store, OfflineProvider()), project) == (run, output)


def add_session(store: Store, tmp_path: Path, project: str, name: str) -> int:
    path = tmp_path / (name + ".jsonl")
    lines(path, [record("采用方法甲，仅用于 data_v2。", name, sessionId=name)])
    scan_file(store, path, "claude", project)
    return store.db.execute("SELECT max(session_pk) FROM sessions").fetchone()[0]


def test_session_order_does_not_block_ingestion_or_other_session(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    session = add_session(store, tmp_path, project, "first")
    entered, release = Event(), Event()

    class Blocked(PipelineProvider):
        def generate(self, request):
            if self.calls == 0:
                entered.set()
                assert release.wait(10)
            return super().generate(request)

    provider = Blocked()

    def process():
        local = Store(store.root)
        try:
            return Worker(local, provider).process(session)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process)
        try:
            assert entered.wait(5)
            contender = PipelineProvider()
            with pytest.raises(TaskBusy):
                Worker(store, contender).process(session)
            assert contender.counts == contender.calls == 0
            other = add_session(store, tmp_path, project, "second")
            assert Worker(store, contender).process(other)["claims"] == 1
            assert not future.done()
        finally:
            release.set()
        assert future.result(timeout=5) == {"segments": 1, "claims": 1, "manual": 0}
    assert Worker(store, OfflineProvider()).process(session)["claims"] == 0
    assert store.health()["pending"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 4
    assert (
        store.db.execute("SELECT count(*) FROM jobs WHERE kind='manual_review'").fetchone()[0] == 0
    )


@pytest.mark.skipif(sys.platform == "win32", reason="本案例使用 POSIX 管道等待子进程")
def test_killed_worker_releases_locks_and_preserves_unknown_usage(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    session = add_session(store, tmp_path, project, "killed")
    code = """
import sys, time
from pathlib import Path
from rg.store.database import Store
from rg.extract.worker import Worker
from tests.test_worker import PipelineProvider
class Blocked(PipelineProvider):
    def generate(self, request):
        print('generation-started', flush=True)
        time.sleep(30)
        raise AssertionError('模拟进程应已终止')
store = Store(Path(sys.argv[1]))
try:
    Worker(store, Blocked()).process(int(sys.argv[2]))
finally:
    store.close()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(store.root), str(session)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        readable, _, _ = select.select([child.stdout], [], [], 5)
        assert readable and child.stdout.readline().strip() == "generation-started"
        assert child.poll() is None
        with pytest.raises(TaskBusy):
            Worker(store, PipelineProvider()).process(session)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
    assert child.returncode is not None and child.returncode != 0
    provider = PipelineProvider()
    assert Worker(store, provider).process(session)["claims"] == 1
    assert provider.calls == 2 and store.health()["pending"] == 0
    attempts = store.db.execute("SELECT * FROM model_attempts ORDER BY attempt_id").fetchall()
    assert len(attempts) == 3
    assert attempts[0]["status"] == "pending" and attempts[0]["sent"] == 1
    assert attempts[0]["input_tokens"] is None and attempts[0]["finished_at"] is None
    assert [row["status"] for row in attempts[1:]] == ["ok", "ok"]
    assert store.db.execute("SELECT reserved_tokens FROM daily_usage").fetchone()[0] == 4070
    assert list((store.root / "locks" / "sessions").glob("*.lock"))
    assert Worker(store, OfflineProvider()).process(session)["claims"] == 0


def test_busy_job_keeps_coverage_pending_without_manual_failure(
    store: Store, tmp_path: Path, monkeypatch
):
    project = store.project("合成项目", [tmp_path])
    session = add_session(store, tmp_path, project, "busy")
    worker = Worker(store, PipelineProvider())

    def busy(*args, **kwargs):
        raise TaskBusy("模拟其他进程正在处理该调用")

    monkeypatch.setattr(worker, "invoke", busy)
    result = worker.process(session)
    assert result["busy"] == 1 and result["manual"] == 0 and result["claims"] == 0
    assert store.health()["pending"] > 0
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM session_results").fetchone()[0] == 0
    assert Worker(store, PipelineProvider()).process(session)["claims"] == 1


def test_projects_do_not_share_model_cache(store: Store, tmp_path: Path):
    first = store.project("甲项目", [tmp_path / "first"])
    second = store.project("乙项目", [tmp_path / "second"])
    provider = FakeProvider([ModelResult("{}", 10, 20, "stop") for _ in range(2)], count=10)
    a = invoke(Worker(store, provider), first)
    b = invoke(Worker(store, provider), second)
    assert a[0] != b[0] and provider.calls == 2
    assert invoke(Worker(store, OfflineProvider()), first) == a
    assert invoke(Worker(store, OfflineProvider()), second) == b


def test_overview_excludes_concurrent_publish_and_allows_other_project(
    store: Store, tmp_path: Path
):
    project = store.project("甲项目", [tmp_path / "first"])
    other = store.project("乙项目", [tmp_path / "second"])
    add_object(store, tmp_path, project, "first")
    add_object(store, tmp_path, other, "second")
    destination = tmp_path / "overview.md"
    destination.write_text("原有文件", encoding="utf-8")
    entered, release = Event(), Event()

    class Blocked(DerivedProvider):
        def generate(self, request):
            entered.set()
            assert release.wait(10)
            return super().generate(request)

    provider = Blocked()

    def publish():
        local = Store(store.root)
        try:
            return overview(Worker(local, provider), project, destination)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publish)
        try:
            assert entered.wait(5)
            contender = DerivedProvider()
            with pytest.raises(TaskBusy):
                overview(Worker(store, contender), project, destination)
            assert contender.counts == contender.calls == 0
            assert destination.read_text(encoding="utf-8") == "原有文件"
            assert overview(Worker(store, contender), other, tmp_path / "other.md")["pages"] == 1
            assert not future.done()
        finally:
            release.set()
        assert future.result(timeout=5) == {"claims": 1, "pages": 1}
    cached = DerivedProvider()
    assert overview(Worker(store, cached), project, destination)["pages"] == 1
    assert cached.calls == 0
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 2


def test_response_cache_still_requires_original_evidence_validation(store: Store, tmp_path: Path):
    project, output, _ = setup(store, tmp_path)
    bad = copy.deepcopy(output)
    bad["claims"][0]["evidence"][0]["quote"] = "伪造引用"
    provider = FakeProvider([ModelResult(dumps(bad), 10, 20, "stop")], count=10)
    run, _ = invoke(Worker(store, provider), project)
    recovered = Worker(store, OfflineProvider())
    cached_run, response = invoke(recovered, project)
    with pytest.raises(InvalidClaim):
        persist(
            store,
            response,
            project,
            cached_run,
            set(),
            {1},
            "test-segment",
            attempt_id=recovered.attempt_ids[run],
        )
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("SELECT status FROM model_attempts").fetchone()[0] == "invalid"
    good = FakeProvider([ModelResult(dumps(output), 10, 20, "stop")], count=10)
    worker = Worker(store, good)
    retried, response = invoke(worker, project)
    assert retried == run and good.calls == 1
    assert (
        len(
            persist(
                store,
                response,
                project,
                retried,
                set(),
                {1},
                "test-segment",
                attempt_id=worker.attempt_ids[run],
            )
        )
        == 2
    )
    assert [
        row[0] for row in store.db.execute("SELECT status FROM model_attempts ORDER BY attempt_id")
    ] == ["invalid", "ok"]
