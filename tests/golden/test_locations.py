from __future__ import annotations

import json
from pathlib import Path

import pytest

from rg.extract.locator import positions, units, windows
from rg.extract.provider import ModelResult
from rg.extract.redact import redact
from rg.extract.segmenter import segment
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.ingest.spans import decoded_range, event_span
from rg.slim.slimmer import slim_session
from rg.store.database import Store, dumps
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record
from tests.test_worker import PipelineProvider


def test_duplicate_blocks_have_distinct_original_positions(store: Store, tmp_path: Path):
    path = tmp_path / "blocks.jsonl"
    lines(
        path,
        [
            record(
                "",
                message={
                    "content": [
                        {"type": "text", "text": "采用方法甲"},
                        {"type": "text", "text": "采用方法甲"},
                    ]
                },
            )
        ],
    )
    scan_file(store, path, "claude")
    events = slim_session(store, 1, FakeProvider())
    assert len(events) == 2
    assert events[0]["raw_end"] < events[1]["raw_start"]
    assert all(
        store.raw(e["event_id"])[e["raw_start"] : e["raw_end"]].decode() == "采用方法甲"
        for e in events
    )


def test_escaped_unicode_and_surrogate_pair_map_to_original_bytes():
    raw = json.dumps(record("🙂甲\n采用方法甲"), ensure_ascii=True).encode()
    span = event_span(raw, "claude", 0)
    assert span is not None
    start, end, _ = span
    a, b = decoded_range(raw, start, end, 0, 2)
    assert raw[a:b] == b"\\ud83d\\ude42\\u7532"
    a, b = decoded_range(raw, start, end, 3, 8)
    assert json.loads('"' + raw[a:b].decode() + '"') == "采用方法甲"


def test_duplicate_json_keys_follow_the_parser_last_value():
    raw = b'{"message":{"content":"old","content":"new"},"type":"user"}'
    span = event_span(raw, "claude", 0)
    assert span and raw[span[0] : span[1]] == b"new"


def test_encoded_rg_context_is_not_exposed_or_citable(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "encoded.jsonl"
    raw = json.dumps(
        record("采用方法甲。<rg-context>采用方法乙。</rg-context>后续验证。"), ensure_ascii=True
    ).encode()
    path.write_bytes(raw + b"\n")
    scan_file(store, path, "claude", project)
    item = segment(slim_session(store, 1, FakeProvider()), FakeProvider())[0]
    input_units = units(store, item, FakeProvider())
    assert "方法乙" not in dumps(input_units)
    located = positions(input_units, [])
    raw_input, _, owned = windows(store, item, input_units, located)
    protected = redact(raw).private_ranges
    assert protected and raw_input and owned
    assert all(
        not (a < window["byte_end"] and b > window["byte_start"])
        for a, b in protected
        for window in raw_input
    )


def test_rule_recall_has_real_byte_positions_when_model_returns_none(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "rule.jsonl"
    lines(path, [record("选 B。")])
    scan_file(store, path, "claude", project)
    result = Worker(store, PipelineProvider()).process(1)
    assert result["manual"] == 0
    locations = store.db.execute("SELECT * FROM candidate_locations").fetchall()
    assert locations and all(row["source"] == "rule" for row in locations)
    assert all(
        store.raw(row["event_id"])[row["byte_start"] : row["byte_end"]].decode() == "选 B。"
        for row in locations
    )


def test_model_cannot_invent_or_shift_a_registered_window(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "bad-position.jsonl"
    lines(path, [record("采用方法甲。")])
    scan_file(store, path, "claude", project)

    class Shifted(PipelineProvider):
        def generate(self, request):
            self.calls += 1
            source = json.loads(request["input"])[0]
            a, b = source["byte_range"]
            return ModelResult(
                dumps(
                    {
                        "candidates": [
                            {
                                "event_id": source["event_id"],
                                "byte_range": [a + 1, b],
                                "cue": "decision",
                            }
                        ]
                    }
                ),
                10,
                20,
                "stop",
            )

    result = Worker(store, Shifted()).process(1)
    assert result["manual"] > 0 and store.health()["pending"] > 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM candidate_locations").fetchone()[0] == 0
    assert (
        store.db.execute("SELECT count(*) FROM extraction_runs WHERE status='invalid'").fetchone()[
            0
        ]
        > 0
    )


def test_only_candidate_body_window_is_returned_to_pass2(store: Store, tmp_path: Path):
    path = tmp_path / "selected.jsonl"
    lines(path, [record("背景描述。采用方法甲。后续说明。")])
    scan_file(store, path, "claude")
    item = segment(slim_session(store, 1, FakeProvider()), FakeProvider())[0]
    input_units = units(store, item, FakeProvider())
    raw_input, _, _ = windows(store, item, input_units, positions(input_units, []))
    assert [w["raw"] for w in raw_input] == ["采用方法甲。"]


def test_one_mb_tool_result_tail_is_located_without_full_output(store: Store, tmp_path: Path):
    path = tmp_path / "tail.jsonl"
    lines(
        path,
        [
            record("检查结果", "u1"),
            {
                "type": "assistant",
                "sessionId": "demo",
                "uuid": "call",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "a",
                            "name": "Bash",
                            "input": {"command": "不执行的测试文字"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "sessionId": "demo",
                "uuid": "result",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "a",
                            "content": "x" * 1100000 + "\nERROR: 尾部错误",
                        },
                    ]
                },
            },
        ],
    )
    scan_file(store, path, "claude")
    item = segment(slim_session(store, 1, FakeProvider()), FakeProvider())[0]
    input_units = units(store, item, FakeProvider())
    assert len(dumps(input_units)) < 10000
    errors = [u for u in input_units if "尾部错误" in u["text"]]
    assert errors and all(u["byte_range"][0] > 1000000 for u in errors)
    raw_input, _, _ = windows(store, item, input_units, positions(input_units, []))
    assert any("尾部错误" in w["raw"] for w in raw_input)
    assert all(FakeProvider().count_text(w["raw"]) <= 2000 for w in raw_input)


def test_context_positions_never_become_current_candidates():
    source = [{"event_id": 1, "byte_range": [10, 20], "text": "采用甲", "context_only": True}]
    assert positions(source, [{"event_id": 1, "byte_range": [10, 20], "cue": "decision"}]) == []
    with pytest.raises(ValueError):
        positions(source, [{"event_id": 1, "byte_range": [9, 20], "cue": "decision"}])
