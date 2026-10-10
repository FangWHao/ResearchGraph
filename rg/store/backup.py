from __future__ import annotations

import shutil
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path

from rg.snapshot.capture import identifier
from rg.snapshot.git import Git
from rg.store.database import Store, dumps
from rg.store.locking import exclusive
from rg.store.objects import atomic_write, digest


def backup(store: Store, destination: Path) -> dict[str, int]:
    if destination.exists():
        raise ValueError("备份目录已存在，请使用新的目录")
    if destination.resolve().is_relative_to(store.root.resolve()):
        raise ValueError("备份目录不能在数据目录内")
    with ExitStack() as locks:
        locks.enter_context(
            exclusive(
                store.root / "locks" / "postprocess.lock", "自动关联或概览正在运行，请稍后备份"
            )
        )
        for (project,) in store.db.execute("SELECT project_id FROM projects ORDER BY project_id"):
            locks.enter_context(
                exclusive(
                    store.root / "locks" / "overviews" / (digest(project.encode()) + ".lock"),
                    "概览正在写入，请稍后备份",
                )
            )
        # 自有影子仓库只追加对象；复制时也避开尚未改名的临时对象和引用。
        repositories = sorted((store.root / "snapshots").glob("*.git"))
        for repository in repositories:
            locks.enter_context(
                exclusive(repository.with_suffix(".lock"), "快照正在写入，请稍后备份")
            )
        return _backup(store, destination)


def _backup(store: Store, destination: Path) -> dict[str, int]:
    destination.mkdir(parents=True)
    target = sqlite3.connect(destination / "rg.db")
    try:
        store.db.backup(target)
        shas = [
            row[0]
            for row in target.execute(
                "SELECT object_sha256 FROM raw_events "
                "UNION SELECT object_sha256 FROM spool_receipts "
                "UNION SELECT record_sha256 FROM workspace_snapshots "
                "WHERE record_sha256 IS NOT NULL "
                "UNION SELECT content_sha256 FROM artifact_versions "
                "WHERE content_sha256 IS NOT NULL "
                "UNION SELECT patch_sha256 FROM edit_records WHERE patch_sha256 IS NOT NULL"
            )
        ]
        for sha in shas:
            store.objects.get(sha)
            source = store.objects.path(sha)
            dest = destination / "objects" / source.relative_to(store.objects.root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        result = {
            "objects": len(shas),
            "events": target.execute("SELECT count(*) FROM raw_events").fetchone()[0],
        }
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份数据库完整性检查失败")
        for name in ["snapshots", "spool", "overviews"]:
            if (store.root / name).exists():
                shutil.copytree(store.root / name, destination / name, symlinks=True)
        projects = target.execute(
            "SELECT DISTINCT project_id FROM workspace_snapshots WHERE shadow_commit IS NOT NULL"
        ).fetchall()
        for (project,) in projects:
            repository = destination.resolve() / "snapshots" / f"{identifier(project)}.git"
            commits = [
                row[0]
                for row in target.execute(
                    "SELECT DISTINCT shadow_commit FROM workspace_snapshots "
                    "WHERE project_id=? AND shadow_commit IS NOT NULL",
                    (project,),
                )
            ]
            if any(
                not isinstance(commit, str)
                or len(commit) != 40
                or any(character not in "0123456789abcdef" for character in commit)
                for commit in commits
            ):
                raise ValueError("备份包含未知影子提交格式")
            Git(destination.resolve(), time.monotonic() + 30, repository).run(
                "rev-list",
                "--objects",
                "--missing=error",
                "--stdin",
                data=("\n".join(commits) + "\n").encode(),
            )
        atomic_write(destination / "manifest.json", dumps(result).encode())
        return result
    finally:
        target.close()
