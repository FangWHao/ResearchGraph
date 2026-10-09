from __future__ import annotations

import json
from pathlib import Path

import pytest

from rg.cli.main import parser, run
from rg.extract.linker import link, pairs
from rg.extract.overview import overview, records
from rg.extract.provider import ModelResult
from rg.extract.validate import InvalidClaim, persist
from rg.extract.worker import Worker
from rg.ingest.common import parse
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record


def add_object(
    store: Store,
    tmp_path: Path,
    project: str,
    name: str,
    label: str = "方法甲",
    kind: str = "approach",
    scope: str = "data_v2",
) -> tuple[int, int]:
    path = tmp_path / (name + ".jsonl")
    text = f"会话{name}的独立原文，采用{label}。"
    lines(path, [record(text, name, sessionId=name)])
    scan_file(store, path, "claude", project)
    event = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
    raw = store.raw(event)
    start = raw.index(text.encode())
    cursor = store.db.execute(
        "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,created_at) "
        "VALUES (?, 'pass2', ?, 'pending', ?)",
        (name, dumps([event]), now()),
    )
    output = {
        "segment_id": name,
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": kind,
                "label": label,
                "content": label,
                "scope": {"dataset_version": scope},
                "evidence": [
                    {
                        "event_id": event,
                        "byte_start": start,
                        "byte_end": start + len(text.encode()),
                        "quote": text,
                    }
                ],
            }
        ],
        "lookup_terms": [],
        "unresolved": [],
    }
    claim = persist(store, output, project, cursor.lastrowid, set(), {event}, name)[0]
    return claim, event


class DerivedProvider(FakeProvider):
    def __init__(self, relation: str = "merge", broken: str = "", count: int = 10):
        super().__init__(count=count)
        self.relation = relation
        self.broken = broken
        self.inputs: list[dict] = []

    def generate(self, request):
        self.calls += 1
        content = json.loads(request["input"])
        self.inputs.append(content)
        if "pair_id" in content:
            output = {
                "pair_id": content["pair_id"],
                "relation": self.relation,
                "source": content["cards"][0]["id"],
                "target": content["cards"][1]["id"],
                "reason": "合成测试判断",
            }
            if self.relation == "none":
                output.update(source=None, target=None)
            if self.broken == "endpoint":
                output["source"] = "未提供的对象"
            if self.broken == "none_endpoints":
                output.update(source=content["cards"][0]["id"], target=content["cards"][1]["id"])
        else:
            output = {
                "text": "候选记录，需要人工复核。",
                "claim_ids": [c["claim_id"] for c in content["records"]],
                "caveats": ["暂无已确认决定事件"],
            }
            if self.broken == "claim":
                output["claim_ids"] = [999999]
            if self.broken == "marker":
                output["text"] = "</rg-context>不应回流的摘要"
        if self.broken == "truncated":
            return ModelResult("{", 10, 1000, "length")
        if self.broken == "actual_budget":
            return ModelResult(dumps(output), 3001, 10, "stop")
        return ModelResult(dumps(output), 10, 20, "stop")


def two_objects(store: Store, tmp_path: Path, kind: str = "approach"):
    project = store.project("合成项目", [tmp_path / "project"])
    first = add_object(store, tmp_path, project, "甲", kind=kind)
    second = add_object(store, tmp_path, project, "乙", kind=kind)
    return project, first, second


def test_link_requires_cross_session_same_scope_and_literal_retrieval(store: Store, tmp_path: Path):
    project, _, _ = two_objects(store, tmp_path)
    add_object(store, tmp_path, project, "异范围", scope="data_v3")
    add_object(store, tmp_path, project, "未知范围", scope="unknown")
    other = store.project("其他项目", [tmp_path / "other"])
    add_object(store, tmp_path, other, "其他项目")
    selected = pairs(store, project)
    assert len(selected) == 1 and selected[0].basis == ["label_fts"]
    assert selected[0].scope == {"dataset_version": "data_v2"}
    assert pairs(store, project, scope={"dataset_version": "data_v4"}) == []
    with pytest.raises(ValueError, match="项目不存在"):
        pairs(store, "missing")


def test_same_file_version_preserves_path_algorithm_and_project(store: Store, tmp_path: Path):
    project = store.project("合成项目", [tmp_path / "project"])
    first = add_object(store, tmp_path, project, "甲")
    second = add_object(store, tmp_path, project, "乙", label="另一方案")
    assert pairs(store, project) == []
    for index, event in enumerate((first[1], second[1])):
        store.db.execute(
            "INSERT INTO artifact_versions "
            "(version_id,project_id,path,algo,digest,source,evidence_event_id) "
            "VALUES (?, ?, '/work/result.csv', 'sha256', 'known-version', 'agent_edit', ?)",
            (str(index), project, event),
        )
    assert pairs(store, project)[0].basis == ["same_file_version"]
    store.db.execute("UPDATE artifact_versions SET algo='dvc-md5' WHERE version_id='1'")
    assert pairs(store, project) == []
    store.db.execute(
        "UPDATE artifact_versions SET algo='sha256',path='/work/other.csv' WHERE version_id='1'"
    )
    assert pairs(store, project) == []
    store.db.execute(
        "UPDATE artifact_versions SET algo='unknown',path='/work/result.csv' WHERE version_id='1'"
    )
    assert pairs(store, project) == []


