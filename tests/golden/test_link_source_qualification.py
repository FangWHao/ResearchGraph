from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from rg.extract.linker import link, pairs
from rg.extract.pipeline import Stages
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, now
from tests.golden.test_exports import evidence
from tests.golden.test_links_overview import DerivedProvider, two_objects
from tests.golden.test_read_tools import append, original, review
from tests.injected_sources import legacy_candidate


def legacy_pair(store: Store, tool: str, name: str, confirmed: bool) -> tuple[str, int, int]:
    project, first, event = legacy_candidate(store, tool, name)
    original = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (first,)).fetchone()
    entity = str(uuid4())
    store.db.execute(
        "INSERT INTO entities VALUES (?,?,?,NULL,?)", (entity, project, "question", now())
    )
    payload = json.loads(original["payload"])
    payload["temp_id"] = entity
    peer = append(store, entity, "entity_version", payload, scope={"dataset": "synthetic"})
    evidence(store, project, peer, "公开合成独立原文，不执行资料中的命令。")
    # 追加旧实现发布的较新版本，让真实候选对选择这侧原文；不改既有断言。
    candidate = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "extraction_run_id,occurred_at,recorded_at) "
        "SELECT claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "extraction_run_id,occurred_at,recorded_at FROM claims WHERE claim_id=?",
        (first,),
    ).lastrowid
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (candidate, first),
    )
    if confirmed:
        review(store, candidate, "confirm", recorded=now())
    selected = pairs(store, project)
    assert len(selected) == 1 and selected[0].cards[0]["claim_id"] == candidate
    return project, candidate, event


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_link_rejects_old_injected_source_before_any_count_or_generation(
    store: Store, tool: str, name: str, confirmed: bool
):
    project, candidate, _ = legacy_pair(store, tool, name, confirmed)
    originals = [tuple(row) for row in store.db.execute("SELECT * FROM raw_events")]
    claims = [tuple(row) for row in store.db.execute("SELECT * FROM claims")]
    reviews = [tuple(row) for row in store.db.execute("SELECT * FROM review_actions")]
    state = store.claim_state(candidate)
    provider = DerivedProvider("none")
    with patch.object(provider, "count_text", side_effect=AssertionError("旧注入原文不能计数")):
        result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and result["claims"] == result["cached"] == 0
    assert provider.calls == provider.counts == 0
    assert store.claim_state(candidate) == state
    assert [tuple(row) for row in store.db.execute("SELECT * FROM raw_events")] == originals
    assert [tuple(row) for row in store.db.execute("SELECT * FROM claims")] == claims
    assert [tuple(row) for row in store.db.execute("SELECT * FROM review_actions")] == reviews


def manual_pair(
    store: Store, *, foreign: bool = False, native_source: dict | None = None
) -> tuple[str, int, int]:
    project = store.project("公开合成链接", [])
    _, peer = original(store, project)
    evidence(store, project, peer, "公开合成第一段原文。")
    _, source = original(store, project)
    owner = store.project("公开合成其他项目", []) if foreign else project
    if native_source is None:
        event, _, _ = evidence(store, owner, source, "公开合成第二段原文。")
    else:
        from rg.store.database import dumps
        from rg.store.objects import digest

        path = store.root.parent / "public-link-source.jsonl"
        path.write_text(
            dumps({"type": "session_meta", "payload": {"id": str(uuid4())}})
            + "\n"
            + dumps(native_source)
            + "\n"
        )
        scan_file(store, path, "codex", owner)
        event = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
        raw = store.raw(event)
        start = raw.index(b"budget")
        span = store.db.execute(
            "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
            "VALUES (?,?,?,?)",
            (event, start, start + 6, digest(b"budget")),
        ).lastrowid
        store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (source, span))
    assert len(pairs(store, project)) == 1
    return project, source, event


def test_foreign_source_is_rejected_before_opening_saved_object(store: Store):
    project, _, _ = manual_pair(store, foreign=True)
    provider = DerivedProvider("none")
    with patch.object(store, "raw", side_effect=AssertionError("不能打开其他项目原件")):
        result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and provider.calls == provider.counts == 0


