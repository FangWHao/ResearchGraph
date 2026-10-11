from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from rg.ingest.scanner import scan_file
from rg.ingest.sources import Source
from rg.ingest.spool import consume, enqueue, register
from rg.store import clear, record_denials
from rg.store.backup import backup
from rg.store.database import Store, dumps
from rg.store.locking import TaskBusy
from rg.store.objects import digest
from tests.directory_aliases import directory_alias

MARKER = "SYNTHETIC_RECORD_ERASE_38146"


def line(native: str, text: str, tool: str) -> bytes:
    if tool == "claude":
        value = {"type": "user", "sessionId": native, "message": {"content": text}}
    else:
        value = {"type": "event_msg", "payload": {"type": "user_message", "message": text}}
    return (dumps(value) + "\n").encode()


def header(native: str, tool: str) -> bytes:
    if tool == "codex":
        return (dumps({"type": "session_meta", "payload": {"id": native}}) + "\n").encode()
    return line(native, "synthetic public header " + native, tool)


def scene(tmp_path: Path, tool: str = "claude"):
    root = tmp_path / "data"
    private = line("synthetic-old", MARKER, tool)
    source = tmp_path / "original.jsonl"
    source.write_bytes(header("synthetic-old", tool) + private)
    with closing(Store(root)) as store:
        project = store.project("合成清除", [])
        other = store.project("合成保留", [])
        scan_file(store, source, tool, project)
    return root, project, other, private


def erase(root: Path, project: str):
    value = clear.preview(root, project)
    assert not value["blockers"]
    return clear.execute(root, project, str(uuid4()), value["preview_sha256"])


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_relocated_record_is_audited_before_object_write_and_cursor_continues(tmp_path, tool):
    root, project, other, private = scene(tmp_path, tool)
    erase(root, project)
    source = tmp_path / "changed-native-and-path.jsonl"
    prefix = header("synthetic-new", tool)
    after = line("synthetic-new", "later public", tool)
    source.write_bytes(prefix + private + after)
    with closing(Store(root)) as store:
        result = scan_file(store, source, tool, other)
        assert result["privacy_skipped_records"] == 1
        assert result["events"] == 2
        row = store.db.execute("SELECT * FROM source_files").fetchone()
        assert row["committed_offset"] == len(prefix + private + after)
        assert row["tail_fragment"] is None
        assert not store.objects.path(digest(private)).exists()
        assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 2
        assert not store.db.execute(
            "SELECT 1 FROM event_search WHERE text LIKE ?", ("%" + MARKER + "%",)
        ).fetchone()
        assert scan_file(store, source, tool, other) == {}
        assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == len(
            prefix + private + after
        )
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 1
    raw = (root / "clear-records" / record_denials.NAME).read_bytes()
    assert MARKER.encode() not in raw and b"synthetic-old" not in raw
    assert str(source).encode() not in raw and digest(private).encode() not in raw


def test_same_text_in_a_distinct_record_is_allowed_and_partial_tail_is_not_copied(tmp_path):
    root, project, other, private = scene(tmp_path)
    erase(root, project)
    different = tmp_path / "different.jsonl"
    different.write_bytes(line("synthetic-distinct", MARKER, "claude"))
    with closing(Store(root)) as store:
        assert scan_file(store, different, "claude", other)["events"] == 1
        partial = tmp_path / "partial.jsonl"
        prefix = header("synthetic-tail", "claude")
        partial.write_bytes(prefix + private[:-1])
        assert scan_file(store, partial, "claude", other)["privacy_tail_held"] == 1
        row = store.db.execute(
            "SELECT committed_offset,tail_fragment FROM source_files WHERE path=?", (str(partial),)
        ).fetchone()
        assert tuple(row) == (len(prefix), None)
        partial.write_bytes(prefix + private)
        assert scan_file(store, partial, "claude", other)["privacy_skipped_records"] == 1


def test_shared_original_bytes_are_retained_and_not_denied(tmp_path):
    root, project, other, private = scene(tmp_path)
    second = tmp_path / "retained.jsonl"
    second.write_bytes(header("synthetic-retained", "claude") + private)
    with closing(Store(root)) as store:
        scan_file(store, second, "claude", other)
    erase(root, project)
    with record_denials.guard(root) as denied:
        assert denied is not None and not denied("claude", digest(private))
    with closing(Store(root)) as store:
        assert store.objects.get(digest(private)) == private
        assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 2


