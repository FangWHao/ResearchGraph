from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from rg.api import views
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import query
from rg.slim.slimmer import slim_session
from rg.store import migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps
from rg.store.fragments import assign
from rg.store.objects import digest
from tests.conftest import FakeProvider
from tests.golden.test_fragment_assignments import intent, owners
from tests.golden.test_record_denials import MARKER, erase, header, line, scene
from tests.test_migrations import legacy_store


def moved(tmp_path, tool="claude", suffix=b""):
    root, project, other, private = scene(tmp_path, tool)
    erase(root, project)
    path = tmp_path / "continued.jsonl"
    prefix = header("synthetic-new", tool)
    after = line("synthetic-new", "synthetic post 缺口后 🐾", tool)
    path.write_bytes(prefix + private + after + suffix)
    return root, other, path, prefix, private, after


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_skipped_record_is_audited_before_cursor_and_following_content_is_retained(tmp_path, tool):
    root, other, path, prefix, private, after = moved(tmp_path, tool)
    with closing(Store(root)) as store:
        result = scan_file(store, path, tool, other)
        assert result["privacy_skipped_records"] == 1 and result["events"] == 2
        assert not result.get("privacy_held")
        source = store.db.execute("SELECT * FROM source_files").fetchone()
        assert source["committed_offset"] == len(prefix + private + after)
        gap = store.db.execute("SELECT * FROM source_record_skips").fetchone()
        assert (gap["byte_start"], gap["byte_end"]) == (len(prefix), len(prefix + private))
        assert gap["record_key"] != digest(private)
        assert not store.objects.path(digest(private)).exists()
        assert not store.db.execute(
            "SELECT 1 FROM event_search WHERE text LIKE ?", ("%" + MARKER + "%",)
        ).fetchone()
        assert [
            store.raw(r[0])
            for r in store.db.execute("SELECT event_id FROM raw_events ORDER BY event_id")
        ] == [prefix, after]
        identities = owners(store)
        assert identities[0][1] == identities[1][1] == other
        assert identities[0][2] != identities[1][2]
        page = query(
            store, {"session": str(source["session_pk"]), "source": str(source["file_instance_id"])}
        )
        assert page["privacy_skipped_records"] == 1
        assert page["privacy_skipped_bytes"] == len(private)
        assert page["records"][-1]["assignment"]["logical_session_pk"] == identities[-1][2]
        for table in ("source_record_skips", "source_continuations"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store.db.execute("DELETE FROM " + table)
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store.db.execute("UPDATE " + table + " SET recorded_at=recorded_at")
        before = list(store.db.iterdump())
        assert scan_file(store, path, tool, other) == {}
        assert list(store.db.iterdump()) == before
        for table in ("source_record_skips", "source_continuations"):
            raw = dumps([dict(r) for r in store.db.execute("SELECT * FROM " + table)])
            assert MARKER not in raw and "synthetic-old" not in raw and str(path) not in raw


def test_gap_and_cursor_rollback_together_without_copying_private_record(tmp_path):
    root, other, path, prefix, private, after = moved(tmp_path)
    seen = 0

    def fail():
        nonlocal seen
        seen += 1
        if seen == 2:
            raise OSError("synthetic transaction interruption")

    with closing(Store(root)) as store:
        with pytest.raises(OSError):
            scan_file(store, path, "claude", other, fault=fail)
        assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == len(
            prefix
        )
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM source_continuations").fetchone()[0] == 0
        assert not store.objects.path(digest(private)).exists()
    with closing(Store(root)) as store:
        result = scan_file(store, path, "claude", other)
        assert result["privacy_skipped_records"] == result["events"] == 1
        assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == len(
            prefix + private + after
        )
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_restart_appends_to_same_continuation_and_partial_tail_never_persists(tmp_path):
    root, other, path, prefix, private, after = moved(tmp_path)
    path.write_bytes(prefix + private[:-1])
    with closing(Store(root)) as store:
        assert scan_file(store, path, "claude", other)["privacy_tail_held"] == 1
        assert store.db.execute("SELECT tail_fragment FROM source_files").fetchone()[0] is None
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 0
    path.write_bytes(prefix + private + after)
    with closing(Store(root)) as store:
        assert scan_file(store, path, "claude", other)["privacy_skipped_records"] == 1
        logical = owners(store)[-1][2]
        frozen = [tuple(r) for r in store.db.execute("SELECT * FROM source_continuations")]
    with path.open("ab") as stream:
        stream.write(line("synthetic-new", "new after restart", "claude"))
    with closing(Store(root)) as store:
        assert scan_file(store, path, "claude", other)["events"] == 1
        assert owners(store)[-1][2] == logical
        assert [tuple(r) for r in store.db.execute("SELECT * FROM source_continuations")] == frozen


def test_tools_evidence_and_slim_never_join_across_a_privacy_gap(tmp_path):
    root, other, path, prefix, private, _ = moved(tmp_path)
    call = (
        dumps(
            {
                "type": "assistant",
                "sessionId": "synthetic-new",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "synthetic-call",
                            "name": "Bash",
                            "input": {"command": "synthetic-do-not-execute"},
                        }
                    ]
                },
            }
        )
        + "\n"
    ).encode()
    result = (
        dumps(
            {
                "type": "user",
                "sessionId": "synthetic-new",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "synthetic-call",
                            "content": "synthetic output",
                        }
                    ]
                },
            }
        )
        + "\n"
    ).encode()
    path.write_bytes(prefix + call + private + result)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        row = store.db.execute(
            "SELECT * FROM effective_raw_events WHERE kind='tool_result'"
        ).fetchone()
        assert row["tool_name"] is None
        assert tuple(
            store.db.execute(
                "SELECT state,error FROM l1_derivations WHERE event_id=?", (row["event_id"],)
            ).fetchone()
        ) == ("waiting", "call_not_recorded")
        assert not store.db.execute(
            "SELECT 1 FROM run_observations WHERE state='exited'"
        ).fetchone()
        assert views.evidence(store, row["event_id"], {"context": "5"})["before"] == []
        assert {r["event_id"] for r in slim_session(store, row["session_pk"], FakeProvider())} <= {
            row["event_id"]
        }
        assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_directory_context_resets_at_gap_and_pagination_does_not_restore_old_cwd(tmp_path):
    root, other, path, _, private, after = moved(tmp_path)
    prefix = (
        dumps(
            {
                "type": "user",
                "sessionId": "synthetic-new",
                "cwd": "/synthetic/before",
                "message": {"content": "synthetic header"},
            }
        )
        + "\n"
    ).encode()
    path.write_bytes(prefix + private + after + line("synthetic-new", "last public", "claude"))
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        source = store.db.execute("SELECT * FROM source_files").fetchone()
        values = {
            "session": str(source["session_pk"]),
            "source": str(source["file_instance_id"]),
            "limit": "1",
        }
        first = query(store, values)
        second = query(
            store,
            values
            | {
                "after_event": str(first["next_after_event"]),
                **{k: str(first[k]) for k in ("snapshot", "observation_snapshot")},
                "expected_scope_key": first["scope_key"],
            },
        )
        assert first["records"][0]["context"]["cwd"] == "/synthetic/before"
        assert second["records"][0]["context"]["reason"] == "privacy_gap"
        assert second["records"][0]["context"]["cwd"] is None
        assert second["records"][0]["project_hint"]["projects"] == []
        before = list(store.db.iterdump())
        with pytest.raises(ValueError, match="缺口"):
            assign(
                store,
                intent(
                    store,
                    other,
                    first=first["records"][0]["first_event_id"],
                    last=second["records"][0]["first_event_id"],
                    source=source["file_instance_id"],
                    session=source["session_pk"],
                ),
            )
        assert list(store.db.iterdump()) == before


