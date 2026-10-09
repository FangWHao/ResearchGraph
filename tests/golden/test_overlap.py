from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rg.extract.segmenter import Segment, segment, split
from rg.extract.validate import InvalidClaim, persist
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup
from tests.golden.test_ingestion import lines, record
from tests.test_worker import PipelineProvider


def event(number: int, kind: str, text: str, call_id: str | None = None):
    return {
        "event_id": number,
        "kind": kind,
        "text": text,
        "call_id": call_id,
        "raw_start": 0,
        "raw_end": len(text.encode()),
        "raw_text": text,
    }


def test_previous_turn_is_context_and_not_new_coverage():
    events = [
        event(1, "user_msg", "采用方法甲"),
        event(2, "tool_call", "运行测试", "call-a"),
        event(3, "tool_result", "测试通过", "call-a"),
        event(4, "assistant_msg", "已记录"),
        event(5, "user_msg", "先暂停它"),
    ]
    items = segment(events, FakeProvider())
    assert [item.ids for item in items] == [[1, 2, 3, 4], [5]]
    assert [e["event_id"] for e in items[1].context_events] == [1, 2, 3, 4]
    assert items[1].input_ids == [1, 2, 3, 4, 5]
    assert all(e["context_only"] for e in items[1].input_events[:-1])
    assert not items[1].input_events[-1]["context_only"]


def test_long_turn_keeps_all_owned_bytes_and_records_missing_context():
    source = "正文" * 5000 + "最后撤回方法甲"
    items = segment([event(1, "user_msg", source)], FakeProvider(), budget=16000)
    pieces = [e for item in items for e in item.events]
    assert "".join(e["raw_text"] for e in pieces) == source
    assert len(items) > 1 and any(item.context_gaps for item in items[1:])
    assert all(FakeProvider().count_text(item.content()) <= 16000 for item in items)
    assert all(
        a["raw_end"] == b["raw_start"] for a, b in zip(pieces, pieces[1:], strict=False)
    )


def test_full_previous_turn_is_not_silently_truncated_when_it_cannot_fit():
    items = segment(
        [
            event(1, "user_msg", "前文" * 6000),
            event(2, "user_msg", "继续"),
        ],
        FakeProvider(),
        budget=16000,
    )
    final = items[-1]
    assert final.ids == [2]
    assert "".join(e["raw_text"] for e in final.context_events) == "前文" * 6000
    assert FakeProvider().count_text(final.content()) > 16000


def test_retry_preserves_context_and_call_pairs():
    previous = event(1, "user_msg", "刚才采用方法甲")
    owned = [
        event(2, "assistant_msg", "开始验证"),
        event(3, "tool_call", "调用 A", "a"),
        event(4, "tool_call", "调用 B", "b"),
        event(5, "tool_result", "结果 A", "a"),
        event(6, "tool_result", "结果 B", "b"),
        event(7, "assistant_msg", "已完成"),
    ]
    children = split(Segment("retry", owned, [previous]))
    assert len(children) == 2
    assert all(previous in child.context_events for child in children)
    assert all({3, 5}.issubset(child.ids) or {3, 5}.isdisjoint(child.ids) for child in children)
    assert all({4, 6}.issubset(child.ids) or {4, 6}.isdisjoint(child.ids) for child in children)
    assert {3, 4, 5, 6}.issubset(e["event_id"] for e in children[1].context_events)


def test_each_claim_needs_current_bytes_even_when_event_id_is_shared(
    store: Store,
    tmp_path: Path,
):
    project, output, run = setup(store, tmp_path)
    evidence = output["claims"][0]["evidence"][0]
    owned = (evidence["byte_start"], evidence["byte_end"])
    output["claims"][1]["evidence"] = [
        {
            "event_id": 1,
            "byte_start": 0,
            "byte_end": 1,
            "quote": "{",
        }
    ]
    before = deepcopy(output)
    with pytest.raises(InvalidClaim, match="重叠上下文"):
        persist(
            store,
            output,
            project,
            run,
            set(),
            {1},
            "test-segment",
            {1: [(0, 1), owned]},
            {1: [owned]},
        )
    assert output == before
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_worker_overlap_does_not_replay_claims_or_cover_old_events_twice(
    store: Store,
    tmp_path: Path,
):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "two-turns.jsonl"
    lines(path, [record("采用方法甲", "u1"), record("继续验证", "u2")])
    scan_file(store, path, "claude", project)
    provider = PipelineProvider()
    result = Worker(store, provider).process(1)
    assert result == {"segments": 2, "claims": 1, "manual": 0}
    assert provider.calls == 3
    assert store.health()["pending"] == 0
    assert (
        store.db.execute(
            "SELECT count(*) FROM segment_coverage WHERE event_id=1 AND stage='pass2'"
        ).fetchone()[0]
        == 1
    )


def test_overlap_that_cannot_fit_stays_pending_in_manual_queue(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "oversized-context.jsonl"
    lines(path, [record("前文" * 6000, "u1"), record("继续", "u2")])
    scan_file(store, path, "claude", project)
    result = Worker(store, PipelineProvider(), input_budget=24000).process(1)
    assert result["manual"] > 0
    assert store.db.execute(
        "SELECT status FROM coverage WHERE event_id=2 AND stage='pass2'"
    ).fetchone()[0] == "pending"
    assert store.db.execute("SELECT count(*) FROM session_results").fetchone()[0] == 0
    assert store.db.execute(
        "SELECT count(*) FROM extraction_runs WHERE status='over_budget'"
    ).fetchone()[0] > 0
