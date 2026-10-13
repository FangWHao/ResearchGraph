"""十类语义的独立真值、实际解析、候选写入、HTTP 与导出验收。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from rg.api.server import LocalServer
from rg.derive.edits import diff
from rg.export.package import archive
from rg.extract.differences import confirmed_differences
from rg.ingest.scanner import scan_file
from rg.query.graph import node_id, query
from rg.slim.slimmer import slim_session
from rg.store.objects import digest
from tests.conftest import FakeProvider
from tests.golden.test_exports import unchanged, unpack
from tests.semantic_cases import cases, day, seed_case

CASES = cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_native_input_complete_http_and_export_match_independent_semantics(store, tmp_path, case):
    index = seed_case(store, tmp_path, case)
    project, expected = index["project_id"], case["expected"]
    conditions = {"known_until": "2099-01-01T00:00:00+00:00", "occurred_until": day(4)}
    before = unchanged(store)
    nodes = query(store, project, conditions | {"collection": "nodes", "limit": 100})
    assert nodes["total"] == expected["node_count"] and nodes["semantic_graph_complete"]
    assert not nodes["l1_dependencies_complete"]
    by_id = {n["node_id"]: n for n in nodes["items"]}
    for key, identity in index["nodes"].items():
        node = by_id[node_id(identity["entity_id"], identity["scope"])]
        assert node["scope"] == identity["scope"] and node["kind"] == case["nodes"][key]["kind"]
        version = next(
            v for v in node["versions"] if v["claim_id"] == index["claims"]["v:" + key]["claim_id"]
        )
        assert version["payload"]["label"] == case["nodes"][key]["label"]
        assert node["selected_content_claim_id"] is None
    for state in expected["states"]:
        identity = index["nodes"][state["node"]]
        actual = by_id[node_id(identity["entity_id"], identity["scope"])]["states"]
        for axis, value in state.items():
            if axis != "node":
                assert actual[axis]["state"] == value

    relations = query(store, project, conditions | {"collection": "edges", "limit": 100})
    assert relations["total"] == len(case["relations"])
    assert all(edge["resolved"] and edge["valid_direction"] for edge in relations["items"])
    for relation in case["relations"]:
        edge = next(
            e
            for e in relations["items"]
            if e["claim_id"] == index["claims"][relation["key"]]["claim_id"]
        )
        assert edge["relation"] == relation["relation"]
        assert edge["retrieval_only"] == (relation["relation"] == "same_topic")
        for role in ("source", "target"):
            identity = index["nodes"][relation[role]]
            assert edge[role]["node_id"] == node_id(identity["entity_id"], identity["scope"])
    if "join" in expected:
        join = query(store, project, conditions | {"collection": "joins"})["items"][0]
        oracle = expected["join"]
        assert join["valid_semantics"] and join["resolved"]
        assert join["semantics"] == oracle["semantics"]
        assert join["statistical_independence_asserted"] is oracle["independence"]
        selected = index["nodes"][oracle["selected"]]["entity_id"] if oracle["selected"] else None
        assert join["selected"] == selected
        for port, truth in zip(join["inputs"], oracle["inputs"], strict=True):
            assert port["port"] == truth["port"] and port["required"] is truth["required"]
            assert port["ref"] == index["nodes"][truth["ref"]]["entity_id"]
            assert (port["role"] == "input") is truth["semantic"]

    (tmp_path / "index.html").write_text("合成验收页")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-semantic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            params = conditions | {"project": project, "collection": "claims", "limit": 100}
            assert client.get("/api/semantic-graph", params=params).status_code == 401
            client.headers["Authorization"] = "Bearer synthetic-semantic-token"
            response = client.get("/api/semantic-graph", params=params)
            assert response.status_code == 200
            records = response.json()
            assert records["next_offset"] is None and records["revision"] == nodes["revision"]
            assert {r["claim_id"] for r in records["items"]} == {
                c["claim_id"] for c in index["claims"].values()
            }
            for citation in index["claims"].values():
                record = next(r for r in records["items"] if r["claim_id"] == citation["claim_id"])
                assert record["evidence"][0]["span_id"] == citation["span_id"]
                evidence = client.get(
                    f"/api/evidence/{citation['event_id']}",
                    params=conditions
                    | {
                        "project": project,
                        "start": citation["byte_start"],
                        "end": citation["byte_end"],
                        "context": 0,
                        "expected_revision": records["revision"],
                    },
                )
                assert evidence.status_code == 200
                event = evidence.json()["event"]
                assert event["quote"] == citation["quote"]
                assert (
                    event["quote_sha256"]
                    == citation["quote_sha256"]
                    == digest(event["quote"].encode())
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    _, package = archive(store, conditions | {"project_id": project})
    exported = unpack(package)
    assert {c["claim_id"] for c in exported["claims.json"]} == {
        c["claim_id"] for c in index["claims"].values()
    }
    assert exported["graph.json"]["semantic"]["counts"]["nodes"] == expected["node_count"]
    assert unchanged(store) == before


def scenario(store, tmp_path, identity):
    case = next(c for c in CASES if c["id"] == identity)
    return seed_case(store, tmp_path, case)


def test_a02_native_code_rollback_does_not_withdraw_method_or_fabricate_physical_versions(
    store, tmp_path
):
    index = scenario(store, tmp_path, "A02")
    edit = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert edit is not None
    result = json.loads(store.raw(edit["result_event_id"]))["payload"]
    assert result["success"] is True and result["status"] == "completed"
    assert edit["before_version"] is None and edit["after_version"] is None
    assert diff(store, dict(edit))["format"] == "patch_only"
    assert store.db.execute("SELECT count(*) FROM edit_records").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0
    events = query(store, index["project_id"], {"collection": "state_events"})["items"]
    assert [e["payload"]["action"] for e in events] == ["accepted"]


def test_a07_mirror_and_compaction_do_not_duplicate_sources_decisions_or_reimport(store, tmp_path):
    index = scenario(store, tmp_path, "A07")
    assert store.db.execute("SELECT count(*) FROM dedupe_links").fetchone()[0] == 1
    assert (
        store.db.execute(
            "SELECT count(*) FROM raw_events WHERE kind='compact_boundary'"
        ).fetchone()[0]
        == 1
    )
    session = store.db.execute(
        "SELECT session_pk FROM sessions WHERE project_id=?", (index["project_id"],)
    ).fetchone()[0]
    slim = slim_session(store, session, FakeProvider())
    canonical = index["claims"]["v:B"]["quote"]
    assert sum(e["text"].count(canonical) for e in slim) == 1
    before = unchanged(store)
    assert scan_file(store, Path(index["source_paths"][0]), "codex", index["project_id"]) == {}
    assert unchanged(store) == before
    events = query(store, index["project_id"], {"collection": "state_events"})["items"]
    assert len(events) == 1 and events[0]["claim_id"] == index["claims"]["accepted"]["claim_id"]


def test_a08_tool_request_without_return_never_becomes_execution_or_negative_evidence(
    store, tmp_path
):
    index = scenario(store, tmp_path, "A08")
    run = store.db.execute("SELECT * FROM runs").fetchone()
    assert run["state"] == "requested" and run["exit_code"] is None and run["ended_at"] is None
    assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 0
    assert query(store, index["project_id"], {"collection": "state_events"})["items"] == []
    assert (
        store.db.execute("SELECT count(*) FROM raw_events WHERE kind='tool_result'").fetchone()[0]
        == 0
    )


def test_a09_late_import_obeys_known_and_occurred_cutoffs_and_keeps_occurrence_order(
    store, tmp_path
):
    index = scenario(store, tmp_path, "A09")
    project, claims = index["project_id"], index["claims"]
    all_events = query(store, project, {"collection": "state_events"})["items"]
    before_known = query(
        store, project, {"collection": "state_events", "known_until": index["known_before_late"]}
    )["items"]
    early_occurred = query(
        store, project, {"collection": "state_events", "occurred_until": day(2)}
    )["items"]
    assert {e["claim_id"] for e in all_events} == {
        claims[k]["claim_id"] for k in ("current", "late")
    }
    assert [e["claim_id"] for e in before_known] == [claims["current"]["claim_id"]]
    assert [e["claim_id"] for e in early_occurred] == [claims["late"]["claim_id"]]
    assert claims["late"]["recorded_at"] > index["known_before_late"]
    assert claims["current"]["claim_id"] < claims["late"]["claim_id"]
    assert (
        next(
            n
            for n in query(store, project, {"collection": "nodes"})["items"]
            if n["entity_id"] == index["nodes"]["B"]["entity_id"]
        )["states"]["adoption"]["state"]
        == "accepted"
    )


def test_a10_two_validated_model_outputs_keep_human_correction_and_raw_history(store, tmp_path):
    index = scenario(store, tmp_path, "A10")
    claims = index["claims"]
    runs = store.db.execute(
        "SELECT * FROM extraction_runs WHERE stage='pass2' ORDER BY extraction_run_id"
    ).fetchall()
    assert [r["model"] for r in runs] == ["synthetic-model-v1", "synthetic-model-v2"]
    assert all(r["status"] == "ok" for r in runs) and runs[0]["job_key"] != runs[1]["job_key"]
    assert json.loads(runs[0]["input_event_ids"]) == json.loads(runs[1]["input_event_ids"])
    attempts = store.db.execute(
        "SELECT * FROM model_attempts WHERE stage='pass2' ORDER BY attempt_id"
    ).fetchall()
    assert len(attempts) == 2
    assert all(
        a["sent"] == 1
        and 0 < a["measured_input_tokens"] <= a["input_budget"]
        and a["status"] == "ok"
        for a in attempts
    )
    initial = store.db.execute(
        "SELECT * FROM claims WHERE claim_id=?", (claims["initial"]["claim_id"],)
    ).fetchone()
    correction = store.db.execute(
        "SELECT * FROM claims WHERE claim_id=?", (claims["correction"]["claim_id"],)
    ).fetchone()
    new = store.db.execute(
        "SELECT * FROM claims WHERE claim_id=?", (claims["reextracted"]["claim_id"],)
    ).fetchone()
    assert initial["claim_state"] == new["claim_state"] == "candidate"
    assert (
        correction["claim_state"] == "confirmed"
        and correction["replaces_claim"] == initial["claim_id"]
    )
    assert json.loads(correction["payload"])["action"] == "deferred"
    assert store.claim_state(new["claim_id"]) == "candidate"
    assert store.claim_state(correction["claim_id"]) == "confirmed"
    assert store.claim_state(initial["claim_id"]) == "dismissed"
    differences = confirmed_differences(store, new["claim_id"])
    assert [d["claim_id"] for d in differences] == [correction["claim_id"]]
    assert "action" in differences[0]["differing_fields"]
    assert new["actor"].startswith("model:") and correction["actor"].startswith("human:")
    assert claims["initial"]["span_id"] == claims["correction"]["span_id"]
    for key in ("initial", "correction", "reextracted"):
        citation = claims[key]
        assert (
            digest(store.raw(citation["event_id"])[citation["byte_start"] : citation["byte_end"]])
            == citation["quote_sha256"]
        )
