from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from rg.api.server import LocalServer
from rg.artifacts import files
from rg.artifacts.service import Service
from rg.artifacts.views import versions
from rg.artifacts.worker import Worker
from rg.cli.main import parser, run
from rg.ingest.watch import watch
from rg.snapshot.capture import MAX_FILE_BYTES, capture
from rg.snapshot.git import Git
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.locking import TaskBusy, exclusive
from tests.golden.test_snapshots_hooks import project, queued
from tests.test_migrations import legacy_store


def snapshot(store, identity, root, work):
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None
    queued(store)
    return result


def large_file(work):
    content = b"A" * (MAX_FILE_BYTES + 127) + b"complete-end"
    path = work / "large.bin"
    path.write_bytes(content)
    return path, content


def rows(store, source):
    return store.db.execute("SELECT * FROM artifact_versions WHERE source=?", (source,)).fetchall()


def test_archived_bytes_modes_links_survive_missing_workspace_and_backup(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"never-follow-this")
    (work / "link").symlink_to(outside)
    (work / "run.sh").write_bytes(b"exit 99\r\n")
    (work / "run.sh").chmod(0o755)
    (work / "nested").mkdir()
    (work / "nested" / "a\n中文.txt").write_bytes(b"exact\r\nbytes")
    snap = snapshot(store, identity, root, work)
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    shutil.rmtree(work)
    result = Worker(store).run()
    assert result["done"] == 4
    captured = {Path(r["path"]).name: r for r in rows(store, "shadow_snapshot")}
    assert store.objects.get(captured["a\n中文.txt"]["content_sha256"]) == b"exact\r\nbytes"
    assert store.objects.get(captured["link"]["content_sha256"]) == os.fsencode(outside)
    assert captured["link"]["representation"] == "symlink_target_bytes"
    for value in captured.values():
        content = store.objects.get(value["content_sha256"])
        assert (
            value["digest"]
            == hashlib.sha1(
                f"blob {len(content)}\0".encode() + content, usedforsecurity=False
            ).hexdigest()
        )
        assert value["algo"] == "git-sha1" and value["claim_state"] == "candidate"
    observations = store.db.execute("SELECT * FROM artifact_observations").fetchall()
    assert {r["mode"] for r in observations} == {"100644", "100755", "120000"}
    assert all(
        json.loads(r["details"])["shadow_commit"] == snap["shadow_commit"] for r in observations
    )
    assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == raw
    assert store.db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 0
    destination = tmp_path / "backup"
    backup(store, destination)
    with closing(Store(destination)) as restored:
        assert versions(restored, {}) == versions(store, {})
        assert all(restored.objects.get(r["content_sha256"]) for r in captured.values())
        assert Worker(restored).run().get("processed", 0) == 0


