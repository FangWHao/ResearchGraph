from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from rg.snapshot.capture import identifier
from rg.store.database import Store, dumps
from rg.store.objects import atomic_write


def registry_path(data_root: Path) -> Path:
    return data_root / "snapshots" / "roots.json"


def load_registry(data_root: Path) -> list[dict[str, Any]]:
    path = registry_path(data_root)
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("快照根目录清单非法")
    data = json.loads(path.read_bytes())
    if not isinstance(data, list):
        raise ValueError("快照根目录清单格式非法")
    for root in data:
        if not isinstance(root, dict):
            raise ValueError("快照根目录记录非法")
        identifier(root["project_id"])
        identifier(root["root_id"])
        if not isinstance(root["path"], str) or not Path(root["path"]).is_absolute():
            raise ValueError("快照根目录必须为绝对路径")
        if root.get("snapshot_mode") not in {"sync", "async"}:
            raise ValueError("快照模式非法")
    return data


def export_registry(store: Store) -> list[dict[str, Any]]:
    modes = {value["root_id"]: value["snapshot_mode"] for value in load_registry(store.root)}
    values = [
        dict(row)
        for row in store.db.execute(
            "SELECT project_id,root_id,path FROM source_roots WHERE host_id='local' "
            "AND kind IN ('repo','worktree','alias') ORDER BY root_id"
        )
    ]
    for value in values:
        value["snapshot_mode"] = modes.get(value["root_id"], "sync")
    atomic_write(registry_path(store.root), dumps(values).encode())
    return values


def choose(data_root: Path, cwd: object) -> dict[str, Any]:
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("钩子 cwd 非绝对路径")
    path = Path(cwd).resolve()
    matches = [
        value
        for value in load_registry(data_root)
        if path.is_relative_to(Path(value["path"]).resolve())
    ]
    if len(matches) != 1:
        raise ValueError("钩子工作区未登记或归属有歧义")
    return matches[0]


def examples(store: Store, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("配置示例目录已存在，请指定新目录")
    values = export_registry(store)
    output.mkdir(parents=True)
    for tool in ["claude", "codex"]:
        events = {}
        for event in [
            "SessionStart",
            "UserPromptSubmit",
            "PostToolUse",
            "PreCompact",
            "PostCompact",
            "SubagentStop",
            "Stop",
            "SessionEnd",
        ]:
            command = shlex.join(["rg-hook", tool, event, "--data-dir", str(store.root.resolve())])
            handler: dict[str, Any] = {"type": "command", "command": command, "timeout": 2}
            if event not in {"SessionStart", "UserPromptSubmit"}:
                handler["async"] = True
            group: dict[str, Any] = {"hooks": [handler]}
            if event == "PostToolUse":
                group["matcher"] = "Bash|exec_command"
            events[event] = [group]
        atomic_write(output / f"{tool}.json", dumps({"hooks": events}).encode())
    return {"output": str(output.resolve()), "registered_roots": len(values), "installed": False}
