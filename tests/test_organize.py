from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rg.model import AuthError, ModelError, Truncated
from rg.organize import (
    EVENT_SYSTEM,
    Progress,
    add_sessions,
    chunk_ranges,
    organize,
    validate_events,
)
from rg.sources import read_info
from rg.store import Store
from tests.conftest import FIXTURES, FakeModel

NEW = re.compile(r"^★ E(\d+) .*?｜(.*?)｜", re.M)
CLAUDE = FIXTURES / "claude" / "research-synthetic.jsonl"


def new_project(store: Store, paths: list[Path], allowed: bool = True) -> int:
    with store.tx() as db:
        pid = db.execute(
            "INSERT INTO projects(name, model_allowed, created_at) VALUES ('p', ?, 'now')",
            (1 if allowed else 0,),
        ).lastrowid
    add_sessions(store, pid, [read_info(p) for p in paths])
    return pid


def run(store: Store, model, pid: int, full: bool = False) -> Progress:
    progress = Progress()
    organize(store, model, pid, [], progress, full)
    return progress


class ByPrefix(dict):
    """按标题开头取值：ids["先试"]。"""

    def __getitem__(self, prefix: str):
        matches = [v for k, v in self.items() if k.startswith(prefix)]
        assert len(matches) == 1, (prefix, list(self))
        return matches[0]


def new_events(user: str) -> ByPrefix:
    """归类提示里的新事件：标题 → 编号。"""
    return ByPrefix({title: int(eid) for eid, title in NEW.findall(user)})


def batch_story(user: str) -> dict:
    ids = new_events(user)
    return {
        "threads": [
            {
                "id": None,
                "title": "去批次",
                "summary": "比较两种方法",
                "branches": [
                    {
                        "id": None,
                        "title": "主线",
                        "from": None,
                        "into": None,
                        "events": [f"E{ids['想比较']}", f"E{ids['先试']}", f"E{ids['比较后']}"],
                    },
                    {
                        "id": None,
                        "title": "scVI 对照",
                        "from": f"E{ids['先试']}",
                        "into": f"E{ids['比较后']}",
                        "events": [f"E{ids['也试试']}"],
                    },
                ],
            }
        ]
    }


def events(store: Store, pid: int) -> ByPrefix:
    rows = store.q("SELECT * FROM events WHERE project_id = ? AND hidden = 0", (pid,))
    return ByPrefix({r["title"]: r for r in rows})


def test_no_model_call_before_consent(store, copy_fixture):
    model = FakeModel()
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")], allowed=False)
    progress = run(store, model, pid)
    assert progress.phase == "consent"
    assert model.calls == []
    assert (
        progress.estimate and progress.estimate["sessions"] == 1 and progress.estimate["calls"] == 2
    )
    assert store.one("SELECT message_count FROM sessions")["message_count"] == 9


def test_full_pipeline_builds_threads_branches_and_merges(store, copy_fixture):
    model = FakeModel(batch_story)
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    progress = run(store, model, pid)
    assert progress.phase == "done", progress.errors
    rows = events(store, pid)
    assert len(rows) == 4
    branches = store.q("SELECT * FROM branches ORDER BY position")
    main, side = branches
    assert {rows[k]["branch_id"] for k in ("想比较", "先试", "比较后")} == {main["id"]}
    assert rows["也试试"]["branch_id"] == side["id"]
    assert side["from_event"] == rows["先试"]["id"]
    assert side["into_event"] == rows["比较后"]["id"]
    assert store.one("SELECT title FROM threads")["title"] == "去批次"
    # 事件时间来自引用的那条消息。
    assert rows["也试试"]["ts"] == "2026-10-01T01:10:00Z"


def test_placement_rejects_backwards_links_and_duplicates(store, copy_fixture):
    def bad(user: str) -> dict:
        ids = new_events(user)
        story = batch_story(user)
        side = story["threads"][0]["branches"][1]
        side["from"], side["into"] = f"E{ids['比较后']}", f"E{ids['想比较']}"  # 时间颠倒
        story["threads"][0]["branches"][1]["events"].append(f"E{ids['想比较']}")  # 重复
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(bad), pid)
    side = store.q("SELECT * FROM branches ORDER BY position")[1]
    assert side["from_event"] is None and side["into_event"] is None
    main = store.q("SELECT * FROM branches ORDER BY position")[0]
    assert events(store, pid)["想比较"]["branch_id"] == main["id"]


