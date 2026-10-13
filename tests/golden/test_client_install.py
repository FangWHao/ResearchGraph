from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from rg.clients.install import install


def project(store, tmp_path: Path):
    root = tmp_path / "项目 with space '中文"
    root.mkdir()
    return store.project("公开项目", [root]), root


def test_both_clients_preserve_foreign_configuration_and_idempotence(store, tmp_path):
    identity, root = project(store, tmp_path)
    config = root / ".codex/config.toml"
    config.parent.mkdir()
    original = b'# keep comment\nmodel="public"\n[mcp_servers.other]\ncommand="foreign"\n'
    config.write_bytes(original)
    claude = root / ".mcp.json"
    foreign = {"unknown": ["保留"], "mcpServers": {"other": {"command": "foreign"}}}
    claude.write_text(json.dumps(foreign), encoding="utf-8")
    result = install(store, identity, root, "both")
    assert result["changed"] and result["configured"]
    assert result["activation"] == "unknown" and not result["hooks_installed"]
    assert config.read_bytes().startswith(original)
    assert tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["other"] == {
        "command": "foreign"
    }
    merged = json.loads(claude.read_bytes())
    assert merged["unknown"] == foreign["unknown"]
    assert merged["mcpServers"]["other"] == foreign["mcpServers"]["other"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not install(store, identity, root, "both")["changed"]
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not (root / ".codex/hooks.json").exists()
    assert not (root / ".claude/settings.local.json").exists()


def test_preview_writes_no_workspace_files(store, tmp_path):
    identity, root = project(store, tmp_path)
    result = install(store, identity, root, "both", check=True)
    assert not result["changed"] and not result["configured"]
    assert result["files_to_write"] and list(root.iterdir()) == []


@pytest.mark.parametrize(
    "path,body",
    [
        (".mcp.json", b'{"mcpServers":{"researchgraph":{"command":"foreign"}}}'),
        (".mcp.json", b'{"mcpServers":{},"mcpServers":{}}'),
        (".mcp.json", b'{"mcpServers":null}'),
        (".mcp.json", b"{"),
        (".codex/config.toml", b'[mcp_servers.researchgraph]\ncommand="foreign"'),
        (".codex/config.toml", b"mcp_servers = 1"),
        (".claude/skills/research-note/SKILL.md", b"foreign skill"),
        (".agents/skills/research-context/scripts/invoke.py", b"foreign script"),
    ],
)
def test_conflicts_preflight_both_clients_without_overwriting(store, tmp_path, path, body):
    identity, root = project(store, tmp_path)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        install(store, identity, root, "both")
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_reject_cross_project_and_oversize_config(store, tmp_path):
    identity, root = project(store, tmp_path)
    other = store.project("其它公开项目", [tmp_path])
    with pytest.raises(ValueError, match="歧义"):
        install(store, identity, root, "both")
    with pytest.raises(ValueError, match="唯一登记"):
        install(store, other, root, "both")


def test_oversize_and_link_preserve_external_file(store, tmp_path):
    identity, root = project(store, tmp_path)
    path = root / ".mcp.json"
    raw = b" " * (1024 * 1024 + 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="有界"):
        install(store, identity, root, "both")
    assert path.read_bytes() == raw
    path.unlink()
    outside = tmp_path / "external.json"
    outside.write_bytes(b"{}")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("系统没有建立符号链接的权限")
    with pytest.raises(ValueError, match="链接"):
        install(store, identity, root, "both")
    assert outside.read_bytes() == b"{}"
