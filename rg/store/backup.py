from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from rg.store.database import Store, dumps
from rg.store.objects import atomic_write


def backup(store: Store, destination: Path) -> dict[str, int]:
    if destination.exists():
        raise ValueError("备份目录已存在，请使用新的目录")
    if destination.resolve().is_relative_to(store.root.resolve()):
        raise ValueError("备份目录不能在数据目录内")
    destination.mkdir(parents=True)
    target = sqlite3.connect(destination / "rg.db")
    try:
        store.db.backup(target)
        shas = [row[0] for row in target.execute("SELECT DISTINCT object_sha256 FROM raw_events")]
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
        for name in ["snapshots", "spool"]:
            if (store.root / name).exists():
                shutil.copytree(store.root / name, destination / name)
        atomic_write(destination / "manifest.json", dumps(result).encode())
        return result
    finally:
        target.close()
