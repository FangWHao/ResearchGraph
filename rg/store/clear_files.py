"""管理目录中的副本清单；外部日志和工作区只用于归属，不删除。"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import zstandard

from rg.store.clear_selection import OBJECTS, Selection
from rg.store.objects import ObjectStore, digest

LIMIT = 4 * 1024 * 1024


def read_json(path: Path, limit: int = LIMIT) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("管理目录中存在链接、异常文件或超大记录")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("管理记录超过读取上限")
    return json.loads(raw)


def path_hash(value: str) -> str:
    return digest(str(Path(value).expanduser().resolve()).encode())


def session_hash(tool: str, native: str) -> str:
    return digest((tool + "\0" + native).encode())


class Files:
    def __init__(self, root: Path, db: sqlite3.Connection, selection: Selection):
        self.root, self.db, self.selection = root, db, selection
        self.project = selection.project
        self.blockers: list[str] = []
        self.remove: list[str] = []
        self.signatures: list[tuple] = []
        self.roots = list(db.execute("SELECT path,project_id FROM source_roots"))
        self.sources: dict[str, set[str | None]] = {}
        for path, owner in db.execute(
            "SELECT f.path,s.project_id FROM source_files f JOIN sessions s USING(session_pk)"
        ):
            self.sources.setdefault(path, set()).add(owner)
        self.sessions: dict[tuple[str, str], set[str | None]] = {}
        for tool, native, owner in db.execute(
            "SELECT tool,native_session_id,project_id FROM sessions"
        ):
            self.sessions.setdefault((tool, native), set()).add(owner)
        if any(
            self.project in owners and len(owners) > 1
            for owners in [*self.sources.values(), *self.sessions.values()]
        ):
            self.blockers.append("来源命名空间同时属于多个项目或未分配会话")
        for path, owner in self.roots:
            if owner == self.project and any(
                other != owner
                and (
                    Path(path).resolve().is_relative_to(Path(p).resolve())
                    or Path(p).resolve().is_relative_to(Path(path).resolve())
                )
                for p, other in self.roots
            ):
                self.blockers.append("项目根目录与其他项目重叠，不能建立清除来源屏障")
        self.denials = {
            "projects": [digest(self.project.encode())],
            "sessions": sorted(
                {
                    session_hash(r[0], r[1])
                    for r in db.execute(
                        "SELECT tool,native_session_id FROM sessions WHERE project_id=?",
                        (self.project,),
                    )
                }
            ),
            "paths": sorted(
                {
                    path_hash(r[0])
                    for r in db.execute(
                        "SELECT f.path FROM source_files f JOIN sessions s USING(session_pk) "
                        "WHERE s.project_id=? UNION SELECT path FROM ingest_sources "
                        "WHERE project_id=?",
                        (self.project, self.project),
                    )
                }
            ),
            "prefixes": sorted({path_hash(r[0]) for r in self.roots if r[1] == self.project}),
        }

    def owner(self, payload: Any, tool: str | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        owners = set()
        if isinstance(payload.get("project_id"), str):
            owners.add(payload["project_id"])
        native = payload.get("session_id")
        if isinstance(native, str) and tool:
            owners.update(self.sessions.get((tool, native), set()))
        for key in ("transcript_path", "agent_transcript_path"):
            value = payload.get(key)
            if isinstance(value, str):
                owners.update(self.sources.get(str(Path(value).resolve()), set()))
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and Path(cwd).is_absolute():
            matches = {
                project
                for path, project in self.roots
                if Path(cwd).resolve().is_relative_to(Path(path).resolve())
            }
            owners.update(matches)
        owners.discard(None)
        return next(iter(owners)) if len(owners) == 1 else None

    def signature(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("管理目录中存在符号链接；未删除任何文件")
        stat = path.stat()
        self.signatures.append(
            (
                path.relative_to(self.root).as_posix(),
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
            )
        )

    def tree(self, path: Path, *, remove: bool) -> None:
        if not path.exists() and not path.is_symlink():
            return
        self.signature(path)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                self.tree(child, remove=False)
        if remove:
            self.remove.append(path.relative_to(self.root).as_posix())

    def scan(self) -> None:
        for directory in (self.root / "spool", self.root / "snapshots" / "pending"):
            if not directory.exists():
                continue
            self.signature(directory)
            for path in sorted(directory.iterdir()):
                self.signature(path)
                payload = read_json(path)
                match = re.search(r"-(claude|codex)\.json$", path.name)
                owner = self.owner(payload, match[1] if match else None)
                if owner is None:
                    self.blockers.append("存在无法确定归属的 spool 或待登记快照")
                elif owner == self.project:
                    if not re.fullmatch(
                        r"[0-9]+-[0-9]+-[a-f0-9]{32}-(claude|codex)\.json", path.name
                    ):
                        self.blockers.append("待清除提示文件名不符合管理合同")
                    else:
                        self.remove.append(path.relative_to(self.root).as_posix())
        # 已应答并移除 spool 文件的回执仍包含对象副本，也必须确定项目归属。
        objects = ObjectStore(self.root / "objects", readonly=True)
        for row in self.db.execute("SELECT rowid,* FROM spool_receipts"):
            try:
                path = objects.path(row["object_sha256"])
                if path.stat().st_size > LIMIT:
                    raise ValueError("回执对象过大")
                # 最大解压输出也有边界；不把正文放入清除记录。
                with (
                    path.open("rb") as source,
                    zstandard.ZstdDecompressor().stream_reader(source) as reader,
                ):
                    raw = reader.read(LIMIT + 1)
                if len(raw) > LIMIT or digest(raw) != row["object_sha256"]:
                    raise ValueError("回执对象无效")
                owner = self.owner(json.loads(raw), row["tool"])
            except (OSError, ValueError, UnicodeError, zstandard.ZstdError):
                owner = None
            if owner is None:
                self.blockers.append("存在无法确定归属的 spool 回执")
            elif owner == self.project:
                self.selection.add(
                    "spool_receipts",
                    "SELECT rowid FROM spool_receipts WHERE receipt_id=?",
                    (row["receipt_id"],),
                )
        registry = self.root / "snapshots" / "roots.json"
        if registry.exists() or registry.is_symlink():
            self.signature(registry)
            entries = read_json(registry, 1024 * 1024)
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                raise ValueError("快照根登记格式无效")
        self.tree(self.root / "snapshots" / (self.project + ".git"), remove=True)
        self.tree(self.root / "overviews" / digest(self.project.encode()), remove=True)

    def object_files(self, removed: set[str], shared: set[str]) -> None:
        known = set()
        for table, col in OBJECTS.items():
            known.update(
                r[0]
                for r in self.db.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL')
            )
        directory = self.root / "objects"
        if not directory.exists():
            if removed:
                self.blockers.append("对象库缺失，无法核对清除副本")
            return
        self.signature(directory)
        for prefix in sorted(directory.iterdir()):
            self.signature(prefix)
            if not prefix.is_dir() or not re.fullmatch(r"[a-f0-9]{2}", prefix.name):
                self.blockers.append("对象库存在无法确定归属的文件")
                continue
            for path in sorted(prefix.iterdir()):
                self.signature(path)
                sha = path.name.removesuffix(".zst")
                if (
                    not path.is_file()
                    or not re.fullmatch(r"[a-f0-9]{64}\.zst", path.name)
                    or sha[:2] != prefix.name
                    or sha not in known
                ):
                    self.blockers.append("对象库存在未登记或异常副本")
                elif sha in removed:
                    self.remove.append(path.relative_to(self.root).as_posix())
        for sha in removed | shared:
            if not (directory / sha[:2] / (sha + ".zst")).is_file():
                self.blockers.append("引用对象缺失，无法核对清除副本")