def test_placement_accepts_ids_followed_by_copied_text(store, copy_fixture):
    def verbose(user: str) -> dict:
        story = batch_story(user)
        for branch in story["threads"][0]["branches"]:
            branch["events"] = [f"{e} 10-01 09:00 S1 尝试｜抄回来的内容" for e in branch["events"]]
            if branch["from"]:
                branch["from"] += " 10-01 09:05 S1 尝试｜先试"
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(verbose), pid)
    assert store.one("SELECT COUNT(*) AS n FROM threads WHERE title = '未归类'")["n"] == 0
    side = store.q("SELECT * FROM branches ORDER BY position")[1]
    assert side["from_event"] == events(store, pid)["先试"]["id"]


def test_events_the_model_forgets_are_still_placed(store, copy_fixture):
    def forgetful(user: str) -> dict:
        story = batch_story(user)
        story["threads"][0]["branches"].pop()  # 漏掉 scVI
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(forgetful), pid)
    rows = events(store, pid)
    assert rows["也试试"]["branch_id"] == rows["先试"]["branch_id"]

    pid2 = new_project(store, [copy_fixture("codex/research-synthetic.jsonl")])
    run(store, FakeModel(lambda user: {"threads": []}), pid2)
    misc = store.one("SELECT * FROM threads WHERE project_id = ?", (pid2,))
    assert misc["title"] == "未归类"
    assert all(r["branch_id"] for r in events(store, pid2).values())


