"""父UUID只证明来源记录声明；副本、迟到和故障不能改写正文或猜出历史。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest import claude_chain
from rg.ingest.scanner import scan_file
from rg.ingest.watch import cycle
from rg.query.event_chain import query
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.locking import exclusive
from rg.store.objects import digest
from tests.golden.test_ingestion import lines, record

A = "01900000-0000-7000-8000-000000000001"
B = "01900000-0000-7000-8000-000000000002"
C = "01900000-0000-7000-8000-000000000003"
ABSENT = object()


def message(identity=A, parent=ABSENT, side=ABSENT, **fields):
    value = record(uuid=identity, **fields)
    if parent is not ABSENT:
        value["parentUuid"] = parent
    if side is not ABSENT:
        value["isSidechain"] = side
    return value


def add(store, tmp_path, name, rows, project=None):
    path = tmp_path / (name + ".jsonl")
    lines(path, rows)
    result = scan_file(store, path, "claude", project)
    file_id = store.db.execute(
        "SELECT file_instance_id FROM source_files WHERE path=? "
        "ORDER BY file_instance_id DESC LIMIT 1",
        (str(path),),
    ).fetchone()[0]
    events = [
        r[0]
        for r in store.db.execute(
            "SELECT event_id FROM raw_events WHERE file_instance_id=? "
            "ORDER BY byte_start,record_index",
            (file_id,),
        )
    ]
    return path, events, result


def view(store, event=1, **fields):
    return query(store, {"event": str(event), **fields})


def legacy(tmp_path, monkeypatch, rows, name="old"):
    root, path = tmp_path / "data", tmp_path / (name + ".jsonl")
    lines(path, rows)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 21)
        with closing(Store(root)) as old:
            scan_file(old, path, "claude")
            before = [
                tuple(r) for r in old.db.execute("SELECT * FROM raw_events ORDER BY event_id")
            ]
    return root, path, before


@pytest.mark.parametrize(
    "parent,state",
    [
        (ABSENT, "parent_not_declared"),
        (None, "null_parent"),
        ("invalid", "invalid_parent"),
        (1, "invalid_parent"),
    ],
)
def test_missing_null_and_invalid_declarations_are_distinct(store, tmp_path, parent, state):
    add(store, tmp_path, "record", [message(parent=parent)])
    result = view(store)
    assert result["state"] == result["ancestry_state"] == state
    assert result["parent_references"] == [] and result["source_metadata_complete"]
    assert result["basis"] == "direct_record" and result["native_uuid"] == A


@pytest.mark.parametrize(
    "side,value,state",
    [
        (True, True, "declared"),
        (False, False, "declared"),
        (ABSENT, None, "missing"),
        (None, None, "invalid"),
        (1, None, "invalid"),
        ("false", None, "invalid"),
    ],
)
def test_branch_marker_is_strict_and_never_infers_parent(store, tmp_path, side, value, state):
    add(store, tmp_path, "record", [message(parent=None, side=side)])
    result = view(store)
    assert result["is_sidechain"] is value and result["sidechain_state"] == state
    assert result["state"] == "null_parent"
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


@pytest.mark.parametrize("parent_first", [True, False])
def test_native_parent_resolves_late_without_sequence_rewrite(store, tmp_path, parent_first):
    rows = [message(B, None), message(A, B)]
    if not parent_first:
        rows.reverse()
    _, events, _ = add(store, tmp_path, "same", rows)
    event = events[1] if parent_first else events[0]
    result = view(store, event)
    assert result["state"] == "linked" and result["resolution_scope"] == "source_file"
    assert result["parent_references"][0]["event_id"] == (events[0] if parent_first else events[1])
    assert result["ancestry_state"] == "null_parent"
    assert [r[0] for r in store.db.execute("SELECT seq FROM raw_events ORDER BY event_id")] == [
        1,
        2,
    ]


def test_project_uuid_fallback_updates_on_late_parent_without_writes_in_query(store, tmp_path):
    _, child, _ = add(store, tmp_path, "child", [message(A, B)])
    assert view(store, child[0])["state"] == "missing_parent"
    add(store, tmp_path, "parent", [message(B, None)])
    before = list(store.db.iterdump())
    result = view(store, child[0])
    assert result["state"] == "linked" and result["resolution_scope"] == "project_uuid"
    assert list(store.db.iterdump()) == before


def test_uuid_normalization_does_not_change_saved_native_body_key(store, tmp_path):
    identity = "019abcde-0000-7000-8000-000000000002"
    add(store, tmp_path, "same", [message(A, identity.upper()), message(identity, None)])
    assert view(store)["parent_uuid"] == identity and view(store)["state"] == "linked"
    assert (
        store.db.execute("SELECT native_id FROM raw_events WHERE event_id=1").fetchone()[0]
        == A + ":0"
    )
    assert json.loads(store.raw(1))["parentUuid"] == identity.upper()


def test_multiblock_records_share_physical_declaration_and_anchor(store, tmp_path):
    _, events, _ = add(
        store,
        tmp_path,
        "multi",
        [
            message(B, None),
            message(
                A,
                B,
                True,
                message={
                    "content": [
                        {"type": "text", "text": "决定甲"},
                        {"type": "tool_use", "id": "synthetic-call", "name": "Read", "input": {}},
                    ]
                },
            ),
        ],
    )
    assert len(events) == 3
    first, second = view(store, events[1]), view(store, events[2])
    assert first["record_event_id"] == second["record_event_id"] == events[1]
    assert first["parent_references"] == second["parent_references"]
    assert first["is_sidechain"] is second["is_sidechain"] is True
    assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 2


def test_fork_copies_keep_own_parent_and_marker_while_body_remains_deduplicated(store, tmp_path):
    _, one, _ = add(store, tmp_path, "one", [message(B, None), message(A, B, False)])
    _, two, result = add(store, tmp_path, "two", [message(C, None), message(A, C, True)])
    assert result["aliases"] == 1
    assert view(store, one[1])["parent_uuid"] == B and view(store, two[1])["parent_uuid"] == C
    assert view(store, one[1])["is_sidechain"] is False
    assert view(store, two[1])["is_sidechain"] is True
    assert view(store, one[1])["parent_references"][0]["event_id"] == one[0]
    assert view(store, two[1])["parent_references"][0]["event_id"] == two[0]
    assert (
        store.db.execute("SELECT alias_of FROM raw_events WHERE event_id=?", (two[1],)).fetchone()[
            0
        ]
        == one[1]
    )
    assert json.loads(store.raw(two[1]))["parentUuid"] == C


@pytest.mark.parametrize(
    "fields", [{"parent": C}, {"parent": None}, {"side": True}, {"text": "不同正文"}]
)
def test_same_source_conflicting_record_is_not_selected_arbitrarily(store, tmp_path, fields):
    other = message(A, **fields) if "parent" in fields else message(A, B, **fields)
    add(store, tmp_path, "same", [message(B, None), message(A, B, False), other])
    for event in [2, 3]:
        assert view(store, event)["state"] == "conflicting_record"
        assert view(store, event)["parent_references"] == []


@pytest.mark.parametrize("fields", [{"parent": C}, {"side": True}, {"text": "冲突正文"}])
def test_global_uuid_copies_with_conflicting_parent_marker_or_body_are_ambiguous(
    store, tmp_path, fields
):
    add(store, tmp_path, "first", [message(B, None, False)])
    other = message(B, **fields) if "parent" in fields else message(B, None, **fields)
    add(store, tmp_path, "second", [other])
    _, events, _ = add(store, tmp_path, "child", [message(A, B)])
    result = view(store, events[0])
    assert result["state"] == "ambiguous_parent" and result["parent_references"] == []


def test_identical_copies_link_uuid_but_never_choose_source_context_for_ancestry(store, tmp_path):
    for i in range(23):
        add(store, tmp_path, f"copy{i}", [message(B, None)])
    _, child, _ = add(store, tmp_path, "child", [message(A, B)])
    result = view(store, child[0])
    assert result["state"] == "linked" and result["parent_references_total"] == 23
    assert len(result["parent_references"]) == 20 and result["parent_references_partial"]
    assert result["parent_source_contexts"] == 23
    assert result["ancestry_state"] == "multiple_source_contexts"
    assert len({r["file_instance_id"] for r in result["parent_references"]}) == 20


def test_local_source_parent_takes_precedence_over_other_fork_context(store, tmp_path):
    add(store, tmp_path, "foreign-context", [message(B, C)])
    add(store, tmp_path, "local", [message(B, None), message(A, B)])
    result = view(store, 3)
    assert result["state"] == "linked" and result["resolution_scope"] == "source_file"
    assert result["ancestry_state"] == "null_parent"
    assert result["parent_references"][0]["event_id"] == 2


@pytest.mark.parametrize(
    "rows",
    [
        [message(A, A)],
        [message(A, B), message(B, A)],
        [message(A, B), message(B, C), message(C, A)],
    ],
)
def test_cycles_are_explicit_without_parent_navigation(store, tmp_path, rows):
    add(store, tmp_path, "cycle", rows)
    for event in range(1, len(rows) + 1):
        result = view(store, event)
        assert result["state"] == result["ancestry_state"] == "cycle"
        assert result["parent_references"] == []


def test_ancestor_cycle_does_not_negate_child_direct_declaration(store, tmp_path):
    add(store, tmp_path, "cycle", [message(A, B), message(B, C), message(C, B)])
    result = view(store)
    assert result["state"] == "linked" and result["ancestry_state"] == "cycle"
    assert result["parent_references"][0]["event_id"] == 2


def test_ancestry_is_bounded_and_depth_limit_does_not_assert_termination(store, tmp_path):
    identities = [str(UUID(int=i + 1)) for i in range(66)]
    rows = [message(x, identities[i + 1] if i < 65 else None) for i, x in enumerate(identities)]
    add(store, tmp_path, "long", rows)
    result = view(store)
    assert result["state"] == "linked" and result["ancestry_state"] == "depth_limit"
    assert result["ancestry_steps"] == result["ancestry_limit"] == 64


def test_excluded_and_unknown_records_retain_metadata_without_reentering_science(store, tmp_path):
    add(
        store,
        tmp_path,
        "excluded",
        [
            message(B, None, isCompactSummary=True),
            message(A, B, message={"content": "<rg-context>注入结论</rg-context>"}),
            message(C, A, type="new-unknown-record"),
        ],
    )
    assert view(store)["state"] == "null_parent" and view(store, 2)["state"] == "linked"
    assert view(store, 3)["state"] == "linked"
    assert (
        store.db.execute(
            "SELECT count(*) FROM raw_events WHERE exclude_reason IS NOT NULL"
        ).fetchone()[0]
        == 3
    )
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert json.loads(store.raw(3))["type"] == "new-unknown-record"


def test_invalid_uuid_not_copied_to_metadata_or_response(store, tmp_path):
    add(
        store,
        tmp_path,
        "invalid",
        [message("synthetic-private-identifier", "synthetic-private-parent")],
    )
    result = view(store)
    assert result["state"] == "uuid_unavailable" and result["uuid_state"] == "invalid"
    assert "synthetic-private" not in json.dumps(result)
    assert "synthetic-private" not in str(
        tuple(store.db.execute("SELECT * FROM claude_chain_records").fetchone())
    )


def test_observation_catalog_and_both_cursors_rollback_together(store, tmp_path):
    path = tmp_path / "fault.jsonl"
    lines(path, [message(A, B)])

    def fail():
        raise RuntimeError("合成提交前中断")

    with pytest.raises(RuntimeError):
        scan_file(store, path, "claude", fault=fail)
    for table in ["raw_events", "claude_chain_records", "parser_records"]:
        assert store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert tuple(
        store.db.execute(
            "SELECT committed_offset,chain_observed_offset FROM source_files"
        ).fetchone()
    ) == (0, 0)
    scan_file(store, path, "claude")
    assert view(store)["state"] == "missing_parent"
    before = list(store.db.iterdump())
    assert scan_file(store, path, "claude") == {}
    assert list(store.db.iterdump()) == before


def test_actual_process_exit_before_commit_does_not_leave_chain_fact(tmp_path):
    root, path = tmp_path / "data", tmp_path / "crash.jsonl"
    lines(path, [message(A, B)])
    script = (
        "import os,sys\nfrom pathlib import Path\nfrom rg.store.database import Store\n"
        "from rg.ingest.scanner import scan_file\ns=Store(Path(sys.argv[1]))\n"
        "scan_file(s,Path(sys.argv[2]),'claude',fault=lambda:os._exit(79))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(path)], capture_output=True, text=True
    )
    assert result.returncode == 79
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
        assert tuple(
            store.db.execute(
                "SELECT committed_offset,chain_observed_offset FROM source_files"
            ).fetchone()
        ) == (0, 0)
        scan_file(store, path, "claude")
        assert view(store)["state"] == "missing_parent"


def test_migration_failure_leaves_v21_and_old_l0_intact(tmp_path, monkeypatch):
    root, _, before = legacy(tmp_path, monkeypatch, [message(A, None)])
    with monkeypatch.context() as patch:
        patch.setitem(
            migrations.MIGRATIONS, 22, migrations.MIGRATIONS[22] + ("SELECT missing_column",)
        )
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 21
        assert "chain_observed_offset" not in [
            r[1] for r in db.execute("PRAGMA table_info(source_files)")
        ]
        assert db.execute("SELECT * FROM raw_events ORDER BY event_id").fetchall() == before
    with closing(Store(root)) as store:
        assert view(store)["state"] == "unobserved"


def test_old_batch_and_new_tail_do_not_skip_old_metadata_gaps(tmp_path, monkeypatch):
    rows = [message(str(UUID(int=i + 1)), None) for i in range(260)]
    root, path, before = legacy(tmp_path, monkeypatch, rows)
    with closing(Store(root)) as store:
        assert view(store)["state"] == "unobserved"
        with path.open("a") as stream:
            stream.write(json.dumps(message(A, None)) + "\n")
        assert scan_file(store, path, "claude")["events"] == 1
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 257
        assert view(store, 261)["state"] == "metadata_incomplete"
        assert view(store, 260)["state"] == "unobserved"
        assert scan_file(store, path, "claude") == {}
        assert view(store, 261)["state"] == "null_parent"
        assert view(store, 260)["source_metadata_complete"]
        assert [
            tuple(r)
            for r in store.db.execute(
                "SELECT * FROM raw_events WHERE event_id<=260 ORDER BY event_id"
            )
        ] == before
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 261


def test_source_gone_normal_cycle_backfills_saved_objects_and_backup(tmp_path, monkeypatch):
    root, path, before = legacy(tmp_path, monkeypatch, [message(B, None), message(A, B)])
    path.unlink()
    with closing(Store(root)) as store:
        assert cycle(store)["chain_backfilled"] == 2
        expected = view(store, 2)
        assert expected["state"] == "linked" and expected["source_metadata_complete"]
        assert [
            tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")
        ] == before
        old_time = store.db.execute(
            "SELECT recorded_at FROM raw_events WHERE event_id=2"
        ).fetchone()[0]
        assert expected["recorded_at"] >= old_time
        backup(store, tmp_path / "backup")
    with closing(Store(tmp_path / "backup", readonly=True)) as restored:
        assert view(restored, 2) == expected
        assert json.loads(restored.raw(expected["parent_references"][0]["event_id"]))["uuid"] == B


def test_backfill_failure_rolls_back_cursor_and_retries_without_l0_edits(tmp_path, monkeypatch):
    root, _, before = legacy(tmp_path, monkeypatch, [message(A, None)])
    original = claude_chain.observe

    def fail(*args):
        original(*args)
        raise RuntimeError("合成补记提交前中断")

    with closing(Store(root)) as store:
        with monkeypatch.context() as patch:
            patch.setattr(claude_chain, "observe", fail)
            assert claude_chain.backfill_saved(store)["chain_errors"] == 1
        assert view(store)["state"] == "unobserved"
        assert store.db.execute("SELECT chain_observed_offset FROM source_files").fetchone()[0] == 0
        assert claude_chain.backfill_saved(store)["chain_backfilled"] == 1
        assert [
            tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")
        ] == before


def test_missing_object_does_not_become_absent_metadata_and_other_sources_continue(
    tmp_path, monkeypatch
):
    root, _, _ = legacy(tmp_path, monkeypatch, [message(A, None)])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 21)
        with closing(Store(root)) as old:
            _, events, _ = add(old, tmp_path, "other", [message(B, None)])
            sha = old.db.execute(
                "SELECT object_sha256 FROM raw_events WHERE event_id=1"
            ).fetchone()[0]
            old.objects.path(sha).unlink()
    with closing(Store(root)) as store:
        result = claude_chain.backfill_saved(store)
        assert result["chain_errors"] == result["chain_backfilled"] == 1
        assert (
            view(store)["state"] == "unobserved"
            and view(store, events[0])["state"] == "null_parent"
        )
        assert view(store, events[0])["scope_metadata_incomplete"]


def test_locked_source_keeps_pending_metadata_and_retry_is_idempotent(tmp_path, monkeypatch):
    root, path, _ = legacy(tmp_path, monkeypatch, [message(A, None)])
    with closing(Store(root)) as store:
        with exclusive(root / "locks/sources" / (digest(str(path).encode()) + ".lock"), "合成锁"):
            assert claude_chain.backfill_saved(store)["chain_busy"] == 1
        assert claude_chain.backfill_saved(store)["chain_backfilled"] == 1
        assert claude_chain.backfill_saved(store) == {}


def test_project_fallback_waits_for_old_scope_metadata(tmp_path, monkeypatch):
    root, _, _ = legacy(tmp_path, monkeypatch, [message(B, None)])
    with closing(Store(root)) as store:
        _, child, _ = add(store, tmp_path, "child", [message(A, B)])
        assert view(store, child[0])["state"] == "metadata_incomplete"
        assert view(store, child[0])["parent_references"] == []
        claude_chain.backfill_saved(store)
        assert view(store, child[0])["state"] == "linked"


def test_cross_project_ids_never_expose_parent_references_or_inherit_project(store, tmp_path):
    first, second = store.project("合成甲", []), store.project("合成乙", [])
    add(store, tmp_path, "parent", [message(B, None, sessionId="parent")], second)
    _, child, _ = add(store, tmp_path, "child", [message(A, B, sessionId="child")], first)
    result = view(store, child[0], project=first)
    assert result["state"] == "outside_project" and result["parent_references"] == []
    assert result["parent_uuid"] == B
    assert (
        store.db.execute(
            "SELECT project_id FROM sessions WHERE session_pk=?", (result["session_pk"],)
        ).fetchone()[0]
        == first
    )
    with pytest.raises(ValueError, match="不属于"):
        view(store, 1, project=first)


def test_whole_project_clear_removes_owned_chain_without_removing_other_child(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        target, retained = store.project("清除甲", []), store.project("保留乙", [])
        add(store, tmp_path, "parent", [message(B, None, sessionId="parent")], target)
        _, child, _ = add(store, tmp_path, "child", [message(A, B, sessionId="child")], retained)
        event = child[0]
        assert view(store, event)["state"] == "outside_project"
    preview = clear.preview(root, target)
    assert preview["blockers"] == []
    clear.execute(root, target, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 1
        assert view(store, event)["state"] == "missing_parent"
        assert json.loads(store.raw(event))["uuid"] == A


@pytest.mark.parametrize(
    "sql", ["DELETE FROM claude_chain_records", "UPDATE claude_chain_records SET parent_uuid=NULL"]
)
def test_observations_are_append_only(store, tmp_path, sql):
    add(store, tmp_path, "same", [message(A, None)])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(sql)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"event": "0"},
        {"event": "-1"},
        {"event": "9223372036854775808"},
        {"event": "x' OR 1=1"},
        {"event": "1", "extra": "x"},
        {"event": "999"},
    ],
)
def test_invalid_query_is_parameter_bound(store, values):
    with pytest.raises(ValueError):
        query(store, values)


def test_cli_authenticated_http_and_embedded_evidence_share_readonly_snapshot(store, tmp_path):
    add(store, tmp_path, "same", [message(B, None), message(A, B)])
    expected, before = view(store, 2), list(store.db.iterdump())
    output = subprocess.run(
        [
            str(Path(sys.executable).with_name("rg")),
            "--data-dir",
            str(store.root),
            "event-chain",
            "--event",
            "2",
        ],
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0 and json.loads(output.stdout) == expected
    (tmp_path / "index.html").write_text("合成界面")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-event-chain-http")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert client.get("/api/event-chain", params={"event": 2}).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.get("/api/event-chain", params={"event": 2})
            assert response.status_code == 200 and response.json() == expected
            assert response.headers["Cache-Control"] == "no-store"
            assert client.get("/api/evidence/2").json()["event_chain"] == expected
            assert client.get("/api/event-chain", params={"event": 0}).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive() and list(store.db.iterdump()) == before


def test_snapshot_keeps_resolution_consistent_when_same_source_conflict_arrives(
    store, tmp_path, monkeypatch
):
    import rg.query.event_chain as module

    path, _, _ = add(store, tmp_path, "same", [message(B, None), message(A, B)])
    expected, original = view(store, 2), module._parent
    added = False

    def concurrent(db, node, project):
        nonlocal added
        if not added:
            added = True
            with path.open("a") as stream:
                stream.write(json.dumps(message(A, C)) + "\n")
            with closing(Store(store.root)) as writer:
                scan_file(writer, path, "claude")
        return original(db, node, project)

    with closing(Store(store.root, readonly=True)) as reader:
        with monkeypatch.context() as patch:
            patch.setattr(module, "_parent", concurrent)
            assert view(reader, 2) == expected
        assert view(reader, 2)["state"] == "conflicting_record"


def test_rotation_keeps_both_physical_chains_and_originals(store, tmp_path):
    path, first, _ = add(store, tmp_path, "rotating", [message(B, None), message(A, B)])
    original = store.raw(first[1])
    path.rename(tmp_path / "rotated.jsonl")
    lines(path, [message(C, None), message(A, C)])
    assert scan_file(store, path, "claude")["aliases"] == 1
    assert view(store, first[1])["parent_uuid"] == B
    assert view(store, 4)["parent_uuid"] == C and view(store, 4)["state"] == "linked"
    assert view(store, first[1])["file_instance_id"] != view(store, 4)["file_instance_id"]
    assert store.raw(first[1]) == original


def test_other_tool_uuid_and_nearby_record_do_not_prove_parent(store, tmp_path):
    codex = tmp_path / "codex.jsonl"
    lines(codex, [{"type": "session_meta", "payload": {"id": B}}])
    scan_file(store, codex, "codex")
    _, child, _ = add(store, tmp_path, "child", [message(C, None), message(A, B)])
    assert view(store, child[1])["state"] == "missing_parent"
    assert view(store, 1)["state"] == "unsupported"


def test_same_parent_uuid_tool_result_body_conflict_is_not_collapsed(store, tmp_path):
    content = [{"type": "tool_result", "tool_use_id": "synthetic-call", "content": "相同摘要"}]
    add(
        store,
        tmp_path,
        "first",
        [message(B, None, message={"content": content}, toolUseResult={"status": 0})],
    )
    _, _, result = add(
        store,
        tmp_path,
        "second",
        [message(B, None, message={"content": content}, toolUseResult={"status": 1})],
    )
    assert result.get("aliases", 0) == 0
    _, child, _ = add(store, tmp_path, "child", [message(A, B)])
    assert view(store, child[0])["state"] == "ambiguous_parent"


def test_cycle_does_not_double_backfill_a_live_source_in_one_round(tmp_path, monkeypatch):
    rows = [message(str(UUID(int=i + 1)), None) for i in range(520)]
    root, _, _ = legacy(tmp_path, monkeypatch, rows)
    with closing(Store(root)) as store:
        cycle(store)
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 256
        cycle(store)
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 512
        cycle(store)
        assert view(store, 520)["state"] == "null_parent"


def test_damaged_compressed_object_is_counted_and_leaves_unknown_metadata(tmp_path, monkeypatch):
    root, _, _ = legacy(tmp_path, monkeypatch, [message(A, None)])
    with closing(Store(root)) as store:
        sha = store.db.execute("SELECT object_sha256 FROM raw_events WHERE event_id=1").fetchone()[
            0
        ]
        store.objects.path(sha).write_bytes(b"synthetic-corrupt-object")
        assert claude_chain.backfill_saved(store)["chain_errors"] == 1
        assert view(store)["state"] == "unobserved"
        assert not view(store)["source_metadata_complete"]


def test_old_bad_json_is_preserved_and_metadata_cursor_can_continue(tmp_path, monkeypatch):
    root, path = tmp_path / "data", tmp_path / "bad.jsonl"
    raw = b"broken\n" + (json.dumps(message(A, None)) + "\n").encode()
    path.write_bytes(raw)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 21)
        with closing(Store(root)) as old:
            scan_file(old, path, "claude")
    path.unlink()
    with closing(Store(root)) as store:
        assert claude_chain.backfill_saved(store)["chain_backfilled"] == 2
        assert (
            view(store)["state"] == "uuid_unavailable" and view(store, 2)["state"] == "null_parent"
        )
        assert store.raw(1) == b"broken\n" and view(store, 2)["source_metadata_complete"]


def test_actual_exit_during_saved_backfill_rolls_back_observation_and_cursor(tmp_path, monkeypatch):
    root, path, _ = legacy(tmp_path, monkeypatch, [message(A, None)])
    path.unlink()
    script = (
        "import os,sys\nfrom pathlib import Path\nfrom rg.store.database import Store\n"
        "from rg.ingest import claude_chain as c\ns=Store(Path(sys.argv[1]))\n"
        "original=c.observe\ndef fail(*args):\n original(*args)\n os._exit(79)\n"
        "c.observe=fail\nc.backfill_saved(s)\n"
    )
    output = subprocess.run(
        [sys.executable, "-c", script, str(root)], capture_output=True, text=True
    )
    assert output.returncode == 79
    with closing(Store(root)) as store:
        assert view(store)["state"] == "unobserved"
        assert store.db.execute("SELECT chain_observed_offset FROM source_files").fetchone()[0] == 0
        assert claude_chain.backfill_saved(store)["chain_backfilled"] == 1
        assert view(store)["state"] == "null_parent"


def test_v21_backup_restored_after_source_loss_can_upgrade_and_backfill(tmp_path, monkeypatch):
    root, path, _ = legacy(tmp_path, monkeypatch, [message(B, None), message(A, B)])
    destination = tmp_path / "old-backup"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 21)
        with closing(Store(root)) as old:
            backup(old, destination)
    path.unlink()
    with closing(Store(destination)) as restored:
        assert view(restored, 2)["state"] == "unobserved"
        assert cycle(restored)["chain_backfilled"] == 2
        assert view(restored, 2)["state"] == "linked"


def test_backfill_mid_batch_failure_is_atomic_not_partially_counted(tmp_path, monkeypatch):
    root, _, _ = legacy(tmp_path, monkeypatch, [message(B, None), message(A, B)])
    original = claude_chain.observe

    def fail(db, file_id, event, value):
        original(db, file_id, event, value)
        if event == 2:
            raise RuntimeError("合成批内中断")

    with closing(Store(root)) as store:
        with monkeypatch.context() as patch:
            patch.setattr(claude_chain, "observe", fail)
            assert claude_chain.backfill_saved(store) == {"chain_errors": 1}
        assert store.db.execute("SELECT count(*) FROM claude_chain_records").fetchone()[0] == 0
        assert store.db.execute("SELECT chain_observed_offset FROM source_files").fetchone()[0] == 0
        assert claude_chain.backfill_saved(store) == {"chain_backfilled": 2}
        assert view(store, 2)["state"] == "linked"


def test_repeated_uuid_in_different_source_context_does_not_create_false_cycle(store, tmp_path):
    add(store, tmp_path, "fork-one", [message(A, B)])
    add(store, tmp_path, "fork-two", [message(A, C), message(B, A), message(C, None)])
    result = view(store)
    assert result["state"] == "linked" and result["ancestry_state"] == "null_parent"
    assert result["ancestry_steps"] == 3
    assert result["parent_references"][0]["event_id"] == 3
