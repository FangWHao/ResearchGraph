from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rg.extract import linker
from rg.extract.linker import link
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.slim.tokens import CountingUnavailable
from rg.store.database import Store
from rg.store.locking import exclusive
from tests.golden.test_ingestion import lines, record
from tests.golden.test_links_overview import DerivedProvider, add_object, two_objects
from tests.test_worker import PipelineProvider


def three_objects(store: Store, tmp_path: Path):
    project, _, _ = two_objects(store, tmp_path)
    add_object(store, tmp_path, project, "丙")
    return project


def test_pages_reach_pairs_after_first_fifty_and_do_not_recount_completed_pairs(
    store: Store,
    tmp_path: Path,
):
    project = store.project("合成分页项目", [tmp_path / "project"])
    for index in range(12):
        add_object(store, tmp_path, project, f"独立会话{index}")
    provider = DerivedProvider("none")
    first = link(Worker(store, provider), project)
    assert first["processed"] == 50 and first["has_more"] == 1 and provider.calls == 50
    assert first["manual"] == 0
    root = store.root
    store.close()
    resumed = Store(root)
    try:
        second = link(Worker(resumed, provider), project)
        assert second["processed"] == 16 and second["cached"] == 50
        assert second["has_more"] == 0 and provider.calls == 66
        assert (
            resumed.db.execute("SELECT count(*) FROM link_progress WHERE state='done'").fetchone()[
                0
            ]
            == 66
        )
        before = provider.counts
        completed = link(Worker(resumed, provider), project)
        assert completed["processed"] == 0 and completed["cached"] == 66
        assert completed["has_more"] == 0 and provider.counts == before
        add_object(resumed, tmp_path, project, "新增会话")
        latest = link(Worker(resumed, provider), project)
        assert latest["processed"] == 12 and latest["cached"] == 66
        assert provider.calls == 78 and latest["has_more"] == 0
    finally:
        resumed.close()


def test_failed_pairs_do_not_starve_next_page_or_duplicate_manual_jobs(
    store: Store, tmp_path: Path
):
    project = three_objects(store, tmp_path)
    provider = DerivedProvider(broken="endpoint")
    for index in range(3):
        page = link(Worker(store, provider), project, limit=1)
        assert page["processed"] == 1 and page["manual"] == 1
        assert page["skipped_failed"] == index
    assert provider.calls == 3 and store.health()["manual_jobs"] == 3
    repeated = link(Worker(store, provider), project, limit=1)
    assert repeated["processed"] == 0 and repeated["skipped_failed"] == 3
    assert provider.calls == 3 and store.health()["manual_jobs"] == 3
    provider.broken = ""
    recovered = link(Worker(store, provider), project, limit=1, retry_failed=True)
    assert recovered["claims"] == 1 and recovered["processed"] == 1
    assert store.health()["manual_jobs"] == 2
    assert store.db.execute("SELECT max(attempts) FROM link_progress").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='link_review'").fetchone()[0] == 3


def test_daily_budget_pauses_pending_pair_and_resumes_without_retry_failed(
    store: Store, tmp_path: Path
):
    project = three_objects(store, tmp_path)
    provider = DerivedProvider("none")
    paused = link(Worker(store, provider, daily_budget=1030), project)
    assert paused["paused"] == 1 and paused["manual"] == 0 and provider.calls == 1
    states = [r[0] for r in store.db.execute("SELECT state FROM link_progress")]
    assert sorted(states) == ["done", "pending"]
    assert store.health()["manual_jobs"] == 0
    resumed = link(Worker(store, provider, daily_budget=5000), project)
    assert resumed["processed"] == 2 and resumed["paused"] == 0 and resumed["has_more"] == 0
    assert provider.calls == 3
    assert (
        store.db.execute("SELECT count(*) FROM link_progress WHERE state='done'").fetchone()[0] == 3
    )
    assert store.db.execute(
        "SELECT count(*) FROM extraction_runs WHERE status='ok' AND error IS NOT NULL"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["count", "generate"])
def test_provider_failure_keeps_queue_pending_and_does_not_visit_remaining_pairs(
    store: Store,
    tmp_path: Path,
    failure: str,
):
    project = three_objects(store, tmp_path)

    class Unavailable(DerivedProvider):
        broken_service = True

        def count_request(self, request):
            if self.broken_service and failure == "count":
                raise CountingUnavailable("合成计数不可用")
            return super().count_request(request)

        def generate(self, request):
            if self.broken_service and failure == "generate":
                raise RuntimeError("合成服务不可用")
            return super().generate(request)

    provider = Unavailable("none")
    paused = link(Worker(store, provider), project)
    assert paused["paused"] == 1 and paused["manual"] == 0 and paused["processed"] == 1
    assert store.db.execute("SELECT state FROM link_progress").fetchone()[0] == "pending"
    assert store.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 1
    provider.broken_service = False
    result = link(Worker(store, provider), project)
    assert result["paused"] == 0 and result["has_more"] == 0 and provider.calls == 3


