"""发现会话目录，列出会话元数据。元数据只读文件开头和结尾，按大小和修改时间缓存。"""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rg.parse import NotASession, parse_lines
from rg.store import Store

HEAD_LINES = 400
TAIL_BYTES = 256 * 1024
_DRIVE = re.compile(r"^([A-Za-z]):[\\/]")


@dataclass
class SessionInfo:
    path: str
    tool: str
    native_id: str | None
    cwd: str | None
    title: str | None
    started_at: str | None
    ended_at: str | None
    size: int
    mtime: float
    subagent: bool


def _homes() -> list[Path]:
    homes = [Path.home()]
    if "microsoft" in platform.release().lower() and Path("/mnt/c/Users").is_dir():
        skip = {"Public", "Default", "Default User", "All Users"}
        try:
            homes += [p for p in Path("/mnt/c/Users").iterdir() if p.name not in skip]
        except OSError:
            pass
    return homes


def default_dirs() -> list[dict[str, Any]]:
    """本机常见的 Claude Code / Codex 会话目录，只返回存在的。"""
    candidates: list[tuple[str, Path]] = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(("claude", Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"))
    if os.environ.get("CODEX_HOME"):
        candidates.append(("codex", Path(os.environ["CODEX_HOME"]) / "sessions"))
    for home in _homes():
        candidates.append(("claude", home / ".claude" / "projects"))
        candidates.append(("codex", home / ".codex" / "sessions"))
    found, seen = [], set()
    for tool, path in candidates:
        try:
            if not path.is_dir() or path.resolve() in seen:
                continue
        except OSError:
            continue
        seen.add(path.resolve())
        found.append({"path": str(path), "tool": tool, "files": len(session_files(path))})
    return found


def session_files(root: Path) -> list[Path]:
    """目录下的会话文件。Claude 的子 Agent 文件在 subagents 目录里，跳过。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "subagents"]
        files += [Path(dirpath) / f for f in filenames if f.endswith(".jsonl")]
    return sorted(files)


def normalize_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    match = _DRIVE.match(cwd)
    if match and os.name != "nt":
        rest = cwd[2:].replace("\\", "/").lstrip("/")
        cwd = f"/mnt/{match[1].lower()}/{rest}"
    return cwd.rstrip("/\\") or cwd


def under(cwd: str | None, workspace: str) -> bool:
    if not cwd:
        return False
    cwd, root = normalize_cwd(cwd) or "", normalize_cwd(workspace) or ""
    return cwd == root or cwd.startswith(root.rstrip("/") + "/")


def read_info(path: Path) -> SessionInfo:
    stat = path.stat()
    with path.open(encoding="utf-8", errors="replace") as handle:
        head = [line for _, line in zip(range(HEAD_LINES), handle, strict=False)]
    session = parse_lines(head)
    ended = session.ended_at
    title = session.custom_title or session.ai_title
    if stat.st_size > TAIL_BYTES:
        with path.open("rb") as handle:
            handle.seek(stat.st_size - TAIL_BYTES)
            tail = handle.read().decode("utf-8", errors="replace").splitlines()[1:]
        for line in tail:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            title = record.get("customTitle") or record.get("aiTitle") or title
            ts = record.get("timestamp")
            if isinstance(ts, str) and (ended is None or ts > ended):
                ended = ts
    return SessionInfo(
        path=str(path),
        tool=session.tool,
        native_id=session.native_id,
        cwd=normalize_cwd(session.cwd),
        title=title or session.title,
        started_at=session.started_at,
        ended_at=ended,
        size=stat.st_size,
        mtime=stat.st_mtime,
        subagent=session.subagent,
    )


def scan(store: Store, roots: list[str]) -> list[SessionInfo]:
    """列出来源目录下的主会话（不含子 Agent）。"""
    cached = {r["path"]: r for r in store.q("SELECT * FROM catalog")}
    infos: list[SessionInfo] = []
    fresh: list[tuple[str, int, float, str]] = []
    for root in roots:
        for path in session_files(Path(root).expanduser()):
            key = str(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            row = cached.get(key)
            if row and row["size"] == stat.st_size and row["mtime"] == stat.st_mtime:
                meta = json.loads(row["meta"])
                if meta is None:
                    continue
                info = SessionInfo(**meta)
            else:
                try:
                    info = read_info(path)
                except (NotASession, OSError, UnicodeError):
                    fresh.append((key, stat.st_size, stat.st_mtime, "null"))
                    continue
                fresh.append((key, info.size, info.mtime, json.dumps(asdict(info))))
            if not info.subagent:
                infos.append(info)
    if fresh:
        with store.tx() as db:
            db.executemany("INSERT OR REPLACE INTO catalog VALUES (?, ?, ?, ?)", fresh)
    infos.sort(key=lambda i: i.ended_at or "", reverse=True)
    return infos


def workspaces(infos: list[SessionInfo]) -> list[dict[str, Any]]:
    """按会话的工作目录分组，最近活跃的在前。"""
    groups: dict[str, dict[str, Any]] = {}
    for info in infos:
        if not info.cwd:
            continue
        group = groups.setdefault(
            info.cwd, {"cwd": info.cwd, "sessions": 0, "claude": 0, "codex": 0, "last": None}
        )
        group["sessions"] += 1
        group[info.tool] = group.get(info.tool, 0) + 1
        if info.ended_at and (group["last"] is None or info.ended_at > group["last"]):
            group["last"] = info.ended_at
    return sorted(groups.values(), key=lambda g: g["last"] or "", reverse=True)
