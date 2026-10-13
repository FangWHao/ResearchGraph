"""管理目录中的链接检查；Windows 重解析点也不能作为清除归属。"""

from __future__ import annotations

import stat
from pathlib import Path


def is_link(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def reject_links(root: Path, path: Path) -> None:
    """允许数据目录本身的既有别名，但拒绝其管理子路径的任何链接祖先。"""
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if is_link(current):
            raise ValueError("管理路径含符号链接或重解析点，停止并保留原件及恢复记录")
