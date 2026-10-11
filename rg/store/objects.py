from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

import zstandard


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    import io

    atomic_stream(path, io.BytesIO(data))


def _publish(source: str, destination: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        native: Any = ctypes
        library = native.WinDLL("kernel32", use_last_error=True)
        library.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        library.MoveFileExW.restype = wintypes.BOOL

        def full_path(value: str) -> str:
            absolute = os.path.abspath(value)
            if absolute.startswith("\\\\?\\"):
                return absolute
            if absolute.startswith("\\\\"):
                return "\\\\?\\UNC\\" + absolute[2:]
            return "\\\\?\\" + absolute

        # 临时文件位于同一目录；不允许跨卷复制。正文已 fsync，再请求写穿透发布。
        if not library.MoveFileExW(full_path(source), full_path(str(destination)), 1 | 8):
            raise native.WinError(native.get_last_error())
        return
    os.replace(source, destination)
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_stream(path: Path, stream: BinaryIO, prefix: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(prefix)
            while block := stream.read(1024 * 1024):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        _publish(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class ObjectStore:
    def __init__(self, root: Path, *, readonly: bool = False):
        self.root = root
        self.readonly = readonly
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("非法对象摘要")
        return self.root / sha[:2] / f"{sha}.zst"

    def put(self, data: bytes) -> str:
        if self.readonly:
            raise PermissionError("只读对象库不能写入")
        sha = digest(data)
        path = self.path(sha)
        if not path.exists():
            atomic_write(path, zstandard.ZstdCompressor().compress(data))
        elif self.get(sha) != data:
            raise ValueError("对象摘要冲突")
        return sha

    def get(self, sha: str) -> bytes:
        data = zstandard.ZstdDecompressor().decompress(self.path(sha).read_bytes())
        if digest(data) != sha:
            raise ValueError("原文对象损坏")
        return data
