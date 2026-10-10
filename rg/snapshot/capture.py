from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from rg.snapshot.files import WorkspaceFiles
from rg.snapshot.git import Deadline, Git
from rg.store.lease import lease
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import atomic_write

MAX_FILE_BYTES = 5_000_000
MAX_RECORD_BYTES = 4 * 1024 * 1024


def identifier(value: str) -> str:
    """数据库标识只用 UUID；不能把项目名或输入路径当成 Git/文件路径。"""
    if not isinstance(value, str):
        raise ValueError("快照 UUID 必须为字符串")
    return str(uuid.UUID(value))


def _signature(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns


def _content(
    files: WorkspaceFiles, relative: str
) -> tuple[bytes | None, str, dict[str, Any], tuple[int, ...]]:
    before = files.stat(relative)
    metadata = {"path": relative, "size": before.st_size, "mtime_ns": before.st_mtime_ns}
    if stat.S_ISLNK(before.st_mode):
        raw, mode = files.link(relative), "120000"
    elif stat.S_ISREG(before.st_mode):
        if before.st_size > MAX_FILE_BYTES:
            return None, "", {**metadata, "reason": "large_file"}, _signature(before)
        descriptor = files.open(relative)
        with os.fdopen(descriptor, "rb") as stream:
            if _signature(before) != _signature(os.fstat(stream.fileno())):
                raise ValueError("文件在快照读取前变化")
            raw = stream.read(MAX_FILE_BYTES + 1)
            if _signature(before) != _signature(os.fstat(stream.fileno())):
                raise ValueError("文件在快照读取期间变化")
        if len(raw) != before.st_size or len(raw) > MAX_FILE_BYTES:
            raise ValueError("快照文件长度变化")
        mode = "100755" if before.st_mode & 0o111 else "100644"
    else:
        return (
            None,
            "",
            {**metadata, "reason": "non_regular_or_nested_repository"},
            _signature(before),
        )
    return raw, mode, metadata, _signature(before)


def _tree(
    git: Git, temporary: Path, data_root: Path
) -> tuple[str, list[dict[str, Any]], set[bytes]]:
    with WorkspaceFiles(git.worktree) as files:
        return _populate_tree(git, temporary, data_root, files)


def _populate_tree(
    git: Git, temporary: Path, data_root: Path, files: WorkspaceFiles
) -> tuple[str, list[dict[str, Any]], set[bytes]]:
    git.run("read-tree", "--empty")
    arguments = ["ls-files", "--others", "--exclude-standard", "-z"]
    ignore = git.worktree / ".rgignore"
    if ignore.is_symlink():
        raise ValueError(".rgignore 不能是符号链接")
    if ignore.is_file():
        arguments.append(f"--exclude-from={ignore}")
    relatives = git.run(*arguments).split(b"\0")
    paths, entries, omitted, signatures = [], [], [], []
    for encoded in relatives:
        git.remaining()
        if not encoded:
            continue
        relative = os.fsdecode(encoded).rstrip("/")
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise ValueError("Git 返回越界路径")
        if ".git" in parts or (git.worktree / relative).is_relative_to(data_root):
            continue
        raw, mode, metadata, signature = _content(files, relative)
        signatures.append((relative, signature))
        if raw is None:
            omitted.append(metadata)
            continue
        # 保存已核验的字节，再批量 hash；不对原工作区执行 filters，也不受换行文件名影响。
        staging = temporary / f"blob-{len(paths)}"
        staging.write_bytes(raw)
        paths.append(str(staging).encode() + b"\n")
        entries.append((mode, encoded))
    objects = git.run(
        "hash-object", "-w", "--no-filters", "--stdin-paths", data=b"".join(paths)
    ).splitlines()
    if len(objects) != len(entries):
        raise ValueError("影子对象数量不一致")
    if git.run(*arguments).split(b"\0") != relatives:
        raise ValueError("工作区文件列表在快照期间变化")
    for relative, signature in signatures:
        git.remaining()
        if _signature(files.stat(relative)) != signature:
            raise ValueError("工作区文件在快照期间变化")
    files.verify()
    index = b"".join(
        mode.encode() + b" " + sha + b"\t" + path + b"\0"
        for (mode, path), sha in zip(entries, objects, strict=True)
    )
    git.run("update-index", "-z", "--index-info", data=index)
    return git.run("write-tree").decode().strip(), omitted, {entry[1] for entry in entries}


def capture(
    data_root: Path,
    project_id: str,
    root_id: str,
    worktree: Path,
    *,
    tool: str = "codex",
    trigger: str = "manual",
    source_sha256: str | None = None,
    native_session_id: str | None = None,
    prompt_id: str | None = None,
    asynchronous: bool = False,
    requested_at: str | None = None,
    seconds: float = 2,
) -> dict[str, Any]:
    with lease(data_root, writable=True):
        from rg.store.clear_denials import denied

        if denied(
            data_root,
            project=project_id,
            tool=tool,
            native=native_session_id,
            paths=[str(worktree)],
        ):
            raise PermissionError("该项目已清除，停止创建影子快照")
        return _capture(
            data_root,
            project_id,
            root_id,
            worktree,
            tool=tool,
            trigger=trigger,
            source_sha256=source_sha256,
            native_session_id=native_session_id,
            prompt_id=prompt_id,
            asynchronous=asynchronous,
            requested_at=requested_at,
            seconds=seconds,
        )


def _capture(
    data_root: Path,
    project_id: str,
    root_id: str,
    worktree: Path,
    *,
    tool: str,
    trigger: str,
    source_sha256: str | None,
    native_session_id: str | None,
    prompt_id: str | None,
    asynchronous: bool,
    requested_at: str | None,
    seconds: float,
) -> dict[str, Any]:
    from datetime import UTC, datetime

    project_id, root_id = identifier(project_id), identifier(root_id)
    if tool not in {"claude", "codex"} or not 0 < seconds <= 2:
        raise ValueError("快照来源或时间预算非法")
    worktree, data_root = worktree.resolve(), data_root.resolve()
    started = time.monotonic()
    taken_at = datetime.now(UTC).isoformat()
    record: dict[str, Any] = {
        "rg_record_type": "workspace_snapshot",
        "snapshot_key": uuid.uuid4().hex,
        "project_id": project_id,
        "root_id": root_id,
        "worktree": str(worktree),
        "trigger": trigger,
        "source_sha256": source_sha256,
        "native_session_id": native_session_id,
        "prompt_id": prompt_id,
        "async_race": asynchronous,
        "requested_at": requested_at or taken_at,
        "taken_at": taken_at,
        "head_commit": None,
        "branch": None,
        "dirty": None,
        "shadow_commit": None,
        "skipped": None,
        "omitted_files": [],
    }
    repository = data_root / "snapshots" / f"{project_id}.git"
    try:
        if not worktree.is_dir() or worktree.is_relative_to(data_root):
            raise ValueError("工作区不存在或位于数据目录中")
        if repository.is_symlink():
            raise ValueError("影子仓库不能是符号链接")
        with exclusive(data_root / "snapshots" / f"{project_id}.lock", "影子快照忙"):
            with tempfile.TemporaryDirectory(prefix="rg-capture-") as name:
                temporary = Path(name)
                git = Git(worktree, started + seconds, repository, temporary / "index")
                if not (repository / "HEAD").is_file():
                    # 空模板，不读取用户 Git 模板，更不执行仓库 hook。
                    template = temporary / "template"
                    template.mkdir()
                    git.environment.pop("GIT_WORK_TREE")
                    git.run("init", "--bare", "--quiet", f"--template={template}", str(repository))
                    git.environment["GIT_WORK_TREE"] = str(worktree)
                tree, omitted, paths = _tree(git, temporary, data_root)
                record["omitted_files"] = omitted
                record.update(Git(worktree, started + seconds).metadata(paths, bool(omitted)))
                git.environment.update(
                    {
                        "GIT_AUTHOR_NAME": "ResearchGraph",
                        "GIT_AUTHOR_EMAIL": "snapshot@localhost",
                        "GIT_COMMITTER_NAME": "ResearchGraph",
                        "GIT_COMMITTER_EMAIL": "snapshot@localhost",
                    }
                )
                commit = git.run(
                    "commit-tree", tree, data=f"快照 {record['snapshot_key']}\n".encode()
                )
                # 自包含快照；不按时间猜父提交。消费者验证成功后才固定保留引用。
                record["shadow_commit"] = commit.decode().strip()
                record["duration_ms"] = round((time.monotonic() - started) * 1000)
                if len(json.dumps(record, ensure_ascii=True).encode()) > MAX_RECORD_BYTES:
                    raise ValueError("快照元数据超出持久化上限")
    except Deadline:
        record.update(shadow_commit=None, skipped="timeout")
    except TaskBusy:
        record.update(shadow_commit=None, skipped="busy")
    except (OSError, ValueError, RuntimeError) as failure:
        record.update(shadow_commit=None, skipped=type(failure).__name__)
    record["duration_ms"] = round((time.monotonic() - started) * 1000)
    if record["duration_ms"] > 2000 and record["shadow_commit"] is not None:
        record.update(shadow_commit=None, skipped="timeout")
    record["finished_at"] = datetime.now(UTC).isoformat()
    publish(data_root, tool, record)
    return record


def publish(data_root: Path, tool: str, record: dict[str, Any]) -> Path:
    # 独立待登记记录，避免原始 hook 提示被消费者确认后才拍完快照的竞态。
    filename = f"{time.time_ns()}-{os.getpid()}-{record['snapshot_key']}-{tool}.json"
    path = data_root / "snapshots" / "pending" / filename
    with lease(data_root, writable=True):
        from rg.store.clear_denials import check_payload

        check_payload(data_root, tool, record)
        atomic_write(path, json.dumps(record, ensure_ascii=True, sort_keys=True).encode())
    return path