def test_explicit_partition_after_gap_keeps_history_and_new_tail_enters_inbox(tmp_path):
    root, other, path, _, _, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        third = store.project("synthetic third", [])
        source = store.db.execute("SELECT * FROM source_files").fetchone()
        records = list(store.db.execute("SELECT event_id FROM raw_events ORDER BY byte_start"))
        assign(
            store,
            intent(
                store,
                third,
                first=records[-1][0],
                last=records[-1][0],
                source=source["file_instance_id"],
                session=source["session_pk"],
            ),
        )
        old = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    with path.open("ab") as stream:
        stream.write(line("synthetic-new", "unassigned fresh tail", "claude"))
    with closing(Store(root)) as store:
        scan_file(store, path, "claude")
        assert [r[1] for r in owners(store)] == [other, third, None]
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")][
            :2
        ] == old
        assert store.db.execute("SELECT count(*) FROM source_continuations").fetchone()[0] == 2
    erase(root, other)
    with path.open("ab") as stream:
        stream.write(line("synthetic-new", "later inbox tail", "claude"))
    with closing(Store(root)) as store:
        scan_file(store, path, "claude")
        assert [r[1] for r in owners(store)] == [third, None, None]
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 2
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_rotated_shared_source_continues_after_clear_and_preserves_retained_originals(
    store, tmp_path, tool
):
    first, second = (
        store.project("synthetic mixed first", []),
        store.project("synthetic mixed second", []),
    )
    path = tmp_path / "mixed.jsonl"
    path.write_bytes(
        header("synthetic-mixed", tool)
        + b"".join(
            line("synthetic-mixed", "synthetic mixed public " + str(i), tool) for i in range(3)
        )
    )
    scan_file(store, path, tool, first)
    assign(store, intent(store, second))
    original = path.read_bytes()
    kept = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events WHERE event_id IN (3,4)")]
    root = store.root
    store.close()
    erase(root, first)
    changed = tmp_path / "rotation.tmp"
    changed.write_bytes(original + line("synthetic-tail", "new mixed-source tail", tool))
    changed.replace(path)
    with closing(Store(root)) as current:
        result = scan_file(current, path, tool)
        assert result["privacy_skipped_records"] == 2
        assert (
            current.db.execute(
                "SELECT committed_offset FROM source_files WHERE status='active'"
            ).fetchone()[0]
            == path.stat().st_size
        )
        assert [
            tuple(r) for r in current.db.execute("SELECT * FROM raw_events WHERE event_id IN (3,4)")
        ] == kept
        assert [r[1] for r in owners(current)][:2] == [second, second]
        assert owners(current)[-1][1] is None
        assert current.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 4


