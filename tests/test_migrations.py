from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rg.ingest.scanner import scan_file
from rg.store import migrations
from rg.store.database import Store
from tests.golden.test_ingestion import lines, record


def legacy_store(tmp_path: Path, monkeypatch) -> tuple[Path, bytes]:
    root = tmp_path / "legacy"
    with monkeypatch.context() as change:
        change.setattr(migrations, "LATEST_VERSION", 1)
        value = Store(root)
        path = tmp_path / "event.jsonl"
        lines(path, [record("保留原始事件")])
        scan_file(value, path, "claude")
        raw = value.raw(1)
        value.close()
    return root, raw


def test_v1_upgrade_keeps_original_events_and_is_idempotent(tmp_path: Path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch)
    value = Store(root)
    assert value.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert value.raw(1) == raw
    with pytest.raises(sqlite3.IntegrityError):
        value.db.execute("DELETE FROM raw_events")
    value.close()
    repeated = Store(root)
    assert repeated.health()["events"] == 1
    assert repeated.db.execute("SELECT count(*) FROM candidate_locations").fetchone()[0] == 0
    repeated.close()


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
