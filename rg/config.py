"""数据目录、模型设置与 API Key。"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".researchgraph"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"
DB_NAME = "timeline.db"
KEY_NAME = "api_key"


def data_dir(path: str | Path | None = None) -> Path:
    root = Path(path or os.environ.get("RG_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _clean_key(value: str) -> str | None:
    match = re.search(r"\bsk-[A-Za-z0-9_-]+", value)
    if match:
        return match[0]
    value = value.strip().strip("\"'")
    if not value or any(ch.isspace() for ch in value):
        return None
    return value


def read_key(root: Path) -> str | None:
    """环境变量 RG_API_KEY 优先，其次 RG_API_KEY_FILE，最后数据目录里的 api_key。"""
    if os.environ.get("RG_API_KEY"):
        return _clean_key(os.environ["RG_API_KEY"])
    path = Path(os.environ.get("RG_API_KEY_FILE") or root / KEY_NAME).expanduser()
    try:
        return _clean_key(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):
        return None


def save_key(root: Path, key: str) -> None:
    cleaned = _clean_key(key)
    if not cleaned:
        raise ValueError("API Key 格式不正确")
    path = root / KEY_NAME
    tmp = path.with_name(KEY_NAME + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(cleaned)
    os.replace(tmp, path)
