from __future__ import annotations

import copy
from pathlib import Path

import pytest

from rg.extract.monitor import monitor, set_status
from rg.extract.provider import ModelResult
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidCitation, persist
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.slim.tokens import BudgetExceeded, CountingUnavailable
from rg.store.database import Store, dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup
from tests.golden.test_ingestion import lines, record


def invoke(worker: Worker, project: str, identity: str = "meter"):
    return worker.invoke(
        "pass2", Segment(identity, [{"event_id": 1}]), project, "合成正文", {"type": "object"}, []
    )


def test_retry_keeps_bad_quote_attempt_and_usage_without_double_charging_cache(
    store: Store,
    tmp_path: Path,
):
    project, output, _ = setup(store, tmp_path)
    output = copy.deepcopy(output)
    provider = FakeProvider(count=100, results=[ModelResult(dumps(output), 80, 20, "stop")] * 2)
    worker = Worker(store, provider)
    run, bad = invoke(worker, project)
    bad["claims"][0]["evidence"][0]["quote"] = "错误引用"
    with pytest.raises(InvalidCitation):
        persist(
            store, bad, project, run, set(), {1}, "test-segment", attempt_id=worker.attempt_ids[run]
        )
    run2, good = invoke(worker, project)
    assert run2 == run
    persist(
        store, good, project, run, set(), {1}, "test-segment", attempt_id=worker.attempt_ids[run]
    )
    before = provider.counts
    invoke(worker, project)
    assert provider.calls == 2 and provider.counts == before
    rows = store.db.execute("SELECT * FROM model_attempts ORDER BY attempt_id").fetchall()
    assert [row["status"] for row in rows] == ["invalid", "ok"]
    assert rows[0]["failure_kind"] == "citation" and rows[0]["finished_at"]
    view = monitor(store)
    stage = view["stages"][0]
    assert stage["citation_failures"] == 1 and stage["validation_rejection_rate"] == 0.5
    assert view["daily_usage"]["reserved_or_settled_tokens"] == 200
    assert view["daily_usage"]["known_input_tokens"] == 160


def test_interleaved_workers_finish_their_own_attempts(store: Store, tmp_path: Path):
    project, output, _ = setup(store, tmp_path)
    good_provider = FakeProvider(count=100, results=[ModelResult(dumps(output), 80, 20, "stop")])
    other = Worker(store, good_provider)

    class Interleaved(FakeProvider):
        def generate(self, request):
            run, good = invoke(other, project)
            persist(
                store,
                good,
                project,
                run,
                set(),
                {1},
                "test-segment",
                attempt_id=other.attempt_ids[run],
            )
            bad = copy.deepcopy(output)
            bad["claims"][0]["evidence"][0]["quote"] = "合成错误原话"
            return ModelResult(dumps(bad), 80, 20, "stop")

    worker = Worker(store, Interleaved(count=100))
    run, bad = invoke(worker, project)
    with pytest.raises(InvalidCitation):
        persist(
            store, bad, project, run, set(), {1}, "test-segment", attempt_id=worker.attempt_ids[run]
        )
    rows = store.db.execute("SELECT status,failure_kind FROM model_attempts ORDER BY attempt_id")
    assert [tuple(row) for row in rows] == [("invalid", "citation"), ("ok", None)]
    assert monitor(store)["daily_usage"]["reserved_or_settled_tokens"] == 200


@pytest.mark.parametrize("case", ["daily", "input", "count", "provider", "unknown_usage"])
def test_unsent_pauses_and_unknown_usage_are_not_reported_as_zero_cost(
    store: Store,
    tmp_path: Path,
    case: str,
):
    project, _, _ = setup(store, tmp_path)

    class Counter(FakeProvider):
        def count_request(self, request):
            if case == "count":
                raise CountingUnavailable("合成计数故障")
            return super().count_request(request)

    provider = Counter(count=128001 if case == "input" else 100)
    if case == "unknown_usage":
        provider.results = [ModelResult("{}", -1, -1, "stop")]
    worker = Worker(store, provider, daily_budget=1 if case == "daily" else 500000)
    if case == "unknown_usage":
        run, _ = invoke(worker, project)
        set_status(store, run, "ok", attempt_id=worker.attempt_ids[run])
    else:
        with pytest.raises((RuntimeError, BudgetExceeded)):
            invoke(worker, project)
    row = store.db.execute("SELECT * FROM model_attempts").fetchone()
    assert row["sent"] == int(case in {"provider", "unknown_usage"})
    assert row["input_tokens"] is None and row["output_tokens"] is None
    assert (
        row["status"]
        == {
            "daily": "budget_paused",
            "input": "over_budget",
            "count": "failed",
            "provider": "failed",
            "unknown_usage": "ok",
        }[case]
    )
    view = monitor(store)
    assert view["daily_usage"]["unsettled_sent_attempts"] == row["sent"]
    assert view["daily_usage"]["reserved_or_settled_tokens"] == (4100 if row["sent"] else 0)
    codes = {alert["code"] for alert in view["alerts"]}
    if case == "daily":
        assert "budget_paused" in codes and "over_budget" not in codes


