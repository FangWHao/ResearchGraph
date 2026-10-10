from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rg.extract.validate import InvalidCitation, validate
from rg.extract.worker import Worker
from rg.ingest.common import Parsed, parse
from rg.ingest.scanner import scan_file
from rg.mcp.server import MODERN, Server
from rg.mcp.tools import TOOLS, ToolService
from rg.slim.slimmer import slim_session
from rg.store.database import Store, dumps
from rg.store.objects import digest
from tests.conftest import FakeProvider

FIXTURES = Path(__file__).parents[2] / "fixtures/transcripts"


@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_actual_native_mcp_calls_and_results_are_excluded(store: Store, tool: str, name: str):
    path = FIXTURES / tool / (name + ".jsonl")
    project = store.project("公开合成原生MCP", [])
    scan_file(store, path, tool, project)
    calls = list(store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'"))
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "mcp__researchgraph__research_context"
    assert calls[0]["exclude_reason"] == "injected_by_rg"
    result = store.db.execute(
        "SELECT * FROM raw_events WHERE call_id=? AND kind IN ('tool_result','injected_context')",
        (calls[0]["call_id"],),
    ).fetchone()
    assert result is not None and result["exclude_reason"] == "injected_by_rg"
    before = list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id"))
    assert slim_session(store, calls[0]["session_pk"], FakeProvider()) == []
    assert scan_file(store, path, tool, project) == {}
    assert list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id")) == before
    assert store.raw(calls[0]["event_id"])
    assert store.raw(result["event_id"])


def legacy_parse(tool: str, record: dict) -> list[Parsed]:
    saved = copy.deepcopy(record)
    if tool == "codex" and isinstance(saved.get("payload"), dict):
        saved["payload"].pop("namespace", None)
    return parse(tool, saved)


@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_legacy_mcp_cache_is_removed_before_token_count_without_rewriting_l0(
    store: Store, tool: str, name: str
):
    from rg.slim.slimmer import SLIM_VERSION

    path = FIXTURES / tool / (name + ".jsonl")
    project = store.project("旧命名合成原件", [])
    with (
        patch("rg.ingest.scanner.parse", legacy_parse),
        patch("rg.ingest.scanner.PARSER_VERSION", "2"),
        patch("rg.ingest.common.RG_MCP_ALIASES", frozenset()),
    ):
        scan_file(store, path, tool, project)
    original = list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id"))
    call = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'").fetchone()
    assert call["exclude_reason"] is None
    provider = FakeProvider()
    for row in original:
        if row["exclude_reason"] is None:
            store.db.execute(
                "INSERT INTO slim_events VALUES (?,?,?,?,0)",
                (
                    row["event_id"],
                    "此前错误保留的缓存",
                    1,
                    provider.provider + ":" + provider.model + ":" + SLIM_VERSION,
                ),
            )
    with patch.object(provider, "count_text", side_effect=AssertionError("排除前不能计数")):
        assert slim_session(store, call["session_pk"], provider) == []
    assert list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id")) == original
    assert store.db.execute("SELECT count(*) FROM slim_events").fetchone()[0] == 0
    assert all(store.raw(row["event_id"]) for row in original)


@pytest.mark.parametrize(
    "namespace,name,expected,kind,excluded",
    [
        (
            "mcp__researchgraph",
            "research_context",
            "mcp__researchgraph__research_context",
            "tool_call",
            "injected_by_rg",
        ),
        ("mcp__other", "research_context", "mcp__other__research_context", "tool_call", None),
        ("mcp__other", "apply_patch", "mcp__other__apply_patch", "tool_call", None),
        ("mcp__other", "exec_command", "mcp__other__exec_command", "tool_call", None),
        ("functions", "apply_patch", "functions.apply_patch", "file_edit", None),
        (None, "exec_command", "exec_command", "tool_call", None),
        (True, "exec_command", None, "unknown", "unknown_tool_namespace"),
    ],
)
def test_namespaces_preserve_tool_identity_and_do_not_guess_foreign_rg_or_local_edits(
    namespace, name, expected, kind, excluded
):
    events = parse(
        "codex",
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": name,
                "namespace": namespace,
                "call_id": "public-synthetic-call",
                "arguments": "{}",
            },
        },
    )
    assert len(events) == 1
    assert (events[0].tool_name, events[0].kind, events[0].excluded) == (expected, kind, excluded)


