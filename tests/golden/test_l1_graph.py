from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import httpx
import pytest

from rg.api.graph import source
from rg.api.server import LocalServer
from rg.export.package import archive
from rg.query.l1 import FileRunGraph, query
from rg.query.l1_records import all_pages
from rg.query.reader import Reader
from rg.record.manifest import record
from rg.store import migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from tests.golden.test_exports import unchanged, unpack
from tests.golden.test_l1 import claude_call, claude_result, ingest, reported_edit
from tests.golden.test_read_tools import T1, T2, T3, T4, append, original
from tests.golden.test_run_manifests import body
from tests.golden.test_version_queries import captured, observed, scene
from tests.l1_graph_browser import SCOPE, seed_l1_graph
from tests.l1_graph_browser import T2 as B2
from tests.test_migrations import legacy_store


def collect(store, project, collection="nodes", **values):
    return all_pages(
        lambda offset: query(
            store, project, values | {"collection": collection, "limit": 100, "offset": offset}
        )
    )


def test_complete_registered_graph_retains_role_duplicates_missing_empty_and_separate_facts(store):
    owner = seed_l1_graph(store)
    before = unchanged(store)
    data = FileRunGraph(Reader(store, owner, {})).snapshot()
    assert (
        data["l1_graph_complete"]
        and not data["projection"]
        and not data["l1_dependencies_complete"]
    )
    assert data["actual_io_completeness"] == "unknown" and not data["report_is_execution_fact"]
    assert data["node_kinds"] == {
        "artifact_version": 2,
        "native_run": 1,
        "run_manifest": 2,
        "edit_record": 1,
        "workspace_snapshot": 1,
        "attempt": 1,
    }
    execution = next(n["record"] for n in data["nodes"] if n["kind"] == "native_run")
    assert execution["state"] == "exited" and execution["exit_code"] == 3
    reports = [n["record"] for n in data["nodes"] if n["kind"] == "run_manifest"]
    report = next(r for r in reports if r["reported"]["inputs"])
    assert report["reported_exit_conflicts_with_native"] and report["reported"]["exit_code"] == 0
    unknown = next(r for r in reports if r["reported"]["inputs"] is None)
    assert unknown["reported"]["scripts"] == [] and unknown["reported"]["outputs"] == []
    edges = collect(store, owner, "edges")
    inputs = [e for e in edges if e.get("role") == "inputs"]
    assert len(inputs) == 106 and {e["ordinal"] for e in inputs} == set(range(106))
    assert len({e["edge_id"] for e in inputs}) == 106
    assert all(
        e["target"]["kind"] == "run_manifest"
        and e["association_state"] == "reported_only"
        and e["claim_state"] == "candidate"
        for e in inputs
    )
    assert sum(e["resolved"] for e in inputs) == 105
    assert not any(
        e["relation"] in {"consumes", "produces"}
        and (e["source"]["kind"] == "native_run" or e["target"]["kind"] == "native_run")
        for e in edges
    )
    attempt = next(n["record"] for n in data["nodes"] if n["kind"] == "attempt")
    assert len(attempt["active_claim_ids"]) == 2 and attempt["selected_content_claim_id"] is None
    assert unchanged(store) == before
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_native_execution_and_late_result_recomputed_from_entire_visible_history(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.ingest.scanner.now", lambda: T1)
    monkeypatch.setattr("rg.derive.runtime.now", lambda: T1)
    _, owner = ingest(store, tmp_path, [claude_call(timestamp=T1)])
    first = query(store, owner, {"known_until": T2, "occurred_until": T2})
    assert first["items"][0]["record"]["state"] == "requested"
    monkeypatch.setattr("rg.ingest.scanner.now", lambda: T3)
    monkeypatch.setattr("rg.derive.runtime.now", lambda: T3)
    ingest(
        store,
        tmp_path,
        [claude_call(timestamp=T1), claude_result(metadata={"exit_code": 2}, timestamp=T1)],
        project=owner,
    )
    old = query(store, owner, {"known_until": T2, "occurred_until": T2})
    assert old["items"][0]["record"]["state"] == "requested"
    latest = query(store, owner, {"known_until": T4, "occurred_until": T2, "limit": 1})
    assert (
        latest["items"][0]["record"]["state"] == "exited"
        and latest["items"][0]["record"]["exit_code"] == 2
    )
    assert latest["counts"]["observations"] == 2


def test_late_physical_observation_does_not_resolve_a_version_in_old_graph(
    store, tmp_path, monkeypatch
):
    owner, root, version = scene(store, tmp_path)
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    record(store, owner, body(inputs=[version]))
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, recorded=T3)
    old = FileRunGraph(Reader(store, owner, {"known_until": T2, "occurred_until": T4})).snapshot()
    new = FileRunGraph(Reader(store, owner, {"known_until": T4, "occurred_until": T4})).snapshot()
    assert old["node_kinds"]["artifact_version"] == 0 and old["counts"]["observations"] == 0
    assert new["node_kinds"]["artifact_version"] == 1 and new["counts"]["observations"] == 1
    assert not next(e for e in old["edges"] if e["relation"] == "consumes")["resolved"]
    assert next(e for e in new["edges"] if e["relation"] == "consumes")["resolved"]
    discovery = next(e for e in new["edges"] if e["relation"] == "discovered_from")
    assert discovery["association_state"] == "discovery_only"
    assert not any(e["relation"] == "captured_in" for e in new["edges"])


