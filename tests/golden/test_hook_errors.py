"""钩子失败报告不能丢失、漏采当零、携带正文或被分页混入后来记录。"""

from __future__ import annotations

import json
import os
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
from rg.ingest import hook_errors
from rg.ingest.hook_errors import collect
from rg.ingest.watch import cycle
from rg.query.hook_errors import health, query
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.objects import digest


def log(store, raw=b"2026-10-10T08:09:10.123456+00:00 hook ValueError\n"):
    path = store.root / "logs" / "hook-errors.log"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    return path


def append(path, raw):
    with path.open("ab") as stream:
        stream.write(raw)


def saved(store, table):
    return [dict(row) for row in store.db.execute(f"SELECT * FROM {table}")]


def test_missing_is_unknown_and_repeated_poll_does_not_fabricate_history(store):
    assert health(store)["hook_failures"] is None
    collect(store)
    assert query(store, {})["available"] is False
    assert query(store, {})["counts"] is None
    assert query(store, {})["observation"]["status"] == "missing"
    before = saved(store, "hook_error_checks")
    collect(store)
    assert saved(store, "hook_error_checks") == before


def test_observed_empty_log_is_zero_reports_with_unknown_full_failure_history(store):
    log(store, b"")
    collect(store)
    result = query(store, {})
    assert result["available"] and result["counts"]["reported_failures"] == 0
    assert result["observation"]["status"] == "synced"
    assert result["complete_failure_history"] is False


def test_append_partial_tail_restart_and_noop_preserve_each_physical_report(store):
    raw = b"2026-10-10T08:09:10Z hook ValueError\n"
    path = log(store, raw + raw[:20])
    assert collect(store)["hook_error_records"] == 1
    first = query(store, {})
    assert first["observation"]["status"] == "partial_line"
    with closing(Store(store.root)) as restarted:
        collect(restarted)
        assert query(restarted, {}) == first
        append(path, raw[20:])
        assert collect(restarted)["hook_error_records"] == 1
        assert query(restarted, {})["counts"]["reported_failures"] == 2
        assert query(restarted, {})["source_instances"] == 1
        previous = list(restarted.db.iterdump())
        collect(restarted)
        assert list(restarted.db.iterdump()) == previous


def test_rotation_retains_old_reports_even_when_identical_copy_is_new_physical_source(store):
    path = log(store)
    collect(store)
    raw = path.read_bytes()
    path.rename(path.with_suffix(".old"))
    path.write_bytes(raw)
    collect(store)
    result = query(store, {})
    assert result["counts"]["reported_failures"] == 2
    assert result["source_instances"] == 2
    path.unlink()
    collect(store)
    result = query(store, {})
    assert result["counts"]["reported_failures"] == 2
    assert result["observation"]["status"] == "missing"


@pytest.mark.parametrize("rewrite", ["shorter", "prefix", "boundary"])
def test_truncation_and_in_place_rewrite_create_new_instance(store, rewrite):
    raw = b"2026-10-10T08:09:10Z hook ValueError\n"
    path = log(store, raw * 150)
    collect(store)
    if rewrite == "shorter":
        path.write_bytes(raw)
        expected = 151
    elif rewrite == "prefix":
        path.write_bytes(raw.replace(b"ValueError", b"IndexError") * 150)
        expected = 300
    else:
        data = bytearray(path.read_bytes())
        data[-len(raw):] = raw.replace(b"ValueError", b"IndexError")
        path.write_bytes(data)
        expected = 300
    collect(store)
    result = query(store, {})
    assert result["source_instances"] == 2
    assert result["counts"]["reported_failures"] == expected


def test_transaction_failure_rolls_back_reports_cursor_and_check(store):
    path = log(store)
    collect(store)
    before = list(store.db.iterdump())
    append(path, path.read_bytes())

    def fail():
        raise RuntimeError("合成事务中断")

    with pytest.raises(RuntimeError, match="合成事务中断"):
        collect(store, fault=fail)
    assert list(store.db.iterdump()) == before
    assert collect(store)["hook_error_records"] == 1
    assert query(store, {})["counts"]["reported_failures"] == 2


def test_process_killed_before_commit_can_resume_without_lost_or_duplicate_report(store):
    path = log(store)
    collect(store)
    before = list(store.db.iterdump())
    append(path, path.read_bytes())
    code = (
        "import os,sys;from pathlib import Path;from rg.store.database import Store;"
        "from rg.ingest.hook_errors import collect;"
        "store=Store(Path(sys.argv[1]));collect(store,fault=lambda:os._exit(73))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(store.root)], capture_output=True, timeout=10,
    )
    assert result.returncode == 73 and result.stdout == result.stderr == b""
    assert list(store.db.iterdump()) == before
    assert collect(store)["hook_error_records"] == 1
    assert query(store, {})["counts"]["reported_failures"] == 2


