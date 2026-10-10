from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rg.cli.main import parser, run
from rg.hooks import entry
from rg.ingest.spool import MAX_BYTES, consume, register
from rg.snapshot import cli as snapshot_cli
from rg.snapshot.capture import MAX_FILE_BYTES, capture
from rg.snapshot.config import examples, export_registry, load_registry, registry_path
from rg.snapshot.git import Git
from rg.store.backup import backup
from rg.store.database import Store, dumps
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import atomic_write, digest


def git(path: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(path), *arguments], check=True, capture_output=True
    ).stdout


def project(store: Store, tmp_path: Path, repository: bool = True) -> tuple[str, str, Path]:
    work = tmp_path / "work"
    work.mkdir()
    (work / "source.txt").write_bytes(b"original\n")
    if repository:
        git(work, "init", "-b", "main")
        git(work, "add", ".")
        git(work, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "合成")
    identity = store.project("合成快照项目", [work])
    root = store.db.execute("SELECT root_id FROM source_roots").fetchone()[0]
    export_registry(store)
    return identity, root, work


def shadow(store: Store, identity: str, *arguments: str) -> bytes:
    path = store.root / "snapshots" / f"{identity}.git"
    return subprocess.run(
        ["git", f"--git-dir={path}", *arguments], capture_output=True, check=True
    ).stdout


def queued(store: Store) -> int:
    register(store)
    consume(store, [])
    return store.db.execute("SELECT count(*) FROM workspace_snapshots").fetchone()[0]