def test_scope_selects_only_explicit_reports_not_same_path_or_other_attempt_scope(
    store, monkeypatch
):
    owner = seed_l1_graph(store)
    filtered = FileRunGraph(Reader(store, owner, {"scope": SCOPE})).snapshot()
    assert filtered["node_kinds"]["run_manifest"] == 1 and filtered["node_kinds"]["native_run"] == 1
    assert (
        next(n["record"] for n in filtered["nodes"] if n["kind"] == "native_run")["scope_basis"]
        == "reported_only"
    )
    unknown = FileRunGraph(Reader(store, owner, {"scope": None})).snapshot()
    assert (
        unknown["node_kinds"]["run_manifest"] == 1
        and unknown["node_kinds"]["native_run"] == 0
        and unknown["node_kinds"]["artifact_version"] == 0
    )
    assert query(store, owner, {"scope": {"data": "different"}})["counts"]["nodes"] == 0
    attempt = next(n["record"]["entity_id"] for n in filtered["nodes"] if n["kind"] == "attempt")
    other = append(
        store, attempt, "entity_version", {"label": "别的范围"}, scope={"data": "different"}
    )
    assert (
        other
        not in next(n["record"] for n in collect(store, owner) if n["kind"] == "attempt")[
            "active_claim_ids"
        ]
    )


def test_unknown_version_never_borrows_late_foreign_project_or_current_file(
    store, tmp_path, monkeypatch
):
    owner = store.project("合成报告", [])
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    record(store, owner, body(inputs=["foreign-version"]))
    foreign, claim = original(store, store.project("另一个项目", []))
    event = store.db.execute(
        "SELECT event_id FROM raw_events ORDER BY event_id DESC LIMIT 1"
    ).fetchone()[0]
    foreign_project = store.db.execute(
        "SELECT project_id FROM entities WHERE entity_id=?", (foreign,)
    ).fetchone()[0]
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
        "evidence_event_id) VALUES ('foreign-version',?,'/synthetic/secret','sha256',"
        "'synthetic','agent_edit',?)",
        (foreign_project, event),
    )
    values = FileRunGraph(Reader(store, owner, {})).snapshot()
    assert values["node_kinds"]["artifact_version"] == 0
    assert "/synthetic/secret" not in json.dumps(values)
    assert not next(e for e in values["edges"] if e["relation"] == "consumes")["resolved"]


def test_actual_parser_edits_keep_both_versions_original_evidence_and_revision(store, tmp_path):
    before = store.revision()
    source_path, owner = ingest(
        store,
        tmp_path,
        [
            claude_call("edit", "Edit", {"file_path": "/synthetic/project/code.py"}),
            claude_result("edit", metadata=reported_edit()),
        ],
    )
    assert store.revision() > before
    source_path.unlink()
    before = unchanged(store)
    graph = FileRunGraph(Reader(store, owner, {})).snapshot()
    assert graph["node_kinds"]["edit_record"] == 1 and graph["node_kinds"]["artifact_version"] == 2
    edges = [e for e in graph["edges"] if e["relation"] in {"consumes", "produces"}]
    assert len(edges) == 2 and all(
        e["resolved"]
        and e["association_state"] == "tool_reported"
        and e["claim_state"] == "candidate"
        for e in edges
    )
    assert len(graph["evidence"]) == 2 and unchanged(store) == before


