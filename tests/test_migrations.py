from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rg.ingest.scanner import scan_file
from rg.store import migrations
from rg.store.database import Store
from tests.golden.test_ingestion import lines, record


def legacy_store(tmp_path: Path, monkeypatch, version: int = 1) -> tuple[Path, bytes]:
    root = tmp_path / "legacy"
    with monkeypatch.context() as change:
        change.setattr(migrations, "LATEST_VERSION", version)
        value = Store(root)
        path = tmp_path / "event.jsonl"
        lines(path, [record("保留原始事件")])
        scan_file(value, path, "claude")
        raw = value.raw(1)
        value.close()
    return root, raw


def test_v10_queue_migration_is_atomic_and_does_not_invent_pending_tasks(tmp_path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=10)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 11, (*migrations.MIGRATIONS[11], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    db = sqlite3.connect(root / "rg.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 10
    assert (
        db.execute("SELECT count(*) FROM sqlite_master WHERE name='extraction_queue'").fetchone()[0]
        == 0
    )
    db.close()
    value = Store(root)
    assert value.raw(1) == raw and value.health()["extraction_queue"]["total"] == 0
    value.close()


def test_v1_upgrade_keeps_original_events_and_is_idempotent(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch)
    value = Store(root)
    assert value.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
    assert value.raw(1) == raw
    with pytest.raises(sqlite3.IntegrityError):
        value.db.execute("DELETE FROM raw_events")
    value.close()
    repeated = Store(root)
    assert repeated.health()["events"] == 1
    assert repeated.db.execute("SELECT count(*) FROM candidate_locations").fetchone()[0] == 0
    repeated.close()


def test_v3_monitor_migration_is_atomic_and_does_not_fabricate_attempts(
    tmp_path: Path, monkeypatch
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=3)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 4, (*migrations.MIGRATIONS[4], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='model_attempts'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    assert recovered.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
    recovered.close()


def test_v9_decision_migration_is_atomic_and_preserves_question_receipt(
    tmp_path: Path, monkeypatch
):
    from uuid import uuid4

    from rg.record.question import question

    root, raw = legacy_store(tmp_path, monkeypatch, version=9)
    # 旧版本记录服务只查询版本 9 已有的回执表。
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 9)
        patch.setattr("rg.record.events.RECEIPTS", {"question": "explicit_records"})
        old = Store(root)
        project = old.project("合成旧人工项目", [])
        data = {
            "project_id": project,
            "text": "原人工问题",
            "scope": None,
            "actor": "human:合成",
            "request_id": str(uuid4()),
            "expected_revision": old.revision(),
        }
        saved = question(old, data)
        original = old.raw(saved["event_id"])
        old.close()
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 10, (*migrations.MIGRATIONS[10], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='decision_requests'"
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("SELECT count(*) FROM explicit_records").fetchone()[0] == 1
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw and recovered.raw(saved["event_id"]) == original
    assert question(recovered, data)["claim_id"] == saved["claim_id"]
    assert recovered.db.execute("SELECT count(*) FROM decision_requests").fetchone()[0] == 0
    recovered.close()


def test_v4_plan_migration_is_atomic_and_does_not_invent_history(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=4)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 5, (*migrations.MIGRATIONS[5], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='extraction_plans'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    assert recovered.db.execute("SELECT count(*) FROM extraction_plans").fetchone()[0] == 0
    assert recovered.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
    recovered.close()


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 2, (*migrations.MIGRATIONS[2], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='candidate_locations'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    recovered.close()


def test_newer_database_is_not_silently_downgraded(tmp_path: Path):
    root = tmp_path / "future"
    root.mkdir()
    connection = sqlite3.connect(root / "rg.db")
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    with pytest.raises(ValueError, match="不能降级"):
        Store(root)


def test_v2_upgrade_preserves_locations_and_failed_v3_rolls_back(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=2)
    connection = sqlite3.connect(root / "rg.db")
    run = connection.execute(
        "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,created_at) "
        "VALUES ('legacy-position','pass1','[1]','ok','2026-10-09')"
    ).lastrowid
    start = raw.index("保留".encode())
    connection.execute(
        "INSERT INTO candidate_locations VALUES (?,1,?,?,'decision','rule')",
        (run, start, start + len("保留".encode())),
    )
    connection.commit()
    connection.close()
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 3, (*migrations.MIGRATIONS[3], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='link_progress'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    value = Store(root)
    assert value.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
    assert value.raw(1) == raw
    position = value.db.execute("SELECT byte_start,byte_end FROM candidate_locations").fetchone()
    assert raw[position[0] : position[1]].decode() == "保留"
    assert value.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 0
    value.close()


def test_v5_spool_migration_rolls_back_and_does_not_fabricate_sources(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=5)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 6, (*migrations.MIGRATIONS[6], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE name IN ('ingest_sources','spool_receipts','jobs_spool_queue')"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    assert recovered.db.execute("SELECT count(*) FROM ingest_sources").fetchone()[0] == 0
    assert recovered.db.execute("SELECT count(*) FROM spool_receipts").fetchone()[0] == 0
    recovered.close()


def test_v6_snapshot_migration_rolls_back_and_keeps_legacy_rows(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=6)
    connection = sqlite3.connect(root / "rg.db")
    connection.execute(
        "INSERT INTO workspace_snapshots(taken_at,skipped) VALUES ('2026-10-09','legacy')"
    )
    connection.commit()
    connection.close()
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 7, (*migrations.MIGRATIONS[7], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
    assert "snapshot_key" not in {
        value[1] for value in connection.execute("PRAGMA table_info(workspace_snapshots)")
    }
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    row = recovered.db.execute("SELECT * FROM workspace_snapshots").fetchone()
    assert (
        row["skipped"] == "legacy" and row["snapshot_key"] is None and row["record_sha256"] is None
    )
    recovered.close()


def test_v7_l1_migration_rolls_back_and_does_not_invent_runtime_history(
    tmp_path: Path, monkeypatch
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=7)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 8, (*migrations.MIGRATIONS[8], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
    assert "request_event_id" not in {
        row[1] for row in connection.execute("PRAGMA table_info(runs)")
    }
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='run_observations'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    value = Store(root)
    assert value.raw(1) == raw
    assert value.db.execute("SELECT count(*) FROM run_observations").fetchone()[0] == 0
    assert value.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0
    value.close()


def test_v8_explicit_record_migration_is_atomic_and_keeps_old_history(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=8)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 9, (*migrations.MIGRATIONS[9], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    connection = sqlite3.connect(root / "rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='explicit_records'"
        ).fetchone()[0]
        == 0
    )
    connection.close()
    recovered = Store(root)
    assert recovered.raw(1) == raw
    assert recovered.db.execute("SELECT count(*) FROM explicit_records").fetchone()[0] == 0
    recovered.close()
