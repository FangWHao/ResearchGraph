from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rg.extract.provider import ModelResult
from rg.extract.redact import redact
from rg.extract.segmenter import Segment, segment
from rg.extract.validate import InvalidClaim, persist, validate
from rg.extract.worker import Worker
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.slim.slimmer import slim_session
from rg.slim.tokens import BudgetExceeded
from rg.store.database import ConflictError, Store, dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record


def setup(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path / "project"])
    path = tmp_path / "input.jsonl"
    lines(path, [record("采用方法甲，用于 data_v2；回退代码后方法继续采用。")])
    scan_file(store, path, "claude", project)
    raw = store.raw(1)
    quote = "采用方法甲".encode()
    start = raw.index(quote)
    evidence = {
        "event_id": 1,
        "byte_start": start,
        "byte_end": start + len(quote),
        "quote": quote.decode(),
    }
    result = {
        "segment_id": "test-segment",
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": "approach",
                "label": "方法甲",
                "content": "在 data_v2 使用方法甲",
                "scope": {"dataset_version": "data_v2"},
                "evidence": [evidence],
            },
            {
                "claim_type": "decision_event",
                "target": "new:1",
                "action": "accepted",
                "reason": "用户明确采用",
                "speaker": "user",
                "explicitness": "explicit",
                "referent_unique": True,
                "scope": {"dataset_version": "data_v2"},
                "evidence": [evidence],
            },
        ],
        "lookup_terms": [],
        "unresolved": [],
    }
    run = store.db.execute(
        "INSERT INTO extraction_runs "
        "(job_key, stage, input_event_ids, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("test", "pass2", "[1]", "pending", now()),
    ).lastrowid
    return project, result, run


def test_model_output_candidates_and_review_conflicts(store: Store, tmp_path: Path):
    project, output, run = setup(store, tmp_path)
    ids = persist(store, output, project, run, set(), {1}, "test-segment")
    assert [store.claim_state(x) for x in ids] == ["candidate", "candidate"]
    before = store.revision()
    store.review(ids[1], "confirm", "human:tester", before)
    assert store.claim_state(ids[1]) == "confirmed"
    assert (
        store.db.execute("SELECT claim_state FROM claims WHERE claim_id = ?", (ids[1],)).fetchone()[
            0
        ]
        == "candidate"
    )
    with pytest.raises(ConflictError):
        store.review(ids[0], "confirm", "human:tester", before)
    with pytest.raises(ValueError):
        store.review(ids[0], "confirm", "model:pretend", store.revision())


def test_db_blocks_model_confirmation(store: Store):
    with pytest.raises(sqlite3.IntegrityError, match="candidates"):
        store.db.execute(
            "INSERT INTO claims "
            "(claim_type, payload, basis, actor, claim_state, recorded_at) "
            "VALUES ('merge', '{}', 'model_inference', 'model:1', 'confirmed', ?)",
            (now(),),
        )


@pytest.mark.parametrize(
    "bad", ["offset", "quote", "hash", "event", "target", "scope", "direction", "l1"]
)
def test_invalid_claims_write_nothing(store: Store, tmp_path: Path, bad: str):
    project, output, run = setup(store, tmp_path)
    span = output["claims"][0]["evidence"][0]
    if bad == "offset":
        span["byte_end"] = 999999
    elif bad == "quote":
        span["quote"] = "伪造原话"
    elif bad == "hash":
        span["quote_sha256"] = "0" * 64
    elif bad == "event":
        span["event_id"] = 900
    elif bad == "target":
        output["claims"][1]["target"] = "不存在的对象"
    elif bad == "scope":
        output["claims"][0]["scope"] = {}
    elif bad == "l1":
        output["claims"].append(
            {
                "claim_type": "relation",
                "source": "new:1",
                "target": "new:1",
                "relation": "produces",
                "scope": {"dataset_version": "data_v2"},
                "evidence": [span],
            }
        )
    else:
        output["claims"].append(
            {
                "claim_type": "relation",
                "source": "new:1",
                "target": "new:1",
                "relation": "supports",
                "scope": {"dataset_version": "data_v2"},
                "evidence": [span],
            }
        )
    with pytest.raises(InvalidClaim):
        persist(store, output, project, run, set(), {1}, "test-segment")
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM entities").fetchone()[0] == 0


def test_utf8_and_private_spans(store: Store, tmp_path: Path):
    project, output, _ = setup(store, tmp_path)
    span = output["claims"][0]["evidence"][0]
    span["byte_start"] += 1
    with pytest.raises(InvalidClaim):
        validate(store, output, project, set(), {1}, "test-segment")
    source = "电话 13812345678，邮箱 alice@example.org，密钥 sk-syntheticsecret123".encode()
    masked = redact(source)
    assert len(masked.data) == len(source)
    assert b"13812345678" not in masked.data
    assert b"alice@example.org" not in masked.data
    assert b"sk-synthetic" not in masked.data
    assert masked.original_span(0, len("电话".encode())) == (0, len("电话".encode()))
    with pytest.raises(ValueError):
        masked.original_span(source.index(b"138"), source.index(b"138") + 11)


