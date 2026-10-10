from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.spool import enqueue, enqueue_stream
from rg.snapshot.capture import capture, publish
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.lease import lease
from rg.store.locking import TaskBusy


@contextmanager
def holder(root: Path, ready: Path, *, readonly: bool = False, exclusive: bool = False):
    script = """
import sys
from pathlib import Path
from contextlib import closing
from rg.store.database import Store
from rg.store.lease import lease
root, ready = map(Path, sys.argv[1:3])
ctx = (lease(root, exclusive=True) if sys.argv[4] == 'yes'
       else closing(Store(root, readonly=sys.argv[3] == 'yes')))
with ctx:
    ready.write_text('ready')
    sys.stdin.buffer.read(1)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            str(ready),
            "yes" if readonly else "no",
            "yes" if exclusive else "no",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode())
            if time.monotonic() > deadline:
                raise AssertionError("占用子进程未就绪")
            time.sleep(0.01)
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize("readonly", [False, True])
def test_real_store_process_prevents_exclusive_and_allows_normal_connections(
    tmp_path: Path,
    readonly: bool,
):
    root = tmp_path / "data"
    Store(root).close()
    with holder(root, tmp_path / "ready", readonly=readonly):
        with closing(Store(root)), closing(Store(root, readonly=True)):
            with pytest.raises(TaskBusy):
                with lease(root, exclusive=True):
                    pytest.fail("仍有连接时不应取得独占")
    # 强杀真实持有者后由系统释放，无 PID 文件或人工解锁。
    with lease(root, exclusive=True):
        pass


def test_exclusive_blocks_stores_spool_capture_and_publication(tmp_path: Path):
    root = tmp_path / "data"
    Store(root).close()
    with holder(root, tmp_path / "ready", exclusive=True):
        for operation in [
            lambda: Store(root),
            lambda: Store(root, readonly=True),
            lambda: enqueue(root, "codex", b'{"sensitive":"synthetic"}'),
            lambda: enqueue_stream(root, "codex", io.BytesIO(b"tail"), b"prefix"),
            lambda: capture(root, "unused", "unused", tmp_path),
            lambda: publish(root, "codex", {"snapshot_key": "synthetic"}),
        ]:
            with pytest.raises(TaskBusy):
                operation()
        assert not (root / "spool").exists()
        assert not (root / "snapshots").exists()
    with closing(Store(root)) as store:
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_real_hook_remains_exit_zero_and_does_not_publish_during_exclusive(tmp_path: Path):
    root = tmp_path / "data"
    Store(root).close()
    with holder(root, tmp_path / "ready", exclusive=True):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rg.hooks.entry",
                "codex",
                "SessionStart",
                "--data-dir",
                str(root),
            ],
            input=b'{"session_id":"synthetic","cwd":"/synthetic"}',
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == b"" and result.stderr == b""
        assert not (root / "spool").exists()
        assert not (root / "snapshots").exists()
        # 错误日志只记类型，不能泄露拒绝的输入。
        log = (root / "logs" / "hook-errors.log").read_bytes()
        assert log.endswith(b" hook TaskBusy\n")
        assert b"session_id" not in log and b"/synthetic" not in log


def test_failed_constructor_releases_and_missing_readonly_creates_nothing(tmp_path: Path):
    missing = tmp_path / "missing"
    with pytest.raises(sqlite3.OperationalError):
        Store(missing, readonly=True)
    assert not missing.exists()
    root = tmp_path / "corrupt"
    root.mkdir()
    (root / "rg.db").write_bytes(b"synthetic corrupt database")
    with pytest.raises(sqlite3.DatabaseError):
        Store(root)
    with lease(root, exclusive=True):
        pass


@pytest.mark.skipif(os.name == "nt", reason="Windows 不支持本案例的目录符号链接")
def test_directory_alias_uses_same_lease_and_readonly_changes_no_files(tmp_path: Path):
    root = tmp_path / "data"
    Store(root).close()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    with closing(Store(alias, readonly=True)):
        with pytest.raises(TaskBusy):
            with lease(root, exclusive=True):
                pass
    after = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    # SQLite 自身只读可能创建 WAL/SHM；占用保护不新增元数据文件。
    assert set(after) - set(before) <= {"rg.db-wal", "rg.db-shm"}
    assert all(after[name] == value for name, value in before.items())


def test_backup_destination_is_occupied_through_copy(tmp_path: Path, monkeypatch):
    from rg.store import backup as module

    root, destination = tmp_path / "data", tmp_path / "backup"
    with closing(Store(root)) as store:
        store.project("合成项目", [])
        original = module._copy_backup

        def observed(store: Store, destination: Path):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; from rg.store.lease import lease; "
                    "from rg.store.locking import TaskBusy; import sys; "
                    "ctx=lease(Path(sys.argv[1]),exclusive=True); "
                    "\ntry: ctx.__enter__()\nexcept TaskBusy: sys.exit(9)\nelse: sys.exit(1)",
                    str(destination),
                ],
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 9, result.stderr.decode()
            return original(store, destination)

        monkeypatch.setattr(module, "_copy_backup", observed)
        assert backup(store, destination)["events"] == 0
    with lease(destination, exclusive=True):
        pass


def test_real_http_reports_busy_without_mutating_database(tmp_path: Path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        project = store.project("合成占用项目", [])
        revision = store.revision()
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("合成界面")
    server = LocalServer(root, web, port=0, token="synthetic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=server.origin,
            trust_env=False,
            timeout=5,
            headers={"Authorization": "Bearer synthetic-token"},
        ) as client:
            with holder(root, tmp_path / "ready", exclusive=True):
                assert client.get("/api/projects").status_code == 409
                response = client.post(
                    "/api/privacy",
                    json={
                        "project": project,
                        "patterns": [],
                        "actor": "human:合成",
                        "expected_revision": revision,
                    },
                )
                assert response.status_code == 409, response.text
                assert "Traceback" not in response.text
            assert client.get("/api/projects").status_code == 200
        with closing(Store(root, readonly=True)) as store:
            assert store.revision() == revision
            assert store.db.execute("SELECT count(*) FROM project_privacy").fetchone()[0] == 0
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert not thread.is_alive()