@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_legacy_mcp_citations_are_rejected_without_reclassifying_raw_events(
    store: Store, tool: str, name: str
):
    project = store.project("旧命名引用", [])
    with (
        patch("rg.ingest.scanner.parse", legacy_parse),
        patch("rg.ingest.scanner.PARSER_VERSION", "2"),
        patch("rg.ingest.common.RG_MCP_ALIASES", frozenset()),
    ):
        scan_file(store, FIXTURES / tool / (name + ".jsonl"), tool, project)
    call = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'").fetchone()
    raw = store.raw(call["event_id"])
    start = raw.index(b"budget")
    output = {
        "segment_id": "native-mcp-test",
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": "question",
                "label": "合成问题",
                "content": "不允许把MCP参数当作研究原话",
                "scope": {"dataset": "synthetic"},
                "evidence": [
                    {
                        "event_id": call["event_id"],
                        "byte_start": start,
                        "byte_end": start + 6,
                        "quote": "budget",
                    }
                ],
            }
        ],
        "lookup_terms": [],
        "unresolved": [],
    }
    before = list(store.db.iterdump())
    with pytest.raises(InvalidCitation, match="保存原件属于排除内容"):
        validate(store, output, project, set(), {call["event_id"]}, "native-mcp-test")
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("name", ["context", "evidence", "history", "node", "search"])
def test_exact_generated_claude_mcp_names_are_excluded_but_nearby_names_are_not(name: str):
    for actual, excluded in [
        (f"mcp__researchgraph__research_{name}", "injected_by_rg"),
        (f"mcp__other__research_{name}", None),
        (f"mcp__researchgraph__research_{name}_other", None),
    ]:
        event = parse(
            "claude",
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": actual,
                            "id": "public-synthetic-call",
                            "input": {},
                        }
                    ]
                },
            },
        )[0]
        assert event.excluded == excluded


def test_unknown_namespace_is_saved_and_counted(store: Store, tmp_path: Path):
    path = tmp_path / "unknown-namespace.jsonl"
    value = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "namespace": {"invalid": "public-synthetic"},
            "call_id": "bad-namespace",
            "arguments": "{}",
        },
    }
    raw = (json.dumps(value) + "\n").encode()
    path.write_bytes(raw)
    scan_file(store, path, "codex")
    row = store.db.execute("SELECT * FROM raw_events").fetchone()
    assert row["kind"] == "unknown" and row["exclude_reason"] == "unknown_tool_namespace"
    assert store.raw(row["event_id"]) == raw
    assert store.health()["unknown"] == 1


@pytest.mark.parametrize(
    "tool,name", [("codex", "0.162-native-mcp"), ("claude", "2.1.280-native-mcp")]
)
def test_old_pending_plan_is_not_replayed_after_input_policy_changes(
    store: Store, tool: str, name: str
):
    from rg.extract.progress import register
    from rg.extract.segmenter import Segment

    project = store.project("旧片段恢复", [])
    with (
        patch("rg.ingest.scanner.parse", legacy_parse),
        patch("rg.ingest.common.RG_MCP_ALIASES", frozenset()),
    ):
        scan_file(store, FIXTURES / tool / (name + ".jsonl"), tool, project)
    call = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'").fetchone()
    original = list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id"))
    provider = FakeProvider()
    worker = Worker(store, provider)
    old_definition = worker._definition(call["session_pk"], None)[:-1]
    old_config = digest(
        dumps([call["session_pk"], project, old_definition, "progress-v1"]).encode()
    )
    item = Segment(
        "old-injected-segment",
        [{"event_id": call["event_id"], "kind": "tool_call", "text": "旧缓存正文"}],
    )
    with store.transaction():
        register(store, old_config, call["session_pk"], [item])
    before = store.db.execute("SELECT * FROM extraction_plans").fetchone()
    with patch.object(provider, "count_text", side_effect=AssertionError("不能计数旧注入内容")):
        result = worker.process(call["session_pk"])
    assert result == {"segments": 0, "claims": 0, "manual": 0}
    assert provider.calls == 0 and provider.counts == 0
    assert store.db.execute("SELECT * FROM extraction_plans").fetchone() == before
    assert list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id")) == original


def test_legacy_result_without_marker_inherits_exclusion_from_saved_call(
    store: Store, tmp_path: Path
):
    records = [
        json.loads(line)
        for line in (FIXTURES / "codex/0.162-native-mcp.jsonl").read_text().splitlines()
    ]
    records[-1]["payload"]["output"] = "公开合成协议错误，没有状态卡标记"
    path = tmp_path / "missing-marker.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    with (
        patch("rg.ingest.scanner.parse", legacy_parse),
        patch("rg.ingest.common.RG_MCP_ALIASES", frozenset()),
    ):
        scan_file(store, path, "codex", store.project("旧工具错误", []))
    result = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_result'").fetchone()
    assert result["exclude_reason"] is None
    before = list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id"))
    assert slim_session(store, result["session_pk"], FakeProvider()) == []
    assert list(store.db.execute("SELECT * FROM raw_events ORDER BY event_id")) == before


def test_discovery_keeps_evidence_available_and_server_enforces_exclusive_reference(store: Store):
    project = store.project("发现与互斥引用", [])
    server = Server(ToolService(store, project))
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    before = list(store.db.iterdump())
    reply = server.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
    )
    assert reply is not None
    tools = reply["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "research." + name for name in ("context", "evidence", "history", "node", "search")
    }
    evidence = next(tool for tool in tools if tool["name"] == "research.evidence")
    assert not {"oneOf", "anyOf", "allOf"}.intersection(evidence["inputSchema"])
    assert (
        "oneOf"
        in next(tool for tool in TOOLS if tool["name"] == "research.evidence")["inputSchema"]
    )
    for arguments in [{}, {"claim_id": 1, "event_id": 1}]:
        result = server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"_meta": meta, "name": "research.evidence", "arguments": arguments},
            }
        )
        assert result is not None and result["result"]["isError"]
        assert "<rg-context" in result["result"]["content"][0]["text"]
    assert list(store.db.iterdump()) == before