def sample(store: Store, project: str, index: int, tokens: int, state: str):
    run = store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,created_at) "
        "VALUES (?,'pass2','[]',?,?)",
        (str(index), state, now()),
    ).lastrowid
    store.db.execute(
        "INSERT INTO model_attempts(extraction_run_id,project_id,stage,provider,model,segment_id, "
        "input_budget,output_budget,measured_input_tokens,input_tokens,output_tokens,sent,status, "
        "created_at) VALUES (?,?,'pass2','test','test',?,100,10,?,?,1,1,?,?)",
        (run, project, str(index), tokens, tokens, state, now()),
    )


def test_alerts_are_strict_at_80_10_and_5_percent_and_scope_stays_separate(
    store: Store,
    tmp_path: Path,
):
    first = store.project("合成边界项目", [tmp_path / "a"])
    other = store.project("合成其他项目", [tmp_path / "b"])
    for index in range(20):
        sample(store, first, index, 81 if index < 2 else 80, "invalid" if index == 0 else "ok")
    view = monitor(store, first)
    assert not view["alerts"]  # 恰好 10% 与 5%，不报警；80% 本身不计入高利用率。
    assert view["stages"][0]["over_80_percent"] == 2
    sample(store, other, 20, 100, "invalid")
    assert not monitor(store, first)["alerts"]
    global_view = monitor(store)
    assert {a["code"] for a in global_view["alerts"]} == {
        "input_utilization",
        "validation_rejections",
    }


def test_pending_coverage_paginates_with_no_raw_text_and_without_mutation(
    store: Store,
    tmp_path: Path,
):
    project = store.project("合成覆盖项目", [tmp_path])
    for index in range(3):
        path = tmp_path / f"source{index}.jsonl"
        lines(path, [record("合成原话，不应出现在健康输出", f"u{index}", sessionId=f"s{index}")])
        scan_file(store, path, "claude", project)
    store.db.execute("UPDATE coverage SET status='excluded:test' WHERE stage!='pass2'")
    revision = store.revision()
    pages = [monitor(store, project, limit=1, offset=i)["coverage"] for i in range(3)]
    assert [page["next_offset"] for page in pages] == [1, 2, None]
    assert len({page["gaps"][0]["session_pk"] for page in pages}) == 3
    assert [page["gaps"][0]["event_id"] for page in pages] == [1, 2, 3]
    assert all(page["pending_event_stages"] == 3 for page in pages)
    assert "合成原话" not in dumps(pages) and store.revision() == revision
    assert store.health()["pending"] == 3


def test_retries_do_not_turn_one_hot_segment_into_many_hot_segments(store: Store, tmp_path: Path):
    project = store.project("合成重试占比项目", [tmp_path])
    for index in range(30):
        sample(store, project, index, 90 if index == 0 or index >= 20 else 80, "ok")
        if index >= 20:
            store.db.execute(
                "UPDATE model_attempts SET segment_id='0' WHERE attempt_id=?", (index + 1,)
            )
    view = monitor(store)
    stage = view["stages"][0]
    assert stage["over_80_percent_ratio"] > 0.1
    assert stage["measured_segments"] == 20
    assert stage["over_80_percent_segment_ratio"] == 0.05
    assert "input_utilization" not in {alert["code"] for alert in view["alerts"]}


def test_missing_legacy_history_and_incomplete_attempt_never_imply_success(
    store: Store,
    tmp_path: Path,
):
    project, _, _ = setup(store, tmp_path)
    provider = FakeProvider(count=100, results=[ModelResult("{}", 90, 10, "stop")])
    invoke(Worker(store, provider), project)
    view = monitor(store)
    assert view["legacy_runs_without_attempt_history"] == 1
    assert view["stages"][0]["statuses"] == {"validated": 1}
    assert view["stages"][0]["validation_rejection_rate"] is None


def test_midnight_counting_is_billed_on_reservation_day(store: Store, tmp_path: Path, monkeypatch):
    project, _, _ = setup(store, tmp_path)
    times = iter(["2026-01-01T23:59:59+00:00"] * 3)
    monkeypatch.setattr("rg.extract.worker.now", lambda: next(times, "2026-01-02T00:00:01+00:00"))
    provider = FakeProvider(count=100, results=[ModelResult("{}", 80, 20, "stop")])
    worker = Worker(store, provider)
    run, _ = invoke(worker, project)
    set_status(store, run, "ok", attempt_id=worker.attempt_ids[run])
    assert monitor(store, day="2026-01-01")["stages"] == []
    assert monitor(store, day="2026-01-02")["daily_usage"]["reserved_or_settled_tokens"] == 100
    assert monitor(store, day="2026-01-02")["stages"][0]["sent"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"day": "2026-1-1"},
        {"daily_budget": 0},
        {"offset": -1},
        {"limit": 1001},
        {"project": "不存在"},
    ],
)
def test_bad_monitor_arguments_are_rejected(store: Store, kwargs):
    with pytest.raises(ValueError):
        monitor(store, **kwargs)
