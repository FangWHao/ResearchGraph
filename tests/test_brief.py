from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from rg.brief import brief_state, home
from rg.model import Truncated
from tests.conftest import FakeModel
from tests.test_organize import batch_story, new_project, run

STEP = re.compile(r"^E(\d+) ", re.M)
LINE = re.compile(r"^T(\d+)「", re.M)


def test_brief_keeps_only_real_refs_and_is_not_rewritten_when_nothing_changed(store, copy_fixture):
    seen: list[str] = []

    def brief(user: str) -> dict:
        seen.append(user)
        step, line = STEP.search(user), LINE.search(user)
        assert step and line
        return {
            "headline": f"在比较去批次方法（E{step[1]}），T{line[1]} 还在推进",
            "progress": [{"text": "定了 Harmony", "events": [f"E{step[1]}", "E99999"]}],
            "open": [
                {"text": "scVI 还没验证", "events": ["E99998"], "thread": f"T{line[1]}"},
                {"text": "编出来的线", "events": [], "thread": "T999"},
            ],
            "next": [{"text": "写方法部分"}, {"text": ""}],
        }

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    model = FakeModel(batch_story, brief=brief)
    assert run(store, model, pid).phase == "done"
    state = brief_state(store, pid)
    assert state and not state["stale"] and not state["error"]
    content = state["content"]
    real = int(STEP.search(seen[0])[1])
    assert content["progress"] == [{"text": "定了 Harmony", "events": [real]}]
    assert content["open"][0]["events"] == [] and content["open"][0]["thread"] is not None
    assert content["open"][1]["thread"] is None  # 不存在的研究线编号丢掉
    assert content["next"] == [{"text": "写方法部分"}]
    # 正文里不留模型看到的内部编号，研究线编号换成名字
    tid = int(LINE.search(seen[0])[1])
    title = store.one("SELECT title FROM threads WHERE id = ?", (tid,))["title"]
    assert content["headline"] == f"在比较去批次方法，「{title}」还在推进"

    run(store, model, pid)
    assert len(seen) == 1  # 研究线没变，不重写

    # 删掉一个事件：简报落后于时间线；删掉的事件不再出现在给模型的内容里
    gone = store.one("SELECT id, title FROM events WHERE project_id = ? ORDER BY id", (pid,))
    with store.tx() as db:
        db.execute("UPDATE events SET hidden = 1 WHERE id = ?", (gone["id"],))
    assert brief_state(store, pid)["stale"]
    run(store, model, pid)
    assert len(seen) == 2 and f"E{gone['id']} " not in seen[1]


def test_truncated_brief_keeps_the_last_one_and_is_not_retried(store, copy_fixture):
    calls: list[str] = []

    def ok(user: str) -> dict:
        calls.append(user)
        return {"headline": "第一版", "progress": [], "open": [], "next": []}

    def cut(user: str) -> dict:
        calls.append(user)
        raise Truncated("max_tokens")

    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    assert run(store, FakeModel(batch_story, brief=ok), pid).phase == "done"
    with store.tx() as db:
        db.execute("UPDATE threads SET summary = '换了说法', edited = 1")
    progress = run(store, FakeModel(batch_story, brief=cut), pid)
    assert progress.phase == "done"  # 简报失败不影响时间线
    state = brief_state(store, pid)
    assert state["content"]["headline"] == "第一版" and "截断" in state["error"]
    assert not state["stale"]  # 同样的输入出过错，不算落后，主页不会自动再试
    run(store, FakeModel(batch_story, brief=cut), pid)
    assert len(calls) == 2


def test_home_counts_key_steps_by_local_day(store, copy_fixture):
    pid = new_project(store, [copy_fixture("claude/research-synthetic.jsonl")])
    assert run(store, FakeModel(batch_story), pid).phase == "done"
    day = (datetime.now(UTC) - timedelta(days=3)).date()
    with store.tx() as db:
        db.execute("UPDATE events SET ts = ?, importance = 2", (f"{day}T18:30:00Z",))
    events = store.one("SELECT COUNT(*) AS n FROM events")["n"]
    # UTC 18:30 在 UTC+8 已经是第二天凌晨
    east = {d["day"]: d["key"] for d in home(store, -480)["activity"]}
    utc = {d["day"]: d["key"] for d in home(store, 0)["activity"]}
    after = (day + timedelta(days=1)).isoformat()
    assert (east[after], east[day.isoformat()]) == (events, 0)
    assert (utc[day.isoformat()], utc[after]) == (events, 0)
    project = home(store, 0)["projects"][0]
    assert project["events"] == events and project["threads"]
    # 每个事件都算在某条研究线或“未归入”里，一个不丢
    assert sum(t["events"] for t in project["threads"]) + project["unplaced"] == events
