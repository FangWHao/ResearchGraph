from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rg.cli.main import parser, run
from rg.hooks import install as installer
from rg.ingest.spool import MAX_BYTES
from rg.snapshot.config import export_registry
from rg.store.database import Store


def workspace(store: Store, tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "研究项目 with space '$(`)"
    root.mkdir()
    project_id = store.project("公开合成项目", [root])
    export_registry(store)
    return project_id, root


def target(root: Path, client: str) -> Path:
    return root / (".claude/settings.local.json" if client == "claude" else ".codex/hooks.json")


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_install_preserves_foreign_settings_and_repeat_bytes(store, tmp_path, client):
    project_id, root = workspace(store, tmp_path)
    path = target(root, client)
    path.parent.mkdir()
    foreign = {
        "env": {"SYNTHETIC_KEY": "public-fixture"},
        "cleanupPeriodDays": 3650,
        "hooks": {
            "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo old"}]}],
            "UnknownFutureEvent": [{"preserve": [1, 2, {"unknown": True}]}],
        },
    }
    raw = json.dumps(foreign).encode()
    path.write_bytes(raw)
    if client == "codex" and os.name == "nt":
        with pytest.raises(ValueError, match="尚未验收"):
            installer.install(store, project_id, root, client)
        assert path.read_bytes() == raw
        return
    first = installer.install(store, project_id, root, client)
    body = json.loads(path.read_bytes())
    assert first["changed"] and first["configured"] and first["events_to_add"] == 8
    assert first["activation"] == "unknown" and not first["personal_settings_modified"]
    assert body["env"] == foreign["env"] and body["cleanupPeriodDays"] == 3650
    assert body["hooks"]["Stop"][0] == foreign["hooks"]["Stop"][0]
    assert body["hooks"]["UnknownFutureEvent"] == foreign["hooks"]["UnknownFutureEvent"]
    installed = path.read_bytes()
    second = installer.install(store, project_id, root, client)
    assert second["configured"] and not second["changed"] and second["events_to_add"] == 0
    assert path.read_bytes() == installed
    assert second["before_sha256"] == first["after_sha256"] == second["after_sha256"]


def test_check_cli_creates_no_client_config_or_registry_write(store, tmp_path):
    project_id, root = workspace(store, tmp_path)
    before = (store.root / "snapshots/roots.json").read_bytes()
    args = parser().parse_args(
        [
            "hook-install",
            "--project",
            project_id,
            "--root",
            str(root),
            "--client",
            "claude",
            "--check",
        ]
    )
    result = run(args, store)
    assert result["check_only"] and not result["configured"] and not result["changed"]
    assert not (root / ".claude").exists()
    assert (store.root / "snapshots/roots.json").read_bytes() == before


@pytest.mark.parametrize(
    "body", [b"{", b"[]", b'{"hooks":null}', b'{"hooks":{"Stop":null}}', b'{"hooks":{},"hooks":{}}']
)
def test_malformed_existing_config_is_preserved(store, tmp_path, body):
    project_id, root = workspace(store, tmp_path)
    path = target(root, "claude")
    path.parent.mkdir()
    path.write_bytes(body)
    with pytest.raises(ValueError):
        installer.install(store, project_id, root, "claude")
    assert path.read_bytes() == body


@pytest.mark.parametrize("change", ["modified", "duplicated", "shared"])
def test_owned_definition_conflicts_do_not_overwrite_other_handlers(store, tmp_path, change):
    project_id, root = workspace(store, tmp_path)
    installer.install(store, project_id, root, "claude")
    path = target(root, "claude")
    body = json.loads(path.read_bytes())
    owned = body["hooks"]["Stop"][0]
    if change == "modified":
        owned["hooks"][0]["timeout"] = 90
    elif change == "duplicated":
        body["hooks"]["Stop"].append(owned)
    else:
        owned["hooks"].append({"type": "command", "command": "echo foreign"})
    raw = json.dumps(body).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="定义被修改或重复"):
        installer.install(store, project_id, root, "claude")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("case", ["unknown", "wrong_project", "subdirectory", "ambiguous"])
def test_unregistered_or_ambiguous_roots_are_not_installed(store, tmp_path, case):
    project_id, root = workspace(store, tmp_path)
    selected = root
    if case == "unknown":
        selected = tmp_path / "unknown"
        selected.mkdir()
    elif case == "wrong_project":
        project_id = "not-the-project"
    elif case == "subdirectory":
        selected = root / "nested"
        selected.mkdir()
    else:
        store.project("另一个归属", [tmp_path])
    with pytest.raises(ValueError):
        installer.install(store, project_id, selected, "claude")
    assert not target(selected, "claude").exists()


