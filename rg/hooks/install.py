"""仅安装已登记工作区的本地钩子，不修改个人配置或客户端信任。"""

from __future__ import annotations

import copy
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

from rg.snapshot.config import choose, export_registry
from rg.store.database import Store
from rg.store.locking import exclusive
from rg.store.objects import atomic_write, digest
from rg.store.paths import reject_links

EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)
MAX_CONFIG_BYTES = 1024 * 1024


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("客户端配置含重复 JSON 字段，未修改")
        result[key] = value
    return result


def _read(workspace: Path, path: Path) -> bytes | None:
    reject_links(workspace, path)
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
        raise ValueError("客户端配置不是有界普通文件，未修改")
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("客户端配置超过读取上限，未修改")
    return raw


def definitions(store: Store, client: str, workspace: Path, project_id: str) -> dict[str, Any]:
    # 保留 venv 解释器路径；resolve 会把其链接解析成未安装 rg 的系统 Python。
    python = str(Path(sys.executable).absolute())
    launcher = str(Path(__file__).with_name("launcher.py").resolve())
    marker = "ResearchGraph " + digest(str(workspace).encode())[:16]
    result = {}
    for event in EVENTS:
        arguments = [
            "-I",
            launcher,
            client,
            event,
            "--data-dir",
            str(store.root.resolve()),
            "--scope-root",
            str(workspace),
            "--scope-project",
            project_id,
        ]
        handler: dict[str, Any] = {
            "type": "command",
            "timeout": 2,
            "statusMessage": marker,
        }
        if client == "claude":
            handler.update(command=python, args=arguments)
        else:
            if os.name == "nt":
                raise ValueError("原生 Windows Codex 钩子命令尚未验收；请从 WSL 安装")
            handler["command"] = shlex.join([python, *arguments])
        if event not in {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}:
            handler["async"] = True
        group: dict[str, Any] = {"hooks": [handler]}
        if event == "PostToolUse":
            group["matcher"] = "^(Bash|exec_command)$"
        result[event] = group
    return result


def _merge(config: dict[str, Any], groups: dict[str, Any]) -> tuple[dict[str, Any], int]:
    merged = copy.deepcopy(config)
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("客户端 hooks 不是对象，未修改")
    added = 0
    for event, owned in groups.items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError("客户端钩子事件不是列表，未修改")
        marker = owned["hooks"][0]["statusMessage"]
        matches = [
            group
            for group in existing
            if isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and any(
                isinstance(h, dict) and h.get("statusMessage") == marker for h in group["hooks"]
            )
        ]
        if matches:
            if matches != [owned]:
                raise ValueError("已有 ResearchGraph 定义被修改或重复；保留原配置，请先核对")
            continue
        existing.append(owned)
        added += 1
    return merged, added


def install(
    store: Store,
    project_id: str,
    root: Path,
    client: str,
    *,
    check: bool = False,
) -> dict[str, Any]:
    if client not in {"codex", "claude"}:
        raise ValueError("不支持的客户端")
    workspace = root.expanduser().absolute()
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("工作区必须为已登记的实际目录")
    for ancestor in [workspace, *workspace.parents]:
        if (ancestor / "docs/ResearchGraph_执行版_v2.md").is_file() and (
            ancestor / "rg/hooks/entry.py"
        ).is_file():
            raise ValueError("M2 验收前不在 ResearchGraph 开发目录启用钩子（§14.8）")
    workspace = workspace.resolve()
    roots = store.db.execute(
        "SELECT project_id,path FROM effective_source_roots "
        "WHERE host_id='local' AND kind IN ('repo','worktree','alias')"
    ).fetchall()
    matches = [row for row in roots if Path(row["path"]).resolve() == workspace]
    if len(matches) != 1 or matches[0]["project_id"] != project_id:
        raise ValueError("安装目录必须是该项目唯一登记的本机工作区根")
    projects = {
        row["project_id"] for row in roots if workspace.is_relative_to(Path(row["path"]).resolve())
    }
    if projects != {project_id}:
        raise ValueError("工作区归属有歧义，未安装")
    relative = ".claude/settings.local.json" if client == "claude" else ".codex/hooks.json"
    target = workspace / relative
    lock = store.root / "locks/hook-install.lock"
    reject_links(store.root, lock)
    with exclusive(lock, "钩子安装正在进行，请稍后重试"):
        original = _read(workspace, target)
        config = {} if original is None else json.loads(original, object_pairs_hook=_pairs)
        if not isinstance(config, dict):
            raise ValueError("客户端配置根必须为 JSON 对象，未修改")
        groups = definitions(store, client, workspace, project_id)
        merged, added = _merge(config, groups)
        raw = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode()
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("合并配置超过上限，未修改")
        if not check:
            export_registry(store)
            selected = choose(store.root, str(workspace))
            if selected["project_id"] != project_id:
                raise ValueError("工作区归属有歧义，未安装")
            if _read(workspace, target) != original:
                raise ValueError("客户端配置在安装期间已变化，保留新配置，请重试")
            if added:
                atomic_write(target, raw)
        return {
            "client": client,
            "config_path": str(target),
            "check_only": check,
            "configured": not check or added == 0,
            "changed": bool(added) and not check,
            "events_to_add": added,
            "events": list(groups),
            "before_sha256": digest(original) if original is not None else None,
            "after_sha256": digest(raw) if added else digest(original or b""),
            "activation": "unknown",
            "personal_settings_modified": False,
            "next_step": "在客户端重开该项目；Codex 还需在 /hooks 信任定义"
            if client == "codex"
            else "在客户端重开该项目；检查工作区信任及 hooks 是否禁用",
        }