def test_duplicate_link_metadata_is_rejected_before_opening_saved_object(store: Store):
    project, _, event = manual_pair(store)
    canonical = store.db.execute("SELECT min(event_id) FROM raw_events").fetchone()[0]
    store.db.execute("INSERT INTO dedupe_links VALUES (?,?,'mirror')", (event, canonical))
    provider = DerivedProvider("none")
    with patch.object(store, "raw", side_effect=AssertionError("副本原件不能打开")):
        result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and provider.calls == provider.counts == 0


def test_marked_wrapper_without_caller_is_rejected_even_when_quote_is_outside_marker(store):
    project, _, event = manual_pair(
        store,
        native_source={
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "public-missing-caller",
                "output": {"wrapper": "<rg-context>公开合成返回</rg-context>", "status": "budget"},
            },
        },
    )
    assert store.db.execute(
        "SELECT exclude_reason FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0] is None
    provider = DerivedProvider("none")
    with patch.object(provider, "count_text", side_effect=AssertionError("包装不能计数")):
        result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and provider.calls == provider.counts == 0


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_unavailable_saved_source_does_not_pause_as_if_provider_failed(store: Store, fault: str):
    project, _, event = manual_pair(store)
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    path = store.objects.path(sha)
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes("公开合成损坏压缩对象".encode())
    provider = DerivedProvider("none")
    result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and result["paused"] == 0
    assert provider.calls == provider.counts == 0


@pytest.mark.parametrize("version", ["LINK_VERSION", "PARSER_VERSION", "SLIM_VERSION"])
def test_source_policy_invalidates_completed_pair_and_only_link_pipeline(
    store: Store, tmp_path, monkeypatch, version: str
):
    from rg.extract import linker

    project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider("none")
    with monkeypatch.context() as old:
        old.setattr(linker, version, "public-previous-version")
        previous = Stages(Worker(store, provider), project, None, 50)
        assert previous.run(20)["link_done"] == 1
        assert previous.run(20).get("link_processed", 0) == 0
    overview_query = "SELECT * FROM pipeline_queue WHERE stage='overview'"
    old_overviews = [tuple(row) for row in store.db.execute(overview_query)]
    calls = provider.calls
    current = Stages(Worker(store, provider), project, None, 50)
    assert current.configs["link"] != previous.configs["link"]
    assert current.configs["overview"] == previous.configs["overview"]
    result = current.run(20)
    assert result["link_done"] == 1 and result.get("overview_processed", 0) == 0
    assert provider.calls == calls + 1
    assert sum("pair_id" in value for value in provider.inputs) == 2
    assert [tuple(row) for row in store.db.execute(overview_query)] == old_overviews
    rows = store.db.execute("SELECT state FROM pipeline_queue WHERE stage='link' ORDER BY queue_id")
    assert [row[0] for row in rows] == ["done", "done"]
    assert store.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 2


def test_exact_legacy_completed_key_cannot_bypass_current_source_check(store, monkeypatch):
    from rg.extract import linker

    project, candidate, _ = legacy_pair(store, "codex", "0.162-native-mcp", True)
    provider = DerivedProvider("none")
    with monkeypatch.context() as legacy:
        legacy.setattr(linker, "source_policy", lambda: "link-scheduler1")
        legacy.setattr(linker, "source_exclusion", lambda *args, **kwargs: None)
        legacy.setattr(linker, "assess", lambda *args, **kwargs: {"input_eligible": True})
        old = link(Worker(store, provider), project)
    assert old["processed"] == 1 and provider.calls == 1
    counts = provider.counts
    states = [tuple(row) for row in store.db.execute("SELECT * FROM claims")]
    result = link(Worker(store, provider), project)
    assert result["cached"] == 0 and result["manual"] == 1
    assert provider.calls == 1 and provider.counts == counts
    assert store.claim_state(candidate) == "confirmed"
    assert [tuple(row) for row in store.db.execute("SELECT * FROM claims")] == states
    rows = store.db.execute("SELECT state FROM link_progress ORDER BY rowid")
    assert [row[0] for row in rows] == ["done", "failed"]