def test_development_workspace_is_excluded(store, tmp_path):
    project_id, root = workspace(store, tmp_path)
    (root / "docs").mkdir()
    (root / "docs/ResearchGraph_执行版_v2.md").write_text("公开合成标记", encoding="utf-8")
    (root / "rg/hooks").mkdir(parents=True)
    (root / "rg/hooks/entry.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="§14.8"):
        installer.install(store, project_id, root, "claude")
    assert not (root / ".claude").exists()


@pytest.mark.parametrize("position", ["file", "parent"])
def test_linked_client_config_is_not_followed(store, tmp_path, position):
    project_id, root = workspace(store, tmp_path)
    outside = tmp_path / "foreign"
    outside.mkdir()
    original = b'{"foreign":"preserve"}'
    (outside / "settings.local.json").write_bytes(original)
    path = target(root, "claude")
    try:
        if position == "parent":
            path.parent.symlink_to(outside, target_is_directory=True)
        else:
            path.parent.mkdir()
            path.symlink_to(outside / "settings.local.json")
    except OSError:
        pytest.skip("当前系统未授予建立符号链接的权限")
    with pytest.raises(ValueError, match="链接|重解析"):
        installer.install(store, project_id, root, "claude")
    assert (outside / "settings.local.json").read_bytes() == original


def test_concurrent_config_edit_is_preserved(store, tmp_path, monkeypatch):
    project_id, root = workspace(store, tmp_path)
    path = target(root, "claude")
    original_read = installer._read
    reads = 0
    concurrent = b'{"env":{"SYNTHETIC_NEW":"preserve"}}'

    def edit(work, selected):
        nonlocal reads
        reads += 1
        if reads == 2:
            selected.parent.mkdir(exist_ok=True)
            selected.write_bytes(concurrent)
        return original_read(work, selected)

    monkeypatch.setattr(installer, "_read", edit)
    with pytest.raises(ValueError, match="安装期间已变化"):
        installer.install(store, project_id, root, "claude")
    assert path.read_bytes() == concurrent


def test_config_publish_failure_preserves_original(store, tmp_path, monkeypatch):
    project_id, root = workspace(store, tmp_path)
    path = target(root, "claude")
    path.parent.mkdir()
    original = b'{"permission":"preserve"}'
    path.write_bytes(original)

    def fail(*args, **kwargs):
        raise OSError("public-fixture-failure")

    monkeypatch.setattr(installer, "atomic_write", fail)
    with pytest.raises(OSError):
        installer.install(store, project_id, root, "claude")
    assert path.read_bytes() == original


def invoke(store, root, project_id, raw, *extra):
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(installer.__file__).with_name("launcher.py")),
            "claude",
            "SessionStart",
            "--data-dir",
            str(store.root),
            "--scope-root",
            str(root),
            "--scope-project",
            project_id,
            *extra,
        ],
        input=raw,
        cwd=root,
        capture_output=True,
        timeout=15,
    )


def test_fixed_launcher_ignores_workspace_code_and_spools_exact_bytes(store, tmp_path):
    project_id, root = workspace(store, tmp_path)
    (root / "rg").mkdir()
    marker = root / "executed"
    (root / "rg/__init__.py").write_text(
        "from pathlib import Path; Path('executed').write_text('bad')", encoding="utf-8"
    )
    raw = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "cwd": str(root),
            "session_id": "public-native-fixture",
            "future": ["原件"],
        }
    ).encode()
    result = invoke(store, root, project_id, raw)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert not marker.exists()
    files = list((store.root / "spool").glob("*.json"))
    assert len(files) == 1 and files[0].read_bytes() == raw


@pytest.mark.parametrize("case", ["outside", "different_nested_project"])
def test_scope_does_not_collect_other_known_workspaces(store, tmp_path, case):
    project_id, root = workspace(store, tmp_path)
    other = tmp_path / "outside" if case == "outside" else root / "other"
    other.mkdir()
    store.project("另一个公开项目", [other])
    export_registry(store)
    raw = json.dumps({"hook_event_name": "SessionStart", "cwd": str(other)}).encode()
    result = invoke(store, root, project_id, raw)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert not list((store.root / "spool").glob("*.json"))


@pytest.mark.parametrize("raw", [b"invalid-json", b'{"future_record":true}'])
def test_unparseable_scope_input_is_preserved_without_inventing_attribution(store, tmp_path, raw):
    project_id, root = workspace(store, tmp_path)
    result = invoke(store, root, project_id, raw)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    files = list((store.root / "spool").glob("*.json"))
    assert len(files) == 1 and files[0].read_bytes() == raw


def test_oversized_scope_input_is_preserved_for_held_queue(store, tmp_path):
    project_id, root = workspace(store, tmp_path)
    raw = b"x" * (MAX_BYTES + 17)
    result = invoke(store, root, project_id, raw)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    files = list((store.root / "spool").glob("*.json"))
    assert len(files) == 1 and files[0].read_bytes() == raw


def test_launcher_import_failure_is_silent_and_nonblocking(tmp_path):
    launcher = Path(installer.__file__).with_name("launcher.py")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(launcher)], cwd=tmp_path, capture_output=True, timeout=15
    )
    assert result.returncode == 0 and result.stdout == result.stderr == b""
