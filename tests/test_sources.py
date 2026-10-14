from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from rg.sources import (
    default_dirs,
    normalize_cwd,
    read_info,
    scan,
    session_files,
    under,
    workspaces,
)
from tests.conftest import FIXTURES


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    claude = home / ".claude" / "projects" / "-workspace-demo"
    codex = home / ".codex" / "sessions" / "2026" / "10" / "02"
    (claude / "abc" / "subagents").mkdir(parents=True)
    codex.mkdir(parents=True)
    shutil.copy(FIXTURES / "claude" / "research-synthetic.jsonl", claude / "abc.jsonl")
    shutil.copy(
        FIXTURES / "claude" / "research-synthetic.jsonl", claude / "abc" / "subagents" / "a.jsonl"
    )
    shutil.copy(FIXTURES / "codex" / "research-synthetic.jsonl", codex / "rollout-1.jsonl")
    shutil.copy(FIXTURES / "codex" / "subagent-synthetic.jsonl", codex / "rollout-2.jsonl")
    (codex / "notes.jsonl").write_text('{"hello": 1}\n' * 30)
    return home


def test_default_dirs_finds_both_clients(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    monkeypatch.setattr("rg.sources._homes", lambda: [home])
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    found = {d["tool"]: d for d in default_dirs()}
    assert found["claude"]["path"] == str(home / ".claude" / "projects")
    assert found["claude"]["files"] == 1  # subagents 目录不算
    assert found["codex"]["files"] == 3


def test_scan_skips_subagents_and_non_sessions_and_caches(tmp_path, store):
    home = make_home(tmp_path)
    roots = [str(home / ".claude" / "projects"), str(home / ".codex" / "sessions")]
    infos = scan(store, roots)
    assert sorted(i.tool for i in infos) == ["claude", "codex"]
    assert store.one("SELECT COUNT(*) AS n FROM catalog")["n"] == 4
    claude = next(i for i in infos if i.tool == "claude")
    assert claude.title == "批次与注释"
    # 第二次扫描走缓存，结果一致。
    assert [i.path for i in scan(store, roots)] == [i.path for i in infos]


def test_session_files_skips_subagent_dirs(tmp_path):
    home = make_home(tmp_path)
    names = [p.name for p in session_files(home)]
    assert "abc.jsonl" in names and "a.jsonl" not in names


def test_read_info_reads_title_from_tail_of_large_file(tmp_path):
    path = tmp_path / "big.jsonl"
    filler = json.dumps({"type": "progress", "sessionId": "s", "data": "x" * 1000})
    lines = [
        json.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/w",
                "timestamp": "2026-10-01T00:00:00Z",
                "message": {"content": "开头的问题"},
            }
        )
    ]
    lines += [filler] * 600
    lines.append(json.dumps({"type": "ai-title", "aiTitle": "尾部标题", "sessionId": "s"}))
    lines.append(
        json.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-10-05T00:00:00Z",
                "message": {"content": "最后"},
            }
        )
    )
    path.write_text("\n".join(lines) + "\n")
    info = read_info(path)
    assert info.title == "尾部标题"
    assert info.started_at == "2026-10-01T00:00:00Z"
    assert info.ended_at == "2026-10-05T00:00:00Z"


def test_workspace_paths_and_grouping():
    if os.name != "nt":
        assert normalize_cwd("C:\\Users\\demo\\proj") == "/mnt/c/Users/demo/proj"
    assert normalize_cwd("/workspace/demo/") == "/workspace/demo"
    assert under("/workspace/demo/analysis", "/workspace/demo")
    assert under("/workspace/demo", "/workspace/demo/")
    assert not under("/workspace/demo2", "/workspace/demo")
    assert not under(None, "/workspace/demo")


def test_workspaces_sorted_by_recent(tmp_path, store):
    home = make_home(tmp_path)
    infos = scan(store, [str(home)])
    groups = workspaces(infos)
    assert [g["cwd"] for g in groups] == ["/workspace/demo/analysis", "/workspace/demo"]
    assert groups[1]["claude"] == 1