def test_interruption_after_claim_commit_recovers_without_new_model_call_or_duplicate_claim(
    store: Store,
    tmp_path: Path,
    monkeypatch,
):
    project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider()

    def stop_after_commit(*args):
        raise SystemExit("合成中断")

    with monkeypatch.context() as change:
        change.setattr(linker, "_done", stop_after_commit)
        with pytest.raises(SystemExit):
            link(Worker(store, provider), project)
    assert (
        store.db.execute("SELECT count(*) FROM claims WHERE claim_type='merge'").fetchone()[0] == 1
    )
    assert store.db.execute("SELECT state FROM link_progress").fetchone()[0] == "pending"
    before = provider.counts
    result = link(Worker(store, provider), project)
    assert result["cached"] == 1 and result["processed"] == 0
    assert provider.calls == 1 and provider.counts == before
    assert (
        store.db.execute("SELECT count(*) FROM claims WHERE claim_type='merge'").fetchone()[0] == 1
    )
    assert store.db.execute("SELECT state FROM link_progress").fetchone()[0] == "done"


@pytest.mark.parametrize("change_kind", ["model", "prompt", "output_budget"])
def test_processing_identity_invalidates_only_when_model_prompt_or_output_budget_changes(
    store: Store,
    tmp_path: Path,
    monkeypatch,
    change_kind: str,
):
    project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider("none")
    link(Worker(store, provider), project)
    if change_kind == "model":
        provider.model = "new-test-model"
    if change_kind == "prompt":
        original = Path.read_text

        def new_prompt(path: Path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            return text + "\n合成提示版本二。" if path.name == "link.txt" else text

        monkeypatch.setattr(Path, "read_text", new_prompt)
    worker = Worker(store, provider, output_budget=800 if change_kind == "output_budget" else 4000)
    result = link(worker, project)
    assert result["processed"] == 1 and result["cached"] == 0 and provider.calls == 2
    assert store.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 2


def test_process_lock_excludes_duplicate_worker_but_does_not_lock_ingestion(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider("none")
    from rg.store.objects import digest

    path = store.root / "locks" / (digest(project.encode()) + ".link")
    with exclusive(path):
        with pytest.raises(RuntimeError, match="正在运行"):
            link(Worker(store, provider), project)
        # 链接锁不持有数据库写事务，采集仍能提交。
        add_object(store, tmp_path, project, "锁内采集")
    assert provider.calls == 0 and link(Worker(store, provider), project)["processed"] == 3


def test_killed_lock_owner_releases_kernel_lock_without_trusting_lock_file(tmp_path: Path):
    path = tmp_path / "worker.lock"
    script = (
        "from pathlib import Path; import sys,time; from rg.store.locking import exclusive\n"
        "with exclusive(Path(sys.argv[1])):\n"
        " print('ready',flush=True)\n"
        " time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "ready"
        assert process.poll() is None
        with pytest.raises(RuntimeError):
            with exclusive(path):
                pytest.fail("活进程的锁不应被重新获取")
        process.kill()
        process.wait(timeout=5)
        assert path.exists()
        with exclusive(path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_extraction_daily_pause_preserves_unprocessed_coverage_and_resumes(
    store: Store, tmp_path: Path
):
    project = store.project("合成暂停项目", [tmp_path / "project"])
    source = tmp_path / "input.jsonl"
    lines(source, [record("采用方法甲。", f"u{i}") for i in range(3)])
    scan_file(store, source, "claude", project)
    provider = PipelineProvider()
    paused = Worker(store, provider, daily_budget=4030).process(1)
    assert paused["paused"] == 1 and paused["manual"] == 0 and provider.calls == 1
    assert store.health()["pending"] > 0 and store.health()["manual_jobs"] == 0
    queued = store.db.execute("SELECT * FROM jobs WHERE kind='budget_pause'").fetchone()
    assert queued["state"] == "queued" and json.loads(queued["payload"])["session_pk"] == 1
    result = Worker(store, provider, daily_budget=20000).process(1)
    assert result["manual"] == 0 and store.health()["pending"] == 0
    assert (
        store.db.execute("SELECT state FROM jobs WHERE kind='budget_pause'").fetchone()[0] == "done"
    )
    assert (
        store.db.execute("SELECT error FROM jobs WHERE kind='budget_pause'").fetchone()[0] is None
    )
    assert store.db.execute(
        "SELECT count(*) FROM extraction_runs WHERE status='ok' AND error IS NOT NULL"
    ).fetchone()[0] == 0
