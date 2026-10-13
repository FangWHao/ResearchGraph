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
from rg.extract.overview import overview
from rg.extract.pipeline import Pipeline, Stages
from rg.extract.worker import Worker
from rg.slim.tokens import CountingUnavailable
from rg.store.backup import backup
from rg.store.database import Store, dumps
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest
from tests.golden.test_auto_queue import add_session
from tests.golden.test_links_overview import add_object, two_objects
from tests.test_worker import PipelineProvider


class AutomaticProvider(PipelineProvider):
    def __init__(self):
        super().__init__()
        self.stages: list[str] = []
        self.inputs: list[dict] = []
        self.relation = "none"
        self.broken_link = False

    def generate(self, request):
        properties = request["schema"]["properties"]
        stage = (
            "link" if "pair_id" in properties else "overview" if "text" in properties else "extract"
        )
        self.stages.append(stage)
        if stage == "extract":
            return super().generate(request)
        self.calls += 1
        content = json.loads(request["input"])
        self.inputs.append(content)
        if stage == "link":
            output = {
                "pair_id": content["pair_id"],
                "relation": self.relation,
                "source": None,
                "target": None,
                "reason": "合成逐对判断",
            }
            if self.relation != "none":
                output.update(source=content["cards"][0]["id"], target=content["cards"][1]["id"])
            if self.broken_link:
                output["source"] = "未提供的端点"
        else:
            output = {
                "text": "合成候选记录概览。",
                "claim_ids": [r["claim_id"] for r in content["records"]],
                "caveats": ["候选不等于确认"],
            }
        from rg.extract.provider import ModelResult

        return ModelResult(dumps(output), 10, 20, "stop")


def stages(store: Store, provider=None, project=None, **kwargs) -> Stages:
    return Stages(Worker(store, provider or AutomaticProvider(), **kwargs), project, None, 50)


def tasks(store: Store, stage: str | None = None):
    return store.db.execute(
        "SELECT * FROM pipeline_queue WHERE (? IS NULL OR stage=?) ORDER BY queue_id",
        (stage, stage),
    ).fetchall()


def test_default_pipeline_orders_extract_link_and_both_overviews_and_repeats_zero_calls(
    store: Store,
    tmp_path: Path,
):
    project = store.project("合成自动项目", [])
    add_session(store, tmp_path, project, "甲")
    add_session(store, tmp_path, project, "乙")
    provider = AutomaticProvider()
    pipeline = Pipeline(Worker(store, provider), project)
    result = pipeline.run()
    assert result["done"] == result["claims"] == 2
    assert result["link_done"] == 1 and result["overview_done"] == 3
    assert provider.stages == ["extract"] * 4 + ["link"] + ["overview"] * 3
    assert all(t["state"] == "done" for t in tasks(store))
    original = [list(r) for r in store.db.execute("SELECT * FROM raw_events")]
    before = list(store.db.iterdump())
    calls, counted = provider.calls, provider.counts
    repeated = pipeline.run()
    assert repeated.get("processed", 0) == repeated.get("link_processed", 0) == 0
    assert repeated.get("overview_processed", 0) == 0
    assert provider.calls == calls and provider.counts == counted
    assert list(store.db.iterdump()) == before
    assert [list(r) for r in store.db.execute("SELECT * FROM raw_events")] == original
    for target in (None, 1, 2):
        text = pipeline.stages.destination(project, target).read_text()
        assert text.startswith('<rg-context origin="researchgraph" layer="overview">')
        assert "模型摘要" in text and "输入摘要" in text
    assert not any("模型摘要" in dumps(content) for content in provider.inputs)