def test_backup_keeps_gaps_and_restarts_with_identical_ownership(tmp_path):
    root, other, path, _, _, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        before = owners(store)
        gaps = [tuple(r) for r in store.db.execute("SELECT * FROM source_record_skips")]
    destination = tmp_path / "restored"
    with closing(Store(root)) as store:
        backup(store, destination)
    with closing(Store(destination)) as store:
        assert owners(store) == before
        assert [tuple(r) for r in store.db.execute("SELECT * FROM source_record_skips")] == gaps
        assert scan_file(store, path, "claude", other) == {}


def test_whole_project_clear_removes_gap_metadata_when_source_has_no_retained_part(tmp_path):
    root, other, path, _, _, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
    erase(root, other)
    with closing(Store(root)) as store:
        for table in (
            "source_record_skips",
            "source_continuations",
            "source_files",
            "raw_events",
            "sessions",
        ):
            assert store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v32_migration_rolls_back_both_tables_and_views_without_changing_originals(
    tmp_path, monkeypatch
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=31)
    with sqlite3.connect(root / "rg.db") as db:
        before = list(db.iterdump())
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 32, (*migrations.MIGRATIONS[32], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with sqlite3.connect(root / "rg.db") as db:
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == 31
    with closing(Store(root)) as store:
        assert store.raw(1) == raw
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 0


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_clear_registers_original_source_gaps_for_saved_metadata_and_future_tail(tmp_path, tool):
    root = tmp_path / "data"
    path = tmp_path / "mixed.jsonl"
    original = header("synthetic-mixed", tool) + b"".join(
        line("synthetic-mixed", "synthetic public " + str(i), tool) for i in range(3)
    )
    path.write_bytes(original)
    with closing(Store(root)) as store:
        first, second = store.project("synthetic erased", []), store.project("synthetic kept", [])
        scan_file(store, path, tool, first)
        assign(store, intent(store, second))
        kept = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events WHERE event_id>=3")]
    erase(root, first)
    with path.open("ab") as stream:
        stream.write(line("synthetic-mixed", "new independent tail", tool))
    with closing(Store(root)) as store:
        column = "chain_observed_offset" if tool == "claude" else "parent_observed_offset"
        store.db.execute("UPDATE source_files SET " + column + "=0")
        assert scan_file(store, path, tool)["events"] == 1
        assert [
            tuple(r) for r in store.db.execute("SELECT * FROM raw_events WHERE event_id IN (3,4)")
        ] == kept
        assert [r[1] for r in owners(store)] == [second, second, None]
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 2
        progress = store.db.execute(
            "SELECT " + column + ",committed_offset FROM source_files"
        ).fetchone()
        assert progress[0] == progress[1] == path.stat().st_size
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        before = list(store.db.iterdump())
        assert scan_file(store, path, tool) == {}
        assert list(store.db.iterdump()) == before


def test_new_gap_invalidates_frozen_pagination_even_without_new_kept_records(tmp_path):
    root, other, path, _, private, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        source = store.db.execute("SELECT file_instance_id,session_pk FROM source_files").fetchone()
        values = {"session": str(source[1]), "source": str(source[0]), "limit": "1"}
        first = query(store, values)
        with path.open("ab") as stream:
            stream.write(private)
        assert scan_file(store, path, "claude", other)["privacy_skipped_records"] == 1
        before = list(store.db.iterdump())
        with pytest.raises(ConflictError):
            query(
                store,
                values
                | {
                    "after_event": str(first["next_after_event"]),
                    "snapshot": str(first["snapshot"]),
                    "observation_snapshot": str(first["observation_snapshot"]),
                    "expected_scope_key": first["scope_key"],
                },
            )
        assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_public_native_alias_or_mirror_remains_searchable_on_both_sides_of_gap(tmp_path, tool):
    root, other, path, _, private, _ = moved(tmp_path, tool)
    if tool == "claude":
        public = (
            dumps(
                {
                    "type": "user",
                    "sessionId": "synthetic-new",
                    "uuid": "synthetic-repeat",
                    "message": {"content": "synthetic searchable repeat"},
                }
            )
            + "\n"
        ).encode()
        before, after = public, public
    else:
        before = line("synthetic-new", "synthetic searchable repeat", tool)
        after = (
            dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "synthetic searchable repeat"}],
                    },
                }
            )
            + "\n"
        ).encode()
    path.write_bytes(header("synthetic-new", tool) + before + private + after)
    with closing(Store(root)) as store:
        scan_file(store, path, tool, other)
        rows = list(
            store.db.execute(
                "SELECT event_id,session_pk,exclude_reason,alias_of FROM effective_raw_events "
                "WHERE kind='user_msg' ORDER BY event_id"
            )
        )
        left, right = rows[-2:]
        assert left[1] != right[1] and right[2:] == (None, None)
        assert (
            store.db.execute(
                "SELECT count(*) FROM event_search WHERE text=?", ("synthetic searchable repeat",)
            ).fetchone()[0]
            == 2
        )
        assert not store.db.execute("SELECT 1 FROM effective_dedupe_links").fetchone()
        assert {r["event_id"] for r in slim_session(store, right[1], FakeProvider())} == {right[0]}
        assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_clear_gap_registration_rolls_back_with_original_deletion_and_resume_is_idempotent(
    tmp_path, monkeypatch
):
    from uuid import uuid4

    from rg.store import clear

    root, other, path, _, _, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        third = store.project("synthetic retained third", [])
        source = store.db.execute("SELECT * FROM source_files").fetchone()
        last = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
        assign(
            store,
            intent(
                store,
                third,
                first=last,
                last=last,
                source=source["file_instance_id"],
                session=source["session_pk"],
            ),
        )
        before = list(store.db.iterdump())
        kept = store.raw(last)
    proof, request = clear.preview(root, other), str(uuid4())
    original = clear.skip

    def fail(*args):
        original(*args)
        raise OSError("synthetic interruption after gap registration")

    with monkeypatch.context() as patch:
        patch.setattr(clear, "skip", fail)
        with pytest.raises(OSError):
            clear.execute(root, other, request, proof["preview_sha256"])
    with closing(clear.connect(root, readonly=True)) as db:
        assert list(db.iterdump()) == before
    assert clear.resume(root, request)["state"] == "complete"
    with closing(Store(root)) as store:
        assert store.raw(last) == kept
        assert [r[1] for r in owners(store)] == [third]
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 2
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        final = list(store.db.iterdump())
    assert clear.resume(root, request)["state"] == "complete"
    with closing(Store(root)) as store:
        assert list(store.db.iterdump()) == final


@pytest.mark.parametrize("column", ["chain_observed_offset", "parent_observed_offset"])
def test_metadata_cursor_never_crosses_an_undeclared_or_partial_gap(tmp_path, column):
    from rg.ingest.privacy_gaps import advance_metadata

    root, other, path, prefix, private, _ = moved(tmp_path)
    with closing(Store(root)) as store:
        scan_file(store, path, "claude", other)
        source = store.db.execute("SELECT file_instance_id FROM source_files").fetchone()[0]
        with store.transaction() as db:
            db.execute("UPDATE source_files SET " + column + "=?", (len(prefix) + 1,))
            assert advance_metadata(db, source, column) == len(prefix) + 1
            db.execute("UPDATE source_files SET " + column + "=0")
            assert advance_metadata(db, source, column) == 0
            db.execute("UPDATE source_files SET " + column + "=?", (len(prefix),))
            assert advance_metadata(db, source, column) == len(prefix + private)