def test_remote_permission_blocks_even_token_count(store: Store, tmp_path: Path):
    setup(store, tmp_path)
    provider = FakeProvider()
    provider.remote = True
    with pytest.raises(PermissionError):
        Worker(store, provider).process(1)
    assert provider.counts == 0 and provider.calls == 0


def test_over_budget_not_sent_and_logged(store: Store, tmp_path: Path):
    project, _, _ = setup(store, tmp_path)
    provider = FakeProvider(count=128001)
    item = Segment("over", [{"event_id": 1}])
    with pytest.raises(BudgetExceeded):
        Worker(store, provider).invoke("pass1", item, project, "合成资料", {"type": "object"}, [])
    assert provider.calls == 0
    assert (
        store.db.execute("SELECT status FROM extraction_runs WHERE stage='pass1'").fetchone()[0]
        == "over_budget"
    )


def test_daily_budget_blocks_send(store: Store, tmp_path: Path):
    project, _, _ = setup(store, tmp_path)
    provider = FakeProvider(count=10)
    with pytest.raises(BudgetExceeded):
        Worker(store, provider, daily_budget=20).invoke(
            "pass1", Segment("day", [{"event_id": 1}]), project, "test", {"type": "object"}, []
        )
    assert provider.calls == 0


@pytest.mark.parametrize(
    "reason,contaminated,status",
    [
        ("max_tokens", False, "truncated"),
        ("tool_use", True, "contaminated"),
        ("stop", False, "truncated"),
    ],
)
def test_bad_model_output_discarded(
    store: Store, tmp_path: Path, reason: str, contaminated: bool, status: str
):
    project, _, _ = setup(store, tmp_path)
    provider = FakeProvider([ModelResult("{", 10, 10, reason, contaminated)], count=10)
    with pytest.raises(InvalidClaim):
        Worker(store, provider).invoke(
            "pass1", Segment("bad", [{"event_id": 1}]), project, "test", {"type": "object"}, []
        )
    row = store.db.execute(
        "SELECT status, output_json FROM extraction_runs WHERE stage='pass1'"
    ).fetchone()
    assert tuple(row) == (status, None)
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_large_tool_output_summary_and_call_pair(store: Store, tmp_path: Path):
    path = tmp_path / "big.jsonl"
    lines(
        path,
        [
            record("采用甲"),
            {
                "type": "assistant",
                "uuid": "call",
                "sessionId": "demo",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "big",
                            "name": "Bash",
                            "input": {"command": "test"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "uuid": "result",
                "sessionId": "demo",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "big", "content": "x" * 1100000}
                    ]
                },
            },
        ],
    )
    scan_file(store, path, "claude")
    events = slim_session(store, 1, FakeProvider())
    output = next(x for x in events if x["kind"] == "tool_result")
    assert len(output["text"]) < 3000
    assert json.loads(output["text"])["bytes"] == 1100000
    items = segment(events, FakeProvider(), budget=4000)
    calls = [x for item in items for x in [item.ids] if 2 in x]
    assert len(calls) == 1 and 3 in calls[0]
    assert {x for item in items for x in item.ids} == {1, 2, 3}


def test_500kb_conversation_coverage(store: Store, tmp_path: Path):
    path = tmp_path / "long.jsonl"
    lines(path, [record("正文" * 1000, f"u{i}") for i in range(100)])
    assert path.stat().st_size > 500000
    scan_file(store, path, "claude")
    events = slim_session(store, 1, FakeProvider())
    items = segment(events, FakeProvider(), budget=16000)
    assert {x for item in items for x in item.ids} == {x["event_id"] for x in events}
    assert all(FakeProvider().count_text(dumps(item.events)) <= 16000 for item in items)


def test_working_set_budget_and_reextraction_keeps_human(store: Store, tmp_path: Path):
    project, output, run = setup(store, tmp_path)
    ids = persist(store, output, project, run, set(), {1}, "test-segment")
    store.review(ids[1], "confirm", "human:tester", store.revision())
    working = working_set(store, project, "方法甲", FakeProvider(), budget=3000)
    assert working[0]["state"] == "accepted"
    assert len(dumps(working).encode()) <= 3000
    output["claims"] = [output["claims"][1]]
    output["claims"][0]["target"] = working[0]["id"]
    output["claims"][0]["action"] = "withdrawn"
    new_ids = persist(store, output, project, run, {working[0]["id"]}, {1}, "test-segment")
    assert store.claim_state(new_ids[0]) == "candidate"
    assert store.claim_state(ids[1]) == "confirmed"
    assert working_set(store, project, "方法甲", FakeProvider())[0]["state"] == "accepted"


def test_missing_tool_return_is_preserved(store: Store, tmp_path: Path):
    path = tmp_path / "missing.jsonl"
    lines(
        path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "missing",
                    "arguments": "{}",
                },
            }
        ],
    )
    scan_file(store, path, "codex")
    events = slim_session(store, 1, FakeProvider())
    assert len(events) == 1 and events[0]["kind"] == "tool_call"
    assert "failed" not in events[0]["text"]