def test_link_claims_trigger_overview_but_not_their_own_link_loop(store: Store, tmp_path: Path):
    project = store.project("合成发现项目", [])
    add_object(store, tmp_path, project, "甲", kind="finding")
    add_object(store, tmp_path, project, "乙", kind="finding")
    provider = AutomaticProvider()
    provider.relation = "supports"
    schedule = stages(store, provider, project)
    result = schedule.run(20)
    assert result["link_claims"] == 1 and result["overview_done"] == 3
    relation = store.db.execute(
        "SELECT claim_id FROM claims WHERE claim_type='relation'"
    ).fetchone()[0]
    assert store.claim_state(relation) == "candidate"
    assert any(
        relation in [r["claim_id"] for r in v["records"]] for v in provider.inputs if "records" in v
    )
    calls = provider.calls
    assert schedule.run(20).get("link_processed", 0) == 0 and provider.calls == calls


def test_append_changes_link_input_and_overview_without_resending_done_pairs(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    provider = AutomaticProvider()
    schedule = stages(store, provider, project)
    schedule.run(20)
    add_object(store, tmp_path, project, "新会话")
    before = provider.stages.count("link")
    schedule.run(20)
    assert provider.stages.count("link") - before == 2
    assert len(tasks(store, "link")) == 2
    assert schedule.run(20).get("overview_processed", 0) == 0


def test_review_refreshes_structured_stages_without_reextracting_history(
    store: Store, tmp_path: Path
):
    project = store.project("合成审核增量", [])
    add_session(store, tmp_path, project, "甲")
    add_session(store, tmp_path, project, "乙")
    provider = AutomaticProvider()
    pipeline = Pipeline(Worker(store, provider), project)
    pipeline.run()
    claim = store.db.execute("SELECT min(claim_id) FROM claims").fetchone()[0]
    store.review(claim, "confirm", "human:合成审核", store.revision())
    before = len(provider.stages)
    result = pipeline.run()
    assert result.get("processed", 0) == 0
    assert result["link_done"] == 1 and result["overview_done"] == 2
    assert provider.stages[before:] == ["link", "overview", "overview"]
    assert store.claim_state(claim) == "confirmed"


def test_return_to_cached_configuration_cancels_other_pending_configuration(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    original = stages(store, project=project)
    original.run(20)
    provider = AutomaticProvider()
    provider.model = "changed-model"
    changed = stages(store, provider, project)
    changed._enqueue(project, "link", None, False)
    assert tasks(store)[-1]["state"] == "queued"
    assert original.run(20).get("link_processed", 0) == 0
    assert tasks(store)[-1]["state"] == "cancelled"
    assert provider.calls == provider.counts == 0


def test_link_pages_wait_before_overviews_and_explicit_retry_does_not_loop_failed_pairs(
    store: Store,
    tmp_path: Path,
):
    project, _, _ = two_objects(store, tmp_path)
    add_object(store, tmp_path, project, "第三")
    provider = AutomaticProvider()
    provider.broken_link = True
    schedule = Stages(Worker(store, provider), project, None, 1)
    for _ in range(2):
        result = schedule.run(20)
        assert result["link_queued"] == 1 and not tasks(store, "overview")
    assert schedule.run(20)["link_partial"] == 1
    assert provider.stages.count("link") == 3
    schedule.run(20)
    assert provider.stages.count("link") == 3
    schedule.run(20, retry_failed=True)
    schedule.run(20)
    schedule.run(20)
    assert provider.stages.count("link") == 6
    assert schedule.run(20).get("link_processed", 0) == 0
    assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='link_review'").fetchone()[0] == 3


def test_remote_disabled_project_is_skipped_before_count_and_claim_checks_revocation(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    provider = AutomaticProvider()
    provider.remote = True
    schedule = stages(store, provider, project)
    assert schedule.run(20)["pipeline_remote_disabled"] == 1
    assert provider.calls == provider.counts == 0 and not tasks(store)
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (project,))
    schedule._enqueue(project, "link", None, False)
    store.db.execute("UPDATE projects SET remote_model_allowed=0 WHERE project_id=?", (project,))
    assert schedule._claim(tasks(store)[0]["queue_id"]) is None
    assert tasks(store)[0]["state"] == "blocked" and provider.counts == 0


def test_daily_budget_pause_is_inherited_by_new_content_and_resumes_next_utc_day(
    store: Store,
    tmp_path: Path,
    monkeypatch,
):
    timestamp = "2026-10-10T23:59:00+00:00"
    monkeypatch.setattr("rg.extract.pipeline.now", lambda: timestamp)
    monkeypatch.setattr("rg.extract.worker.now", lambda: timestamp)
    project, _, _ = two_objects(store, tmp_path)
    provider = AutomaticProvider()
    store.db.execute("INSERT INTO daily_usage VALUES ('2026-10-10',10000)")
    schedule = stages(store, provider, project, daily_budget=10000)
    assert schedule.run(20)["link_paused"] == 1 and provider.calls == 0
    assert not tasks(store, "overview")
    add_object(store, tmp_path, project, "新增")
    assert schedule.run(20).get("link_processed", 0) == 0
    assert tasks(store)[-1]["next_attempt_at"] == "2026-10-11T00:00:00+00:00"
    counts = provider.counts
    schedule.run(20)
    assert provider.counts == counts
    timestamp = "2026-10-11T00:00:01+00:00"
    schedule.run(20)
    assert provider.calls > 0


def test_transient_service_failure_waits_without_review_and_recovers_after_delay(
    store: Store, tmp_path: Path, monkeypatch
):
    timestamp = "2026-10-10T00:00:00+00:00"
    monkeypatch.setattr("rg.extract.pipeline.now", lambda: timestamp)
    project, _, _ = two_objects(store, tmp_path)

    class Unavailable(AutomaticProvider):
        fail = True

        def count_request(self, request):
            if self.fail:
                raise CountingUnavailable("合成服务不可用")
            return super().count_request(request)

    provider = Unavailable()
    schedule = stages(store, provider, project)
    assert schedule.run(20)["link_paused"] == 1
    assert tasks(store)[0]["defer_reason"] == "CountingUnavailable"
    assert store.health()["manual_jobs"] == 0
    add_object(store, tmp_path, project, "服务故障期间追加")
    assert schedule.run(20).get("link_processed", 0) == 0
    assert tasks(store)[-1]["defer_reason"] == "CountingUnavailable"
    provider.fail = False
    timestamp = "2026-10-10T00:00:31+00:00"
    assert schedule.run(20)["link_done"] == 1


def test_overview_input_change_during_generation_does_not_replace_previous_file(
    store: Store, tmp_path: Path
):
    project, (claim, _), _ = two_objects(store, tmp_path)
    output = tmp_path / "旧概览.md"
    output.write_text("旧概览保留")

    class Updating(AutomaticProvider):
        updated = False

        def generate(self, request):
            if not self.updated:
                self.updated = True
                assert not store.db.in_transaction
                independent = Store(store.root)
                independent.review(claim, "confirm", "human:合成修改", independent.revision())
                independent.close()
            return super().generate(request)

    schedule = stages(store, Updating(), project)
    schedule._enqueue(project, "overview", None, False)
    queue = tasks(store)[0]["queue_id"]
    task, owner = schedule._claim(queue)
    from rg.extract.overview import ChangedInput

    with pytest.raises(ChangedInput):
        overview(schedule.worker, project, output, expected_input=task["input_key"])
    assert output.read_text() == "旧概览保留"
    schedule._finish(queue, owner, "cancelled", {}, "input_changed")
    assert schedule.run(20)["overview_done"] == 3


def test_recovery_owner_append_only_history_health_pagination_and_backup(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    schedule = stages(store, project=project)
    schedule._enqueue(project, "link", None, False)
    queue = tasks(store)[0]["queue_id"]
    _, owner = schedule._claim(queue)
    with pytest.raises(RuntimeError, match="归属"):
        schedule._finish(queue, str(uuid4()), "done", {})
    for sql in (
        "UPDATE pipeline_queue SET input_key='forged'",
        "DELETE FROM pipeline_queue_events",
        "UPDATE pipeline_queue_events SET kind='forged'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(sql)
    result = schedule.run(20)
    assert result["pipeline_recovered"] == 1 and result["link_done"] == 1
    health = store.health(project, limit=1)["pipeline_queue"]
    assert health["total"] == 4 and health["next_offset"] == 1
    assert health["counts"]["done"] == 4
    other = store.project("另一个项目", [])
    assert store.health(other)["pipeline_queue"]["total"] == 0
    destination = tmp_path / "备份"
    backup(store, destination)
    assert (destination / "overviews").exists()
    original = store.root
    store.close()
    shutil.rmtree(original)
    recovered = Store(destination)
    try:
        assert recovered.health()["pipeline_queue"]["total"] == 4
        assert stages(recovered, project=project).run(20).get("link_processed", 0) == 0
    finally:
        recovered.close()


def test_pipeline_watch_and_cli_default_use_all_stages_and_extract_only_remains_available(
    store: Store,
    tmp_path: Path,
    monkeypatch,
):
    project = store.project("合成命令行项目", [])
    add_session(store, tmp_path, project, "first")
    provider = AutomaticProvider()
    provider.close = lambda: None
    monkeypatch.setattr("rg.cli.main.Provider", lambda *args: provider)
    monkeypatch.setattr("rg.cli.main.load_key", lambda *args: "synthetic-only")
    arguments = [
        "extract",
        "--base-url",
        "http://127.0.0.1",
        "--model",
        "synthetic",
        "--project",
        project,
    ]
    assert run(parser().parse_args([*arguments, "--extract-only"]), store)["claims"] == 1
    assert not tasks(store)
    assert run(parser().parse_args(arguments), store)["overview_done"] == 2
    monkeypatch.setattr("rg.extract.queue.time.sleep", lambda _: None)
    watch = Pipeline(Worker(store, provider), project).watch()
    try:
        assert next(watch)["extract"].get("overview_processed", 0) == 0
        add_session(store, tmp_path, project, "second")
        assert next(watch)["extract"]["overview_done"] == 2
    finally:
        watch.close()


def test_missing_or_changed_overview_is_rebuilt_from_cache_and_backup_uses_publish_lock(
    store: Store, tmp_path: Path
):
    project, _, _ = two_objects(store, tmp_path)
    provider = AutomaticProvider()
    schedule = stages(store, provider, project)
    schedule.run(20)
    calls, counted = provider.calls, provider.counts
    output = schedule.destination(project, None)
    output.unlink()
    assert schedule.run(20)["overview_done"] == 1
    assert output.exists() and provider.calls == calls and provider.counts == counted
    output.write_text("不可信的旧内容")
    assert schedule.run(20)["overview_done"] == 1
    assert "不可信的旧内容" not in output.read_text()
    assert provider.calls == calls and provider.counts == counted
    for path in (
        store.root / "locks" / "postprocess.lock",
        store.root
        / "locks"
        / "overviews"
        / (schedule.destination(project, None).parts[-3] + ".lock"),
    ):
        destination = tmp_path / "忙时备份"
        with exclusive(path), pytest.raises(TaskBusy):
            backup(store, destination)
        assert not destination.exists()


def test_publication_receipt_does_not_hash_another_writers_replacement(
    store: Store, tmp_path: Path, monkeypatch
):
    project, _, _ = two_objects(store, tmp_path)
    provider = AutomaticProvider()
    schedule = stages(store, provider, project)

    def replaced(worker, project, destination, *args, **kwargs):
        result = overview(worker, project, destination, *args, **kwargs)
        destination.write_text("模拟锁释放后的另一发布者")
        return result

    monkeypatch.setattr("rg.extract.pipeline.overview", replaced)
    schedule.run(20)
    for task in tasks(store, "overview"):
        receipt = json.loads(task["result"])
        assert receipt["sha256"] != digest(
            schedule.destination(project, task["session_pk"]).read_bytes()
        )
        assert not schedule._published(task)
    calls, counted = provider.calls, provider.counts
    monkeypatch.setattr("rg.extract.pipeline.overview", overview)
    assert schedule.run(20)["overview_done"] == 3
    assert provider.calls == calls and provider.counts == counted
    assert all(schedule._published(task) for task in tasks(store, "overview"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程终止验收")
def test_killed_link_scheduler_recovers_without_losing_unknown_usage(store: Store, tmp_path: Path):
    project, _, _ = two_objects(store, tmp_path)
    code = """
import signal, sys
from pathlib import Path
from rg.store.database import Store
from rg.extract.pipeline import Stages
from rg.extract.worker import Worker
from tests.golden.test_auto_pipeline import AutomaticProvider
class Blocked(AutomaticProvider):
    def generate(self, request):
        print('sent', flush=True)
        signal.pause()
value = Store(Path(sys.argv[1]))
Stages(Worker(value, Blocked()), sys.argv[2], None, 50).run(20)
"""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(store.root), project],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        readable, _, _ = select.select([child.stdout], [], [], 10)
        assert readable and child.stdout.readline().strip() == "sent"
        assert tasks(store)[0]["state"] == "running"
        with pytest.raises(TaskBusy):
            stages(store, project=project).run(20)
    finally:
        child.kill()
        child.communicate(timeout=5)
    before = store.db.execute("SELECT sum(reserved_tokens) FROM daily_usage").fetchone()[0]
    assert before == 1010
    provider = AutomaticProvider()
    schedule = stages(store, provider, project)
    result = schedule.run(20)
    assert result["pipeline_recovered"] == result["link_done"] == 1
    assert result["overview_done"] == 3
    assert (
        store.db.execute("SELECT sum(reserved_tokens) FROM daily_usage").fetchone()[0]
        == before + 120
    )
    assert (
        store.db.execute(
            "SELECT count(*) FROM model_attempts WHERE sent=1 AND input_tokens IS NULL"
        ).fetchone()[0]
        == 1
    )
    assert schedule.run(20).get("link_processed", 0) == 0 and provider.calls == 4


def test_health_pipeline_pagination_is_independent_and_invalid_offsets_fail(
    store: Store, tmp_path: Path
):
    from rg.api.views import health

    project, _, _ = two_objects(store, tmp_path)
    stages(store, project=project).run(20)
    page = health(
        store, {"project": project, "limit": "1", "offset": "17", "pipeline_offset": "2"}, 500000
    )
    assert page["extraction_queue"]["offset"] == page["extraction"]["coverage"]["offset"] == 17
    assert page["pipeline_queue"]["offset"] == 2
    assert len(page["pipeline_queue"]["tasks"]) == 1
    assert page["pipeline_queue"]["next_offset"] == 3
    arguments = [
        "health",
        "--project",
        project,
        "--limit",
        "1",
        "--offset",
        "17",
        "--pipeline-offset",
        "2",
    ]
    cli = run(parser().parse_args(arguments), store)
    assert cli["pipeline_queue"]["tasks"] == page["pipeline_queue"]["tasks"]
    assert cli["extraction_queue"]["offset"] == 17
    assert health(store, {"offset": "3"}, 500000)["pipeline_queue"]["offset"] == 0
    for value in ("-1", "2147483648", "bad", "1.5"):
        with pytest.raises(ValueError):
            health(store, {"pipeline_offset": value}, 500000)
    for value in ("-1", "2147483648"):
        with pytest.raises(ValueError):
            run(parser().parse_args(["health", "--pipeline-offset", value]), store)


@pytest.mark.parametrize("value", [0, -1, 1001])
def test_invalid_link_limit_is_rejected_before_provider(store: Store, value: int):
    with pytest.raises(ValueError):
        run(parser().parse_args(["extract", "--link-limit", str(value)]), store)
    assert not tasks(store)
