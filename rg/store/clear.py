"""独占整项目清除：先预览、持久恢复记录、提交删除，再清理副本。"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from rg.store.clear_files import LIMIT, Files, read_json
from rg.store.clear_selection import Selection, identifier
from rg.store.database import ConflictError, dumps, now
from rg.store.lease import lease
from rg.store.migrations import LATEST_VERSION
from rg.store.objects import atomic_write, digest

ACTIVE = ".clear-active.json"
BOUNDARY = "清除本数据目录管理的项目资料；外部日志、工作区及用户保存或分享的副本保留"


def canonical(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("清除标识必须为 UUID")
    try:
        result = str(uuid.UUID(value))
    except ValueError:
        raise ValueError("清除标识必须为 UUID") from None
    if result != value:
        raise ValueError("清除标识必须为规范 UUID")
    return result


def connect(root: Path, *, readonly: bool) -> sqlite3.Connection:
    path = root / "rg.db"
    if path.is_symlink() or not path.is_file():
        raise ValueError("清除要求已有普通数据库文件")
    db = sqlite3.connect(
        path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        isolation_level=None,
        timeout=1,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA temp_store=MEMORY")
    if not readonly:
        db.execute("PRAGMA synchronous=FULL")
    if db.execute("PRAGMA user_version").fetchone()[0] != LATEST_VERSION:
        db.close()
        raise ValueError("请先升级数据库，再预览清除")
    return db


def plan(root: Path, db: sqlite3.Connection, project: str) -> tuple[dict, Selection]:
    if not db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
        raise ValueError("项目不存在")
    selected = Selection(db, project)
    selected.expand()
    files = Files(root, db, selected)
    files.scan()
    selected.expand()
    removed, shared = selected.objects()
    files.object_files(removed, shared)
    blockers = sorted(set(selected.blockers() + files.blockers))
    revision = db.execute("SELECT revision FROM graph_clock WHERE id=1").fetchone()[0]
    summary = {
        "project_id": project,
        "revision": revision,
        "rows": selected.counts(),
        "objects": len(removed),
        "shared_objects_retained": len(shared),
        "managed_paths": len(files.remove),
        "blockers": blockers,
        "boundary": BOUNDARY,
    }
    hasher = hashlib.sha256(dumps(summary).encode())
    # 状态更新不一定增加图修订，因此逐行摘要目标数据；正文只进入本地摘要器。
    for table in sorted(selected.counts()):
        hasher.update(table.encode())
        for row in db.execute(
            f"SELECT c.* FROM {identifier(table)} c WHERE "
            + selected.selected(table)
            + " ORDER BY c.rowid"
        ):
            values = ["bytes:" + digest(v) if isinstance(v, bytes) else v for v in row]
            hasher.update(dumps(values).encode())
    hasher.update(
        dumps([files.signatures, sorted(removed), sorted(shared), files.denials]).encode()
    )
    summary["preview_sha256"] = hasher.hexdigest()
    return summary | {"remove": sorted(set(files.remove)), "denials": files.denials}, selected


def public(value: dict) -> dict:
    return {k: v for k, v in value.items() if k not in {"remove", "denials"}}


def preview(root: Path, project: str) -> dict:
    project = canonical(project)
    with lease(root), closing(connect(root, readonly=True)) as db:
        db.execute("BEGIN")
        try:
            result, _ = plan(root, db, project)
            return public(result)
        finally:
            db.rollback()


def status(root: Path, request_id: str | None = None) -> dict:
    if (root / "clear-records").is_symlink():
        raise ValueError("清除记录目录不能为符号链接")
    if request_id is not None:
        request_id = canonical(request_id)
    # 恢复入口不能打开普通 Store；部分删除期间它必须保持拒绝。
    path = root / ACTIVE
    if path.exists() or path.is_symlink():
        value = read_json(path)
        if request_id is not None and value["request_id"] != request_id:
            raise ConflictError("另一个清除请求等待恢复")
        return public(value) | {"state": "pending"}
    if request_id is not None:
        path = root / "clear-records" / (request_id + ".json")
        if path.exists():
            return public(read_json(path))
    return {"state": "idle", "boundary": BOUNDARY}


def _persist(root: Path, value: dict) -> None:
    raw = dumps(value).encode()
    if len(raw) > LIMIT - 4096:
        raise ValueError("清除清单超过当前恢复记录上限，未执行删除")
    if (root / "clear-records").is_symlink():
        raise ValueError("清除记录目录不能为符号链接")
    atomic_write(root / ACTIVE, raw)


def _delete(db: sqlite3.Connection, selected: Selection) -> None:
    db.execute("PRAGMA secure_delete=ON")
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("PRAGMA defer_foreign_keys=ON")
        # 只在隐私事务中暂停不可变保护；回滚及成功路径都恢复原定义。
        triggers = list(
            db.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='trigger' "
                "AND sql LIKE '%RAISE(ABORT%'"
            )
        )
        for name, _ in triggers:
            db.execute("DROP TRIGGER " + identifier(name))
        for table in sorted(selected.counts()):
            db.execute(
                f"DELETE FROM {identifier(table)} WHERE rowid IN "
                "(SELECT n FROM temp.clear_rows WHERE t=?)",
                (table,),
            )
        for _, sql in triggers:
            db.execute(sql)
        for index in ("slim_fts", "event_fts"):
            db.execute(f'INSERT INTO "{index}"("{index}") VALUES (?)', ("rebuild",))
        db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("清除后的外键检查未通过")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _managed_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not re.fullmatch(
        r"(?:objects/[a-f0-9]{2}/[a-f0-9]{64}\.zst|"
        r"(?:spool|snapshots/pending)/[0-9]+-[0-9]+-[a-f0-9]{32}-(?:claude|codex)\.json|"
        r"snapshots/[a-f0-9-]{36}\.git|overviews/[a-f0-9]{64})",
        relative,
    ):
        raise ValueError("清除恢复记录含未授权管理路径")
    path = root / relative
    for current in (path, *path.parents):
        if current == root:
            break
        if current.is_symlink():
            raise ValueError("清除路径含符号链接，停止并保留恢复记录")
    return path


def _finish(root: Path, value: dict, db: sqlite3.Connection) -> dict:
    # VACUUM 去掉旧页，FTS 已从保留内容重建；WAL 必须实际截断后才报告成功。
    db.execute("PRAGMA secure_delete=ON")
    db.execute("VACUUM")
    checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint[0] or checkpoint[1] != checkpoint[2]:
        raise ConflictError("数据库仍被外部连接占用，清除等待恢复")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("清除后数据库完整性检查未通过")
    for relative in value["remove"]:
        path = _managed_path(root, relative)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        if path.parent.exists():
            _sync(path.parent)
    registry = root / "snapshots" / "roots.json"
    if registry.exists() or registry.is_symlink():
        entries = read_json(registry, 1024 * 1024)
        atomic_write(
            registry, dumps([e for e in entries if e["project_id"] != value["project_id"]]).encode()
        )
    complete = {k: v for k, v in value.items() if k != "remove"}
    complete.update(state="complete", finished_at=now())
    atomic_write(root / "clear-records" / (value["request_id"] + ".json"), dumps(complete).encode())
    (root / ACTIVE).unlink()
    _sync(root)
    return public(complete)


def _sync(path: Path) -> None:
    if os.name == "nt":
        return  # 原生 Windows 的持久性验收仍待完成。
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute(root: Path, project: str, request_id: str, proof: str) -> dict:
    project, request_id = canonical(project), canonical(request_id)
    if not isinstance(proof, str) or not re.fullmatch(r"[a-f0-9]{64}", proof):
        raise ValueError("执行清除必须携带当前预览摘要")
    with lease(root, exclusive=True), closing(connect(root, readonly=False)) as db:
        if (root / ACTIVE).exists() or (root / ACTIVE).is_symlink():
            raise ConflictError("已有清除等待恢复，请使用 resume")
        previous = root / "clear-records" / (request_id + ".json")
        if previous.exists():
            value = read_json(previous)
            if value["project_id"] != project or value["preview_sha256"] != proof:
                raise ConflictError("清除请求标识已用于不同输入")
            return public(value)
        db.execute("BEGIN IMMEDIATE")
        value, selected = plan(root, db, project)
        if value["preview_sha256"] != proof:
            raise ConflictError("项目或管理副本已变化，请重新预览清除")
        if value["blockers"]:
            raise ConflictError("清除存在归属阻碍，请先处理后重新预览")
        value.update(request_id=request_id, state="pending", started_at=now())
        _persist(root, value)
        _delete(db, selected)
        return _finish(root, value, db)


def resume(root: Path, request_id: str) -> dict:
    request_id = canonical(request_id)
    with lease(root, exclusive=True), closing(connect(root, readonly=False)) as db:
        previous = root / "clear-records" / (request_id + ".json")
        if previous.exists() and not (root / ACTIVE).exists():
            return public(read_json(previous))
        value = read_json(root / ACTIVE)
        if value["request_id"] != request_id:
            raise ConflictError("恢复请求与等待清除的请求不符")
        if db.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (value["project_id"],)
        ).fetchone():
            db.execute("BEGIN IMMEDIATE")
            current, selected = plan(root, db, value["project_id"])
            if current["preview_sha256"] != value["preview_sha256"] or current["blockers"]:
                raise ConflictError("等待恢复的资料已变化，保留阻止访问状态")
            _delete(db, selected)
        return _finish(root, value, db)
