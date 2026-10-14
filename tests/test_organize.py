from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rg.model import AuthError, ModelError, Truncated, cached
from rg.organize import (
    EVENT_SYSTEM,
    Progress,
    add_sessions,
    chunk_ranges,
    organize,
    pending,
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


STORY = ("想比较", "先试", "比较后", "也试试")


def one_new_line(title: str, ids) -> dict:
    """把这一批的新事件全部放进一条新研究线。"""
    return {
        "new_threads": [{"key": "N1", "title": title, "summary": ""}],
        "placements": [{"event": f"E{i}", "branch": "N1"} for i in ids],
    }


def batch_story(user: str) -> dict:
    ids = new_events(user)
    if not all(any(title.startswith(p) for title in ids) for p in STORY):
        return {"placements": []}  # 不是这个故事的事件（比如补问的另一个会话），不安排
    return {
        "new_threads": [{"key": "N1", "title": "去批次", "summary": "比较两种方法"}],
        "new_branches": [
            {
                "key": "NB1",
                "thread": "N1",
                "title": "scVI 对照",
                "from": f"E{ids['先试']}",
                "into": f"E{ids['比较后']}",
            }
        ],
        "placements": [
            {"event": f"E{ids['想比较']}", "branch": "N1"},
            {"event": f"E{ids['先试']}", "branch": "N1"},
            {"event": f"E{ids['比较后']}", "branch": "N1"},
            {"event": f"E{ids['也试试']}", "branch": "NB1"},
        ],
        "merges": [],
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
        side = story["new_branches"][0]
        side["from"], side["into"] = f"E{ids['比较后']}", f"E{ids['想比较']}"  # 时间颠倒
        story["placements"].append({"event": f"E{ids['想比较']}", "branch": "NB1"})  # 重复
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
        for item in story["placements"]:
            item["event"] += " 10-01 09:00 尝试｜抄回来的内容"
        story["new_branches"][0]["from"] += " 10-01 09:05 尝试｜先试"
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(verbose), pid)
    assert store.one("SELECT COUNT(*) AS n FROM threads WHERE title = '未归类'")["n"] == 0
    side = store.q("SELECT * FROM branches ORDER BY position")[1]
    assert side["from_event"] == events(store, pid)["先试"]["id"]


def test_events_the_model_forgets_are_still_placed(store, copy_fixture):
    def forgetful(user: str) -> dict:
        story = batch_story(user)
        story["placements"] = [p for p in story["placements"] if p["branch"] != "NB1"]  # 漏掉 scVI
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    model = FakeModel(forgetful)
    run(store, model, pid)
    assert model.count("threads") == 2  # 漏掉的先单独再问一次，再漏才由程序兜底
    # 补问的提示和第一次不同，不会从缓存里拿回同一个结果
    asks = [user for kind, user in model.calls if kind == "threads"]
    assert "上一轮漏掉" not in asks[0] and "上一轮漏掉" in asks[1]
    rows = events(store, pid)
    assert rows["也试试"]["branch_id"] == rows["先试"]["branch_id"]

    pid2 = new_project(store, [copy_fixture("codex/research-synthetic.jsonl")])
    run(store, FakeModel(lambda user: {"placements": []}), pid2)
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
        return {"placements": [{"event": f"E{eid}", "branch": f"B{main}"}]}

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
        return one_new_line("重整", ids.values())

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

    def picky(system, user, schema, tokens, meter=None):
        if system == EVENT_SYSTEM and len(re.findall(r"^#\d+ \[", user, re.M)) > 2:
            raise Truncated("太长")
        return base(system, user, schema, tokens)

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    progress = run(store, picky, pid)
    assert progress.phase == "done", progress.errors
    assert len(events(store, pid)) == 4


def test_model_failure_keeps_progress_for_retry(store, copy_fixture):
    calls = {"n": 0}

    def flaky(system, user, schema, tokens, meter=None):
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

    def unauthorized(system, user, schema, tokens, meter=None):
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
        return one_new_line("主线", new_events(user).values())

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
    assert branch["title"] == fork_event["title"]  # 用 fork 之后第一步命名，不用会话标题


def test_deleted_session_file_keeps_imported_messages(store, copy_fixture):
    path = copy_fixture("codex/research-synthetic.jsonl")
    pid = new_project(store, [path], allowed=False)
    run(store, FakeModel(), pid)
    path.unlink()
    run(store, FakeModel(), pid)
    row = store.one("SELECT missing, message_count FROM sessions")
    assert row == {"missing": 1, "message_count": 5}
    assert store.one("SELECT COUNT(*) AS n FROM messages")["n"] == 5


def usage_rows(store: Store) -> list[dict]:
    return store.q("SELECT project_id, stage, input_tokens, cached FROM usage ORDER BY id")


def test_every_model_call_is_metered_by_project_and_stage(store, copy_fixture):
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    model = cached(store, FakeModel(batch_story), "fake")
    assert run(store, model, pid).phase == "done"
    rows = usage_rows(store)
    assert {r["stage"] for r in rows} == {"extract", "threads", "review", "brief"}
    assert all(r["project_id"] == pid and r["cached"] == 0 and r["input_tokens"] for r in rows)

    # 重新整理时，提取请求和上次一模一样，命中本地缓存：记一笔，但不算 token。
    assert run(store, model, pid, full=True).phase == "done"
    again = [r for r in usage_rows(store)[len(rows) :] if r["stage"] == "extract"]
    assert again and all(r["cached"] == 1 and r["input_tokens"] == 0 for r in again)


def test_auto_update_over_the_limit_waits_for_the_user(store, copy_fixture):
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    model = FakeModel(batch_story)
    progress = Progress()
    organize(store, model, pid, [], progress, limit=100)
    assert progress.phase == "confirm" and not model.calls
    assert progress.estimate and progress.estimate["chars"] > 100
    assert store.one("SELECT extracted_upto FROM sessions")["extracted_upto"] == 0
    progress = Progress()
    organize(store, model, pid, [], progress, limit=1_000_000)
    assert progress.phase == "done" and model.calls


def test_pending_sees_new_content_without_parsing(store, tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    first = root / "claude.jsonl"
    first.write_bytes(CLAUDE.read_bytes())
    with store.tx() as db:
        pid = db.execute(
            "INSERT INTO projects(name, workspace, follow, model_allowed, created_at) "
            "VALUES ('p', '/workspace/demo', 1, 1, 'now')"
        ).lastrowid
    roots = [str(root)]
    found = pending(store, pid, roots)
    assert (found["changed"], found["new"]) == (0, 1)  # 跟随的工作目录里出现了会话
    organize(store, FakeModel(batch_story), pid, roots, Progress())
    assert pending(store, pid, roots) == {
        "changed": 0,
        "new": 0,
        "chars": 0,
        "unplaced": 0,
        "quiet": None,
    }

    with first.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "synthetic-claude-research",
                    "timestamp": "2026-10-03T08:00:00Z",
                    "message": {"content": "再补一个对照。"},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    found = pending(store, pid, roots)
    assert found["changed"] == 1 and found["quiet"] is not None and found["quiet"] < 60
    assert store.one("SELECT message_count FROM sessions")["message_count"] == 9  # 没有解析


def test_unreadable_session_does_not_stay_pending(store, copy_fixture):
    path = copy_fixture("claude/research-synthetic.jsonl")
    pid = new_project(store, [path])
    run(store, FakeModel(batch_story), pid)
    path.write_text("这不是会话\n" * 30, encoding="utf-8")
    assert pending(store, pid, [])["changed"] == 1
    run(store, FakeModel(batch_story), pid)
    assert store.one("SELECT missing FROM sessions")["missing"] == 1
    # 否则自动更新会每隔几十秒重读一次这个文件。
    assert pending(store, pid, [])["changed"] == 0


def test_validate_events_drops_refs_outside_chunk_and_bad_kinds():
    messages = [{"idx": i, "ts": f"t{i}"} for i in range(10)]
    output = {
        "events": [
            {"kind": "result", "title": "ok", "summary": "", "refs": [5, 99, "#4"]},
            {"kind": "result", "title": "outside", "summary": "", "refs": [1]},
            {"kind": "finding", "title": "old kind", "summary": "", "refs": [5]},
            {"kind": "decision", "title": "  ", "summary": "", "refs": [5]},
            {"kind": "attempt", "title": "try", "summary": "", "refs": [6], "importance": 7},
            {
                "kind": "decision",
                "title": "pick",
                "outcome": "failed",
                "refs": [6],
                "importance": 3,
            },
        ]
    }
    event, attempt, decision = validate_events(output, messages, 3, 8)
    assert event["refs"] == [5, 4] and event["anchor"] == 4 and event["ts"] == "t4"
    assert (attempt["outcome"], attempt["importance"]) == ("open", 2)  # 缺的和越界的都补成默认
    # 成败只属于尝试；里程碑只在复审研究线时给，提取给的 3 降为 2
    assert (decision["outcome"], decision["importance"]) == (None, 2)


def test_chunk_ranges_cover_every_message_once():
    sizes = [10, 50, 50, 10, 200, 5]
    ranges = chunk_ranges(sizes, 1, limit=100)
    assert ranges == [(1, 3), (3, 4), (4, 5), (5, 6)]
    assert chunk_ranges(sizes, 6) == []


@pytest.fixture(autouse=True)
def _no_real_sessions(monkeypatch):
    # 测试里绝不扫描真实的会话目录。
    monkeypatch.setattr("rg.sources._homes", lambda: [])


def test_each_placement_batch_only_shows_placed_steps_and_its_own(store, copy_fixture, monkeypatch):
    # 还没轮到的步骤如果也列出来，模型会把它们一起安排，输出超长被截断。
    monkeypatch.setattr("rg.organize.PLACE_BATCH", 2)
    prompts: list[str] = []

    def one_line(user: str) -> dict:
        prompts.append(user)
        return one_new_line("一条线", new_events(user).values())

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    assert run(store, FakeModel(one_line), pid).phase == "done"
    assert len(prompts) == 2
    first_listed = re.findall(r"^[★ ] E\d+ ", prompts[0], re.M)
    assert len(first_listed) == 2  # 第一批只看到自己的两个
    assert len(re.findall(r"^  E\d+ ", prompts[1], re.M)) == 2  # 第二批看到已放好的两个
    assert len(re.findall(r"^★ ", prompts[1], re.M)) == 2
    assert all(r["branch_id"] for r in events(store, pid).values())


def test_events_left_unplaced_by_a_failed_run_count_as_pending(store, copy_fixture):
    def truncated(user: str) -> dict:
        raise Truncated("太长")

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    assert run(store, FakeModel(truncated), pid).phase == "error"
    assert pending(store, pid, [])["unplaced"] == 4  # 重启后错误状态没了，也要能看出来
    assert run(store, FakeModel(batch_story), pid).phase == "done"
    assert pending(store, pid, [])["unplaced"] == 0


def test_old_database_is_migrated_in_place(tmp_path):
    import sqlite3

    from rg.store import SCHEMA

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)  # 迁移之前的库：events 没有 outcome / importance
    db.execute("INSERT INTO projects(name, created_at) VALUES ('p', 'now')")
    db.execute("INSERT INTO sessions(project_id, tool, path) VALUES (1, 'claude', 'x')")
    for kind in ("finding", "output", "question", "decision"):
        db.execute(
            "INSERT INTO events(project_id, session_id, kind, title, anchor) VALUES (1, 1, ?, 't', 0)",
            (kind,),
        )
    db.commit()
    db.close()
    migrated = Store(path)
    kinds = [r["kind"] for r in migrated.q("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["result", "result", "question", "decision"]
    assert migrated.one("SELECT importance, outcome FROM events LIMIT 1") == {
        "importance": 2,
        "outcome": None,
    }
    migrated.close()
    assert Store(path).one("PRAGMA user_version")["user_version"] == 2  # 再打开不会重复迁移


def test_thread_review_picks_milestones_and_respects_manual_edits(store, copy_fixture):
    reviews: list[str] = []

    def review(user: str) -> dict:
        reviews.append(user)
        first = re.search(r"^E(\d+) ", user, re.M)
        assert first
        return {
            "milestones": [f"E{first[1]}", "E99999"],
            "summary": "先比较后采用 Harmony",
            "status": "done",
        }

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    model = FakeModel(batch_story, review)
    assert run(store, model, pid).phase == "done"
    thread = store.one("SELECT * FROM threads WHERE project_id = ?", (pid,))
    assert (thread["summary"], thread["status"]) == ("先比较后采用 Harmony", "done")
    milestones = store.q("SELECT id FROM events WHERE importance = 3")
    assert len(milestones) == 1  # 不存在的编号被丢掉

    run(store, model, pid)
    assert len(reviews) == 1  # 研究线里的事件没变，不重复复审

    # 人工改过的研究线和事件，复审不覆盖
    with store.tx() as db:
        db.execute("UPDATE threads SET summary = '我写的', edited = 1, reviewed = NULL")
        db.execute(
            "UPDATE events SET importance = 1, edited = 1 WHERE id = ?", (milestones[0]["id"],)
        )
    run(store, model, pid)
    assert store.one("SELECT summary FROM threads")["summary"] == "我写的"
    assert (
        store.one("SELECT importance FROM events WHERE id = ?", (milestones[0]["id"],))[
            "importance"
        ]
        == 1
    )


def test_fallback_reuses_one_misc_line(store, copy_fixture):
    pid = new_project(store, [copy_fixture("codex/research-synthetic.jsonl")])
    run(store, FakeModel(lambda user: {"placements": []}), pid)
    add_sessions(store, pid, [read_info(copy_fixture("claude/research-synthetic.jsonl"))])
    run(store, FakeModel(lambda user: {"placements": []}), pid)
    titles = [r["title"] for r in store.q("SELECT title FROM threads WHERE project_id = ?", (pid,))]
    assert titles == ["未归类"]


def test_main_line_stays_first_when_a_side_branch_is_placed_first(store, copy_fixture):
    def side_first(user: str) -> dict:
        story = batch_story(user)
        story["placements"].sort(key=lambda p: p["branch"] != "NB1")
        return story

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(side_first), pid)
    main, side = store.q("SELECT * FROM branches ORDER BY position")
    rows = events(store, pid)
    assert rows["想比较"]["branch_id"] == main["id"] and rows["也试试"]["branch_id"] == side["id"]
    assert side["from_event"] == rows["先试"]["id"] and side["into_event"] == rows["比较后"]["id"]


def test_too_many_lines_are_consolidated_without_losing_events(store, copy_fixture, monkeypatch):
    monkeypatch.setattr("rg.organize.THREAD_TARGET", 100)  # 先不合并，攒出很多条线

    def every_step_alone(user: str) -> dict:
        ids = list(new_events(user).values())
        return {
            "new_threads": [
                {"key": f"N{i}", "title": f"线{i}", "summary": ""} for i in range(len(ids))
            ],
            "placements": [{"event": f"E{e}", "branch": f"N{i}"} for i, e in enumerate(ids)],
        }

    seen: list[str] = []

    def merge_all(user: str) -> dict:
        seen.append(user)
        lines = re.findall(r"^T(\d+)「", user, re.M)
        return {
            "groups": [
                {"into": f"T{lines[0]}", "lines": [f"T{t}" for t in lines[1:]], "title": "合并后"}
            ]
        }

    paths = [
        copy_fixture("claude/research-synthetic.jsonl"),
        copy_fixture("codex/research-synthetic.jsonl"),
    ]
    pid = new_project(store, paths)
    first = run(store, FakeModel(every_step_alone), pid)
    assert first.phase == "done"
    count = store.one("SELECT COUNT(*) AS n FROM threads WHERE project_id = ?", (pid,))["n"]
    assert count > 3

    # 人工改过的研究线不会被并走
    kept = store.q("SELECT id FROM threads WHERE project_id = ? ORDER BY id", (pid,))[-1]["id"]
    with store.tx() as db:
        db.execute("UPDATE threads SET edited = 1, title = '我的线' WHERE id = ?", (kept,))
        db.execute("UPDATE threads SET reviewed = NULL")
    before = {r["id"] for r in store.q("SELECT id FROM events WHERE project_id = ?", (pid,))}
    monkeypatch.setattr("rg.organize.THREAD_TARGET", 3)  # 合成会话只有几个步骤
    run(store, FakeModel(every_step_alone, consolidate=merge_all), pid)
    assert seen and "代表步骤" in seen[0]
    titles = sorted(
        r["title"] for r in store.q("SELECT title FROM threads WHERE project_id = ?", (pid,))
    )
    assert titles == ["合并后", "我的线"]
    rows = store.q("SELECT id, branch_id FROM events WHERE project_id = ?", (pid,))
    assert {r["id"] for r in rows} == before and all(r["branch_id"] for r in rows)

    # 研究线没再变化，下次整理不会重复合并
    asked = len(seen)
    run(store, FakeModel(every_step_alone, consolidate=merge_all), pid)
    assert len(seen) == asked


def test_new_line_with_an_existing_name_reuses_it(store, copy_fixture):
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    run(store, FakeModel(batch_story), pid)
    add_sessions(store, pid, [read_info(copy_fixture("codex/research-synthetic.jsonl"))])
    run(store, FakeModel(lambda user: one_new_line("去批次", new_events(user).values())), pid)
    titles = [r["title"] for r in store.q("SELECT title FROM threads WHERE project_id = ?", (pid,))]
    assert titles == ["去批次"]
