"""清除记录的不可逆摘要索引；在保存原始记录之前核对。"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from itertools import chain
from pathlib import Path

from rg.store.clear_files import read_json
from rg.store.objects import digest
from rg.store.paths import reject_links

NAME = "record-denials.sqlite3"
VERSION = 1
APPLICATION = 0x52474431
SCHEMA = (
    "CREATE TABLE record_denials (record_key TEXT PRIMARY KEY "
    "CHECK(length(record_key)=64 AND record_key NOT GLOB '*[^a-f0-9]*')) WITHOUT ROWID"
)


def key(tool: str, sha: str) -> str:
    if tool not in {"claude", "codex"} or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("清除记录摘要格式不合法")
    return digest(("raw-record-v1\0" + tool + "\0" + sha).encode())


def _path(root: Path) -> Path:
    path = root / "clear-records" / NAME
    for suffix in ("", "-wal", "-shm", "-journal"):
        reject_links(root, path.with_name(NAME + suffix))
    if path.exists() and not path.is_file():
        raise ValueError("清除摘要索引必须为普通文件")
    return path


def _validate(db: sqlite3.Connection) -> None:
    if (
        db.execute("PRAGMA user_version").fetchone()[0] != VERSION
        or db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION
        or list(db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table'"))
        != [("record_denials", SCHEMA)]
        or db.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' LIMIT 1").fetchone()
    ):
        raise ValueError("清除摘要索引格式未知，停止采集并保留恢复记录")


def _connect(path: Path, mode: str) -> sqlite3.Connection:
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=1)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        return db
    except sqlite3.DatabaseError:
        if db is not None:
            db.close()
        raise ValueError("清除摘要索引无法打开，停止并保留恢复记录") from None


@contextmanager
def guard(root: Path) -> Iterator[Callable[[str, str], bool] | None]:
    """不存在时不创建文件；存在时一个来源扫描共用只读连接。"""
    path = _path(root)
    if not path.exists():
        for receipt in path.parent.glob("*.json"):
            barrier = read_json(receipt).get("denials", {}).get("record_barrier")
            if barrier is not None and barrier.get("count", 0) != 0:
                raise ValueError("清除摘要索引缺失，停止采集并保留恢复记录")
        yield None
        return
    with closing(_connect(path, "ro")) as db:
        try:
            db.execute("PRAGMA query_only=ON")
            _validate(db)
        except sqlite3.DatabaseError:
            raise ValueError("清除摘要索引不可读取，停止采集并保留恢复记录") from None

        def contains(tool: str, sha: str) -> bool:
            try:
                return db.execute(
                    "SELECT 1 FROM record_denials WHERE record_key=?", (key(tool, sha),)
                ).fetchone() is not None
            except sqlite3.DatabaseError:
                raise ValueError("清除摘要索引不可读取，停止采集并保留恢复记录") from None

        yield contains


def append(root: Path, keys: Iterable[str]) -> int:
    """在主库删除之前持久提交；重复恢复不会新增同一摘要。"""
    iterator = iter(keys)
    first = next(iterator, None)
    if first is None:
        return 0
    path = _path(root)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path if existed else path.with_name(NAME + "." + uuid.uuid4().hex + ".tmp")
    try:
        with closing(_connect(writing, "rw" if existed else "rwc")) as db:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                if not existed:
                    db.execute(SCHEMA)
                    db.execute(f"PRAGMA user_version={VERSION}")
                    db.execute(f"PRAGMA application_id={APPLICATION}")
                _validate(db)

                def rows() -> Iterator[tuple[str]]:
                    for value in chain((first,), iterator):
                        if not re.fullmatch(r"[a-f0-9]{64}", value):
                            raise ValueError("清除记录摘要格式不合法")
                        yield (value,)

                before = db.total_changes
                db.executemany("INSERT OR IGNORE INTO record_denials VALUES (?)", rows())
                added = db.total_changes - before
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("清除摘要索引完整性检查未通过")
        if not existed:
            os.replace(writing, path)
        return added
    except sqlite3.DatabaseError:
        raise ValueError("清除摘要索引不可写入，保留清除等待恢复状态") from None
    finally:
        if not existed:
            writing.unlink(missing_ok=True)


def backup(root: Path, destination: Path) -> int:
    """通过 SQLite backup API 保存屏障，不复制正在使用的数据库文件。"""
    source = _path(root)
    if not source.exists():
        with guard(root):
            pass
        return 0
    target = _path(destination)
    if target.exists():
        raise ValueError("目标清除摘要索引已存在")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_connect(source, "ro")) as db, closing(_connect(target, "rwc")) as out:
            _validate(db)
            db.backup(out)
            _validate(out)
            if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份清除摘要索引完整性检查失败")
            return out.execute("SELECT count(*) FROM record_denials").fetchone()[0]
    except sqlite3.DatabaseError:
        raise ValueError("备份清除摘要索引失败") from None