def test_edit_with_missing_preimage_retains_unknown_endpoint_and_patch_identity(store, tmp_path):
    _, owner = ingest(
        store,
        tmp_path,
        [
            claude_call(
                "write",
                "Write",
                {"file_path": "/synthetic/project/code.py", "content": "合成新增正文"},
            ),
            claude_result(
                "write",
                metadata={
                    "filePath": "/synthetic/project/code.py",
                    "content": "合成新增正文",
                    "type": "create",
                },
            ),
        ],
    )
    graph = FileRunGraph(Reader(store, owner, {})).snapshot()
    edit = next(n["record"] for n in graph["nodes"] if n["kind"] == "edit_record")
    assert edit["patch_sha256"] and edit["before_version"] is None
    assert any(
        e["source"]["record_id"] is None and not e["resolved"]
        for e in graph["edges"]
        if e["relation"] == "consumes"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"collection": "nope"},
        {"limit": True},
        {"limit": 101},
        {"offset": -1},
        {"expected_revision": False},
        {"unknown": "bad"},
        {"scope": {}},
        {"known_until": "2026-01-01"},
    ],
)
def test_invalid_inputs_do_not_write_or_silently_change_filters(store, values):
    owner = store.project("合成空图", [])
    before = unchanged(store)
    with pytest.raises(ValueError):
        query(store, owner, values)
    assert unchanged(store) == before


def test_schema_v16_upgrade_is_atomic_retains_original_and_edit_revision_trigger(
    tmp_path, monkeypatch
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=16)
    with monkeypatch.context() as change:
        change.setattr(migrations, "LATEST_VERSION", 16)
        with closing(Store(root)) as old:
            owner = seed_l1_graph(old)
            revision = old.revision()
            edit = dict(old.db.execute("SELECT * FROM edit_records").fetchone())
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 17, (*migrations.MIGRATIONS[17], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert db.execute("SELECT revision FROM graph_clock").fetchone()[0] == revision
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='edit_record_revision'"
        ).fetchone()
    with closing(Store(root)) as restored:
        assert restored.raw(1) == raw
        assert restored.revision() == revision + 1
        assert dict(restored.db.execute("SELECT * FROM edit_records").fetchone()) == edit
        assert (
            FileRunGraph(Reader(restored, owner, {})).metadata()["node_kinds"]["edit_record"] == 1
        )
        assert restored.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert restored.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='edit_record_revision'"
        ).fetchone()


