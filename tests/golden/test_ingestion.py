from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rg.ingest.scanner import mark_deleted, scan_file
from rg.slim.slimmer import slim_session
from rg.store.backup import backup
from rg.store.database import Store
from tests.conftest import FakeProvider

FIXTURES = Path(__file__).parents[2] / "fixtures/transcripts"


def lines(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records))


def record(text="采用甲", uuid="one", **extra):
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": "demo",
        "message": {"content": text},
        **extra,
    }


@pytest.mark.parametrize("tool,name", [("claude", "2.1-synthetic"), ("codex", "2026-synthetic")])
def test_reimport_no_new_events(store: Store, tool: str, name: str):
    path = FIXTURES / tool / (name + ".jsonl")
    first = scan_file(store, path, tool)
    assert first["events"] > 0
    before = store.health()["events"]
    assert scan_file(store, path, tool) == {}
    assert store.health()["events"] == before


def test_multiblock_unknown_and_compaction(store: Store):
    scan_file(store, FIXTURES / "claude/2.1-synthetic.jsonl", "claude")
    assert store.health()["unknown"] == 1
    assert (
        store.db.execute("SELECT count(*) FROM raw_events WHERE kind='tool_call'").fetchone()[0]
        == 1
    )
    assert (
        store.db.execute("SELECT count(*) FROM raw_events WHERE kind='compact_summary'").fetchone()[
            0
        ]
        == 1
    )
    slim = slim_session(store, 1, FakeProvider())
    assert all("旧摘要声称" not in x["text"] for x in slim)


def test_mirror_replay_injection_and_tools(store: Store):
    scan_file(store, FIXTURES / "codex/2026-synthetic.jsonl", "codex")
    assert store.db.execute("SELECT count(*) FROM dedupe_links").fetchone()[0] == 1
    assert (
        store.db.execute(
            "SELECT count(*) FROM raw_events WHERE kind='compact_boundary'"
        ).fetchone()[0]
        == 1
    )
    slim = slim_session(store, 1, FakeProvider())
    text = "\n".join(x["text"] for x in slim)
    assert text.count("采用方法甲。") == 1
    assert "当前采用方法乙" not in text
    assert "ResearchGraph 返回" not in text
    assert "先暂缓方法甲" in text
    assert len(store.search("采用方法甲")) == 1


def test_bad_line_and_tail_recovery(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    good = json.dumps(record(), ensure_ascii=False).encode() + b"\n"
    path.write_bytes(b"broken\n" + good[:-1])
    first = scan_file(store, path, "claude")
    assert first["tail_fragments"] == 1
    assert store.health()["bad_lines"] == 1
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert scan_file(store, path, "claude")["user_msg"] == 1
    assert store.health()["events"] == 2
    assert scan_file(store, path, "claude") == {}


def test_short_file_append_does_not_rotate(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record()])
    scan_file(store, path, "claude")
    with path.open("a") as stream:
        stream.write(json.dumps(record("再次采用", "two")) + "\n")
    scan_file(store, path, "claude")
    assert store.db.execute("SELECT count(*) FROM source_files").fetchone()[0] == 1
    assert store.health()["events"] == 2


def test_truncation_preserves_history(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record("长正文" * 50), record("拒绝", "two")])
    scan_file(store, path, "claude")
    lines(path, [record("新事件", "three")])
    scan_file(store, path, "claude")
    assert store.db.execute("SELECT count(*) FROM source_files").fetchone()[0] == 2
    assert store.health()["events"] == 3
    assert store.raw(1)


def test_file_rotation_and_resume_alias(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record()])
    scan_file(store, path, "claude")
    path.rename(tmp_path / "old.jsonl")
    lines(path, [record(parentUuid="different")])
    assert scan_file(store, path, "claude")["aliases"] == 1
    assert store.health()["events"] == 2


def test_crash_before_cursor_rolls_back(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record()])

    def fail():
        raise RuntimeError("模拟进程在写游标前中断")

    with pytest.raises(RuntimeError):
        scan_file(store, path, "claude", fault=fail)
    assert store.health()["events"] == 0
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
    scan_file(store, path, "claude")
    assert store.health()["events"] == 1


def test_original_survives_source_cleanup_and_backup(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record()])
    scan_file(store, path, "claude")
    original = store.raw(1)
    path.unlink()
    assert mark_deleted(store) == 1
    assert store.raw(1) == original
    destination = tmp_path / "backup"
    assert backup(store, destination) == {"objects": 1, "events": 1}
    restored = Store(destination)
    assert restored.raw(1) == original
    restored.close()


def test_l0_is_append_only(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record()])
    scan_file(store, path, "claude")
    for sql in ["DELETE FROM raw_events", "UPDATE raw_events SET kind='unknown'"]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(sql)


def test_subagent_parent_arrives_late(store: Store, tmp_path: Path):
    child = tmp_path / "subagents/agent.jsonl"
    child.parent.mkdir()
    lines(child, [record(uuid="child", agentId="agent")])
    scan_file(store, child, "claude")
    parent = tmp_path / "parent.jsonl"
    lines(parent, [record(uuid="parent")])
    scan_file(store, parent, "claude")
    assert (
        store.db.execute(
            "SELECT parent_session_pk FROM sessions WHERE agent_id='agent'"
        ).fetchone()[0]
        == 2
    )


def test_model_unavailable_search_and_short_terms_work(store: Store, tmp_path: Path):
    path = tmp_path / "input.jsonl"
    lines(path, [record("暂缓 cnv，采用方法甲。")])
    scan_file(store, path, "claude")
    assert store.search("暂缓")
    assert store.search("cnv")
    assert store.search('" OR *') == []
    assert store.search("'; DROP TABLE raw_events; --") == []
