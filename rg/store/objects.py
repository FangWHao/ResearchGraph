from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

import zstandard


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


class ObjectStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("非法对象摘要")
        return self.root / sha[:2] / f"{sha}.zst"

    def put(self, data: bytes) -> str:
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