def test_rollback_preserves_record_barrier_and_resume_is_idempotent(tmp_path, monkeypatch):
    root, project, _, private = scene(tmp_path)
    preview = clear.preview(root, project)
    request = str(uuid4())

    def fail(*args):
        raise RuntimeError("synthetic interrupted main deletion")

    with monkeypatch.context() as patch:
        patch.setattr(clear, "_delete", fail)
        with pytest.raises(RuntimeError):
            clear.execute(root, project, request, preview["preview_sha256"])
    assert clear.status(root)["state"] == "pending"
    with record_denials.guard(root) as denied:
        assert denied is not None and denied("claude", digest(private))
    with pytest.raises(TaskBusy):
        Store(root)
    assert clear.resume(root, request)["state"] == "complete"
    path = root / "clear-records" / record_denials.NAME
    before = path.read_bytes()
    assert clear.resume(root, request)["state"] == "complete"
    assert path.read_bytes() == before


@pytest.mark.parametrize("problem", ["corrupt", "future", "unknown_table"])
def test_invalid_index_stops_before_source_or_object_registration(tmp_path, problem):
    root, project, other, _ = scene(tmp_path)
    erase(root, project)
    path = root / "clear-records" / record_denials.NAME
    if problem == "corrupt":
        path.write_bytes(b"synthetic broken database")
    else:
        with closing(sqlite3.connect(path)) as db:
            if problem == "future":
                db.execute("PRAGMA user_version=2")
            else:
                db.execute("CREATE TABLE unknown(v TEXT)")
            db.commit()
    source = tmp_path / "clean-new.jsonl"
    source.write_bytes(header("synthetic-clean", "claude"))
    with closing(Store(root)) as store:
        before = list(store.db.iterdump())
        with pytest.raises(ValueError, match="清除摘要索引"):
            scan_file(store, source, "claude", other)
        assert list(store.db.iterdump()) == before
        assert not list((root / "objects").glob("*/*.zst"))


def test_managed_index_directory_link_is_rejected_without_touching_external_files(tmp_path):
    root = tmp_path / "data"
    external = tmp_path / "external"
    external.mkdir()
    before = list(external.iterdir())
    with closing(Store(root)), directory_alias(root / "clear-records", external):
        with pytest.raises(ValueError, match="链接或重解析点"):
            with record_denials.guard(root):
                pass
        with pytest.raises(ValueError, match="链接或重解析点"):
            record_denials.append(root, [record_denials.key("claude", digest(b"synthetic"))])
    assert list(external.iterdir()) == before


