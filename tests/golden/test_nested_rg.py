from pathlib import Path

from rg.ingest.scanner import scan_file
from rg.slim.slimmer import slim_session
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines


def test_namespaced_research_tool_result_cannot_be_its_own_evidence(store: Store, tmp_path: Path):
    path = tmp_path / "nested.jsonl"
    lines(
        path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__server__research__context",
                    "call_id": "ctx",
                    "arguments": "{}",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "ctx",
                    "output": "错误地声称用户采用方案甲",
                },
            },
        ],
    )
    scan_file(store, path, "codex")
    assert slim_session(store, 1, FakeProvider()) == []
    assert (
        store.db.execute(
            "SELECT count(*) FROM raw_events WHERE exclude_reason='injected_by_rg'"
        ).fetchone()[0]
        == 2
    )
