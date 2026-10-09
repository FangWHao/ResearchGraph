from __future__ import annotations

import json
from pathlib import Path

import pytest

from rg.extract.progress import cache_key
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from tests.golden.test_concurrency import OfflineProvider
from tests.golden.test_ingestion import lines, record
from tests.test_worker import PipelineProvider


class RecordingProvider(PipelineProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests: list[dict] = []

    def generate(self, request):
        self.requests.append(json.loads(request["input"]))
        return super().generate(request)


def append(path: Path, records: list[dict]) -> None:
    with path.open("ab") as stream:
        for value in records:
            stream.write(dumps(value).encode() + b"\n")


def setup(store: Store, tmp_path: Path, records: list[dict]) -> tuple[str, Path]:
    project = store.project("合成增量项目", [tmp_path])
    path = tmp_path / "incremental.jsonl"
    lines(path, records)
    scan_file(store, path, "claude", project)
    return project, path


def reply(uuid: str = "reply") -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": "demo",
        "message": {"content": "已经安排测试。"},
    }


def owned_inputs(provider: RecordingProvider) -> list[dict]:
    return [
        item
        for request in provider.requests
        if isinstance(request, list)
        for item in request
        if not item.get("context_only")
    ]


class Interrupted(BaseException):
    """模拟候选事务提交后进程退出，不能被工作器当作模型失败重试。"""


def test_excluded_event_does_not_make_overlap_point_to_older_turn(store: Store, tmp_path: Path):
    project, path = setup(
        store,
        tmp_path,
        [
            record("采用方法甲，较早回合。", uuid="first"),
            record("AGENTS.md <INSTRUCTIONS>合成代理说明</INSTRUCTIONS>", uuid="excluded"),
            record("采用方法甲，最近回合。", uuid="recent"),
        ],
    )
    assert Worker(store, RecordingProvider()).process(1)["claims"] == 2
    append(path, [record("采用方法甲，新回合。", uuid="new")])
    scan_file(store, path, "claude", project)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    inputs = [
        item for request in provider.requests if isinstance(request, list) for item in request
    ]
    assert {item["event_id"] for item in inputs} == {3, 4}
    assert all(item["context_only"] for item in inputs if item["event_id"] == 3)
    assert (
        store.db.execute(
            "SELECT status FROM coverage WHERE event_id=2 AND stage='pass2'"
        ).fetchone()[0]
        == "excluded:agent_instructions"
    )
    assert store.health()["pending"] == 0


def test_appended_reply_does_not_extract_previous_user_again(store: Store, tmp_path: Path):
    project = store.project("合成增量项目", [tmp_path])
    path = tmp_path / "incremental.jsonl"
    lines(path, [record("采用方法甲，仅用于 data_v2。", uuid="original")])
    scan_file(store, path, "claude", project)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    original = store.raw(1)
    provider.requests.clear()
    append(
        path,
        [
            {
                "type": "assistant",
                "uuid": "reply",
                "sessionId": "demo",
                "message": {"content": "已经安排测试。"},
            }
        ],
    )
    scan_file(store, path, "claude", project)
    result = Worker(store, provider).process(1)
    assert result["claims"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    assert store.raw(1) == original and store.health()["pending"] == 0
    owned = [
        item
        for request in provider.requests
        if isinstance(request, list)
        for item in request
        if not item.get("context_only")
    ]
    assert owned and all(item["event_id"] != 1 for item in owned)


def test_appended_turn_uses_old_turn_only_as_context(store: Store, tmp_path: Path):
    project = store.project("合成增量项目", [tmp_path])
    path = tmp_path / "incremental.jsonl"
    lines(path, [record("采用方法甲，仅用于 data_v2。", uuid="original")])
    scan_file(store, path, "claude", project)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    provider.requests.clear()
    append(path, [record("采用方法甲，补充一个独立回合。", uuid="new-turn")])
    scan_file(store, path, "claude", project)
    assert Worker(store, provider).process(1)["claims"] == 1
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    inputs = [
        item for request in provider.requests if isinstance(request, list) for item in request
    ]
    assert any(item["event_id"] == 1 and item["context_only"] for item in inputs)
    assert all(item.get("context_only") for item in inputs if item["event_id"] == 1)


def test_old_history_is_not_loaded_or_counted_again(store: Store, tmp_path: Path, monkeypatch):
    project, path = setup(
        store, tmp_path, [record(f"采用方法甲，第{i}回合。", uuid=str(i)) for i in range(3)]
    )
    assert Worker(store, RecordingProvider()).process(1)["claims"] == 3
    append(path, [record("采用方法甲，最新回合。", uuid="latest")])
    scan_file(store, path, "claude", project)
    raw = store.raw
    loaded: list[int] = []

    def tracking(event_id: int) -> bytes:
        loaded.append(event_id)
        return raw(event_id)

    monkeypatch.setattr(store, "raw", tracking)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    assert set(loaded) == {3, 4}
    assert {item["event_id"] for item in owned_inputs(provider)} == {4}


@pytest.mark.parametrize(
    "text,stage,claims", [("采用方法甲。", "pass2", 1), ("没有候选。", "pass1", 0)]
)
def test_committed_result_recovers_coverage_without_model(
    store: Store, tmp_path: Path, monkeypatch, text: str, stage: str, claims: int
):
    setup(store, tmp_path, [record(text)])
    worker = Worker(store, RecordingProvider())
    coverage = worker._coverage

    def interrupt(item, current_stage, status):
        if current_stage == stage and status == "covered":
            raise Interrupted
        coverage(item, current_stage, status)

    monkeypatch.setattr(worker, "_coverage", interrupt)
    with pytest.raises(Interrupted):
        worker.process(1)
    assert store.health()["pending"] > 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == claims
    attempts = store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]
    assert Worker(store, OfflineProvider()).process(1)["claims"] == 0
    assert store.health()["pending"] == 0
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == attempts
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == claims


