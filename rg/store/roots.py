"""显式登记项目根目录；Git 身份仅供定位，不推定会话或文件归属。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from rg.snapshot.git import Deadline, Git
from rg.store.database import ConflictError, Store, dumps
from rg.store.objects import digest

KINDS = {"auto", "repo", "worktree", "data", "alias"}


def _path(store: Store, path: Path, project: str | None = None) -> Path:
    from rg.store.clear_denials import denied

    path = path.expanduser().resolve()
    if denied(store.root, project=project, paths=[str(path)]):
        raise PermissionError("该根目录已整项目清除，停止重新登记")
    if path.exists() and not path.is_dir():
        raise ValueError("项目根目录必须是目录")
    return path


def _git_identity(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {"status": "missing"}
    git = Git(path, time.monotonic() + 2)
    try:
        worktree = git.run("rev-parse", "--show-toplevel", optional=True)
        if not worktree:
            return {"status": "not_git"}
        values: dict[str, Any] = {"status": "registered"}
        for key, option in (
            ("worktree", "--show-toplevel"),
            ("git_dir", "--git-dir"),
            ("git_common_dir", "--git-common-dir"),
        ):
            raw = (
                worktree
                if key == "worktree"
                else git.run("rev-parse", "--path-format=absolute", option)
            )
            value = raw.decode("utf-8").removesuffix("\n")
            if len(raw) > 16384 or not Path(value).is_absolute():
                raise ValueError("Git 路径元数据非法")
            values[key] = str(Path(value).resolve())
        # 只读本地配置，不展开 include、不调用远程帮助程序，也不保留原始 URL。
        raw = git.run(
            "config",
            "--local",
            "--no-includes",
            "--null",
            "--list",
        )
        if len(raw) > 65536:
            raise ValueError("Git 远端元数据超限")
        remotes = []
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            key, separator, url = entry.partition(b"\n")
            name = key.decode("utf-8")
            if not re.fullmatch(r"remote\.[^\n\x00]+\.url", name):
                continue
            if not separator:
                raise ValueError("Git 远端元数据非法")
            remotes.append({"name": name[7:-4], "url_sha256": digest(url)})
        values["remotes"] = sorted(remotes, key=lambda item: (item["name"], item["url_sha256"]))
        values["remote_scope"] = "local_without_includes"
        return values
    except (Deadline, OSError, RuntimeError, ValueError, UnicodeError):
        # 不把仓库配置、凭据或 Git stderr 写进错误信息。
        return {"status": "unavailable"}


def prepare(store: Store, path: Path, kind: str = "auto") -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("根目录类型非法")
    path = _path(store, path)
    metadata = {"status": "not_probed"} if kind == "data" else _git_identity(path)
    linked = (
        metadata.get("status") == "registered" and metadata["git_dir"] != metadata["git_common_dir"]
    )
    if kind == "worktree" and not linked:
        raise ValueError("worktree 需有可读取的独立 Git 目录与共同目录")
    return {
        "root_id": str(uuid.uuid4()),
        "host_id": "local",
        "path": str(path),
        # 保留旧入口对非 Git 工作区的快照授权；数据根需显式选择 data。
        "kind": ("worktree" if linked else "repo") if kind == "auto" else kind,
        "git_common_dir": metadata.get("git_common_dir"),
        "git_metadata": dumps(metadata),
    }


def insert(db: sqlite3.Connection, project: str, value: dict[str, Any]) -> None:
    columns = ["root_id", "host_id", "path", "kind", "git_common_dir"]
    # 仅供旧版本迁移固定案例；正式 Store 在迁移完成后才允许写入。
    if "git_metadata" in {row[1] for row in db.execute("PRAGMA table_info(source_roots)")}:
        columns.append("git_metadata")
    db.execute(
        f"INSERT INTO source_roots(project_id,{','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in range(len(columns) + 1))})",
        (project, *(value[key] for key in columns)),
    )


def _view(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["git_metadata"] = json.loads(value["git_metadata"]) or {"status": "not_recorded"}
    return value


def register(store: Store, project: str, path: Path, kind: str = "auto") -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("根目录类型非法")
    if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
        raise ValueError("项目不存在")
    path = _path(store, path, project)

    def existing() -> dict[str, Any] | None:
        row = store.db.execute(
            "SELECT * FROM source_roots WHERE host_id='local' AND path=?", (str(path),)
        ).fetchone()
        if row is None:
            return None
        if row["project_id"] != project or (kind != "auto" and row["kind"] != kind):
            raise ValueError("根目录已有不同项目或类型，不能静默替换")
        return _view(row)

    found = existing()
    if found is not None:
        return {"root": found, "created": False, "revision": store.revision()}
    value = prepare(store, path, kind)
    with store.transaction() as db:
        found = existing()
        if found is not None:
            return {"root": found, "created": False, "revision": store.revision()}
        insert(db, project, value)
        db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
        return {
            "root": {
                "project_id": project,
                **value,
                "git_metadata": json.loads(value["git_metadata"]),
            },
            "created": True,
            "revision": store.revision(),
        }


def listing(
    store: Store,
    project: str,
    limit: int = 100,
    offset: int = 0,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("根目录分页参数非法")
    with store.snapshot():
        if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
            raise ValueError("项目不存在")
        revision = store.revision()
        if expected_revision is not None and expected_revision != revision:
            raise ConflictError("记录已变化，请重新读取根目录清单")
        total = store.db.execute(
            "SELECT count(*) FROM source_roots WHERE project_id=?", (project,)
        ).fetchone()[0]
        rows = store.db.execute(
            "SELECT * FROM source_roots WHERE project_id=? ORDER BY root_id LIMIT ? OFFSET ?",
            (project, limit, offset),
        ).fetchall()
        return {
            "project_id": project,
            "revision": revision,
            "roots": [_view(row) for row in rows],
            "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }
