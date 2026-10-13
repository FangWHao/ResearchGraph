import sqlite3
from pathlib import Path

import pytest

from rg.store.database import Store


def test_failed_schema_creation_is_recoverable(tmp_path: Path, monkeypatch):
    original = Path.read_text

    def damaged(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        return text + "\nINVALID SQL;\n" if path.name == "schema.sql" else text

    monkeypatch.setattr(Path, "read_text", damaged)
    with pytest.raises(sqlite3.OperationalError):
        Store(tmp_path / "failed")
    connection = sqlite3.connect(tmp_path / "failed/rg.db")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert (
        connection.execute("SELECT count(*) FROM sqlite_master WHERE name='projects'").fetchone()[0]
        == 0
    )
    connection.close()
    monkeypatch.setattr(Path, "read_text", original)
    recovered = Store(tmp_path / "failed")
    assert recovered.health()["events"] == 0
    recovered.close()
