"""父线程必须由原生头记录证明；迟到、冲突与崩溃不能编造会话历史。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.parents import declaration, refresh
from rg.ingest.scanner import scan_file
from rg.query.session_parent import query
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import Store
from tests.golden.test_ingestion import lines, record

A = "01900000-0000-7000-8000-000000000001"
B = "01900000-0000-7000-8000-000000000002"
C = "01900000-0000-7000-8000-000000000003"


def header(identity=A, **kwargs):
    return {
        "type": "session_meta",
        "timestamp": "2026-10-11T00:00:00Z",
        "payload": {"id": identity, "cli_version": "0.120.0", **kwargs},
    }


def spawn(parent=B, depth=1):
    return {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": depth}}}


def add(store, tmp_path, name, rows, project=None):
    path = tmp_path / (name + ".jsonl")
    lines(path, rows)
    scan_file(store, path, "codex", project)
    pk = store.db.execute(
        "SELECT session_pk FROM source_files WHERE path=? ORDER BY file_instance_id DESC LIMIT 1",
        (str(path),),
    ).fetchone()[0]
    return path, pk


def view(store, pk, **kwargs):
    return query(store, {"session": str(pk), **kwargs})


def projected(store, pk):
    return store.db.execute(
        "SELECT parent_session_pk FROM sessions WHERE session_pk=?", (pk,)
    ).fetchone()[0]


@pytest.mark.parametrize("parent_first", [True, False])
@pytest.mark.parametrize("form", ["top", "legacy", "both"])
def test_direct_header_link_is_independent_of_import_order(store, tmp_path, parent_first, form):
    fields = {"parent_thread_id": B} if form != "legacy" else {}
    if form != "top":
        fields["source"] = spawn()
    if parent_first:
        _, parent = add(store, tmp_path, "parent", [header(B)])
    child_path, child = add(store, tmp_path, "child", [header(**fields)])
    if not parent_first:
        assert view(store, child)["state"] == "missing_parent" and projected(store, child) is None
        _, parent = add(store, tmp_path, "parent", [header(B)])
    result = view(store, child)
    assert result["state"] == "linked" and result["parent_session_pk"] == parent
    assert projected(store, child) == parent
    assert store.raw(result["observations"][0]["event_id"]) == child_path.read_bytes()
    assert json.loads(store.raw(result["parent_event_id"]))["payload"]["id"] == B
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_immediate_parent_differs_from_root_and_fork(store, tmp_path):
    _, root = add(store, tmp_path, "root", [header(C)])
    _, child = add(
        store, tmp_path, "child", [header(parent_thread_id=B, session_id=C, forked_from_id=C)]
    )
    _, parent = add(store, tmp_path, "parent", [header(B, parent_thread_id=C)])
    assert projected(store, child) == parent and projected(store, parent) == root
    assert view(store, root)["state"] == "no_parent_declared"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"session_id": B},
        {"forked_from_id": B},
        {"source": "cli"},
        {"source": {"subagent": "review"}},
        {"source": {"subagent": "compact"}},
        {"source": {"subagent": {"other": "synthetic"}}},
        {"parent_thread_id": None},
    ],
)
def test_absent_parent_never_infers_from_root_fork_source_or_filename(store, tmp_path, fields):
    add(store, tmp_path, "parent", [header(B)])
    _, child = add(store, tmp_path, B, [header(**fields)])
    assert view(store, child)["state"] == "no_parent_declared"
    assert projected(store, child) is None


@pytest.mark.parametrize(
    "fields,reason",
    [
        ({"parent_thread_id": "synthetic-secret-value"}, "invalid_top_parent"),
        ({"parent_thread_id": {}}, "invalid_top_parent"),
        ({"source": spawn("invalid")}, "invalid_spawn_parent"),
        ({"source": {"subagent": {"thread_spawn": None}}}, "invalid_spawn_parent"),
        ({"source": spawn(depth=True)}, "invalid_spawn_depth"),
        ({"source": spawn(depth=-1)}, "invalid_spawn_depth"),
        ({"source": spawn(depth=2147483648)}, "invalid_spawn_depth"),
        ({"parent_thread_id": A}, "self_parent"),
        ({"source": spawn(A)}, "self_parent"),
        ({"parent_thread_id": "invalid", "source": spawn()}, "invalid_top_parent"),
        ({"parent_thread_id": B, "source": spawn(depth=None)}, "invalid_spawn_depth"),
    ],
)
def test_malformed_declaration_cannot_use_other_field_as_fallback(store, tmp_path, fields, reason):
    add(store, tmp_path, "parent", [header(B)])
    _, child = add(store, tmp_path, "child", [header(**fields)])
    result = view(store, child)
    assert result["state"] == "invalid" and result["observations"][0]["reason"] == reason
    assert projected(store, child) is None
    assert "synthetic-secret-value" not in json.dumps(result)


def test_invalid_payload_and_identity_use_closed_reasons():
    assert declaration(None, A)["reason"] == "invalid_payload"
    assert declaration({"id": "synthetic-secret-value"}, A)["native_id"] is None
    assert declaration({"id": B, "parent_thread_id": C}, A)["reason"] == "identity_mismatch"
    assert declaration({"id": A.upper(), "parent_thread_id": B.upper()}, A)["state"] == "declared"


def test_conflicting_fields_and_late_conflicting_header_keep_evidence(store, tmp_path):
    add(store, tmp_path, "parent", [header(B)])
    path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    assert projected(store, child) is not None
    with path.open("a") as stream:
        stream.write(json.dumps(header(parent_thread_id=C)) + "\n")
    scan_file(store, path, "codex")
    assert projected(store, child) is None and view(store, child)["state"] == "conflicting"
    assert {r["parent_id"] for r in view(store, child)["observations"]} == {B, C}
    _, conflict = add(store, tmp_path, "conflict", [header(C, parent_thread_id=A, source=spawn(B))])
    row = view(store, conflict)["observations"][0]
    assert row["state"] == "conflict" and row["other_parent_id"] == B
    assert projected(store, conflict) is None


@pytest.mark.parametrize("size", [2, 3])
def test_cycles_clear_only_involved_projection_and_preserve_raw(store, tmp_path, size):
    ids = [A, B, C][:size]
    pks = [
        add(store, tmp_path, str(i), [header(identity, parent_thread_id=ids[(i + 1) % size])])[1]
        for i, identity in enumerate(ids)
    ]
    assert all(view(store, pk)["state"] == "cycle" and projected(store, pk) is None for pk in pks)
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == size
    assert (
        store.db.execute("SELECT count(*) FROM session_parent_observations").fetchone()[0] == size
    )


def test_duplicate_native_identity_is_ambiguous_and_claude_never_matches(store, tmp_path):
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    claude = tmp_path / "claude.jsonl"
    lines(claude, [record(sessionId=B)])
    scan_file(store, claude, "claude")
    assert view(store, child)["state"] == "missing_parent"
    add(store, tmp_path, "parent", [header(B)])
    side = tmp_path / "subagents"
    side.mkdir()
    add(store, side, "copy", [header(B)])
    assert view(store, child)["state"] == "ambiguous_parent" and projected(store, child) is None


def test_filename_identity_without_native_header_does_not_match(store, tmp_path):
    add(store, tmp_path, B, [{"type": "turn_context", "payload": {"cwd": "/synthetic"}}])
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    assert view(store, child)["state"] == "missing_parent"


def test_midfile_header_cannot_change_child_identity(store, tmp_path):
    add(store, tmp_path, "parent", [header(B)])
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B), header(C)])
    assert view(store, child)["state"] == "invalid"
    assert view(store, child)["observations"][-1]["reason"] == "identity_mismatch"
    assert projected(store, child) is None


def test_copies_do_not_create_science_and_declarations_stay_evidenced(store, tmp_path):
    path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    copy = tmp_path / "copy.jsonl"
    shutil.copyfile(path, copy)
    scan_file(store, copy, "codex")
    assert view(store, child)["observations_total"] == 2
    assert scan_file(store, path, "codex") == {}
    assert view(store, child)["observations_total"] == 2
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_header_observation_and_cursor_rollback_together(store, tmp_path):
    path = tmp_path / "crash.jsonl"
    lines(path, [header(parent_thread_id=B)])

    def fail():
        raise RuntimeError("合成提交前故障")

    with pytest.raises(RuntimeError):
        scan_file(store, path, "codex", fault=fail)
    for table in ("raw_events", "session_parent_observations", "parser_records"):
        assert store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
    scan_file(store, path, "codex")
    assert view(store, 1)["state"] == "missing_parent"


def test_projection_failure_recovers_on_empty_scan(store, tmp_path, monkeypatch):
    add(store, tmp_path, "parent", [header(B)])
    path = tmp_path / "child.jsonl"
    lines(path, [header(parent_thread_id=B)])

    def fail(*args):
        raise RuntimeError("合成投影故障")

    with monkeypatch.context() as patch:
        patch.setattr("rg.ingest.parents.refresh", fail)
        with pytest.raises(RuntimeError):
            scan_file(store, path, "codex")
    assert projected(store, 2) is None and view(store, 2)["state"] == "linked"
    assert scan_file(store, path, "codex") == {} and projected(store, 2) == 1


def test_observations_are_immutable(store, tmp_path):
    add(store, tmp_path, "child", [header(parent_thread_id=B)])
    for sql in (
        "UPDATE session_parent_observations SET parent_id=NULL",
        "DELETE FROM session_parent_observations",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with store.transaction() as db:
                db.execute(sql)


def test_atomic_upgrade_and_old_header_recovery(tmp_path, monkeypatch):
    root = tmp_path / "data"
    path = tmp_path / "old.jsonl"
    lines(path, [header(parent_thread_id=B)])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            scan_file(old, path, "codex")
    statements = migrations.MIGRATIONS[21]
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 21, statements + ("SELECT missing_column",))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name=?", ("session_parent_observations",)
            ).fetchone()[0]
            == 0
        )
    with closing(Store(root)) as current:
        assert view(current, 1)["state"] == "metadata_incomplete"
        assert scan_file(current, path, "codex") == {}
        assert view(current, 1)["state"] == "missing_parent"
        assert current.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1


def test_backup_keeps_link_and_evidence_after_sources_disappear(store, tmp_path):
    child_path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    parent_path, _ = add(store, tmp_path, "parent", [header(B)])
    expected = view(store, child)
    backup(store, tmp_path / "backup")
    child_path.unlink()
    parent_path.unlink()
    with closing(Store(tmp_path / "backup", readonly=True)) as restored:
        assert view(restored, child) == expected
        assert json.loads(restored.raw(expected["parent_event_id"]))["payload"]["id"] == B


def test_full_set_determines_state_even_when_recent_observations_are_bounded(store, tmp_path):
    _, child = add(
        store,
        tmp_path,
        "child",
        [header(parent_thread_id=C)] + [header(parent_thread_id=B) for _ in range(25)],
    )
    result = view(store, child)
    assert result["observations_total"] == 26 and result["observations_partial"]
    assert len(result["observations"]) == 20 and result["state"] == "conflicting"
    assert {r["parent_id"] for r in result["observations"]} == {B}


def test_cross_project_parent_is_not_exposed_or_inherited(store, tmp_path):
    project, other = store.project("合成甲", []), store.project("合成乙", [])
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)], project)
    _, parent = add(store, tmp_path, "parent", [header(B)], other)
    result = view(store, child, project=project)
    assert projected(store, child) is None and result["state"] == "outside_project"
    assert (
        result["parent_session_pk"]
        is result["parent_native_id"]
        is result["parent_event_id"]
        is None
    )
    with pytest.raises(ValueError, match="不属于"):
        view(store, parent, project=project)
    assert (
        store.db.execute("SELECT project_id FROM sessions WHERE session_pk=?", (child,)).fetchone()[
            0
        ]
        == project
    )


def test_project_clear_drops_only_owned_observations_and_resets_retained_pointer(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        target, retained = store.project("清除甲", []), store.project("保留乙", [])
        _, parent = add(store, tmp_path, "parent", [header(B)], target)
        path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)], retained)
        assert projected(store, child) is None and view(store, child)["state"] == "outside_project"
    preview = clear.preview(root, target)
    assert preview["blockers"] == []
    clear.execute(root, target, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert view(store, child)["state"] == "missing_parent" and projected(store, child) is None
        assert view(store, child)["observations_total"] == 1 and path.is_file()
        refresh(store)
        assert projected(store, child) is None


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"session": "0"},
        {"session": "-1"},
        {"session": "9223372036854775808"},
        {"session": "x' OR 1=1"},
        {"session": "1", "extra": "x"},
        {"session": "999"},
    ],
)
def test_invalid_query_is_bounded_and_parameter_bound(store, values):
    with pytest.raises(ValueError):
        query(store, values)


def test_actual_cli_authenticated_http_and_evidence_share_read_only_view(store, tmp_path):
    project = store.project("合成HTTP", [])
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)], project)
    add(store, tmp_path, "parent", [header(B)], project)
    expected, before = view(store, child, project=project), list(store.db.iterdump())
    output = subprocess.run(
        [
            str(Path(sys.executable).with_name("rg")),
            "--data-dir",
            str(store.root),
            "session-parent",
            "--session",
            str(child),
            "--project",
            project,
        ],
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0 and json.loads(output.stdout) == expected
    (tmp_path / "index.html").write_text("合成界面")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-parent-http")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            params = {"session": str(child), "project": project}
            assert client.get("/api/session-parent", params=params).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.get("/api/session-parent", params=params)
            assert response.status_code == 200 and response.json() == expected
            assert response.headers["Cache-Control"] == "no-store"
            evidence = client.get("/api/evidence/1")
            assert evidence.status_code == 200 and evidence.json()["session_parent"] == expected
            assert client.get("/api/session-parent", params={"session": "0"}).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive() and list(store.db.iterdump()) == before


def test_actual_process_exit_before_commit_leaves_no_parent_fact(tmp_path):
    root, path = tmp_path / "data", tmp_path / "crash.jsonl"
    lines(path, [header(parent_thread_id=B)])
    script = (
        "import os,sys\nfrom pathlib import Path\n"
        "from rg.store.database import Store\nfrom rg.ingest.scanner import scan_file\n"
        "s=Store(Path(sys.argv[1]))\n"
        "scan_file(s,Path(sys.argv[2]),'codex',fault=lambda:os._exit(79))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 79
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
        assert (
            store.db.execute("SELECT count(*) FROM session_parent_observations").fetchone()[0] == 0
        )
        assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
        scan_file(store, path, "codex")
        assert view(store, 1)["observations_total"] == 1


def test_legacy_catalog_backfill_keeps_all_header_conflicts_and_old_evidence(tmp_path, monkeypatch):
    root, path = tmp_path / "data", tmp_path / "old.jsonl"
    lines(
        path,
        [
            header(parent_thread_id=B),
            {"type": "turn_context", "payload": {"cwd": "/synthetic"}},
            header(parent_thread_id=C),
        ],
    )
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 20)
        with closing(Store(root)) as old:
            scan_file(old, path, "codex")
            event_times = [r[0] for r in old.db.execute("SELECT recorded_at FROM raw_events")]
    with closing(Store(root)) as store:
        assert view(store, 1)["state"] == "metadata_incomplete"
        assert scan_file(store, path, "codex") == {}
        result = view(store, 1)
        assert result["state"] == "conflicting" and result["observations_total"] == 2
        assert [r[0] for r in store.db.execute("SELECT recorded_at FROM raw_events")] == event_times
        assert [r["event_id"] for r in result["observations"]] == [1, 3]
        assert result["observations"][0]["recorded_at"] >= event_times[0]


def test_read_snapshot_is_consistent_when_late_conflict_arrives_during_resolution(
    store, tmp_path, monkeypatch
):
    import rg.query.session_parent as module

    path, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    add(store, tmp_path, "parent", [header(B)])
    expected = view(store, child)
    original = module.resolve

    def concurrent(db):
        with path.open("a") as stream:
            stream.write(json.dumps(header(parent_thread_id=C)) + "\n")
        with closing(Store(store.root)) as writer:
            scan_file(writer, path, "codex")
        return original(db)

    with closing(Store(store.root, readonly=True)) as reader:
        with monkeypatch.context() as patch:
            patch.setattr(module, "resolve", concurrent)
            assert view(reader, child) == expected
        assert view(reader, child)["state"] == "conflicting"


def test_legacy_codex_directory_projection_is_removed_without_changing_claude(store, tmp_path):
    side = tmp_path / "subagents"
    side.mkdir()
    _, parent = add(store, tmp_path, "parent", [header(A)])
    _, child = add(store, side, "child", [header(A)])
    with store.transaction() as db:
        db.execute("UPDATE sessions SET parent_session_pk=? WHERE session_pk=?", (parent, child))
    claude_parent = tmp_path / "claude.jsonl"
    claude_child = side / "claude-child.jsonl"
    lines(claude_parent, [record(sessionId="claude-synthetic")])
    lines(claude_child, [record(sessionId="claude-synthetic", agentId="child")])
    scan_file(store, claude_child, "claude")
    scan_file(store, claude_parent, "claude")
    before = [
        tuple(r)
        for r in store.db.execute(
            "SELECT session_pk,parent_session_pk FROM sessions WHERE tool='claude'"
        )
    ]
    refresh(store)
    assert projected(store, child) is None and view(store, child)["state"] == "no_parent_declared"
    assert [
        tuple(r)
        for r in store.db.execute(
            "SELECT session_pk,parent_session_pk FROM sessions WHERE tool='claude'"
        )
    ] == before


def test_invalid_parent_ancestry_does_not_negate_direct_native_identity(store, tmp_path):
    _, parent = add(store, tmp_path, "parent", [header(B, parent_thread_id="invalid")])
    _, child = add(store, tmp_path, "child", [header(parent_thread_id=B)])
    assert view(store, parent)["state"] == "invalid"
    assert view(store, child)["state"] == "linked" and projected(store, child) == parent
