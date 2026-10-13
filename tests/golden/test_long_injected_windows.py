from __future__ import annotations

import json
from uuid import uuid4

import pytest

from rg.extract.locator import positions, units, windows
from rg.extract.privacy import ProjectCounter
from rg.extract.redact import redact
from rg.extract.segmenter import segment
from rg.ingest.scanner import scan_file
from rg.slim.slimmer import slim_session
from rg.store.database import Store, dumps
from tests.conftest import FakeProvider

CANARY = "PUBLIC_SYNTHETIC_RG_BODY"


@pytest.mark.parametrize(
    "tool,role,encoded",
    [
        ("claude", "user", False),
        ("claude", "assistant", True),
        ("codex", "user", True),
        ("codex", "assistant", False),
    ],
)
def test_long_marked_body_crossing_segment_windows_never_reaches_count_or_locations(
    store: Store, tmp_path, tool: str, role: str, encoded: bool
):
    project = store.project("公开合成长窗口", [])
    identity = str(uuid4())
    text = (
        "采用公开方案甲。" * 30
        + '<rg-context v="1" id="public-long">'
        + (CANARY + "采用自引用方案乙。") * 700
        + "</rg-context>"
        + "为什么复核公开结果。" * 30
    )
    if tool == "claude":
        records = [
            {"type": role, "uuid": identity, "sessionId": identity, "message": {"content": text}}
        ]
    else:
        records = [
            {"type": "session_meta", "payload": {"id": identity}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        ]
    path = tmp_path / "public-long-window.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=encoded) + "\n" for row in records))
    scan_file(store, path, tool, project)
    event = store.db.execute(
        "SELECT * FROM raw_events WHERE role=? ORDER BY event_id DESC LIMIT 1", (role,)
    ).fetchone()
    assert event is not None and event["exclude_reason"] is None
    raw = store.raw(event["event_id"])
    originals = [tuple(row) for row in store.db.execute("SELECT * FROM raw_events")]

    class CleanCounter(FakeProvider):
        def count_text(self, value: str) -> int:
            assert CANARY not in value
            return super().count_text(value)

    counter = ProjectCounter(store, project, CleanCounter())
    items = segment(slim_session(store, event["session_pk"], counter), counter, budget=4000)
    assert len(items) > 1
    protected = redact(raw).private_ranges
    assert protected
    outgoing = []
    for item in items:
        input_units = units(store, item, counter)
        assert CANARY not in dumps(input_units)
        payload, _, _ = windows(store, item, input_units, positions(input_units, []))
        outgoing.extend(payload)
        assert CANARY not in dumps(payload)
        assert all(
            not (start < window["byte_end"] and end > window["byte_start"])
            for start, end in protected
            for window in payload
        )
    assert outgoing
    assert any("公开方案甲" in window["raw"] or "\\u516c" in window["raw"] for window in outgoing)
    assert store.raw(event["event_id"]) == raw
    assert [tuple(row) for row in store.db.execute("SELECT * FROM raw_events")] == originals
