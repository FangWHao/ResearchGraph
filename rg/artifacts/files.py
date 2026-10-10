from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, BinaryIO

from rg.snapshot.files import WorkspaceFiles
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

CHUNK_BYTES = 1024 * 1024


class ChangedDuringRead(ValueError):
    pass


class PhysicalFiles(WorkspaceFiles):
    """绝对根目录的各级父目录也逐级打开，拒绝被替换成外部链接。"""

    def __enter__(self) -> PhysicalFiles:
        if not self.root.is_absolute():
            raise ValueError("物理文件根目录必须为绝对路径")
        self.ancestors: list[int] = []
        self.ancestry: list[tuple[int, str, int]] = []
        options = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        opened = [os.open(self.root.anchor, options)]
        try:
            for part in self.root.parts[1:]:
                parent = opened[-1]
                descriptor = os.open(part, options, dir_fd=parent)
                opened.append(descriptor)
                self.ancestry.append((parent, part, descriptor))
            self.ancestors = opened[:-1]
            self.directories[()] = opened[-1]
        except BaseException:
            for descriptor in opened:
                os.close(descriptor)
            raise
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            super().__exit__(kind, value, traceback)
        finally:
            for descriptor in self.ancestors:
                os.close(descriptor)

    def verify(self) -> None:
        super().verify()
        for parent, name, descriptor in self.ancestry:
            observed = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_mode) != (
                observed.st_dev,
                observed.st_ino,
                observed.st_mode,
            ):
                raise ChangedDuringRead("根目录的父目录在完整摘要期间变化")


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("版本路径格式非法")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or ".git" in path.parts:
        raise ValueError("版本路径超出登记范围")
    return str(path)


def signature(value: os.stat_result) -> list[int]:
    return [
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    ]


def full_digest(stream: BinaryIO) -> tuple[str, int]:
    measured = hashlib.sha256()
    size = 0
    while block := stream.read(CHUNK_BYTES):
        measured.update(block)
        size += len(block)
    return measured.hexdigest(), size


def current_file(store: Store, root: Path, relative: str, root_id: str) -> dict[str, Any]:
    relative = relative_path(relative)
    with PhysicalFiles(root) as files:
        before = files.stat(relative)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("后台完整摘要仅接受登记目录内的普通文件")
        stamp = signature(before)
        key = digest(dumps([root_id, relative, stamp]).encode())
        cached = store.db.execute(
            "SELECT o.*,v.digest,v.size,v.content_sha256,v.observed_at FROM file_hash_cache f "
            "JOIN artifact_observations o USING(observation_id) "
            "JOIN artifact_versions v USING(version_id) WHERE f.cache_key=?",
            (key,),
        ).fetchone()
        started = now()
        descriptor = files.open(relative)
        with os.fdopen(descriptor, "rb") as stream:
            if stamp != signature(os.fstat(stream.fileno())):
                raise ChangedDuringRead("文件在摘要读取前变化")
            if cached:
                sha, size = cached["digest"], cached["size"]
            else:
                sha, size = full_digest(stream)
            if stamp != signature(os.fstat(stream.fileno())):
                raise ChangedDuringRead("文件在摘要读取期间变化")
        try:
            if stamp != signature(files.stat(relative)) or size != before.st_size:
                raise ChangedDuringRead("文件路径或长度在摘要读取期间变化")
            files.verify()
        except (OSError, ValueError) as error:
            raise ChangedDuringRead("文件路径或父目录在摘要读取期间变化") from error
        return {
            "sha256": sha,
            "size": size,
            "signature": stamp,
            "cache_key": key,
            "mode": "100755" if before.st_mode & 0o111 else "100644",
            "hash_started_at": cached["hash_started_at"] if cached else started,
            "hash_finished_at": cached["hash_finished_at"] if cached else now(),
            "cache_reused": bool(cached),
            "cached_from": cached["observation_id"] if cached else None,
            "content_sha256": None,
        }
