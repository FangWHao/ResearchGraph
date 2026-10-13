"""依据路径自身语法规范化，不访问磁盘、不展开变量或解析命令。"""

from __future__ import annotations

import ntpath
import posixpath
import re
from pathlib import PureWindowsPath
from typing import Any


def valid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 16384
        and not any(ord(char) < 32 for char in value)
        and not value.replace("\\", "/").startswith(("//?/", "//./"))
    )


def windows(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:", value) or value.startswith(("\\\\", "//")))


def native_absolute(value: Any) -> str | None:
    if not valid(value):
        return None
    if windows(value):
        if not PureWindowsPath(value).is_absolute():
            return None
        drive, rest = ntpath.splitdrive(value)
        if ":" in rest:
            return None  # 额外数据流不作为普通文件路径。
        normalized = ntpath.normpath(value)
        return normalized[0].upper() + normalized[1:] if drive[1:2] == ":" else normalized
    return posixpath.normpath(value) if value.startswith("/") else None


def absolute(value: Any) -> str | None:
    normalized = native_absolute(value)
    return normalized.replace("\\", "/") if normalized and windows(normalized) else normalized


def resolve(path: Any, working_dir: str | None = None) -> str | None:
    if not valid(path):
        return None
    base = native_absolute(working_dir)
    normalized = native_absolute(path)
    if normalized is not None:
        if base and windows(base) and not windows(normalized):
            return None  # Windows的根相对写法缺少盘符，不借工作目录猜测。
        return normalized
    if base is None or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        return None
    if windows(base):
        return native_absolute(ntpath.join(base, path)) if ":" not in path else None
    if "\\" in path or re.match(r"\w+:", path):
        return None
    return native_absolute(posixpath.join(base, path))


def within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")
