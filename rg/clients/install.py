"""将已生成的技能与 MCP 安装到明确登记的项目，保留用户配置。"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

from rg.clients.package import files
from rg.hooks.install import MAX_CONFIG_BYTES, _pairs, _read
from rg.store.database import Store
from rg.store.locking import exclusive
from rg.store.objects import atomic_write, digest
from rg.store.paths import reject_links


def workspace(store: Store, project: str, root: Path) -> Path:
    target = root.expanduser().absolute()
    if not target.is_dir() or target.is_symlink():
        raise ValueError("接入目录必须为已登记的实际工作区")
    target = target.resolve()
    rows = store.db.execute(
        "SELECT project_id,path FROM effective_source_roots "
        "WHERE host_id='local' AND kind IN ('repo','worktree','alias')"
    ).fetchall()
    matches = [row for row in rows if Path(row["path"]).resolve() == target]
    if len(matches) != 1 or matches[0]["project_id"] != project:
        raise ValueError("接入目录必须是该项目唯一登记的本机工作区根")
    owners = {
        row["project_id"] for row in rows if target.is_relative_to(Path(row["path"]).resolve())
    }
    if owners != {project}:
        raise ValueError("接入工作区归属有歧义")
    return target


def merge_mcp(client: str, original: bytes | None, generated: bytes) -> bytes:
    if client == "claude":
        config = {} if original is None else json.loads(original, object_pairs_hook=_pairs)
        if not isinstance(config, dict):
            raise ValueError("MCP 配置根必须为 JSON 对象")
        merged = copy.deepcopy(config)
        servers = merged.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("已有 mcpServers 不是对象，未修改")
        owned = json.loads(generated)["mcpServers"]["researchgraph"]
        if "researchgraph" in servers:
            if servers["researchgraph"] != owned:
                raise ValueError("已有 researchgraph MCP 指向其它配置，未修改")
            return original or generated
        servers["researchgraph"] = owned
        return (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode()
    config = {} if original is None else tomllib.loads(original.decode("utf-8"))
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("已有 mcp_servers 不是表，未修改")
    owned = tomllib.loads(generated.decode("utf-8"))["mcp_servers"]["researchgraph"]
    if "researchgraph" in servers:
        if servers["researchgraph"] != owned:
            raise ValueError("已有 researchgraph MCP 指向其它配置，未修改")
        return original or generated
    result = (original or b"") + b"\n" + generated
    # TOML 可能在文件末尾处于其它表；重新解析并核对完整原配置。
    parsed = tomllib.loads(result.decode("utf-8"))
    parsed["mcp_servers"].pop("researchgraph")
    if "mcp_servers" not in config and not parsed["mcp_servers"]:
        parsed.pop("mcp_servers")
    if parsed != config:
        raise ValueError("无法无损合并 TOML，未修改")
    return result


def install(
    store: Store,
    project: str,
    root: Path,
    client: str,
    encoding: str = "cl100k_base",
    *,
    check: bool = False,
) -> dict[str, Any]:
    if client not in {"codex", "claude", "both"}:
        raise ValueError("不支持的客户端")
    target = workspace(store, project, root)
    selected = ["codex", "claude"] if client == "both" else [client]
    generated = files(store, project, encoding)
    lock = store.root / "locks/client-install.lock"
    reject_links(store.root, lock)
    with exclusive(lock, "客户端接入安装正在进行"):
        planned: dict[Path, tuple[bytes | None, bytes]] = {}
        for name in selected:
            prefix = name + "/"
            mcp = ".codex/config.toml" if name == "codex" else ".mcp.json"
            for path, content in generated.items():
                if not path.startswith(prefix):
                    continue
                relative = path[len(prefix) :]
                destination = target / relative
                original = _read(target, destination)
                if relative == mcp:
                    content = merge_mcp(name, original, content)
                elif original is not None and original != content:
                    raise ValueError(f"已有同名技能不同，保留原文件：{relative}")
                if len(content) > MAX_CONFIG_BYTES:
                    raise ValueError("客户端文件超过读取上限，未修改")
                planned[destination] = original, content
        changed = {path: pair for path, pair in planned.items() if pair[0] != pair[1]}
        if not check:
            # 两种客户端全部预检后才发布；不复制可能含凭据的用户配置副本。
            for path, (original, _) in planned.items():
                if _read(target, path) != original:
                    raise ValueError("客户端文件在安装期间变化，未发布，请重试")
            for path, (original, content) in changed.items():
                if _read(target, path) != original:
                    raise ValueError("客户端文件在发布期间变化；已发布文件保留，可重跑接续")
                atomic_write(path, content)
        return {
            "project_id": project,
            "root": str(target),
            "clients": selected,
            "check_only": check,
            "configured": not check or not changed,
            "changed": bool(changed) and not check,
            "files_to_write": [str(path.relative_to(target)) for path in changed],
            "files": [
                {
                    "path": str(path.relative_to(target)),
                    "sha256": digest(content),
                    "changed": original != content,
                }
                for path, (original, content) in planned.items()
            ],
            "activation": "unknown",
            "hooks_installed": False,
            "personal_settings_modified": False,
            "next_step": "在客户端重开该项目；首次 MCP 许可及工作区信任由客户端处理",
        }
