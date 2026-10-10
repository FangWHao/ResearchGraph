from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType


class WorkspaceFiles:
    """从打开的目录句柄读取；既避免重复解析路径，也拒绝跟随被替换的父目录链接。"""

    def __init__(self, root: Path):
        self.root = root
        self.directories: dict[tuple[str, ...], int] = {}

    def __enter__(self) -> WorkspaceFiles:
        self.directories[()] = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for descriptor in self.directories.values():
            os.close(descriptor)

    def parent(self, relative: str) -> tuple[int, str]:
        parts = Path(relative).parts
        prefix: tuple[str, ...] = ()
        for part in parts[:-1]:
            following = (*prefix, part)
            if following not in self.directories:
                self.directories[following] = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=self.directories[prefix],
                )
            prefix = following
        return self.directories[prefix], parts[-1]

    def stat(self, relative: str) -> os.stat_result:
        descriptor, name = self.parent(relative)
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)

    def link(self, relative: str) -> bytes:
        descriptor, name = self.parent(relative)
        return os.fsencode(os.readlink(name, dir_fd=descriptor))

    def open(self, relative: str) -> int:
        descriptor, name = self.parent(relative)
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)

    def verify(self) -> None:
        for parts, descriptor in self.directories.items():
            if not parts:
                current = self.root.lstat()
            else:
                current = os.stat(
                    parts[-1], dir_fd=self.directories[parts[:-1]], follow_symlinks=False
                )
            observed = os.fstat(descriptor)
            if (current.st_dev, current.st_ino, current.st_mode) != (
                observed.st_dev,
                observed.st_ino,
                observed.st_mode,
            ):
                raise ValueError("工作区目录在快照期间变化")
