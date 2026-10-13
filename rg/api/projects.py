"""从本机界面登记项目；本地来源与模型外发分别授权。"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from rg.api.views import projects
from rg.ingest.sources import register
from rg.snapshot.config import export_registry
from rg.store.database import Store, now
from rg.store.roots import insert, prepare


def _existing(store: Store, path: str, name: str) -> str | None:
    target = Path(path)
    rows = store.db.execute(
        "SELECT r.path,r.project_id,p.name FROM effective_source_roots r "
        "JOIN active_projects p USING(project_id) WHERE r.host_id='local'"
    ).fetchall()
    exact = next((row for row in rows if Path(row["path"]) == target), None)
    if exact is not None and exact["name"] != name:
        raise ValueError("该工作区已登记到其它名称的项目，请选择已有项目")
    found = exact["project_id"] if exact is not None else None
    for row in rows:
        registered = Path(row["path"])
        if row["project_id"] != found and (
            target.is_relative_to(registered) or registered.is_relative_to(target)
        ):
            raise ValueError("工作区与已有项目根目录重叠，请使用现有项目的根目录管理")
    return found


def create(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    if set(body) != {"name", "root", "collect_local"}:
        raise ValueError("创建项目需名称、工作区目录和本地采集开关")
    name, root = body["name"], body["root"]
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 200
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("项目名称需为不超过200字的非空文字")
    if not isinstance(root, str) or not root or len(root.encode()) > 16384 or "\0" in root:
        raise ValueError("工作区目录无效")
    if type(body["collect_local"]) is not bool:
        raise ValueError("本地采集开关需为布尔值")
    name = name.strip()
    path = Path(root)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("请提供服务所在计算机上现存的绝对工作区目录")
    value = prepare(store, path)
    # Git 元数据探测在写事务外；唯一根与重叠检查在发布事务中再次核对。
    try:
        with store.transaction() as db:
            identity = _existing(store, value["path"], name)
            created = identity is None
            if identity is None:
                identity = str(uuid.uuid4())
                db.execute("INSERT INTO projects VALUES (?, ?, 0, ?)", (identity, name, now()))
                insert(db, identity, value)
                db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
    except sqlite3.IntegrityError as error:
        raise ValueError("工作区登记发生冲突，请重新读取项目列表") from error
    # 衍生登记清单可重新生成；来源登记失败不撤销已经明确创建的项目。
    export_registry(store)
    sources: list[dict[str, str]] = []
    if body["collect_local"]:
        home = Path.home()
        for tool, source in (
            ("claude", home / ".claude/projects"),
            ("codex", home / ".codex/sessions"),
        ):
            state = "missing"
            try:
                if source.is_dir():
                    # 共用日志不能绑定整个新项目；仍由目录线索和既有规则核对归属。
                    register(store, source, tool)
                    state = "registered"
            except (OSError, ValueError, sqlite3.Error):
                state = "unavailable"
            sources.append({"tool": tool, "path": str(source), "state": state})
    view = projects(store)
    return {
        "project": next(item for item in view["projects"] if item["project_id"] == identity),
        "created": created,
        "sources": sources,
        "revision": view["revision"],
    }