def test_raw_worktree_ignore_rules_modes_and_user_index_are_preserved(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    (work / "source.txt").write_bytes(b"staged\r\n")
    git(work, "add", "source.txt")
    (work / "source.txt").write_bytes(b"unstaged\r\n")
    (work / ".gitignore").write_text("ignored.txt\n*.private\n", encoding="utf-8")
    (work / ".rgignore").write_text("rg-only.txt\n", encoding="utf-8")
    (work / "ignored.txt").write_text("不可捕获")
    git(work, "add", "-f", "ignored.txt")
    (work / "rg-only.txt").write_text("不可捕获")
    (work / "nested").mkdir()
    (work / "nested" / ".gitignore").write_text("hidden\n")
    (work / "nested" / "hidden").write_text("不可捕获")
    (work / "nested" / "keep\n中文.txt").write_text("完整换行路径")
    target = tmp_path / "outside"
    target.write_text("不能跟随外部符号链接读取")
    (work / "link").symlink_to(target)
    (work / "run.sh").write_text("printf '不能执行'\n")
    (work / "run.sh").chmod(0o755)
    index_path = work / ".git" / "index"
    before = index_path.read_bytes()
    files = {
        str(p.relative_to(work)): p.read_bytes()
        for p in work.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and result["dirty"] is True
    assert result["head_commit"] == git(work, "rev-parse", "HEAD").decode().strip()
    assert result["branch"] == "main" and index_path.read_bytes() == before
    assert all((work / p).read_bytes() == content for p, content in files.items())
    commit = result["shadow_commit"]
    assert shadow(store, identity, "show", f"{commit}:source.txt") == b"unstaged\r\n"
    names = shadow(store, identity, "ls-tree", "-r", "--name-only", "-z", commit).split(b"\0")
    assert b"ignored.txt" not in names and b"rg-only.txt" not in names
    assert b"nested/hidden" not in names and "nested/keep\n中文.txt".encode() in names
    assert shadow(store, identity, "show", f"{commit}:link") == os.fsencode(target)
    modes = shadow(store, identity, "ls-tree", commit)
    assert b"100755" in modes and b"120000" in modes
    assert queued(store) == 1 and queued(store) == 1
    for query in ["DELETE FROM workspace_snapshots", "UPDATE workspace_snapshots SET branch='x'"]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(query)


def test_repository_filters_fsmonitor_and_hooks_are_never_executed(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    marker = tmp_path / "executed"
    command = f"touch {marker}"
    git(work, "config", "filter.danger.clean", command)
    git(work, "config", "filter.danger.process", command)
    git(work, "config", "filter.danger.required", "true")
    git(work, "config", "core.fsmonitor", command)
    (work / ".gitattributes").write_text("*.txt filter=danger\n")
    hook = work / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\n{command}\n")
    hook.chmod(0o755)
    (work / "source.txt").write_text("强制 status 检查更新")
    before = (work / ".git" / "index").read_bytes()
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and not marker.exists()
    assert result["dirty"] is None and result["dirty_unavailable_reason"] == "configured_filters"
    assert (work / ".git" / "index").read_bytes() == before


def test_large_files_and_nested_repository_are_explicit_gaps(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    large = b"L" * (MAX_FILE_BYTES + 1)
    (work / "large.bin").write_bytes(large)
    nested = work / "nested-repo"
    nested.mkdir()
    git(nested, "init", "-b", "main")
    (nested / "source.txt").write_text("未捕获子仓库")
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None
    omitted = {value["path"]: value for value in result["omitted_files"]}
    assert omitted["large.bin"]["reason"] == "large_file"
    assert omitted["large.bin"]["size"] == MAX_FILE_BYTES + 1
    assert omitted["nested-repo"]["reason"] == "non_regular_or_nested_repository"
    assert queued(store) == 1
    assert store.health()["snapshots"]["partial"] == 1
    sha = hashlib.sha1(f"blob {len(large)}\0".encode() + large).hexdigest()
    repository = store.root / "snapshots" / f"{identity}.git"
    result = subprocess.run(
        ["git", f"--git-dir={repository}", "cat-file", "-e", sha], capture_output=True
    )
    assert result.returncode != 0


def test_tracked_large_file_is_not_rehashed_by_git_status(
    store: Store, tmp_path: Path, monkeypatch
):
    identity, root, work = project(store, tmp_path)
    (work / "large.bin").write_bytes(b"X" * (MAX_FILE_BYTES + 1))
    git(work, "add", "large.bin")
    original = Git.run

    def check(self, *args, **kwargs):
        assert args[0] != "status", "Git status 不能重新读取大文件"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Git, "run", check)
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and result["head_commit"] is not None
    assert result["omitted_files"][0]["reason"] == "large_file"


def test_replaced_directory_cannot_read_external_file(store: Store, tmp_path: Path, monkeypatch):
    identity, root, work = project(store, tmp_path)
    nested = work / "nested"
    nested.mkdir()
    (nested / "source.txt").write_text("原目录内的合成资料")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = b"synthetic-external-content-must-not-be-read"
    (outside / "source.txt").write_bytes(secret)
    original = Git.run
    changed = False

    def change(self, *args, **kwargs):
        nonlocal changed
        value = original(self, *args, **kwargs)
        if args[0] == "ls-files" and not changed:
            nested.rename(work / "old-nested")
            nested.symlink_to(outside, target_is_directory=True)
            changed = True
        return value

    monkeypatch.setattr(Git, "run", change)
    result = capture(store.root, identity, root, work)
    assert result["shadow_commit"] is None and result["skipped"] is not None
    sha = hashlib.sha1(f"blob {len(secret)}\0".encode() + secret).hexdigest()
    repository = store.root / "snapshots" / f"{identity}.git"
    probe = subprocess.run(
        ["git", f"--git-dir={repository}", "cat-file", "-e", sha], capture_output=True
    )
    assert probe.returncode != 0


def test_detached_and_unborn_head_do_not_invent_branches(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    git(work, "checkout", "--detach")
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and result["head_commit"] is not None
    assert result["branch"] is None and result["dirty"] is False
    other = tmp_path / "unborn"
    other.mkdir()
    git(other, "init", "-b", "new-branch")
    identity2 = store.project("初始工作区", [other])
    root2 = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (identity2,)
    ).fetchone()[0]
    result = capture(store.root, identity2, root2, other)
    assert result["skipped"] is None and result["head_commit"] is None
    assert result["branch"] == "new-branch" and result["dirty"] is False


def test_separate_worktrees_keep_both_indexes_and_snapshot_trees(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    other = tmp_path / "second"
    git(work, "worktree", "add", "-b", "second", str(other))
    root2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    store.db.execute(
        "INSERT INTO source_roots(root_id,project_id,host_id,path,kind) "
        "VALUES (?,?,'local',?,'worktree')",
        (root2, identity, str(other)),
    )
    second_index = Path(git(other, "rev-parse", "--git-path", "index").decode().strip())
    before, before2 = (work / ".git" / "index").read_bytes(), second_index.read_bytes()
    (work / "source.txt").write_text("第一工作区")
    (other / "source.txt").write_text("第二工作区")
    first, second = (
        capture(store.root, identity, root, work),
        capture(store.root, identity, root2, other),
    )
    assert first["skipped"] is None and second["skipped"] is None
    assert (
        shadow(store, identity, "show", f"{first['shadow_commit']}:source.txt").decode()
        == "第一工作区"
    )
    assert (
        shadow(store, identity, "show", f"{second['shadow_commit']}:source.txt").decode()
        == "第二工作区"
    )
    assert (work / ".git" / "index").read_bytes() == before
    assert second_index.read_bytes() == before2
    assert queued(store) == 2


def test_no_git_unknown_metadata_and_empty_snapshot(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path, repository=False)
    (work / "source.txt").unlink()
    result = capture(store.root, identity, root, work)
    assert result["skipped"] is None and result["head_commit"] is None
    assert result["dirty"] is None and result["branch"] is None
    assert shadow(store, identity, "ls-tree", result["shadow_commit"]) == b""
    assert not (work / ".git").exists() and queued(store) == 1


def test_deadline_busy_and_changed_file_never_become_valid_snapshots(
    store: Store, tmp_path: Path, monkeypatch
):
    identity, root, work = project(store, tmp_path)
    with exclusive(store.root / "snapshots" / f"{identity}.lock"):
        result = capture(store.root, identity, root, work)
    assert result["skipped"] == "busy" and result["shadow_commit"] is None
    result = capture(store.root, identity, root, work, seconds=0.000001)
    assert result["skipped"] == "timeout" and result["shadow_commit"] is None
    original = Git.run

    def change(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        if args[0] == "hash-object":
            (work / "source.txt").write_text("读取结束后发生的变化")
        return value

    monkeypatch.setattr(Git, "run", change)
    result = capture(store.root, identity, root, work)
    assert result["skipped"] == "ValueError" and result["shadow_commit"] is None
    assert queued(store) == 3
    assert (
        store.db.execute(
            "SELECT count(*) FROM workspace_snapshots WHERE shadow_commit IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_snapshot_receipt_survives_crash_and_backup_without_queue(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    result = capture(store.root, identity, root, work)
    register(store)

    def crash():
        raise SystemExit("合成断点")

    with pytest.raises(SystemExit):
        consume(store, [], fault=crash)
    assert store.db.execute("SELECT count(*) FROM workspace_snapshots").fetchone()[0] == 1
    destination = tmp_path / "backup"
    backup(store, destination)
    shutil.rmtree(destination / "snapshots" / "pending")
    restored = Store(destination)
    consume(restored, [])
    assert restored.db.execute("SELECT count(*) FROM workspace_snapshots").fetchone()[0] == 1
    row = restored.db.execute("SELECT * FROM workspace_snapshots").fetchone()
    assert (
        json.loads(restored.objects.get(row["record_sha256"]))["snapshot_key"]
        == result["snapshot_key"]
    )
    assert shadow(restored, identity, "show", f"{row['shadow_commit']}:source.txt") == b"original\n"
    assert restored.db.execute("SELECT state FROM jobs").fetchone()[0] == "done"
    restored.close()


def test_unregistered_or_forged_snapshot_preserves_raw_and_fails(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    result = capture(store.root, identity, root, work)
    path = next((store.root / "snapshots" / "pending").glob("*.json"))
    value = json.loads(path.read_bytes())
    value["root_id"] = "11111111-2222-3333-4444-555555555555"
    raw = dumps(value).encode()
    path.write_bytes(raw)
    assert queued(store) == 0
    assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "failed"
    assert path.read_bytes() == raw
    sha = store.db.execute("SELECT object_sha256 FROM spool_receipts").fetchone()[0]
    assert store.objects.get(sha) == raw
    assert result["shadow_commit"] is not None


def test_missing_shadow_blob_never_enters_snapshot_history(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    result = capture(store.root, identity, root, work)
    blob = (
        shadow(store, identity, "rev-parse", f"{result['shadow_commit']}:source.txt")
        .decode()
        .strip()
    )
    repository = store.root / "snapshots" / f"{identity}.git"
    (repository / "objects" / blob[:2] / blob[2:]).unlink()
    assert queued(store) == 0
    assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "failed"
    assert next((store.root / "snapshots" / "pending").glob("*.json")).exists()


def test_snapshot_can_be_consumed_after_original_worktree_is_deleted(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    result = capture(store.root, identity, root, work)
    shutil.rmtree(work)
    assert queued(store) == 1
    assert shadow(store, identity, "show", f"{result['shadow_commit']}:source.txt") == b"original\n"


def test_backup_cannot_report_success_when_saved_snapshot_blob_is_missing(
    store: Store, tmp_path: Path
):
    identity, root, work = project(store, tmp_path)
    result = capture(store.root, identity, root, work)
    assert queued(store) == 1
    blob = (
        shadow(store, identity, "rev-parse", f"{result['shadow_commit']}:source.txt")
        .decode()
        .strip()
    )
    repository = store.root / "snapshots" / f"{identity}.git"
    (repository / "objects" / blob[:2] / blob[2:]).unlink()
    destination = tmp_path / "invalid-backup"
    with pytest.raises(RuntimeError):
        backup(store, destination)
    assert not (destination / "manifest.json").exists()


def test_backup_and_consumer_share_shadow_lock_without_following_queue_symlinks(
    store: Store, tmp_path: Path
):
    identity, root, work = project(store, tmp_path)
    capture(store.root, identity, root, work)
    register(store)
    destination = tmp_path / "copy"
    with exclusive(store.root / "snapshots" / f"{identity}.lock"):
        assert consume(store, [])["spool_waiting"] == 1
        with pytest.raises(TaskBusy):
            backup(store, destination)
        assert not destination.exists()
    assert consume(store, [])["spool_done"] == 1
    external = tmp_path / "external"
    external.write_text("合成外部资料")
    (store.root / "spool").mkdir(exist_ok=True)
    (store.root / "spool" / "untrusted.json").symlink_to(external)
    backup(store, destination)
    assert (destination / "spool" / "untrusted.json").is_symlink()


def test_hook_only_spools_and_snapshots_without_database_or_transcript_reads(
    store: Store, tmp_path: Path, monkeypatch
):
    identity, root, work = project(store, tmp_path)
    transcript = tmp_path / "do-not-read.jsonl"
    raw = dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "synthetic",
            "cwd": str(work),
            "transcript_path": str(transcript),
            "turn_id": "turn-1",
            "prompt": "这只是资料",
            "command": "touch 不允许执行",
        }
    ).encode()

    def forbidden(*args, **kwargs):
        raise AssertionError("钩子不能打开 SQLite 或会话日志")

    with monkeypatch.context() as change:
        change.setattr(sqlite3, "connect", forbidden)
        entry.process(store.root, "codex", "UserPromptSubmit", raw)
    source = next((store.root / "spool").glob("*.json"))
    assert source.read_bytes() == raw and not transcript.exists()
    assert queued(store) == 1
    value = store.db.execute("SELECT metadata FROM workspace_snapshots").fetchone()[0]
    assert json.loads(value)["source_sha256"] == digest(raw)
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


@pytest.mark.parametrize(
    "event,raw",
    [
        ("PreCompact", b"broken json"),
        ("UserPromptSubmit", b"{}"),
        ("Stop", b'{"hook_event_name":"Stop"}'),
    ],
)
def test_actual_hook_is_silent_and_exit_zero_on_bad_or_valid_input(
    tmp_path: Path, event: str, raw: bytes
):
    root = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, "-m", "rg.hooks.entry", "codex", event, "--data-dir", str(root)],
        input=raw,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert next((root / "spool").glob("*.json")).read_bytes() == raw
    assert not (root / "rg.db").exists()
    if event != "Stop":
        log = (root / "logs" / "hook-errors.log").read_bytes()
        assert (b"ValueError" in log or b"JSONDecodeError" in log) and raw not in log


def test_invalid_arguments_and_storage_failure_always_exit_zero(tmp_path: Path, monkeypatch):
    root = tmp_path / "data"
    root.write_bytes(b"cannot make a directory")
    monkeypatch.setenv("RG_DATA_DIR", str(root))
    for arguments in [["invalid", "Stop"], ["codex", "PreCompact"]]:
        result = subprocess.run(
            [sys.executable, "-m", "rg.hooks.entry", *arguments, "--data-dir", str(root)],
            input=b"{}",
            capture_output=True,
        )
        assert result.returncode == 0 and result.stdout == result.stderr == b""


def test_oversized_hook_preserves_full_original_as_held_file(tmp_path: Path):
    root = tmp_path / "data"
    raw = b"x" * (MAX_BYTES + 12345)
    result = subprocess.run(
        [sys.executable, "-m", "rg.hooks.entry", "codex", "PreCompact", "--data-dir", str(root)],
        input=raw,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert next((root / "spool").glob("*.json")).read_bytes() == raw
    value = Store(root)
    assert register(value)["spool_held_files"] == 1
    assert value.db.execute("SELECT count(*) FROM spool_receipts").fetchone()[0] == 0
    value.close()


def test_generated_configuration_does_not_install_and_benchmark_switches_mode(
    store: Store, tmp_path: Path, monkeypatch
):
    identity, root, work = project(store, tmp_path)
    output = tmp_path / "examples"
    result = examples(store, output)
    assert result["installed"] is False and result["registered_roots"] == 1
    assert not (tmp_path / ".codex").exists() and not (tmp_path / ".claude").exists()
    data = json.loads((output / "codex.json").read_bytes())["hooks"]
    assert data["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 2
    assert data["PreCompact"][0]["hooks"][0]["async"] is True
    durations = iter([50, 50, 50, 50, 301])

    def fake(*args, **kwargs):
        return {"duration_ms": next(durations), "skipped": None}

    monkeypatch.setattr(snapshot_cli, "capture", fake)
    result = snapshot_cli.snapshot(store, identity, work, 5)
    assert result["p95_ms"] == 301 and result["snapshot_mode"] == "async"
    assert load_registry(store.root)[0]["snapshot_mode"] == "async"
    with pytest.raises(ValueError):
        snapshot_cli.snapshot(store, identity, work, 4)
    assert run(parser().parse_args(["init"]), store)["hooks_installed"] is False


def test_actual_async_snapshot_keeps_race_and_original_input(store: Store, tmp_path: Path):
    identity, root, work = project(store, tmp_path)
    values = load_registry(store.root)
    values[0]["snapshot_mode"] = "async"
    atomic_write(registry_path(store.root), dumps(values).encode())
    raw = dumps(
        {"hook_event_name": "UserPromptSubmit", "cwd": str(work), "session_id": "synthetic"}
    ).encode()
    malicious = work / "rg"
    malicious.mkdir()
    marker = tmp_path / "must-not-execute"
    (malicious / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(entry.__file__).resolve()),
            "codex",
            "UserPromptSubmit",
            "--data-dir",
            str(store.root),
        ],
        input=raw,
        capture_output=True,
        timeout=5,
        cwd=work,
    )
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert not marker.exists()
    until = time.monotonic() + 5
    while not list((store.root / "snapshots" / "pending").glob("*.json")):
        assert time.monotonic() < until
        time.sleep(0.02)
    assert queued(store) == 1
    assert not marker.exists()
    record = store.db.execute("SELECT async_race,metadata FROM workspace_snapshots").fetchone()
    metadata = json.loads(record["metadata"])
    assert record["async_race"] == 1 and metadata["requested_at"] <= metadata["taken_at"]
    assert next((store.root / "spool").glob("*.json")).read_bytes() == raw