def test_complete_large_file_hash_is_current_only_and_never_copied(store, tmp_path, monkeypatch):
    identity, root, work = project(store, tmp_path)
    path, content = large_file(work)
    snapshot(store, identity, root, work)
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    path.write_bytes(b"B" + content[1:])
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: pytest.fail("offline worker"))
    assert Worker(store).run()["done"] == 2
    value = rows(store, "current_file")[0]
    assert value["digest"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert value["content_sha256"] is None and value["algo"] == "sha256"
    observation = store.db.execute(
        "SELECT * FROM artifact_observations WHERE version_id=?", (value["version_id"],)
    ).fetchone()
    assert observation["snapshot_id"] is None and observation["discovery_snapshot_id"] is not None
    assert observation["hash_started_at"] <= observation["hash_finished_at"]
    assert json.loads(observation["details"]) == {
        "complete": True,
        "content_copied": False,
        "matches_discovery_hint": False,
    }
    assert not store.objects.path(value["digest"]).exists()
    assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == raw
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 0


def test_cache_reuses_read_window_and_rehashes_same_size_mtime_with_changed_ctime(
    store, tmp_path, monkeypatch
):
    identity, root, work = project(store, tmp_path)
    path, content = large_file(work)
    snapshot(store, identity, root, work)
    assert Worker(store).run()["done"] == 2
    first = store.db.execute(
        "SELECT o.* FROM artifact_observations o JOIN artifact_versions v USING(version_id) "
        "WHERE v.source='current_file'"
    ).fetchone()
    original = files.full_digest
    monkeypatch.setattr(files, "full_digest", lambda _: pytest.fail("unchanged file reread"))
    assert Worker(store).run().get("processed", 0) == 0
    snapshot(store, identity, root, work)
    assert Worker(store).run()["cache_reused"] == 1
    cached = store.db.execute("SELECT * FROM artifact_observations WHERE cache_reused=1").fetchone()
    assert cached["cached_from"] == first["observation_id"]
    assert cached["hash_finished_at"] == first["hash_finished_at"]
    before = path.stat()
    path.write_bytes(b"Z" + content[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(files, "full_digest", original)
    snapshot(store, identity, root, work)
    assert Worker(store).run()["cache_reused"] == 0
    assert len(rows(store, "current_file")) == 2
    assert store.health(identity)["artifacts"]["cache_reused"] == 1
    assert store.health(identity)["artifacts"]["current_hashed"] == 2


@pytest.mark.parametrize("change", ["bytes", "path", "parent"])
def test_mutation_during_read_discards_result_and_waits(store, tmp_path, monkeypatch, change):
    identity, root, work = project(store, tmp_path)
    (work / "nested").mkdir()
    path = work / "nested" / "large.bin"
    path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    snapshot(store, identity, root, work)
    original = files.full_digest

    def mutate(stream):
        result = original(stream)
        if change == "bytes":
            path.write_bytes(b"y" * (MAX_FILE_BYTES + 1))
        elif change == "path":
            replacement = work / "replacement"
            replacement.write_bytes(b"different")
            replacement.replace(path)
        else:
            (work / "nested").rename(work / "previous")
            (work / "nested").symlink_to(tmp_path)
        return result

    monkeypatch.setattr(files, "full_digest", mutate)
    assert Worker(store).run()["paused"] == 1
    assert not rows(store, "current_file")
    assert store.db.execute("SELECT count(*) FROM file_hash_cache").fetchone()[0] == 0
    assert Worker(store).run().get("processed", 0) == 0
    job = store.db.execute("SELECT * FROM artifact_jobs WHERE kind='file_hash'").fetchone()
    assert job["next_attempt_at"] is not None and job["attempts"] == 1


def test_hash_read_does_not_hold_sqlite_write_transaction(store, tmp_path, monkeypatch):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, identity, root, work)
    original = files.full_digest

    def measured(stream):
        assert not store.db.in_transaction
        with closing(Store(store.root)) as other:
            with other.transaction():
                other.db.execute(
                    "UPDATE projects SET name=? WHERE project_id=?", ("同时可写", identity)
                )
        return original(stream)

    monkeypatch.setattr(files, "full_digest", measured)
    assert Worker(store).run()["done"] == 2


def test_symlink_replacement_is_not_followed_and_failed_job_can_be_retried(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    path, content = large_file(work)
    snapshot(store, identity, root, work)
    outside = tmp_path / "outside"
    outside.write_bytes(b"never read")
    path.unlink()
    path.symlink_to(outside)
    assert Worker(store).run()["failed"] == 1
    assert not rows(store, "current_file")
    path.unlink()
    path.write_bytes(content)
    assert Worker(store).run().get("processed", 0) == 0
    assert Worker(store).run(retry_failed=True)["done"] == 1
    assert rows(store, "current_file")[0]["digest"] == hashlib.sha256(content).hexdigest()


def test_replaced_ancestor_is_rejected_before_reading_external_file(store, tmp_path, monkeypatch):
    parent = tmp_path / "registered-parent"
    parent.mkdir()
    identity, root, work = project(store, parent)
    large_file(work)
    snapshot(store, identity, root, work)
    external = tmp_path / "external"
    external.mkdir()
    (external / "work").mkdir()
    (external / "work" / "large.bin").write_bytes(b"outside-sensitive-bytes")
    parent.rename(tmp_path / "original-parent")
    parent.symlink_to(external)
    monkeypatch.setattr(files, "full_digest", lambda _: pytest.fail("external file read"))
    result = Worker(store).run()
    assert result["failed"] == 1 and result["done"] == 1
    assert not rows(store, "current_file")


def test_batch_limit_project_scope_and_readonly_paging(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    for number in range(5):
        (work / f"file{number}").write_bytes(bytes([number]))
    snapshot(store, identity, root, work)
    other = store.project("其他项目", [tmp_path / "other"])
    assert Worker(store, other).run().get("processed", 0) == 0
    assert Worker(store, identity).run(2)["processed"] == 2
    assert Worker(store, identity).run(2)["processed"] == 2
    assert Worker(store, identity).run(2)["processed"] == 2
    page = versions(store, {"project": identity, "limit": "2", "offset": "2"})
    assert page["total"] == 6 and len(page["versions"]) == 2 and page["partial"]
    assert versions(store, {"project": other})["total"] == 0
    assert versions(store, {"path": "' OR 1=1 --"})["total"] == 0
    with closing(Store(store.root, readonly=True)) as reader:
        assert versions(reader, {})["total"] == 6
    args = parser().parse_args(["versions", "--project", identity, "--limit", "1"])
    assert len(run(args, store)["versions"]) == 1
    for values in ({"limit": "0"}, {"offset": "-1"}, {"project": "missing"}, {"unknown": "x"}):
        with pytest.raises(ValueError):
            versions(store, values)
    for limit in [0, 1001, True]:
        with pytest.raises(ValueError):
            Worker(store).run(limit)


def test_jobs_observations_versions_are_immutable_and_old_owner_cannot_publish(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    snapshot(store, identity, root, work)
    worker = Worker(store)
    worker.discover(Counter())
    job_id = store.db.execute("SELECT job_id FROM artifact_jobs").fetchone()[0]
    job, owner = worker._claim(job_id)
    measured = worker._read(job)
    store.db.execute("UPDATE artifact_jobs SET owner_id='new-owner' WHERE job_id=?", (job_id,))
    with pytest.raises(RuntimeError, match="归属"):
        worker._finish(job, owner, measured, "done", None)
    assert not rows(store, "shadow_snapshot")
    assert worker.run()["recovered"] == 1
    for query in [
        "UPDATE artifact_jobs SET input_json='{}'",
        "UPDATE artifact_job_events SET kind='changed'",
        "DELETE FROM artifact_job_events",
        "UPDATE artifact_observations SET snapshot_id=NULL",
        "DELETE FROM artifact_observations",
        "UPDATE artifact_versions SET digest='wrong'",
        "DELETE FROM artifact_versions",
        "DELETE FROM artifact_discoveries",
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(query)


def test_root_registration_is_rechecked_before_publish(store, tmp_path, monkeypatch):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, identity, root, work)
    original = files.full_digest

    def changed(stream):
        measured = original(stream)
        store.db.execute(
            "UPDATE source_roots SET path=? WHERE root_id=?", (str(tmp_path / "changed"), root)
        )
        return measured

    monkeypatch.setattr(files, "full_digest", changed)
    with pytest.raises(ValueError, match="登记"):
        Worker(store).run()
    assert not rows(store, "current_file")


def test_exclusive_lock_prevents_guessing_running_worker_dead(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    snapshot(store, identity, root, work)
    worker = Worker(store)
    worker.discover(Counter())
    job_id = store.db.execute("SELECT job_id FROM artifact_jobs").fetchone()[0]
    worker._claim(job_id)
    with exclusive(store.root / "locks" / "artifacts.lock", "busy"):
        with pytest.raises(TaskBusy):
            Worker(store).run()
    assert store.db.execute("SELECT state FROM artifact_jobs").fetchone()[0] == "running"


def test_actual_sigkill_restores_unfinished_hash_without_partial_version(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, identity, root, work)
    marker = tmp_path / "reading"
    script = """
import sys,time
from contextlib import closing
from pathlib import Path
from rg.artifacts import files
from rg.artifacts.worker import Worker
from rg.store.database import Store
original=files.full_digest
def blocked(stream):
    Path(sys.argv[2]).write_text('reading')
    time.sleep(30)
    return original(stream)
files.full_digest=blocked
with closing(Store(Path(sys.argv[1]))) as store:
    Worker(store).run()
"""
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(store.root), str(marker)], cwd=tmp_path
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        assert not rows(store, "current_file")
        child.send_signal(signal.SIGKILL)
        assert child.wait(5) == -signal.SIGKILL
        assert Worker(store).run()["recovered"] == 1
        assert len(rows(store, "current_file")) == 1
        assert (
            store.db.execute(
                "SELECT count(*) FROM artifact_job_events WHERE kind='recovered'"
            ).fetchone()[0]
            == 1
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(5)


def test_watch_runs_background_hash_and_closes_actual_child(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, identity, root, work)
    loop = watch(store, interval=0.01)
    try:
        next(loop)
        deadline = time.monotonic() + 12
        while not rows(store, "current_file") and time.monotonic() < deadline:
            time.sleep(0.03)
        assert len(rows(store, "current_file")) == 1
    finally:
        loop.close()
    with Service(store) as service:
        service.tick()
        child = service.process
        assert child is not None and child.poll() is None
        service.tick()
        assert service.process is child
    assert child.poll() == 0


def test_versions_http_auth_readonly_and_project_isolation(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, identity, root, work)
    Worker(store).run()
    other = store.project("空项目", [tmp_path / "empty"])
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("合成页面")
    server = LocalServer(store.root, web, port=0, token="synthetic-local-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = [tuple(r) for r in store.db.execute("SELECT * FROM artifact_jobs")]
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert client.get("/api/versions").status_code == 401
            headers = {"Authorization": f"Bearer {server.token}"}
            assert (
                client.get(
                    "/api/versions", headers=headers | {"Origin": "https://elsewhere.invalid"}
                ).status_code
                == 403
            )
            reply = client.get(
                "/api/versions", headers=headers, params={"project": identity, "limit": 1}
            )
            assert reply.status_code == 200
            assert reply.json()["total"] == 2 and reply.json()["partial"]
            assert (
                client.get("/api/versions", headers=headers, params={"project": other}).json()[
                    "total"
                ]
                == 0
            )
            assert (
                client.get("/api/versions?project=x&project=y", headers=headers).status_code == 400
            )
            assert client.get("/api/versions?limit=0", headers=headers).status_code == 400
            assert client.get("/api/versions?project=missing", headers=headers).status_code == 404
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert [tuple(r) for r in store.db.execute("SELECT * FROM artifact_jobs")] == before


def test_v12_migration_is_atomic_preserves_l0_and_does_not_invent_files(tmp_path, monkeypatch):
    from rg.store import migrations

    root, raw = legacy_store(tmp_path, monkeypatch, version=12)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 13, (*migrations.MIGRATIONS[13], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE name='artifact_jobs'").fetchone()[
                0
            ]
            == 0
        )
    with closing(Store(root)) as restored:
        assert restored.raw(1) == raw
        assert restored.health()["artifacts"]["versions"] == 0
        assert restored.db.execute("SELECT count(*) FROM artifact_jobs").fetchone()[0] == 0


def test_missing_blob_is_a_visible_failure_not_current_file_repair(store, tmp_path, monkeypatch):
    identity, root, work = project(store, tmp_path)
    snapshot(store, identity, root, work)
    worker = Worker(store)
    worker.discover(Counter())
    original = Git.run

    def missing(self, *arguments, **kwargs):
        if arguments[:2] == ("cat-file", "blob"):
            raise RuntimeError("missing archived object")
        return original(self, *arguments, **kwargs)

    monkeypatch.setattr(Git, "run", missing)
    assert worker.run()["failed"] == 1
    assert not rows(store, "shadow_snapshot")
    assert store.health(identity)["artifacts"]["jobs"]["failed"] == 1
    monkeypatch.setattr(Git, "run", original)
    assert worker.run(retry_failed=True)["done"] == 1


def test_snapshot_read_waits_rotate_so_new_snapshots_are_not_starved(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    snapshot(store, identity, root, work)
    existing = dict(store.db.execute("SELECT * FROM workspace_snapshots").fetchone())
    for number in range(20):
        copied = {k: v for k, v in existing.items() if k != "snapshot_id"}
        copied["snapshot_key"] = f"synthetic-copy-{number}"
        columns = list(copied)
        # 字段来自固定表结构；测试复制合成元数据，值仍参数绑定。
        store.db.execute(
            "INSERT INTO workspace_snapshots ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(copied.values()),
        )
    repository = store.root / "snapshots" / f"{identity}.git"
    with exclusive(repository.with_suffix(".lock"), "busy"):
        assert Worker(store).run()["discovery_busy"] == 20
    Worker(store).run()
    assert store.db.execute("SELECT 1 FROM artifact_discoveries WHERE snapshot_id=21").fetchone()
    assert store.db.execute("SELECT count(*) FROM artifact_discovery_attempts").fetchone()[0] == 20


def test_many_observations_are_bounded_and_report_omitted_history(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    large_file(work)
    for _ in range(4):
        snapshot(store, identity, root, work)
        Worker(store).run()
    result = next(v for v in versions(store, {})["versions"] if v["source"] == "current_file")
    assert result["observations_total"] == 4 and result["observations_partial"]
    assert len(result["observations"]) == 3
    assert all(o["snapshot_id"] is None for o in result["observations"])
    assert store.health(identity)["artifacts"]["cache_reused"] == 3


@pytest.mark.skipif(sys.platform != "linux", reason="此进程终止证明使用 Linux proc 状态")
def test_background_child_exits_when_actual_parent_pipe_disappears(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    snapshot(store, identity, root, work)
    marker = tmp_path / "child-id"
    script = """
import sys,time
from pathlib import Path
from rg.store.database import Store
from rg.artifacts.service import Service
store=Store(Path(sys.argv[1]))
with Service(store) as service:
    service.tick()
    Path(sys.argv[2]).write_text(str(service.process.pid))
    time.sleep(30)
"""
    parent = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(store.root), str(marker)], cwd=tmp_path
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        child_pid = int(marker.read_text())
        parent.kill()
        parent.wait(5)
        state = Path(f"/proc/{child_pid}/stat")

        def running():
            # 进程可在存在检查与打开文件之间被系统回收，直接读取一次判定。
            try:
                return state.read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except (FileNotFoundError, ProcessLookupError):
                return False

        deadline = time.monotonic() + 5
        while running() and time.monotonic() < deadline:
            time.sleep(0.03)
        assert not running()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(5)
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
