from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rg.store.windows_files import NativeFiles


class WorkspaceFiles:
    """从打开的目录句柄读取；既避免重复解析路径，也拒绝跟随被替换的父目录链接。"""

    def __init__(self, root: Path):
        self.root = root
        self.directories: dict[tuple[str, ...], int] = {}
        self.native: NativeFiles | None = None

    def __enter__(self) -> WorkspaceFiles:
        if os.name == "nt":
            from rg.store.windows_files import NativeFiles

            self.native = NativeFiles(self.root)
            self.native.enter()
            return self
        self.directories[()] = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.native is not None:
            self.native.close()
            return
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
        if self.native is not None:
            return self.native.stat(relative)
        descriptor, name = self.parent(relative)
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)

    def link(self, relative: str) -> bytes:
        if self.native is not None:
            return self.native.link(relative)
        descriptor, name = self.parent(relative)
        return os.fsencode(os.readlink(name, dir_fd=descriptor))

    def open(self, relative: str) -> int:
        if self.native is not None:
            return self.native.open(relative)
        descriptor, name = self.parent(relative)
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)

    def signature(self, relative: str, value: os.stat_result | None = None) -> tuple[int, ...]:
        value = value if value is not None else self.stat(relative)
        result = (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns)
        return result + (self.native.changed(relative),) if self.native is not None else result

    def verify(self) -> None:
        if self.native is not None:
            self.native.verify()
            return
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
