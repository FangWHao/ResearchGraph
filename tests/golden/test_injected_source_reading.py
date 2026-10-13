from __future__ import annotations

from unittest.mock import patch

import pytest

from rg.extract.qa import ask
from rg.extract.worker import Worker
from rg.query.reader import Reader
from rg.query.retrieval import retrieve
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.injected_sources import legacy_candidate


@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_readonly_source_reports_current_input_exclusion_but_preserves_original_flag(
    store: Store, tool: str, name: str
):
    project, _, event = legacy_candidate(store, tool, name)
    before = list(store.db.iterdump())
    source = Reader(store, project, {"event_id": event}).evidence()
    assert source["exclude_reason"] is None
    assert source["input_exclude_reason"] == "injected_by_rg"
    assert source["text"] and source["object_sha256"]
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_qa_never_sends_old_injected_evidence_even_after_human_review(
    store: Store, tool: str, name: str, confirmed: bool
):
    project, claim, _ = legacy_candidate(store, tool, name, confirmed)
    before = list(store.db.iterdump())
    state = store.claim_state(claim)
    packet = retrieve(store, project, "budget", values={"scope": {"dataset": "synthetic"}})
    assert packet["sources"] == [] and packet["gaps"]
    provider = FakeProvider()
    with patch.object(provider, "count_text", side_effect=AssertionError("不能计数旧注入资料")):
        result = ask(
            Worker(store, provider), project, "budget", values={"scope": {"dataset": "synthetic"}}
        )
    assert '"generation_calls":0' in result
    assert provider.calls == provider.counts == 0
    assert store.claim_state(claim) == state
    assert list(store.db.iterdump()) == before


def test_raw_search_fallback_does_not_send_the_same_old_injected_call(store: Store):
    project, _, _ = legacy_candidate(store, "codex", "0.162-native-mcp")
    before = list(store.db.iterdump())
    packet = retrieve(store, project, "budget")
    assert packet["sources"] == []
    assert packet["gaps_total"] >= 1
    assert list(store.db.iterdump()) == before


def test_wrapped_marked_result_without_caller_is_rejected_before_counting(store, tmp_path):
    from rg.ingest.scanner import scan_file
    from rg.slim.slimmer import slim_session
    from rg.store.database import dumps

    project = store.project("合成无调用包装", [])
    path = tmp_path / "wrapper.jsonl"
    path.write_text(
        dumps({"type": "session_meta", "payload": {"id": "public-wrapper"}})
        + "\n"
        + dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "missing-call",
                    "output": {"wrapper": "<rg-context>budget</rg-context>", "status": "ok"},
                },
            }
        )
        + "\n"
    )
    scan_file(store, path, "codex", project)
    row = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_result'").fetchone()
    assert row["exclude_reason"] is None
    before = list(store.db.execute("SELECT * FROM raw_events"))
    source = Reader(store, project, {"event_id": row["event_id"]}).evidence()
    assert source["input_exclude_reason"] == "injected_by_rg"
    provider = FakeProvider()
    with patch.object(provider, "count_text", side_effect=AssertionError("包装不得计数")):
        assert slim_session(store, row["session_pk"], provider) == []
    assert list(store.db.execute("SELECT * FROM raw_events")) == before


@pytest.mark.parametrize("cutoff", ["known_until", "occurred_until"])
def test_late_call_does_not_reclassify_source_at_earlier_cutoff(store, tmp_path, cutoff):
    from rg.ingest.scanner import scan_file
    from rg.query.graph_records import evidence
    from rg.store.database import dumps
    from tests.golden.test_read_tools import T1, T2, T3, T4

    project = store.project("合成迟到调用", [])
    path = tmp_path / "late.jsonl"
    path.write_text(
        dumps({"type": "session_meta", "timestamp": T1, "payload": {"id": "public-late"}})
        + "\n"
        + dumps(
            {
                "type": "response_item",
                "timestamp": T1,
                "payload": {
                    "type": "function_call_output",
                    "call_id": "late-call",
                    "output": "合成旧结果",
                },
            }
        )
        + "\n"
    )
    with patch("rg.ingest.scanner.now", return_value=T1):
        scan_file(store, path, "codex", project)
    event = store.db.execute("SELECT event_id FROM raw_events WHERE kind='tool_result'").fetchone()[
        0
    ]
    with path.open("a") as stream:
        stream.write(
            dumps(
                {
                    "type": "response_item",
                    "timestamp": T3,
                    "payload": {
                        "type": "function_call",
                        "namespace": "mcp__researchgraph",
                        "name": "research_context",
                        "call_id": "late-call",
                        "arguments": "{}",
                    },
                }
            )
            + "\n"
        )
    with patch("rg.ingest.scanner.now", return_value=T3):
        scan_file(store, path, "codex", project)
    before = list(store.db.iterdump())
    options = {"known_until": T4, "occurred_until": T4, cutoff: T2}
    assert (
        Reader(store, project, options | {"event_id": event}).evidence()["input_exclude_reason"]
        is None
    )
    assert (
        evidence(store, project, event, options | {"context": 0})["event"]["input_exclude_reason"]
        is None
    )
    assert (
        Reader(store, project, {"event_id": event}).evidence()["input_exclude_reason"]
        == "injected_by_rg"
    )
    assert list(store.db.iterdump()) == before