def test_readonly_missing_index_creates_nothing_and_append_is_atomic(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    with record_denials.guard(root) as denied:
        assert denied is None
    assert list(root.iterdir()) == []
    value = record_denials.key("claude", digest(b"synthetic"))
    assert record_denials.append(root, [value, value]) == 1
    assert record_denials.append(root, [value]) == 0
    other = record_denials.key("codex", digest(b"other synthetic"))
    with pytest.raises(ValueError):
        record_denials.append(root, [other, "invalid"])
    with record_denials.guard(root) as denied:
        assert denied is not None and denied("claude", digest(b"synthetic"))
        assert not denied("codex", digest(b"other synthetic"))


def test_sqlite_backup_restores_the_record_barrier_for_changed_source(tmp_path):
    root, project, other, private = scene(tmp_path)
    erase(root, project)
    destination = tmp_path / "restored"
    with closing(Store(root)) as store:
        backup(store, destination)
    source = tmp_path / "relocated.jsonl"
    source.write_bytes(header("synthetic-new", "claude") + private)
    with closing(Store(destination)) as restored:
        assert scan_file(restored, source, "claude", other)["privacy_skipped_records"] == 1
        assert not restored.objects.path(digest(private)).exists()
    assert (destination / "clear-records" / record_denials.NAME).is_file()


def test_missing_committed_index_never_silently_disables_the_barrier(tmp_path):
    root, project, other, _ = scene(tmp_path)
    erase(root, project)
    (root / "clear-records" / record_denials.NAME).unlink()
    source = tmp_path / "relocated.jsonl"
    source.write_bytes(header("synthetic-new", "claude"))
    with closing(Store(root)) as store:
        with pytest.raises(ValueError, match="清除摘要索引缺失"):
            scan_file(store, source, "claude", other)
        with pytest.raises(ValueError, match="清除摘要索引缺失"):
            backup(store, tmp_path / "backup")
        assert not store.db.execute("SELECT 1 FROM source_files").fetchone()


def test_legacy_pending_clear_resumes_with_a_record_barrier(tmp_path, monkeypatch):
    root, project, _, private = scene(tmp_path)
    request = str(uuid4())
    with closing(clear.connect(root, readonly=False)) as db:
        value, _ = clear.plan(root, db, project, record_barrier=False)
        value.update(request_id=request, state="pending", started_at="synthetic")
        clear._persist(root, value)
    with monkeypatch.context() as patch:

        def fail(*args):
            raise RuntimeError("synthetic legacy resume interrupted after index")

        patch.setattr(clear, "_delete", fail)
        with pytest.raises(RuntimeError):
            clear.resume(root, request)
    assert clear.resume(root, request)["state"] == "complete"
    with record_denials.guard(root) as denied:
        assert denied is not None and denied("claude", digest(private))


def test_first_index_publication_failure_is_resumable(tmp_path, monkeypatch):
    root, project, _, private = scene(tmp_path)
    preview, request = clear.preview(root, project), str(uuid4())
    original = record_denials.os.replace

    def fail(source, target):
        if Path(target).name == record_denials.NAME:
            raise OSError("synthetic first index publication failed")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(record_denials.os, "replace", fail)
        with pytest.raises(OSError):
            clear.execute(root, project, request, preview["preview_sha256"])
    assert clear.status(root)["state"] == "pending"
    assert not (root / "clear-records" / record_denials.NAME).exists()
    assert clear.resume(root, request)["state"] == "complete"
    with record_denials.guard(root) as denied:
        assert denied is not None and denied("claude", digest(private))


def test_spool_hint_is_done_only_after_complete_record_is_audited(tmp_path):
    root, project, other, private = scene(tmp_path)
    erase(root, project)
    source = tmp_path / "relocated.jsonl"
    prefix = header("synthetic-new", "claude")
    source.write_bytes(prefix + private)
    payload = dumps(
        {"hook_event_name": "Stop", "session_id": "synthetic-new", "transcript_path": str(source)}
    ).encode()
    hint = enqueue(root, "claude", payload)
    with closing(Store(root)) as store:
        assert register(store)["spool_registered"] == 1
        counts = consume(store, [Source(source, "claude", other, "file")])
        assert counts.get("spool_waiting", 0) == 0 and counts["spool_done"] == 1
        row = store.db.execute("SELECT state,error FROM jobs WHERE kind='spool_hint'").fetchone()
        assert tuple(row) == ("done", None)
        assert not hint.exists() and not store.objects.path(digest(private)).exists()
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 1
        assert consume(store, [Source(source, "claude", other, "file")])["spool_done"] == 0
        assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == len(
            prefix + private
        )


def test_missing_index_after_main_commit_keeps_clear_pending_until_restored(tmp_path, monkeypatch):
    root, project, _, private = scene(tmp_path)
    preview, request = clear.preview(root, project), str(uuid4())
    original = clear._finish
    path = root / "clear-records" / record_denials.NAME
    saved = []

    def lose_index(*args):
        saved.append(path.read_bytes())
        path.unlink()
        return original(*args)

    with monkeypatch.context() as patch:
        patch.setattr(clear, "_finish", lose_index)
        with pytest.raises(ValueError, match="清除摘要索引缺失"):
            clear.execute(root, project, request, preview["preview_sha256"])
    assert clear.status(root)["state"] == "pending"
    with pytest.raises(ValueError, match="清除摘要索引缺失"):
        clear.resume(root, request)
    path.write_bytes(saved[0])
    assert clear.resume(root, request)["state"] == "complete"
    with record_denials.guard(root) as denied:
        assert denied is not None and denied("claude", digest(private))


def test_spool_partial_private_tail_stays_queued_until_completed_gap_is_audited(tmp_path):
    root, project, other, private = scene(tmp_path)
    erase(root, project)
    source = tmp_path / "partial-spool.jsonl"
    prefix = header("synthetic-new", "claude")
    source.write_bytes(prefix + private[:-1])
    hint = enqueue(
        root,
        "claude",
        dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "synthetic-new",
                "transcript_path": str(source),
            }
        ).encode(),
    )
    with closing(Store(root)) as store:
        register(store)
        counts = consume(store, [Source(source, "claude", other, "file")])
        assert counts["spool_waiting"] == 1 and counts["spool_done"] == 0
        assert hint.exists() and not store.objects.path(digest(private)).exists()
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 0
        assert store.db.execute("SELECT tail_fragment FROM source_files").fetchone()[0] is None
        source.write_bytes(prefix + private)
        assert consume(store, [Source(source, "claude", other, "file")])["spool_done"] == 1
        assert not hint.exists()
        assert store.db.execute("SELECT count(*) FROM source_record_skips").fetchone()[0] == 1
        assert not store.objects.path(digest(private)).exists()