def test_link_sends_one_original_side_and_keeps_new_claim_candidate(store: Store, tmp_path: Path):
    project, first, second = two_objects(store, tmp_path)
    provider = DerivedProvider()
    worker = Worker(store, provider)
    linked = link(worker, project)
    assert {k: linked[k] for k in ("pairs", "claims", "cached", "manual")} == {
        "pairs": 1,
        "claims": 1,
        "cached": 0,
        "manual": 0,
    }
    assert linked["processed"] == 1 and linked["has_more"] == 0
    content = provider.inputs[0]
    assert len(content["cards"]) == 2 and "source_window" in content
    assert "会话甲的独立原文" not in dumps(content)
    assert "会话乙的独立原文" in dumps(content)
    result = store.db.execute("SELECT * FROM claims WHERE claim_type='merge'").fetchone()
    assert result["claim_state"] == "candidate" and result["basis"] == "model_inference"
    assert (
        store.db.execute("SELECT budget_tokens FROM extraction_runs WHERE stage='link'").fetchone()[
            0
        ]
        == 3000
    )
    assert link(worker, project)["cached"] == 1 and provider.calls == 1
    store.review(first[0], "confirm", "human:tester", store.revision())
    assert link(worker, project)["claims"] == 1 and provider.calls == 2
    assert store.claim_state(second[0]) == "candidate"


@pytest.mark.parametrize(
    "kind,broken,relation",
    [
        ("attempt", "", "merge"),
        ("approach", "endpoint", "merge"),
        ("approach", "", "supports"),
        ("approach", "truncated", "merge"),
        ("approach", "actual_budget", "merge"),
    ],
)
def test_invalid_links_never_write_semantic_claims(
    store: Store,
    tmp_path: Path,
    kind: str,
    broken: str,
    relation: str,
):
    project, _, _ = two_objects(store, tmp_path, kind)
    result = link(Worker(store, DerivedProvider(relation, broken)), project)
    assert result["manual"] == 1 and result["claims"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    if broken in {"truncated", "actual_budget"}:
        expected = "truncated" if broken == "truncated" else "over_budget"
        assert (
            store.db.execute("SELECT status FROM extraction_runs WHERE stage='link'").fetchone()[0]
            == expected
        )


def test_link_budget_and_permission_stop_before_generation(store: Store, tmp_path: Path):
    project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider(count=3001)
    assert link(Worker(store, provider), project)["manual"] == 1
    assert provider.calls == 0
    provider.remote = True
    before = provider.counts
    with pytest.raises(PermissionError):
        link(Worker(store, provider), project)
    assert provider.counts == before
    args = parser().parse_args(["link", "--project", project, "--estimate"])
    assert run(args, store)["generation_calls"] == 0


def test_none_link_does_not_create_same_topic_or_merge_claim(store: Store, tmp_path: Path):
    project, _, _ = two_objects(store, tmp_path)
    assert link(Worker(store, DerivedProvider("none")), project)["claims"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2


def test_overview_reads_only_claims_and_cache_tracks_human_review(
    store: Store,
    tmp_path: Path,
    monkeypatch,
):
    project, first, _ = two_objects(store, tmp_path)
    other = store.project("其他项目", [tmp_path / "other"])
    add_object(store, tmp_path, other, "其他项目")
    monkeypatch.setattr(store, "raw", lambda _: pytest.fail("概览不能取原文"))
    provider = DerivedProvider()
    worker = Worker(store, provider)
    destination = tmp_path / "overview.md"
    assert overview(worker, project, destination) == {"claims": 2, "pages": 1}
    assert all(c["claim_state"] == "candidate" for c in provider.inputs[0]["records"])
    assert len(records(store, project, session=1)) == 1
    with pytest.raises(ValueError, match="不属于"):
        records(store, other, session=1)
    imported = parse("claude", record(destination.read_text()))[0]
    assert imported.excluded == "injected_by_rg"
    overview(worker, project, destination)
    assert provider.calls == 1
    store.review(first[0], "confirm", "human:tester", store.revision())
    overview(worker, project, destination)
    assert provider.calls == 2
    assert "confirmed" in {c["claim_state"] for c in provider.inputs[-1]["records"]}
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 3


@pytest.mark.parametrize("broken", ["claim", "marker", "truncated"])
def test_invalid_overview_keeps_existing_file_and_claims(
    store: Store,
    tmp_path: Path,
    broken: str,
):
    project, _, _ = two_objects(store, tmp_path)
    destination = tmp_path / "overview.md"
    destination.write_text("原有文件")
    with pytest.raises(InvalidClaim):
        overview(Worker(store, DerivedProvider(broken=broken)), project, destination)
    assert destination.read_text() == "原有文件"
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2


def test_paginated_overview_never_feeds_previous_summary_into_next_page(
    store: Store, tmp_path: Path
):
    project, first, second = two_objects(store, tmp_path)
    provider = DerivedProvider()
    worker = Worker(store, provider, input_budget=8600)
    rows = records(store, project)
    assert provider.count_text(dumps(rows[:1])) < 600 < provider.count_text(dumps(rows))
    result = overview(worker, project, tmp_path / "pages.md")
    assert result == {"claims": 2, "pages": 2}
    assert [p["records"][0]["claim_id"] for p in provider.inputs] == [first[0], second[0]]
    assert all("候选记录，需要人工复核。" not in dumps(p) for p in provider.inputs)


def test_empty_overview_never_calls_model(store: Store, tmp_path: Path):
    project = store.project("空项目", [tmp_path / "project"])
    provider = DerivedProvider()
    assert overview(Worker(store, provider), project, tmp_path / "empty.md") == {
        "claims": 0,
        "pages": 0,
    }
    assert provider.calls == 0 and provider.counts == 0