def test_incremental_update_only_extracts_new_messages(store, copy_fixture):
    path = copy_fixture("claude/research-synthetic.jsonl")
    model = FakeModel(batch_story)
    pid = new_project(store, [path])
    run(store, model, pid)
    before = {k: r["branch_id"] for k, r in dict(events(store, pid)).items()}
    main = store.q("SELECT id FROM branches ORDER BY position")[0]["id"]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            + json.dumps(
                {
                    "type": "user",
                    "sessionId": "synthetic-claude-research",
                    "uuid": "late",
                    "timestamp": "2026-10-03T08:00:00Z",
                    "message": {"content": "用 Harmony 结果画论文图 2。"},
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    def place_new(user: str) -> dict:
        assert len(re.findall(r"^★ ", user, re.M)) == 1
        eid = next(iter(new_events(user).values()))
        return {
            "threads": [
                {
                    "id": "T1",
                    "title": "x",
                    "summary": "",
                    "branches": [
                        {
                            "id": f"B{main}",
                            "title": "",
                            "from": None,
                            "into": None,
                            "events": [f"E{eid}"],
                        }
                    ],
                }
            ]
        }

    model2 = FakeModel(place_new)
    progress = run(store, model2, pid)
    assert progress.phase == "done", progress.errors
    assert model2.count("events") == 1
    extract_prompt = model2.calls[0][1]
    assert (
        "#9 [" in extract_prompt and "[上文] #8" in extract_prompt and "#0 [" not in extract_prompt
    )
    rows = events(store, pid)
    assert {k: dict(rows)[k]["branch_id"] for k in before} == before
    assert rows["用 Harmony"]["branch_id"] == main


def test_full_reorganize_keeps_manual_edits_and_deletions(store, copy_fixture):
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(batch_story), pid)
    rows = events(store, pid)
    with store.tx() as db:
        db.execute(
            "UPDATE events SET title = '人工标题', edited = 1 WHERE id = ?", (rows["先试"]["id"],)
        )
        db.execute("UPDATE events SET hidden = 1, edited = 1 WHERE id = ?", (rows["也试试"]["id"],))

    def keep_going(user: str) -> dict:
        ids = new_events(user)
        return {
            "threads": [
                {
                    "id": None,
                    "title": "重整",
                    "summary": "",
                    "branches": [
                        {
                            "id": None,
                            "title": "",
                            "from": None,
                            "into": None,
                            "events": [f"E{i}" for i in ids.values()],
                        }
                    ],
                }
            ]
        }

    progress = run(store, FakeModel(keep_going), pid, full=True)
    assert progress.phase == "done", progress.errors
    titles = sorted(r["title"] for r in store.q("SELECT title FROM events WHERE hidden = 0"))
    # 人工标题保留且不重复；删掉的事件不会被重新提取回来。
    assert titles.count("人工标题") == 1
    assert not any(t.startswith("先试") or t.startswith("也试试") for t in titles)
    assert len(titles) == 3
    assert all(r["branch_id"] for r in store.q("SELECT branch_id FROM events WHERE hidden = 0"))


def test_truncated_output_splits_the_chunk(store, copy_fixture):
    base = FakeModel(batch_story)

    def picky(system, user, schema, tokens):
        if system == EVENT_SYSTEM and len(re.findall(r"^#\d+ \[", user, re.M)) > 2:
            raise Truncated("太长")
        return base(system, user, schema, tokens)

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    progress = run(store, picky, pid)
    assert progress.phase == "done", progress.errors
    assert len(events(store, pid)) == 4


def test_model_failure_keeps_progress_for_retry(store, copy_fixture):
    calls = {"n": 0}

    def flaky(system, user, schema, tokens):
        if system == EVENT_SYSTEM and "Harmony 的结果" in user:
            calls["n"] += 1
            raise ModelError("HTTP 500")
        return FakeModel()(system, user, schema, tokens)

    pid = new_project(
        store,
        [
            copy_fixture("claude/research-synthetic.jsonl"),
            copy_fixture("codex/research-synthetic.jsonl"),
        ],
    )
    progress = run(store, flaky, pid)
    assert progress.phase == "error" and "提取失败" in progress.errors[0]
    upto = {
        r["tool"]: r["extracted_upto"] for r in store.q("SELECT tool, extracted_upto FROM sessions")
    }
    assert upto == {"claude": 9, "codex": 0}
    progress = run(store, FakeModel(), pid)
    assert progress.phase == "done"
    assert (
        store.one("SELECT extracted_upto FROM sessions WHERE tool = 'codex'")["extracted_upto"] == 5
    )


def test_invalid_key_stops_everything_with_one_message(store, copy_fixture):
    calls = []

    def unauthorized(system, user, schema, tokens):
        calls.append(system)
        raise AuthError("API Key 无效或没有权限（HTTP 401）")

    paths = [
        copy_fixture("claude/research-synthetic.jsonl"),
        copy_fixture("codex/research-synthetic.jsonl"),
    ]
    pid = new_project(store, paths)
    progress = run(store, unauthorized, pid)
    assert progress.phase == "error"
    assert len(progress.errors) == 1 and "HTTP 401" in progress.errors[0]
    assert EVENT_SYSTEM in calls and len(calls) <= 2  # 每个会话最多一次，不再继续发
    assert [r["extracted_upto"] for r in store.q("SELECT extracted_upto FROM sessions")] == [0, 0]
    assert progress.estimate and progress.estimate["sessions"] == 2

    progress = run(store, FakeModel(), pid)  # 改好 Key 后重试，从头接着做
    assert progress.phase == "done"


def write_fork(original: Path, fork: Path, native: bool) -> None:
    """Claude 风格（无标记，开头逐条相同）或 Codex 风格（forked_from_id）的 fork。"""
    lines = original.read_text(encoding="utf-8").splitlines()
    if native:
        meta = json.loads(lines[0])
        meta["payload"].update(id="fork-id", forked_from_id=meta["payload"]["id"])
        meta["payload"]["timestamp"] = meta["timestamp"] = "2026-10-04T00:00:00Z"
        lines[0] = json.dumps(meta, ensure_ascii=False)
        extra = {
            "timestamp": "2026-10-04T00:01:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "在 fork 里换参考图谱。"}],
            },
        }
    else:
        lines = [line.replace("synthetic-claude-research", "fork-id") for line in lines[:-1]]
        extra = {
            "type": "user",
            "sessionId": "fork-id",
            "timestamp": "2026-10-04T00:01:00Z",
            "message": {"content": "在 fork 里换参考图谱。"},
        }
    lines.append(json.dumps(extra, ensure_ascii=False))
    fork.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("native", [False, True], ids=["claude-prefix", "codex-forked-from"])