def test_report_and_observation_history_cannot_be_rewritten_or_removed(store):
    log(store)
    collect(store)
    for table, statement in [
        ("hook_error_reports", "SET status='unknown_record'"),
        ("hook_error_checks", "SET status='missing'"),
        ("hook_error_sources", "SET file_token='replacement'"),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(f"UPDATE {table} {statement}")
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(f"DELETE FROM {table}")


def test_missing_then_reappearing_identical_inode_starts_new_observation_instance(
    store, monkeypatch,
):
    path = log(store)
    collect(store)
    raw = path.read_bytes()
    token = saved(store, "hook_error_sources")[0]["file_token"]
    path.unlink()
    collect(store)
    path.write_bytes(raw)
    original = os.fstat

    def same_inode(fd):
        value = original(fd)
        values = list(value)
        values[1], values[2] = (int(part) for part in token.split(":")[::-1])
        return os.stat_result(values)

    with monkeypatch.context() as patch:
        patch.setattr(hook_errors.os, "fstat", same_inode)
        collect(store)
    assert query(store, {})["source_instances"] == 2
    assert query(store, {})["counts"]["reported_failures"] == 2


def test_batch_budget_exposes_backlog_without_advancing_over_unread_lines(store, monkeypatch):
    monkeypatch.setattr(hook_errors, "MAX_RECORDS", 2)
    raw = b"2026-10-10T08:09:10Z hook ValueError\n"
    log(store, raw * 5)
    collect(store)
    first = query(store, {})
    assert first["counts"]["reported_failures"] == 2
    assert first["observation"]["status"] == "backlog"
    assert first["observation"]["committed_offset"] == len(raw) * 2
    collect(store)
    collect(store)
    assert query(store, {})["counts"]["reported_failures"] == 5
    assert query(store, {})["observation"]["status"] == "synced"


def test_oversized_line_stays_unread_and_does_not_hide_later_gap(store):
    log(store, b"x" * (hook_errors.MAX_LINE + 1) + b"\n")
    collect(store)
    result = query(store, {})
    assert result["available"] and result["counts"]["records_total"] == 0
    assert result["observation"]["status"] == "oversized_line"
    assert result["observation"]["committed_offset"] == 0


def test_unknown_log_record_and_class_cannot_copy_secrets_or_paths(store):
    secret = b"sk-synthetic-private-value /home/synthetic-user/private.txt"
    unknown_class = b"SyntheticPrivateFailure"
    log(store, secret + b"\n2026-10-10T08:09:10Z hook " + unknown_class + b"\n")
    collect(store)
    result = query(store, {})
    assert result["counts"] == {
        "records_total": 2, "reported_failures": 1, "unknown_records": 1,
        "unknown_exception_classes": 1,
    }
    serialized = json.dumps(result).encode()
    assert secret not in serialized and unknown_class not in serialized
    assert result["reports"][0]["exception_class_sha256"] == digest(unknown_class)
    assert secret.decode() not in "\n".join(store.db.iterdump())
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    assert not list((store.root / "objects").rglob("*.zst"))


@pytest.mark.parametrize("raw", [
    b"2026-10-10T08:09:10 hook ValueError\n",
    b"2026-13-10T08:09:10Z hook ValueError\n",
    b"2026-10-10T08:09:10Z unknown ValueError\n",
    b"2026-10-10T08:09:10Z hook ValueError message\n",
    b"2026-10-10T08:09:10Z hook <script>\n",
    b"0001-01-01T00:00:00+23:00 hook ValueError\n",
    b"\xff\xfe\n",
])
def test_invalid_protocol_is_unknown_and_not_a_failure_count(store, raw):
    log(store, raw)
    collect(store)
    result = query(store, {})
    assert result["counts"]["unknown_records"] == 1
    assert result["counts"]["reported_failures"] == 0
    assert result["reports"][0]["occurred_at"] is None


def test_time_is_normalized_to_utc_and_report_has_separate_recording_time(store):
    log(store, b"2026-10-10T16:09:10+08:00 hook TaskBusy\n")
    collect(store)
    row = query(store, {})["reports"][0]
    assert row["occurred_at"] == "2026-10-10T08:09:10+00:00"
    assert row["recorded_at"] != row["occurred_at"]
    assert row["exception_class"] == "TaskBusy"


@pytest.mark.parametrize("target", ["file", "directory", "fifo"])
def test_reader_does_not_follow_log_links_or_block_on_nonregular_file(store, tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hook-errors.log").write_bytes(b"synthetic outside secret\n")
    logs = store.root / "logs"
    if target == "directory":
        logs.symlink_to(outside, target_is_directory=True)
    else:
        logs.mkdir()
        path = logs / "hook-errors.log"
        if target == "file":
            path.symlink_to(outside / "hook-errors.log")
        else:
            os.mkfifo(path)
    collect(store)
    result = query(store, {})
    assert result["available"] is False
    assert result["observation"]["status"] in {"unsafe", "read_error"}
    assert saved(store, "hook_error_reports") == []


def test_file_changed_during_read_discards_batch_and_does_not_commit_cursor(store, monkeypatch):
    path = log(store)
    original = os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            append(path, path.read_bytes())
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(hook_errors.os, "fstat", changed)
        collect(store)
    assert query(store, {})["observation"]["status"] == "changed_during_read"
    assert saved(store, "hook_error_reports") == saved(store, "hook_error_sources") == []
    collect(store)
    assert query(store, {})["counts"]["reported_failures"] == 2


def test_pages_share_frozen_report_and_source_observation_cutoff_and_are_readonly(store):
    raw = b"2026-10-10T08:09:10Z hook ValueError\n"
    path = log(store, raw * 3)
    collect(store)
    first = query(store, {"limit": "2"})
    append(path, raw)
    collect(store)
    before = list(store.db.iterdump())
    second = query(store, {
        "limit": "2", "offset": "2", "snapshot": str(first["snapshot_id"]),
    })
    assert first["counts"] == second["counts"]
    assert first["observation"] == second["observation"]
    assert [r["report_id"] for r in first["reports"] + second["reports"]] == [3, 2, 1]
    assert query(store, {})["counts"]["reported_failures"] == 4
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("params", [
    {"offset": "1"}, {"limit": "201"}, {"limit": "0"}, {"offset": "-1"},
    {"snapshot": "-1"}, {"snapshot": "999"}, {"unexpected": "data"},
])
def test_invalid_pagination_cannot_silently_mix_snapshots(store, params):
    with pytest.raises(ValueError):
        query(store, params)


def test_upgrade_does_not_read_existing_logs_until_scanner_runs(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 19)
        with closing(Store(root)) as previous:
            log(previous)
    with closing(Store(root)) as upgraded:
        assert query(upgraded, {})["available"] is False
        assert saved(upgraded, "hook_error_reports") == []
        collect(upgraded)
        assert query(upgraded, {})["counts"]["reported_failures"] == 1


def test_migration_failure_leaves_old_version_and_no_partial_tables(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 19)
        Store(root).close()
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 20, (*migrations.MIGRATIONS[20], "INVALID SQL"))
        with pytest.raises(sqlite3.DatabaseError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name LIKE 'hook_error_%'"
        ).fetchall()


def test_backup_restores_report_history_without_source_log(store, tmp_path):
    path = log(store)
    collect(store)
    expected = query(store, {})
    destination = tmp_path / "backup"
    backup(store, destination)
    path.unlink()
    with closing(Store(destination, readonly=True)) as restored:
        assert query(restored, {}) == expected
    assert not (destination / "logs" / "hook-errors.log").exists()


def test_whole_project_clear_keeps_only_global_operational_metadata(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        project = store.project("合成清除项目", [])
        log(store)
        collect(store)
        before = query(store, {})
    preview = clear.preview(root, project)
    assert preview["blockers"] == []
    clear.execute(root, project, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert query(store, {}) == before
        assert project not in json.dumps(before)


def test_scan_collects_reports_without_model_or_sources_and_preserves_exit_zero(store):
    entry = Path(sys.executable).with_name("rg-hook")
    output = subprocess.run(
        [str(entry), "claude", "PreCompact", "--data-dir", str(store.root)],
        input=b"{synthetic-broken-input-and-secret", capture_output=True,
        env={**os.environ, "RG_DATA_DIR": str(store.root)},
    )
    assert output.returncode == 0 and output.stdout == output.stderr == b""
    source = store.root / "logs" / "hook-errors.log"
    assert b"synthetic-broken-input-and-secret" not in source.read_bytes()
    result = cycle(store)
    assert result["hook_error_records"] == 1
    assert query(store, {})["counts"]["reported_failures"] == 1


def test_actual_cli_and_authenticated_http_use_global_readonly_frozen_query(store, tmp_path):
    project = store.project("合成HTTP钩子项目", [])
    log(store)
    collect(store)
    expected, before = query(store, {}), list(store.db.iterdump())
    result = subprocess.run(
        [str(Path(sys.executable).with_name("rg")), "--data-dir", str(store.root),
         "hook-errors", "--project", project], capture_output=True, text=True,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == expected
    (tmp_path / "index.html").write_text("合成界面")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-hook-http")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert client.get("/api/hook-errors").status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.get("/api/hook-errors", params={"project": project})
            assert response.status_code == 200 and response.json() == expected
            assert response.headers["Cache-Control"] == "no-store"
            assert client.get("/api/hook-errors", params={"limit": "201"}).status_code == 400
            assert client.get("/api/hook-errors", params={"project": "absent"}).status_code == 404
            current = client.get("/api/health", params={"project": project}).json()
            assert current["hook_failures"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive() and list(store.db.iterdump()) == before