def test_readonly_cli_and_export_use_same_complete_graph_and_source_window_has_no_future_context(
    store, tmp_path
):
    owner = seed_l1_graph(store)
    before = unchanged(store)
    args = [
        sys.executable,
        "-c",
        "from rg.cli.main import main; main()",
        "--data-dir",
        str(store.root),
        "l1-graph",
        "--project",
        owner,
        "--collection",
        "edges",
        "--limit",
        "100",
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (
        json.loads(result.stdout)["total"] == query(store, owner, {"collection": "edges"})["total"]
    )
    manifest, data = archive(store, {"project_id": owner, "known_until": B2, "occurred_until": B2})
    exported = unpack(data)["graph.json"]["l1"]
    expected = FileRunGraph(
        Reader(store, owner, {"known_until": B2, "occurred_until": B2})
    ).snapshot()
    assert exported["counts"] == expected["counts"]
    assert {e["event_id"] for e in exported["evidence"]} <= {
        e["event_id"] for e in unpack(data)["sources.json"]
    }
    assert (
        next(n["record"] for n in exported["nodes"] if n["kind"] == "native_run")["state"]
        == "requested"
    )
    event = next(
        n["record"]["request_event_id"] for n in collect(store, owner) if n["kind"] == "native_run"
    )
    first = source(
        store,
        {
            "project": owner,
            "event_id": str(event),
            "known_until": B2,
            "occurred_until": B2,
            "max_bytes": "4000",
        },
    )
    assert "artifact_versions" not in first and "runs" not in first and first["next_byte_offset"]
    chunks = [first["text"]]
    offset = first["next_byte_offset"]
    while offset is not None:
        window = source(
            store,
            {
                "project": owner,
                "event_id": str(event),
                "known_until": B2,
                "occurred_until": B2,
                "byte_offset": str(offset),
                "max_bytes": "4000",
            },
        )
        chunks.append(window["text"])
        offset = window["next_byte_offset"]
    assert "".join(chunks).encode() == store.raw(event)
    assert unchanged(store) == before and manifest["schema_version"] == migrations.LATEST_VERSION


def test_backup_graph_survives_deleted_original_and_objects_are_not_opened_by_graph_query(
    store, tmp_path, monkeypatch
):
    owner = seed_l1_graph(store)
    before = FileRunGraph(Reader(store, owner, {})).snapshot()
    destination = tmp_path / "backup"
    backup(store, destination)
    store.close()
    shutil.rmtree(store.root)
    with closing(Store(destination, readonly=True)) as restored:
        monkeypatch.setattr(restored.objects, "get", lambda *args: pytest.fail("图不读原件正文"))
        after = FileRunGraph(
            Reader(
                restored,
                owner,
                {"known_until": before["known_until"], "occurred_until": before["occurred_until"]},
            )
        ).snapshot()
        assert before == after


def test_http_auth_duplicate_scope_revision_and_cross_project_evidence(store, tmp_path):
    owner = seed_l1_graph(store)
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "index.html").write_text("合成页面")
    server = LocalServer(store.root, directory, 0, token="synthetic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with httpx.Client(base_url=base, trust_env=False) as client:
            assert client.get("/api/l1-graph", params={"project": owner}).status_code == 401
            headers = {"Authorization": "Bearer synthetic-token"}
            page = client.get(
                "/api/l1-graph", params={"project": owner, "collection": "edges"}, headers=headers
            )
            assert page.status_code == 200 and page.json()["total"] > 100
            assert (
                client.get(
                    "/api/l1-graph",
                    params={"project": owner, "scope": '{"x":1,"x":2}'},
                    headers=headers,
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/l1-graph",
                    params={"project": owner, "expected_revision": store.revision() - 1},
                    headers=headers,
                ).status_code
                == 409
            )
            event = next(
                n["record"]["request_event_id"]
                for n in collect(store, owner)
                if n["kind"] == "native_run"
            )
            foreign = store.project("其它项目", [])
            assert (
                client.get(
                    "/api/l1-evidence",
                    params={"project": foreign, "event_id": event},
                    headers=headers,
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/l1-evidence",
                    params={"project": owner, "event_id": event},
                    headers=headers | {"Origin": "https://example.invalid"},
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/l1-evidence",
                    params={"project": owner, "event_id": event, "max_bytes": 4000},
                    headers=headers,
                ).status_code
                == 200
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_edit_only_insertion_invalidates_all_graph_pages(store):
    owner = seed_l1_graph(store)
    first = query(store, owner, {})
    with store.transaction() as db:
        db.execute(
            "INSERT INTO edit_records SELECT 'synthetic-second-edit',project_id,session_pk,"
            "call_id,request_event_id,result_event_id,path,root_id,operation,patch_sha256,"
            "NULL,NULL,'preimage_unknown',user_modified,occurred_at,recorded_at "
            "FROM edit_records LIMIT 1"
        )
    assert store.revision() == first["revision"] + 1
    with pytest.raises(ConflictError):
        query(store, owner, {"expected_revision": first["revision"]})
    assert query(store, owner, {})["node_kinds"]["edit_record"] == 2


def test_full_record_graph_exceeds_old_display_limit_without_losing_report_ports(store):
    owner = store.project("合成大于画面上限", [])
    for _ in range(201):
        record(store, owner, body(inputs=["synthetic-unknown-version"] * 10))
    first = query(store, owner, {"collection": "edges", "limit": 100})
    assert first["total"] == 2211 and first["next_offset"] == 100
    edges = collect(
        store,
        owner,
        "edges",
        expected_revision=first["revision"],
        known_until=first["known_until"],
        occurred_until=first["occurred_until"],
    )
    assert len(edges) == 2211 and len({e["edge_id"] for e in edges}) == 2211
    assert sum(e["relation"] == "consumes" for e in edges) == 2010
    assert not any(e["resolved"] for e in edges)