def test_fork_becomes_a_new_branch_from_the_fork_point(store, copy_fixture, tmp_path, native):
    name = "codex/research-synthetic.jsonl" if native else "claude/research-synthetic.jsonl"
    original = copy_fixture(name)
    fork = tmp_path / "sessions" / "fork.jsonl"
    write_fork(original, fork, native)

    def one_line(user: str) -> dict:
        assert not re.search(r"^★ .*在 fork", user, re.M)  # fork 的事件不交给模型安排
        return {
            "threads": [
                {
                    "id": None,
                    "title": "主线",
                    "summary": "",
                    "branches": [
                        {
                            "id": None,
                            "title": "",
                            "from": None,
                            "into": None,
                            "events": [f"E{i}" for i in new_events(user).values()],
                        }
                    ],
                }
            ]
        }

    pid = new_project(store, [original, fork])
    progress = run(store, FakeModel(one_line), pid)
    assert progress.phase == "done", progress.errors
    rows = store.q("SELECT * FROM events ORDER BY id")
    titles = [r["title"] for r in rows]
    # 复制过来的历史不重复提取。
    assert len(titles) == len(set(titles))
    fork_session = store.one("SELECT * FROM sessions WHERE path = ?", (str(fork),))
    origin = store.one("SELECT * FROM sessions WHERE path = ?", (str(original),))
    assert fork_session["fork_of"] == origin["id"]
    fork_event = next(r for r in rows if r["title"].startswith("在 fork"))
    last_before = max(
        (r for r in rows if r["session_id"] == origin["id"]), key=lambda r: r["anchor"]
    )
    branch = store.one("SELECT * FROM branches WHERE id = ?", (fork_event["branch_id"],))
    assert branch["from_event"] == last_before["id"]
    assert branch["id"] != last_before["branch_id"]
    assert fork_session["fork_branch"] == branch["id"]


def test_deleted_session_file_keeps_imported_messages(store, copy_fixture):
    path = copy_fixture("codex/research-synthetic.jsonl")
    pid = new_project(store, [path], allowed=False)
    run(store, FakeModel(), pid)
    path.unlink()
    run(store, FakeModel(), pid)
    row = store.one("SELECT missing, message_count FROM sessions")
    assert row == {"missing": 1, "message_count": 5}
    assert store.one("SELECT COUNT(*) AS n FROM messages")["n"] == 5


def test_validate_events_drops_refs_outside_chunk_and_bad_kinds():
    messages = [{"idx": i, "ts": f"t{i}"} for i in range(10)]
    output = {
        "events": [
            {"kind": "finding", "title": "ok", "summary": "", "refs": [5, 99, "#4"]},
            {"kind": "finding", "title": "outside", "summary": "", "refs": [1]},
            {"kind": "weird", "title": "bad kind", "summary": "", "refs": [5]},
            {"kind": "decision", "title": "  ", "summary": "", "refs": [5]},
        ]
    }
    (event,) = validate_events(output, messages, 3, 8)
    assert event["refs"] == [5, 4] and event["anchor"] == 4 and event["ts"] == "t4"


def test_chunk_ranges_cover_every_message_once():
    sizes = [10, 50, 50, 10, 200, 5]
    ranges = chunk_ranges(sizes, 1, limit=100)
    assert ranges == [(1, 3), (3, 4), (4, 5), (5, 6)]
    assert chunk_ranges(sizes, 6) == []


@pytest.fixture(autouse=True)
def _no_real_sessions(monkeypatch):
    # 测试里绝不扫描真实的会话目录。
    monkeypatch.setattr("rg.sources._homes", lambda: [])