def test_marker_in_another_tool_result_does_not_discard_unrelated_source(store, tmp_path):
    from rg.ingest.scanner import scan_file
    from rg.store.database import dumps

    project = store.project("合成同条多结果", [])
    path = tmp_path / "siblings.jsonl"
    path.write_text(
        dumps(
            {
                "type": "user",
                "sessionId": "public-siblings",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "marked",
                            "content": {
                                "wrapper": "<rg-context>budget</rg-context>",
                                "status": "ok",
                            },
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "ordinary",
                            "content": "公开合成独立结果",
                        },
                    ]
                },
            }
        )
        + "\n"
    )
    scan_file(store, path, "claude", project)
    rows = store.db.execute(
        "SELECT * FROM raw_events WHERE kind='tool_result' ORDER BY event_id"
    ).fetchall()
    assert len(rows) == 2 and rows[0]["object_sha256"] == rows[1]["object_sha256"]
    before = list(store.db.iterdump())
    assert (
        Reader(store, project, {"event_id": rows[0]["event_id"]}).evidence()["input_exclude_reason"]
        == "injected_by_rg"
    )
    assert (
        Reader(store, project, {"event_id": rows[1]["event_id"]}).evidence()["input_exclude_reason"]
        is None
    )
    assert list(store.db.iterdump()) == before


def test_unverifiable_legacy_event_still_displays_original_and_is_not_eligible(store):
    from rg.api.views import _event

    project, _, event = legacy_candidate(store, "codex", "0.162-native-mcp")
    identity = store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end,"
        "object_sha256,seq,kind,recorded_at,line_sha256) "
        "SELECT session_pk,file_instance_id,999,byte_start,byte_end,object_sha256,99,"
        "kind,recorded_at,line_sha256 FROM raw_events WHERE event_id=?",
        (event,),
    ).lastrowid
    before = list(store.db.iterdump())
    source = Reader(store, project, {"event_id": identity}).evidence()
    assert source["input_exclude_reason"] == "source_unavailable"
    assert source["exclude_reason"] is None and "budget" in source["text"]
    assert _event(store, identity, None, None)["input_exclude_reason"] == "source_unavailable"
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_unavailable_caller_preserves_readable_result_but_blocks_input(store, tmp_path, fault):
    from rg.ingest.scanner import scan_file
    from rg.store.database import dumps
    from tests.golden.test_native_client_mcp import legacy_parse

    project = store.project("合成不可读调用", [])
    path = tmp_path / "unavailable-call.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "public-unavailable"}},
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "public-call",
                "namespace": "mcp__researchgraph",
                "name": "research_context",
                "arguments": "{}",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "public-call",
                "output": "公开合成可读结果",
            },
        },
    ]
    path.write_text("".join(dumps(record) + "\n" for record in records))
    with patch("rg.ingest.scanner.parse", legacy_parse):
        scan_file(store, path, "codex", project)
    call = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'").fetchone()
    result = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_result'").fetchone()
    assert result["exclude_reason"] is None
    saved = store.objects.path(call["object_sha256"])
    if fault == "missing":
        saved.unlink()
    else:
        saved.write_bytes(b"public-corrupted-zstandard")
    before = list(store.db.iterdump())
    source = Reader(store, project, {"event_id": result["event_id"]}).evidence()
    assert source["input_exclude_reason"] == "source_unavailable"
    assert "公开合成可读结果" in source["text"]
    assert list(store.db.iterdump()) == before
