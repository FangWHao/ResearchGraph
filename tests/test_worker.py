from __future__ import annotations

import json
from pathlib import Path

from rg.extract.provider import ModelResult
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record


class PipelineProvider(FakeProvider):
    def __init__(self, truncate_first: bool = False, fail_always: bool = False):
        super().__init__(count=10)
        self.truncate_first = truncate_first
        self.fail_always = fail_always

    def generate(self, request):
        self.calls += 1
        if self.fail_always or (self.truncate_first and self.calls == 1):
            return ModelResult("{", 10, 5, "max_tokens")
        if "candidates" in request["schema"]["properties"]:
            events = json.loads(request["input"])
            output = {
                "candidates": [
                    {"event_id": x["event_id"], "byte_range": x["byte_range"], "cue": "decision"}
                    for x in events
                    if "采用" in x["text"] and not x.get("context_only")
                ]
            }
        else:
            content = json.loads(request["input"])
            output = {
                "segment_id": content["segment_id"],
                "claims": [],
                "lookup_terms": [],
                "unresolved": [],
            }
            for window in content["raw_windows"]:
                if window.get("context_only"):
                    continue
                raw = window["raw"].encode()
                quote = "采用方法甲".encode()
                if quote not in raw:
                    continue
                start = raw.index(quote) + window["byte_start"]
                output["claims"].append(
                    {
                        "claim_type": "entity_version",
                        "temp_id": "new:1",
                        "kind": "approach",
                        "label": "方法甲",
                        "content": "方法甲",
                        "scope": {"dataset_version": "data_v2"},
                        "evidence": [
                            {
                                "event_id": window["event_id"],
                                "byte_start": start,
                                "byte_end": start + len(quote),
                                "quote": quote.decode(),
                            }
                        ],
                    }
                )
                break
        return ModelResult(dumps(output), 10, 20, "stop")


def test_pipeline_caches_candidates_and_tracks_coverage(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "input.jsonl"
    lines(path, [record("采用方法甲，仅用于 data_v2。")])
    scan_file(store, path, "claude", project)
    provider = PipelineProvider()
    result = Worker(store, provider).process(1)
    assert result == {"segments": 1, "claims": 1, "manual": 0}
    assert store.health()["pending"] == 0
    calls = provider.calls
    assert Worker(store, provider).process(1)["claims"] == 0
    assert provider.calls == calls
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1


def test_truncation_splits_and_retries_with_no_lost_events(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "input.jsonl"
    lines(
        path,
        [
            record("采用方法甲，仅用于 data_v2。"),
            {
                "type": "assistant",
                "uuid": "a1",
                "sessionId": "demo",
                "message": {"content": "已经安排测试。"},
            },
        ],
    )
    scan_file(store, path, "claude", project)
    provider = PipelineProvider(truncate_first=True)
    result = Worker(store, provider).process(1)
    assert result["claims"] == 1 and result["manual"] == 0
    assert provider.calls >= 4
    assert store.health()["pending"] == 0
    assert (
        store.db.execute(
            "SELECT count(*) FROM extraction_runs WHERE status='truncated'"
        ).fetchone()[0]
        == 1
    )


def test_repeated_failure_enters_manual_with_pending_coverage(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path])
    path = tmp_path / "input.jsonl"
    lines(path, [record("采用方法甲，仅用于 data_v2。")])
    scan_file(store, path, "claude", project)
    result = Worker(store, PipelineProvider(fail_always=True)).process(1)
    assert result["manual"] > 0
    assert store.health()["pending"] > 0
    assert store.db.execute("SELECT max(attempts) FROM jobs").fetchone()[0] == 3
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
