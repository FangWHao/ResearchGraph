"""项目简报和主页汇总。

简报由模型根据整理好的结果来写：现在在做什么、最近的进展、还没解决的问题、可能的下一步。
只读研究线的进展叙述、里程碑、最近的关键步骤和失败或未定的尝试，不读原始会话。
输入没变就不重写；模型给的步骤编号和研究线编号由程序核对，对不上的丢掉。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from rg.model import Call, ModelError, Truncated, Usage, metered
from rg.organize import MISC_TITLE, _clean, _items, _list, _ref, event_key, short_time, step_label
from rg.store import Store, now
from rg.usage import recorder

BRIEF_RECENT = 6  # 每条研究线再给几个最近的关键步骤（里程碑之外）
BRIEF_UNRESOLVED = 15  # 整个项目最近的失败或未定的尝试
BRIEF_TOKENS = 3_000
BRIEF_REFS = 4  # 每条最多引用几个步骤
STATUS_TEXT = {"active": "进行中", "done": "已完成", "dropped": "已放弃"}
ACTIVITY_DAYS = 84

BRIEF_SYSTEM = """你在帮一位研究者写项目简报，让研究者本人和接手的 Agent 一眼看清这个项目现在到哪了、还有什么没解决。
下面是项目的研究线：每条线的状态、进展叙述、里程碑和最近的关键步骤，最后是最近失败或未定的尝试。请写：
1. headline：一两句话，项目现在主要在做什么、处在什么阶段，不超过 70 个字。
2. progress：最近最重要的 3 到 5 个进展，从新到旧。每条一句话，不超过 50 个字，写清得到了什么结论或定了什么，附上依据的步骤编号（例如 E12），最多 4 个。
3. open：还没解决的问题 2 到 5 个：失败后还没找到替代的尝试、还没验证或互相矛盾的结论、悬而未决的决定、还在进行但很久没动的研究线。每条一句话，不超过 50 个字，附上相关步骤编号和研究线编号（例如 T3）。后面的步骤已经解决了的不要写。
4. next：从记录看接下来最可能要做的 1 到 3 件事，每条不超过 40 个字。记录里看不出来就给空列表，不要编。
文字里不要出现 E12、T3 这样的编号，编号只填进 events 和 thread；提到研究线就写它的名字。
只根据给出的内容写，不要推测记录里没有的东西。用步骤的主要语言书写。用 submit 工具提交。"""

_REFS = {"type": "array", "items": {"type": "string", "description": "步骤编号，例如 E12"}}

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "progress": _items({"text": {"type": "string"}, "events": _REFS}),
        "open": _items(
            {
                "text": {"type": "string"},
                "events": _REFS,
                "thread": {"type": ["string", "null"], "description": "研究线编号，例如 T3"},
            }
        ),
        "next": _items({"text": {"type": "string"}}),
    },
    "required": ["headline", "progress", "open", "next"],
}


def _day(ts: str | None) -> str:
    return short_time(ts)[:5] if ts else "时间未知"


def _step(event: dict[str, Any]) -> str:
    return f"E{event['id']} {short_time(event['ts'])} {step_label(event)}｜{event['title']}｜{event['summary']}"


def _visible(store: Store, project_id: int) -> list[dict[str, Any]]:
    rows = store.q(
        "SELECT e.id, e.kind, e.outcome, e.importance, e.title, e.summary, e.ts, e.session_id, "
        "e.anchor, b.thread_id FROM events e LEFT JOIN branches b ON b.id = e.branch_id "
        "WHERE e.project_id = ? AND e.hidden = 0",
        (project_id,),
    )
    return sorted(rows, key=event_key)


def _threads(store: Store, project_id: int) -> list[dict[str, Any]]:
    return store.q(
        "SELECT id, title, summary, status FROM threads WHERE project_id = ? AND title != ? "
        "ORDER BY id",
        (project_id, MISC_TITLE),
    )


def brief_input(store: Store, project_id: int) -> tuple[str, str] | None:
    """简报的输入和它的签名。签名不含当天日期，内容没变就不用重写。没有归好的研究线返回 None。"""
    project = store.one("SELECT name FROM projects WHERE id = ?", (project_id,))
    events = _visible(store, project_id)
    threads = _threads(store, project_id)
    members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e["thread_id"] is not None:
            members[e["thread_id"]].append(e)
    threads = [t for t in threads if members.get(t["id"])]
    if not project or not threads:
        return None
    # 最近还在动的研究线排在前面
    threads.sort(key=lambda t: members[t["id"]][-1]["ts"] or "", reverse=True)
    sessions = store.one("SELECT COUNT(*) AS n FROM sessions WHERE project_id = ?", (project_id,))
    lines = [
        f"项目「{project['name']}」：{sessions['n'] if sessions else 0} 个对话，"
        f"整理出 {len(events)} 个步骤，时间从 {_day(events[0]['ts'])} 到 {_day(events[-1]['ts'])}。"
    ]
    for t in threads:
        steps = members[t["id"]]
        key = [e for e in steps if e["importance"] >= 2]
        lines += [
            "",
            f"T{t['id']}「{t['title']}」：{STATUS_TEXT.get(t['status'] or 'active', '进行中')}，"
            f"{len(key)} 个关键步骤，{_day(steps[0]['ts'])} 到 {_day(steps[-1]['ts'])}",
        ]
        if t["summary"]:
            lines.append(f"进展：{t['summary']}")
        milestones = [e for e in steps if e["importance"] == 3]
        if milestones:
            lines += ["里程碑：", *map(_step, milestones)]
        recent = [e for e in key if e["importance"] == 2][-BRIEF_RECENT:]
        if recent:
            lines += ["最近的关键步骤：", *map(_step, recent)]
    known = {t["id"] for t in threads}
    unresolved = [
        e
        for e in events
        if e["kind"] == "attempt"
        and e["outcome"] in ("failed", "open")
        and e["importance"] >= 2
        and e["thread_id"] in known
    ][-BRIEF_UNRESOLVED:]
    if unresolved:
        lines += ["", "最近失败或未定的尝试（可能已经被后面的步骤解决）："]
        lines += [f"{_step(e)}（T{e['thread_id']}）" for e in unresolved]
    text = "\n".join(lines)
    return text, hashlib.sha256(text.encode()).hexdigest()[:16]


_REF_GROUP = re.compile(r"\s*[（(]\s*(?:[ET]\d+\s*[，,、/和与\s]*)+[)）]")
_BARE_STEP = re.compile(r"(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])\s*")
_CJK_GAP = re.compile(
    r"(?<=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])\s+(?=[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef])"
)


def plain(text: str, threads: dict[int, str]) -> str:
    """正文里不留内部编号：括号里的编号整组去掉，研究线编号换成名字（后面紧跟名字就直接去掉）。"""
    text = _REF_GROUP.sub("", text)
    for tid, title in threads.items():
        tag = rf"(?<![A-Za-z0-9])T{tid}(?![A-Za-z0-9])"
        text = re.sub(tag + rf"\s*[「“\"]?{re.escape(title)}[」”\"]?", title, text)
        text = re.sub(tag, f"「{title}」", text)
    text = " ".join(_BARE_STEP.sub("", text).split())
    return _CJK_GAP.sub("", text)


def clean_brief(
    output: dict[str, Any], events: set[int], threads: dict[int, str]
) -> dict[str, Any]:
    """只留下对得上的步骤和研究线编号；空的条目丢掉；正文里的编号去掉。"""

    def refs(value: Any) -> list[int]:
        picked: list[int] = []
        for item in _list(value):
            eid = _ref(item, "E")
            if eid is not None and eid in events and eid not in picked:
                picked.append(eid)
        return picked[:BRIEF_REFS]

    def entries(key: str, limit: int, size: int) -> list[dict[str, Any]]:
        out = []
        for item in _list(output.get(key)):
            if not isinstance(item, dict):
                continue
            text = _clean(plain(str(item.get("text") or ""), threads), size)
            if not text:
                continue
            entry: dict[str, Any] = {"text": text}
            if key != "next":
                entry["events"] = refs(item.get("events"))
            if key == "open":
                thread = _ref(item.get("thread"), "T")
                entry["thread"] = thread if thread in threads else None
            out.append(entry)
        return out[:limit]

    return {
        "headline": _clean(plain(str(output.get("headline") or ""), threads), 200),
        "progress": entries("progress", 5, 120),
        "open": entries("open", 5, 120),
        "next": entries("next", 3, 100),
    }


def _fail(store: Store, project_id: int, signature: str, error: str) -> None:
    """记下出错，保留上一份简报；签名记成这次的输入，同样的输入不再自动重试。"""
    with store.tx() as db:
        db.execute("INSERT OR IGNORE INTO briefs(project_id) VALUES (?)", (project_id,))
        db.execute(
            "UPDATE briefs SET signature = ?, error = ? WHERE project_id = ?",
            (signature, error, project_id),
        )


def _store(
    store: Store, project_id: int, signature: str, content: dict[str, Any], model: str | None
) -> None:
    with store.tx() as db:
        db.execute("INSERT OR IGNORE INTO briefs(project_id) VALUES (?)", (project_id,))
        db.execute(
            "UPDATE briefs SET signature = ?, content = ?, created_at = ?, error = NULL, "
            "model = COALESCE(?, model) WHERE project_id = ?",
            (signature, json.dumps(content, ensure_ascii=False), now(), model, project_id),
        )


def fail_brief(store: Store, project_id: int, message: str) -> None:
    built = brief_input(store, project_id)
    _fail(store, project_id, built[1] if built else "", message)


def write_brief(store: Store, call: Call, project_id: int, force: bool = False) -> bool:
    """写一份简报。输入和上次一样就跳过（force 时照样重写）。返回是否调用了模型。
    截断或格式不对时记下错误，不写半截结果；同样的输入出过错，不自动再试。"""
    built = brief_input(store, project_id)
    if built is None:
        return False
    text, signature = built
    row = store.one("SELECT signature FROM briefs WHERE project_id = ?", (project_id,))
    if row and row["signature"] == signature and not force:
        return False
    models: list[str] = []
    record = recorder(store, project_id, "brief")

    def sink(usage: Usage | None) -> None:
        record(usage)
        if usage and usage.model:
            models.append(usage.model)

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    user = f"今天是 {today}。\n\n{text}"
    if force:
        # 用户要求重写：换一份请求，不拿本地缓存里的旧结果
        user += f"\n\n（重新写一版，{now()}）"
    try:
        output = metered(call, sink)(BRIEF_SYSTEM, user, BRIEF_SCHEMA, BRIEF_TOKENS)
    except Truncated:
        _fail(store, project_id, signature, "简报输出太长被截断，没有保存")
        return True
    except ModelError as error:
        _fail(store, project_id, signature, f"写简报失败：{error}")
        return True
    events = {e["id"] for e in _visible(store, project_id)}
    threads = {t["id"]: t["title"] for t in _threads(store, project_id)}
    content = clean_brief(output, events, threads)
    if not content["headline"]:
        _fail(store, project_id, signature, "模型没有写出简报")
        return True
    _store(store, project_id, signature, content, models[-1] if models else None)
    return True


def brief_state(store: Store, project_id: int) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM briefs WHERE project_id = ?", (project_id,))
    built = brief_input(store, project_id)
    if row is None and built is None:
        return None
    current = built[1] if built else ""
    return {
        "content": json.loads(row["content"]) if row and row["content"] else None,
        "created_at": row["created_at"] if row else None,
        "model": row["model"] if row else "",
        "error": row["error"] if row else None,
        # 研究线有变化、或者还没写过：简报落后于时间线
        "stale": bool(built) and (row is None or row["signature"] != current),
        "ready": built is not None,
    }


# ---------- 主页汇总 ----------


def _local_day(ts: str | None, offset: timedelta) -> str | None:
    if not ts:
        return None
    try:
        moment = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment.astimezone(UTC) - offset).date().isoformat()


def project_home(store: Store, project: dict[str, Any]) -> dict[str, Any]:
    pid = project["id"]
    events = _visible(store, pid)
    threads = _threads(store, pid)
    members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e["thread_id"] is not None:
            members[e["thread_id"]].append(e)
    lines = []
    for t in threads:
        steps = members.get(t["id"])
        if not steps:
            continue
        lines.append(
            {
                **t,
                "events": len(steps),
                "key": sum(1 for e in steps if e["importance"] >= 2),
                "failed": sum(1 for e in steps if e["outcome"] == "failed"),
                "first_ts": steps[0]["ts"],
                "last_ts": steps[-1]["ts"],
                "milestones": [
                    {k: e[k] for k in ("id", "ts", "kind", "outcome", "title")}
                    for e in steps
                    if e["importance"] == 3
                ],
            }
        )
    lines.sort(key=lambda t: t["last_ts"] or "", reverse=True)
    brief = brief_state(store, pid)
    refs: dict[int, dict[str, Any]] = {}
    if brief and brief["content"]:
        wanted = {
            eid
            for part in ("progress", "open")
            for item in brief["content"][part]
            for eid in item.get("events", [])
        }
        refs = {
            e["id"]: {k: e[k] for k in ("id", "ts", "kind", "outcome", "title", "thread_id")}
            for e in events
            if e["id"] in wanted
        }
    sessions = store.one(
        "SELECT COUNT(*) AS n, MAX(ended_at) AS last FROM sessions WHERE project_id = ?", (pid,)
    )
    return {
        "id": pid,
        "name": project["name"],
        "workspace": project["workspace"],
        "model_allowed": project["model_allowed"],
        "organized_at": project["organized_at"],
        "sessions": sessions["n"] if sessions else 0,
        "last_session": sessions["last"] if sessions else None,
        "events": len(events),
        "key": sum(1 for e in events if e["importance"] >= 2),
        "milestones": sum(1 for e in events if e["importance"] == 3),
        "failed": sum(1 for e in events if e["outcome"] == "failed"),
        "unplaced": sum(1 for e in events if e["thread_id"] is None),
        "first_ts": events[0]["ts"] if events else None,
        "last_ts": events[-1]["ts"] if events else None,
        "threads": lines,
        "brief": brief,
        "refs": refs,
    }


def home(store: Store, tz_minutes: int = 0) -> dict[str, Any]:
    """tz_minutes：浏览器的 getTimezoneOffset()（UTC 减本地，分钟），按本地日期数每天的关键步骤。"""
    offset = timedelta(minutes=tz_minutes)
    projects = [project_home(store, p) for p in store.q("SELECT * FROM projects ORDER BY id")]
    today = (datetime.now(UTC) - offset).date()
    start = today - timedelta(days=ACTIVITY_DAYS - 1)
    days: dict[str, list[int]] = {
        (start + timedelta(days=i)).isoformat(): [0, 0] for i in range(ACTIVITY_DAYS)
    }
    for row in store.q("SELECT ts, importance FROM events WHERE hidden = 0 AND importance >= 2"):
        day = _local_day(row["ts"], offset)
        if day in days:
            days[day][0] += 1
            days[day][1] += row["importance"] == 3
    week = (today - timedelta(days=6)).isoformat()
    recent = [v for d, v in days.items() if d >= week]
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    active = store.one(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT project_id) AS p FROM sessions WHERE ended_at >= ?",
        (since,),
    )
    return {
        "today": today.isoformat(),
        "activity": [{"day": d, "key": v[0], "milestones": v[1]} for d, v in days.items()],
        "week": {
            "key": sum(v[0] for v in recent),
            "milestones": sum(v[1] for v in recent),
            "sessions": active["n"] if active else 0,
            "projects": active["p"] if active else 0,
        },
        "projects": projects,
    }