def test_split_restart_skips_completed_child_and_preserves_event_order(
    store: Store, tmp_path: Path, monkeypatch
):
    project, path = setup(store, tmp_path, [record("采用方法甲。"), reply()])
    worker = Worker(store, RecordingProvider(truncate_first=True))
    process_item = worker._process_item

    def interrupt(item, *args):
        if item.segment_id.endswith(":b"):
            raise Interrupted
        process_item(item, *args)

    monkeypatch.setattr(worker, "_process_item", interrupt)
    with pytest.raises(Interrupted):
        worker.process(1)
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    append(path, [record("采用方法甲，新增一轮。", uuid="new")])
    scan_file(store, path, "claude", project)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    assert [item["event_id"] for item in owned_inputs(provider)] == [2, 3]
    assert store.health()["pending"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2


def test_manual_plans_need_explicit_retry_and_allow_new_events(store: Store, tmp_path: Path):
    project, path = setup(store, tmp_path, [record("采用方法甲。")])
    failed = Worker(store, RecordingProvider(fail_always=True)).process(1)
    assert failed["manual"] > 0
    jobs = store.db.execute("SELECT count(*) FROM jobs WHERE kind='manual_review'").fetchone()[0]
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["manual"] == failed["manual"]
    assert not provider.requests
    append(path, [record("采用方法甲，新增一轮。", uuid="new")])
    scan_file(store, path, "claude", project)
    result = Worker(store, provider).process(1)
    assert result["claims"] == 1 and result["manual"] == failed["manual"]
    assert {item["event_id"] for item in owned_inputs(provider)} == {2}
    assert store.health()["pending"] > 0
    assert store.db.execute("SELECT count(*) FROM session_results").fetchone()[0] == 0
    result = Worker(store, RecordingProvider()).process(1, retry_failed=True)
    assert result["manual"] == 0 and store.health()["pending"] == 0
    assert (
        store.db.execute("SELECT count(*) FROM jobs WHERE kind='manual_review'").fetchone()[0]
        == jobs
    )
    assert (
        store.db.execute(
            "SELECT count(*) FROM jobs WHERE kind='manual_review' AND state!='done'"
        ).fetchone()[0]
        == 0
    )
    assert (
        store.db.execute("SELECT count(*) FROM model_attempts WHERE status='truncated'").fetchone()[
            0
        ]
        > 0
    )


def test_failed_explicit_retry_keeps_one_queue_record_per_plan(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲。")])
    Worker(store, RecordingProvider(fail_always=True)).process(1)

    # 计数失败不会再次拆片；人工队列只更新原有叶片记录。
    class CountingFailure(RecordingProvider):
        def count_request(self, request):
            from rg.slim.tokens import CountingUnavailable

            raise CountingUnavailable("合成计数失败")

    jobs = store.db.execute("SELECT count(*) FROM jobs WHERE kind='manual_review'").fetchone()[0]
    Worker(store, CountingFailure()).process(1, retry_failed=True)
    assert (
        store.db.execute("SELECT count(*) FROM jobs WHERE kind='manual_review'").fetchone()[0]
        == jobs
    )
    assert store.health()["pending"] > 0


def test_new_event_during_generation_stays_pending_for_next_snapshot(store: Store, tmp_path: Path):
    project, path = setup(store, tmp_path, [record("采用方法甲。")])

    class AppendingProvider(RecordingProvider):
        def generate(self, request):
            if not self.requests:
                append(path, [reply()])
                scan_file(store, path, "claude", project)
            return super().generate(request)

    provider = AppendingProvider()
    assert Worker(store, provider).process(1)["claims"] == 1
    assert {item["event_id"] for item in owned_inputs(provider)} == {1}
    assert store.health()["pending"] == 3
    assert store.db.execute("SELECT count(*) FROM session_results").fetchone()[0] == 0
    assert Worker(store, RecordingProvider()).process(1)["claims"] == 0
    assert store.health()["pending"] == 0


@pytest.mark.parametrize("move_project", [False, True])
def test_legacy_prefix_requires_exact_config_and_project(
    store: Store, tmp_path: Path, move_project: bool
):
    project, path = setup(store, tmp_path, [record("采用方法甲。")])
    provider = RecordingProvider()
    worker = Worker(store, provider)
    assert worker.process(1)["claims"] == 1
    snapshot = [dict(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY seq")]
    legacy_key = cache_key(1, project, worker._definition(1, None), snapshot, legacy=True)
    statuses = [tuple(row) for row in store.db.execute("SELECT * FROM coverage ORDER BY stage")]
    # 合成数据库回到 v4 的元数据状态，保留完整响应、尝试和覆盖证明。
    store.db.execute("DELETE FROM extraction_plans")
    store.db.execute("DELETE FROM session_results")
    store.db.execute("INSERT INTO session_results VALUES (?,1,1,'2026-10-09')", (legacy_key,))
    if move_project:
        project = store.project("另一合成项目", [tmp_path / "other"])
        store.db.execute("UPDATE sessions SET project_id=? WHERE session_pk=1", (project,))
    append(path, [reply()])
    scan_file(store, path, "claude", project)
    provider.requests.clear()
    result = Worker(store, provider).process(1)
    legacy = store.db.execute(
        "SELECT count(*) FROM extraction_plans WHERE outcome='legacy'"
    ).fetchone()[0]
    if move_project:
        assert legacy == 0 and result["claims"] == 1
        assert (
            store.db.execute(
                "SELECT count(*) FROM claims c JOIN entities e USING(entity_id) "
                "WHERE e.project_id=?",
                (project,),
            ).fetchone()[0]
            == 1
        )
    else:
        assert legacy == 1 and result["claims"] == 0
        assert {item["event_id"] for item in owned_inputs(provider)} == {2}
        assert [
            tuple(row)
            for row in store.db.execute("SELECT * FROM coverage WHERE event_id=1 ORDER BY stage")
        ] == statuses
    assert store.health()["pending"] == 0


def test_changed_budget_replans_without_pending_from_old_config(store: Store, tmp_path: Path):
    setup(store, tmp_path, [record("采用方法甲。")])
    assert Worker(store, RecordingProvider(fail_always=True)).process(1)["manual"] > 0
    provider = RecordingProvider()
    assert Worker(store, provider, input_budget=256000).process(1)["claims"] == 1
    assert store.health()["pending"] == 0
    assert (
        store.db.execute("SELECT count(DISTINCT config_key) FROM extraction_plans").fetchone()[0]
        == 2
    )
    # 旧配置的失败尝试和人工队列保留，不假称旧窗口已经成功。
    assert (
        store.db.execute("SELECT count(*) FROM extraction_plans WHERE state='manual'").fetchone()[0]
        > 0
    )
    assert (
        store.db.execute("SELECT count(*) FROM model_attempts WHERE status='truncated'").fetchone()[
            0
        ]
        > 0
    )


def test_appended_tool_result_keeps_old_call_as_context(store: Store, tmp_path: Path):
    tool = {
        "type": "assistant",
        "uuid": "call",
        "sessionId": "demo",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "合成命令，只当资料"},
                }
            ]
        },
    }
    project, path = setup(store, tmp_path, [record("采用方法甲。"), tool])
    assert Worker(store, RecordingProvider()).process(1)["claims"] == 1
    result = {
        "type": "user",
        "uuid": "result",
        "sessionId": "demo",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "测试通过"}]
        },
    }
    append(path, [result])
    scan_file(store, path, "claude", project)
    provider = RecordingProvider()
    assert Worker(store, provider).process(1)["claims"] == 0
    inputs = [
        item for request in provider.requests if isinstance(request, list) for item in request
    ]
    assert any(item["event_id"] == 2 and item["context_only"] for item in inputs)
    assert {item["event_id"] for item in owned_inputs(provider)} == {3}
    assert store.health()["pending"] == 0


def test_long_event_partial_restart_keeps_unfinished_byte_windows(
    store: Store, tmp_path: Path, monkeypatch
):
    setup(store, tmp_path, [record("采用方法甲。" + "合成续文" * 700)])
    worker = Worker(store, RecordingProvider(), input_budget=14000)
    process_item = worker._process_item
    completed_end = 0

    def interrupt(item, *args):
        nonlocal completed_end
        if completed_end:
            raise Interrupted
        process_item(item, *args)
        completed_end = item.events[-1]["raw_end"]

    monkeypatch.setattr(worker, "_process_item", interrupt)
    with pytest.raises(Interrupted):
        worker.process(1)
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    assert store.health()["pending"] == 2
    provider = RecordingProvider()
    assert Worker(store, provider, input_budget=14000).process(1)["claims"] == 0
    assert store.health()["pending"] == 0
    assert all(item["byte_range"][0] >= completed_end for item in owned_inputs(provider))
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    assert (
        store.db.execute(
            "SELECT status FROM coverage WHERE event_id=1 AND stage='pass2'"
        ).fetchone()[0]
        == "covered"
    )
