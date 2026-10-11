"""清除共享影子仓库的未引用历史，只保留现存快照的确切提交与树。"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from rg.snapshot.git import Git
from rg.store.objects import atomic_write
from rg.store.paths import reject_links


def prune(root: Path, db: sqlite3.Connection, storage: str) -> None:
    repository = root.resolve() / "snapshots" / (storage + ".git")
    reject_links(root, repository)
    commits = sorted(
        {
            r[0]
            for r in db.execute(
                "SELECT shadow_commit FROM workspace_snapshots WHERE "
                "project_id=? AND shadow_commit IS NOT NULL",
                (storage,),
            )
        }
    )
    if not commits or any(
        not isinstance(c, str) or not re.fullmatch(r"[a-f0-9]{40}", c) for c in commits
    ):
        raise ValueError("共享影子提交不能完整核对，保留清除恢复状态")
    git = Git(root.resolve(), time.monotonic() + 60, repository)
    for commit in commits:
        git.run("cat-file", "-e", commit + "^{commit}")
    old = git.run("for-each-ref", "--format=%(refname)").decode().splitlines()
    for commit in commits:
        git.run("update-ref", "refs/rg-retained/" + commit, commit)
    git.run("update-ref", "refs/heads/retained", commits[0])
    git.run("symbolic-ref", "HEAD", "refs/heads/retained")
    for ref in old:
        if ref not in {"refs/heads/retained", *("refs/rg-retained/" + c for c in commits)}:
            git.run("update-ref", "-d", ref)
    # 提交字节不改；显式浅边界阻止未迁移父提交被保留提交的 parent 字段带回。
    atomic_write(repository / "shallow", ("\n".join(commits) + "\n").encode())
    for name in ("FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD"):
        (repository / name).unlink(missing_ok=True)
    git.run("reflog", "expire", "--expire=now", "--all")
    git.run("gc", "--prune=now")
    git.run("rev-list", "--objects", "--missing=error", "--all")
    unreachable = git.run("fsck", "--full", "--no-reflogs", "--unreachable")
    if b"unreachable " in unreachable or b"dangling " in unreachable:
        raise ValueError("影子仓库仍有未引用对象，保留清除恢复状态")
