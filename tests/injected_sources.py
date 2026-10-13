"""公开合成的旧实现候选；仅用于历史污染回归，不是人工参考。"""

from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

from rg.extract.validate import persist
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from tests.golden.test_native_client_mcp import FIXTURES, legacy_parse
from tests.golden.test_read_tools import review


def legacy_candidate(store: Store, tool: str, name: str, confirmed: bool = False) -> tuple:
    identity = uuid4().hex
    project = store.project("公开合成旧引用 " + identity, [])
    fixture = store.root.parent / ("public-legacy-" + identity + ".jsonl")
    records = [
        json.loads(line) for line in (FIXTURES / tool / (name + ".jsonl")).read_text().splitlines()
    ]
    native_ids = {
        record["uuid"]: str(uuid4())
        for record in records
        if tool == "claude" and record.get("uuid")
    }
    for record in records:
        if tool == "codex" and record.get("type") == "session_meta":
            record["payload"]["id"] = identity
        if tool == "claude":
            record["sessionId"] = identity
            for field in ("uuid", "parentUuid", "sourceToolAssistantUUID"):
                if record.get(field) in native_ids:
                    record[field] = native_ids[record[field]]
    fixture.write_text("".join(dumps(record) + "\n" for record in records))
    with (
        patch("rg.ingest.scanner.parse", legacy_parse),
        patch("rg.ingest.scanner.PARSER_VERSION", "2"),
        patch("rg.ingest.common.RG_MCP_ALIASES", frozenset()),
    ):
        scan_file(store, fixture, tool, project)
    call = store.db.execute(
        "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
        "WHERE s.project_id=? AND r.kind='tool_call' ORDER BY r.event_id LIMIT 1",
        (project,),
    ).fetchone()
    assert call["exclude_reason"] is None
    raw = store.raw(call["event_id"])
    start = raw.index(b"budget")
    run = store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,created_at) "
        "VALUES (?,'pass2',?,'pending',?)",
        ("legacy-published-" + identity, dumps([call["event_id"]]), now()),
    ).lastrowid
    output = {
        "segment_id": "legacy-published-" + identity,
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": "question",
                "label": "budget 合成假说",
                "content": "旧实现从工具参数误提取的候选",
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
    with patch("rg.extract.validate.current_exclusion", return_value=None):
        claim = persist(
            store, output, project, run, set(), {call["event_id"]}, output["segment_id"]
        )[0]
    if confirmed:
        review(store, claim, "confirm", recorded=now())
    return project, claim, call["event_id"]
