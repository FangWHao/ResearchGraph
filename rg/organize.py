"""整理流程：同步会话 → 分块提取事件 → 归入研究线和分支。

模型只负责提议；编号、时间先后和“每个事件只放一处”都由程序校验。
人工改过（edited=1）或删掉（hidden=1）的内容不会被自动整理覆盖。
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rg.model import Call, ModelError, Truncated
from rg.parse import Message, NotASession, clip, parse_file
from rg.sources import SessionInfo, normalize_cwd, scan, under
from rg.store import Store, now

KINDS = ("question", "attempt", "finding", "decision", "output")
KIND_LABEL = {
    "question": "问题",
    "attempt": "尝试",
    "finding": "发现",
    "decision": "决定",
    "output": "产出",
}
ROLE_LABEL = {"user": "用户", "assistant": "Agent", "tool": "工具"}
CHUNK_CHARS = 80_000
PROMPT_MSG_CHARS = 3_000
CONTEXT_MSGS = 3
EVENT_TOKENS = 4_000
PLACE_BATCH = 300
WORKERS = 4
MISC_TITLE = "未归类"

EVENT_SYSTEM = """你在帮一位研究者整理他和编程 Agent（Claude Code 或 Codex）的对话，目标是画出研究过程的时间线。
从这段对话里找出值得放上时间线的关键步骤，只记录研究上有意义的事：
- question 问题：提出要回答的研究问题，或者遇到需要解决的难点
- attempt 尝试：开始一种方法、分析、实验或实现
- finding 发现：得到结果、数字或现象，或者发现了错误
- decision 决定：采用、放弃、暂缓、改用某个方案，或者确定参数和范围
- output 产出：完成可以交付的东西，比如图、表、报告、论文段落、模型
不要记录例行的读文件、配环境、改格式、闲聊和 Agent 的自我说明，除非它们改变了研究方向。
宁缺毋滥，普通的一段对话通常只有 0 到 5 个步骤。
每个步骤：
- title：12 个字以内的短语，写清楚对象，比如“改用 Harmony 去批次”
- summary：一两句话，说明做了什么、结果或理由；有关键数字就写上
- refs：支持这一步的消息编号，只能用本段出现的 #编号，最重要的放第一个
用对话的主要语言书写。标为 [上文] 的消息只帮助理解，不要为它们生成步骤。
用 submit 工具提交。"""

THREAD_SYSTEM = """下面是一个研究项目里按时间排列的关键步骤，来自多个对话。请把它们整理成几条研究线。
- 研究线是一个相对独立的研究方向或问题，比如“细胞注释”“生存分析”“论文图 3”。通常 2 到 8 条；内容少时 1 条也可以。
- 每条研究线的第一条分支是主线，按时间推进。
- 研究中同时试另一种做法，或者临时岔开处理一个子问题时，开一个分支：from 填它从哪个步骤分出来；如果后来结果被合并、比较后采用或者回到了主线，into 填汇入的步骤；没有汇回就填 null。
- from 和 into 可以指向别的研究线里的步骤，表示跨研究线的分出或汇合。
- 每个步骤只能放在一个分支里。
- 从别的对话 fork 出来的对话是一条新分支，from 填原对话里 fork 之前的步骤。
- 已有的研究线和分支带编号（T…、B…），要保留编号；已经放好的步骤不要移动。只为标了 ★ 的新步骤安排位置：可以放进已有分支，也可以新建分支或研究线（新建的 id 填 null）。已有分支的 events 只写新放进去的步骤。尚未汇回的已有分支可以补上 into。
- events、from、into 只写编号（例如 E12），不要抄写步骤内容。
- 研究线和分支的标题要短（12 个字以内），用步骤的主要语言。
用 submit 工具提交。"""

EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["kind", "title", "summary", "refs"],
            },
        }
    },
    "required": ["events"],
}

_NULLABLE = {"type": ["string", "null"]}
_EVENT_REF = {"type": ["string", "null"], "description": "步骤编号，例如 E12；没有就填 null"}
THREAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": _NULLABLE,
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "branches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": _NULLABLE,
                                "title": {"type": "string"},
                                "from": _EVENT_REF,
                                "into": _EVENT_REF,
                                "events": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "description": "步骤编号，例如 E12",
                                    },
                                },
                            },
                            "required": ["id", "title", "from", "into", "events"],
                        },
                    },
                },
                "required": ["id", "title", "summary", "branches"],
            },
        }
    },
    "required": ["threads"],
}

Key = tuple[str, int, int, int]


# ---------- 进度 ----------


@dataclass
class Progress:
    phase: str = "idle"  # idle / sync / consent / extract / threads / done / error
    done: int = 0
    total: int = 0
    running: bool = False
    errors: list[str] = field(default_factory=list)
    estimate: dict[str, int] | None = None
    finished_at: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, phase: str, total: int = 0) -> None:
        with self.lock:
            self.phase, self.done, self.total = phase, 0, total

    def step(self) -> None:
        with self.lock:
            self.done += 1

    def error(self, message: str) -> None:
        with self.lock:
            self.errors.append(message)

    def view(self) -> dict[str, Any]:
        with self.lock:
            return {
                "phase": self.phase,
                "done": self.done,
                "total": self.total,
                "running": self.running,
                "errors": list(self.errors[-5:]),
                "estimate": self.estimate,
                "finished_at": self.finished_at,
            }


# ---------- 工具函数 ----------


def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ref(value: Any, prefix: str) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # 模型有时会把整行步骤抄回来（“E12 10-01 …”），只取开头的编号。
        match = re.match(rf"\s*#?{prefix}?(\d+)(?!\d)", value, re.I)
        if match:
            return int(match[1])
    return None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def short_time(ts: str | None) -> str:
    if not ts:
        return "时间未知"
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return ts[:16]


def event_key(event: dict[str, Any]) -> Key:
    return (event["ts"] or "", event["session_id"], event["anchor"], event["id"])


# ---------- 会话同步 ----------


def add_sessions(store: Store, project_id: int, infos: list[SessionInfo]) -> int:
    with store.tx() as db:
        before = db.total_changes
        db.executemany(
            "INSERT OR IGNORE INTO sessions(project_id, tool, path, native_id, cwd, title, "
            "started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (project_id, i.tool, i.path, i.native_id, i.cwd, i.title, i.started_at, i.ended_at)
                for i in infos
            ],
        )
        return db.total_changes - before


def sync_project(store: Store, project_id: int, roots: list[str]) -> None:
    project = store.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise KeyError(project_id)
    if project["follow"] and project["workspace"]:
        infos = [i for i in scan(store, roots) if under(i.cwd, project["workspace"])]
        add_sessions(store, project_id, infos)
    rows = store.q(
        "SELECT * FROM sessions WHERE project_id = ? ORDER BY started_at, id", (project_id,)
    )
    for row in rows:
        sync_session(store, row)


def _fill_times(times: list[str | None], fallback: str | None) -> list[str | None]:
    filled, last = [], None
    for ts in times:
        last = ts or last
        filled.append(last)
    first = next((t for t in filled if t), fallback)
    return [t or first for t in filled]


def sync_session(store: Store, row: dict[str, Any]) -> None:
    path = Path(row["path"])
    try:
        stat = path.stat()
    except OSError:
        # 客户端清理了旧会话：已读入的消息继续保留。
        with store.tx() as db:
            db.execute("UPDATE sessions SET missing = 1 WHERE id = ?", (row["id"],))
        return
    if stat.st_size == row["parsed_size"] and stat.st_mtime == row["parsed_mtime"]:
        return
    try:
        session = parse_file(path, row["tool"] or None)
    except (NotASession, OSError, UnicodeError):
        with store.tx() as db:
            db.execute("UPDATE sessions SET missing = 1 WHERE id = ?", (row["id"],))
        return
    messages = session.messages
    times = _fill_times([m.ts for m in messages], session.started_at)
    copied, fork_of = row["copied"], row["fork_of"]
    if row["parsed_size"] < 0:
        copied, fork_of = find_fork(store, row, session.forked_from, messages)
    upto = min(max(row["extracted_upto"], copied), len(messages))
    with store.tx() as db:
        db.execute("DELETE FROM messages WHERE session_id = ?", (row["id"],))
        db.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            [(row["id"], i, m.role, times[i], m.text) for i, m in enumerate(messages)],
        )
        db.execute(
            "UPDATE sessions SET tool = ?, native_id = ?, cwd = ?, title = ?, started_at = ?, "
            "ended_at = ?, parsed_size = ?, parsed_mtime = ?, message_count = ?, "
            "extracted_upto = ?, copied = ?, fork_of = ?, missing = 0, unknown = ? WHERE id = ?",
            (
                session.tool,
                session.native_id or row["native_id"],
                normalize_cwd(session.cwd) or row["cwd"],
                session.title or row["title"],
                session.started_at or row["started_at"],
                session.ended_at or row["ended_at"],
                stat.st_size,
                stat.st_mtime,
                len(messages),
                upto,
                copied,
                fork_of,
                json.dumps(dict(session.unknown)),
                row["id"],
            ),
        )


def _common_prefix(store: Store, session_id: int, messages: list[Message]) -> int:
    theirs = store.q(
        "SELECT role, text FROM messages WHERE session_id = ? ORDER BY idx LIMIT ?",
        (session_id, len(messages)),
    )
    same = 0
    for mine, their in zip(messages, theirs, strict=False):
        if (their["role"], their["text"]) != (mine.role, mine.text):
            break
        same += 1
    return same


def find_fork(
    store: Store, row: dict[str, Any], forked_from: str | None, messages: list[Message]
) -> tuple[int, int | None]:
    """fork 出来的会话是原会话的一条新分支。

    返回（从原会话复制来的消息数, 原会话 id）。复制部分不重复提取；分出点在归类时接上。
    Codex 在 session_meta 里写明 forked_from_id；Claude 没有标记，只能看开头是否逐条相同。
    """
    if forked_from:
        parent = store.one(
            "SELECT id FROM sessions WHERE project_id = ? AND native_id = ? AND id != ?",
            (row["project_id"], forked_from, row["id"]),
        )
        if parent:
            return _common_prefix(store, parent["id"], messages), parent["id"]
    if len(messages) < 2:
        return 0, None
    others = store.q(
        "SELECT m.session_id FROM messages m JOIN sessions s ON s.id = m.session_id "
        "WHERE s.project_id = ? AND s.id != ? AND m.idx = 0 AND m.role = ? AND m.text = ?",
        (row["project_id"], row["id"], messages[0].role, messages[0].text),
    )
    best, parent_id = 0, None
    for other in others:
        same = _common_prefix(store, other["session_id"], messages)
        if same > best:
            best, parent_id = same, other["session_id"]
    return (best, parent_id) if best >= 2 else (0, None)


# ---------- 提取事件 ----------


def _sizes(store: Store, session_id: int) -> list[int]:
    rows = store.q(
        "SELECT LENGTH(text) AS n FROM messages WHERE session_id = ? ORDER BY idx", (session_id,)
    )
    return [min(r["n"], PROMPT_MSG_CHARS) + 40 for r in rows]


def chunk_ranges(sizes: list[int], start: int, limit: int = CHUNK_CHARS) -> list[tuple[int, int]]:
    ranges, begin, total = [], start, 0
    for i in range(start, len(sizes)):
        if total + sizes[i] > limit and i > begin:
            ranges.append((begin, i))
            begin, total = i, 0
        total += sizes[i]
    if begin < len(sizes):
        ranges.append((begin, len(sizes)))
    return ranges


def estimate(store: Store, project_id: int) -> dict[str, int]:
    sessions = chars = calls = 0
    for row in store.q(
        "SELECT id, extracted_upto FROM sessions WHERE project_id = ?", (project_id,)
    ):
        sizes = _sizes(store, row["id"])
        ranges = chunk_ranges(sizes, row["extracted_upto"])
        if ranges:
            sessions += 1
            calls += len(ranges)
            chars += sum(sizes[row["extracted_upto"] :])
    return {"sessions": sessions, "chars": chars, "calls": calls + (1 if calls else 0)}


def render_chunk(session: dict[str, Any], messages: list[dict[str, Any]], a: int, b: int) -> str:
    tool = "Claude Code" if session["tool"] == "claude" else "Codex"
    lines = [
        f"会话：{session['title'] or '（无标题）'}（{tool}，工作目录 {session['cwd'] or '未知'}）",
        "",
    ]
    for m in messages[max(0, a - CONTEXT_MSGS) : a]:
        lines.append(
            f"[上文] #{m['idx']} {ROLE_LABEL.get(m['role'], m['role'])}：{clip(m['text'], 500)}"
        )
    if a > 0:
        lines.append("")
    for m in messages[a:b]:
        role = ROLE_LABEL.get(m["role"], m["role"])
        lines.append(
            f"#{m['idx']} [{short_time(m['ts'])}] {role}：{clip(m['text'], PROMPT_MSG_CHARS)}"
        )
    return "\n".join(lines)


def validate_events(
    output: dict[str, Any], messages: list[dict[str, Any]], a: int, b: int
) -> list[dict[str, Any]]:
    events = []
    for item in _list(output.get("events")):
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        title = _clean(item.get("title"), 40)
        refs: list[int] = []
        for value in _list(item.get("refs")):
            ref = _ref(value, "")
            if ref is not None and a <= ref < b and ref not in refs:
                refs.append(ref)
        if not title or not refs:
            continue
        anchor = min(refs)
        events.append(
            {
                "kind": item["kind"],
                "title": title,
                "summary": _clean(item.get("summary"), 400),
                "refs": refs,
                "anchor": anchor,
                "ts": messages[anchor]["ts"],
            }
        )
    return events


def extract_range(
    call: Call, session: dict[str, Any], messages: list[dict[str, Any]], a: int, b: int
) -> list[dict[str, Any]]:
    try:
        output = call(
            EVENT_SYSTEM, render_chunk(session, messages, a, b), EVENT_SCHEMA, EVENT_TOKENS
        )
    except Truncated:
        if b - a <= 1:
            raise
        mid = (a + b) // 2
        return extract_range(call, session, messages, a, mid) + extract_range(
            call, session, messages, mid, b
        )
    return validate_events(output, messages, a, b)


def extract_session(store: Store, call: Call, session: dict[str, Any], progress: Progress) -> None:
    messages = store.q(
        "SELECT idx, role, ts, text FROM messages WHERE session_id = ? ORDER BY idx",
        (session["id"],),
    )
    sizes = [min(len(m["text"]), PROMPT_MSG_CHARS) + 40 for m in messages]
    retained = [
        set(json.loads(r["refs"]))
        for r in store.q(
            "SELECT refs FROM events WHERE session_id = ? AND (edited = 1 OR hidden = 1)",
            (session["id"],),
        )
    ]
    for a, b in chunk_ranges(sizes, session["extracted_upto"]):
        events = extract_range(call, session, messages, a, b)
        fresh = [e for e in events if not any(set(e["refs"]) & kept for kept in retained)]
        with store.tx() as db:
            db.executemany(
                "INSERT INTO events(project_id, session_id, kind, title, summary, ts, anchor, refs) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        session["project_id"],
                        session["id"],
                        e["kind"],
                        e["title"],
                        e["summary"],
                        e["ts"],
                        e["anchor"],
                        json.dumps(e["refs"]),
                    )
                    for e in fresh
                ],
            )
            db.execute("UPDATE sessions SET extracted_upto = ? WHERE id = ?", (b, session["id"]))
        progress.step()


def extract(store: Store, call: Call, project_id: int, progress: Progress) -> None:
    sessions = store.q("SELECT * FROM sessions WHERE project_id = ?", (project_id,))
    pending = []
    total = 0
    for s in sessions:
        count = len(chunk_ranges(_sizes(store, s["id"]), s["extracted_upto"]))
        if count:
            pending.append(s)
            total += count
    progress.set("extract", total)

    def run(session: dict[str, Any]) -> None:
        try:
            extract_session(store, call, session, progress)
        except ModelError as error:
            progress.error(f"「{session['title'] or session['id']}」提取失败：{error}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, pending))


# ---------- 归入研究线 ----------


def _visible_events(store: Store, project_id: int) -> list[dict[str, Any]]:
    return store.q(
        "SELECT id, session_id, branch_id, kind, title, summary, ts, anchor FROM events "
        "WHERE project_id = ? AND hidden = 0",
        (project_id,),
    )


def render_structure(store: Store, project_id: int, new_ids: set[int]) -> str:
    events = sorted(_visible_events(store, project_id), key=event_key)
    sessions = store.q(
        "SELECT id, tool, title, started_at, fork_of FROM sessions WHERE project_id = ? "
        "ORDER BY started_at",
        (project_id,),
    )
    threads = store.q("SELECT * FROM threads WHERE project_id = ? ORDER BY id", (project_id,))
    branches = store.q(
        "SELECT b.* FROM branches b JOIN threads t ON t.id = b.thread_id "
        "WHERE t.project_id = ? ORDER BY b.position, b.id",
        (project_id,),
    )
    members: dict[int, list[int]] = defaultdict(list)
    for e in events:
        if e["branch_id"] is not None and e["id"] not in new_ids:
            members[e["branch_id"]].append(e["id"])
    lines = ["对话："]
    for s in sessions:
        tool = "Claude Code" if s["tool"] == "claude" else "Codex"
        fork = f"（从 S{s['fork_of']} fork 出来）" if s["fork_of"] else ""
        lines.append(
            f"S{s['id']} {tool}「{s['title'] or '无标题'}」{short_time(s['started_at'])}{fork}"
        )
    lines += ["", "已有研究线："]
    if not threads:
        lines.append("（还没有研究线）")
    for t in threads:
        lines.append(f"T{t['id']}「{t['title']}」{t['summary']}")
        for b in (b for b in branches if b["thread_id"] == t["id"]):
            source = f"E{b['from_event']}" if b["from_event"] else "—"
            target = f"E{b['into_event']}" if b["into_event"] else "—"
            ids = " ".join(f"E{i}" for i in members[b["id"]]) or "（空）"
            lines.append(
                f"  B{b['id']}「{b['title'] or '主线'}」从 {source} 分出，汇入 {target}：{ids}"
            )
    detailed = len(events) <= 600
    lines += ["", "步骤（按时间排列；★ 是需要安排位置的新步骤）："]
    for e in events:
        new = e["id"] in new_ids
        text = f"E{e['id']} {short_time(e['ts'])} S{e['session_id']} {KIND_LABEL[e['kind']]}｜{e['title']}"
        if new or detailed:
            text += f"｜{e['summary']}"
        lines.append(("★ " if new else "  ") + text)
    return "\n".join(lines)


def _link(value: Any, members: list[int], key: dict[int, Key], after: bool) -> int | None:
    eid = _ref(value, "E")
    if eid is None or eid not in key or eid in members or not members:
        return None
    if after:
        return eid if key[eid] > max(key[m] for m in members) else None
    return eid if key[eid] < min(key[m] for m in members) else None


def apply_placement(
    store: Store, project_id: int, output: dict[str, Any], new_ids: set[int]
) -> None:
    events = {e["id"]: e for e in _visible_events(store, project_id)}
    new_ids = {i for i in new_ids if i in events}
    key = {i: event_key(e) for i, e in events.items()}
    thread_ids = {
        t["id"] for t in store.q("SELECT id FROM threads WHERE project_id = ?", (project_id,))
    }
    branches = {
        b["id"]: b
        for b in store.q(
            "SELECT b.* FROM branches b JOIN threads t ON t.id = b.thread_id WHERE t.project_id = ?",
            (project_id,),
        )
    }
    members: dict[int, list[int]] = defaultdict(list)
    for e in events.values():
        if e["branch_id"] is not None and e["id"] not in new_ids:
            members[e["branch_id"]].append(e["id"])
    assigned: dict[int, int] = {}
    with store.tx() as db:

        def new_branch(tid: int, title: str, source: int | None, target: int | None) -> int:
            position = db.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?", (tid,)
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO branches(thread_id, title, position, from_event, into_event) "
                "VALUES (?, ?, ?, ?, ?)",
                (tid, title, position, source, target),
            )
            bid = int(cursor.lastrowid or 0)
            branches[bid] = {"id": bid, "thread_id": tid, "into_event": target, "edited": 0}
            return bid

        def new_thread(title: str, summary: str) -> int:
            cursor = db.execute(
                "INSERT INTO threads(project_id, title, summary) VALUES (?, ?, ?)",
                (project_id, title or MISC_TITLE, summary),
            )
            thread_ids.add(int(cursor.lastrowid or 0))
            return int(cursor.lastrowid or 0)

        for t in _list(output.get("threads")):
            if not isinstance(t, dict):
                continue
            tid = _ref(t.get("id"), "T")
            tid = tid if tid in thread_ids else None
            for b in _list(t.get("branches")):
                if not isinstance(b, dict):
                    continue
                picked = {_ref(v, "E") for v in _list(b.get("events"))}
                evs = sorted(
                    (e for e in picked if e in new_ids and e not in assigned), key=key.__getitem__
                )
                bid = _ref(b.get("id"), "B")
                if bid is not None and bid in branches:
                    row = branches[bid]
                    if row["into_event"] is None and not row["edited"]:
                        target = _link(b.get("into"), members[bid] + evs, key, after=True)
                        if target is not None:
                            db.execute(
                                "UPDATE branches SET into_event = ? WHERE id = ?", (target, bid)
                            )
                            row["into_event"] = target
                elif evs:
                    if tid is None:
                        tid = new_thread(_clean(t.get("title"), 30), _clean(t.get("summary"), 200))
                    bid = new_branch(
                        tid,
                        _clean(b.get("title"), 30),
                        _link(b.get("from"), evs, key, after=False),
                        _link(b.get("into"), evs, key, after=True),
                    )
                else:
                    continue
                for e in evs:
                    assigned[e] = bid
                    members[bid].append(e)

        misc: list[int] = []

        def fallback(eid: int) -> int:
            # 模型漏掉的事件：跟着同一会话里最近的事件走；都没有就放进“未归类”。
            placed: dict[int, int] = {}
            for i, other in events.items():
                branch = assigned.get(i, other["branch_id"])
                if other["session_id"] == events[eid]["session_id"] and branch is not None:
                    placed[i] = branch
            before = [i for i in placed if key[i] < key[eid]]
            after = [i for i in placed if key[i] > key[eid]]
            if before:
                return placed[max(before, key=key.__getitem__)]
            if after:
                return placed[min(after, key=key.__getitem__)]
            if not misc:
                misc.append(new_branch(new_thread(MISC_TITLE, ""), "", None, None))
            return misc[0]

        for eid in sorted(new_ids - assigned.keys(), key=key.__getitem__):
            assigned[eid] = fallback(eid)
        db.executemany(
            "UPDATE events SET branch_id = ? WHERE id = ?", [(b, e) for e, b in assigned.items()]
        )
        cleanup(db, project_id)


def cleanup(db: Any, project_id: int) -> None:
    """删掉没有事件的自动分支和研究线。人工改过的保留。"""
    db.execute(
        "DELETE FROM branches WHERE edited = 0 AND thread_id IN "
        "(SELECT id FROM threads WHERE project_id = ?) AND id NOT IN "
        "(SELECT branch_id FROM events WHERE branch_id IS NOT NULL AND hidden = 0)",
        (project_id,),
    )
    db.execute(
        "DELETE FROM threads WHERE project_id = ? AND edited = 0 AND id NOT IN "
        "(SELECT thread_id FROM branches)",
        (project_id,),
    )
    db.execute(
        "UPDATE sessions SET fork_branch = NULL WHERE project_id = ? AND fork_branch NOT IN "
        "(SELECT id FROM branches)",
        (project_id,),
    )


def _unplaced(store: Store, project_id: int) -> list[dict[str, Any]]:
    return sorted(
        (e for e in _visible_events(store, project_id) if e["branch_id"] is None), key=event_key
    )


def fork_point(store: Store, session: dict[str, Any]) -> dict[str, Any] | None:
    """原会话里 fork 之前的最后一个事件。"""
    return store.one(
        "SELECT id, branch_id FROM events WHERE session_id = ? AND hidden = 0 AND anchor < ? "
        "ORDER BY anchor DESC, id DESC LIMIT 1",
        (session["fork_of"], session["copied"]),
    )


def attach_forks(store: Store, project_id: int) -> set[int]:
    """fork 会话的新事件放进一条从分出点开始的新分支。

    返回需要等待的事件：分出点本身还没放进研究线，要等模型先放好它。
    原会话在分出前没有事件时，fork 的事件交给模型安排。
    """
    waiting: set[int] = set()
    forks = store.q(
        "SELECT * FROM sessions WHERE project_id = ? AND fork_of IS NOT NULL ORDER BY started_at, id",
        (project_id,),
    )
    for session in forks:
        pending = [
            r["id"]
            for r in store.q(
                "SELECT id FROM events WHERE session_id = ? AND hidden = 0 AND branch_id IS NULL",
                (session["id"],),
            )
        ]
        if not pending:
            continue
        branch = store.one(
            "SELECT b.id FROM branches b JOIN threads t ON t.id = b.thread_id "
            "WHERE b.id = ? AND t.project_id = ?",
            (session["fork_branch"], project_id),
        )
        with store.tx() as db:
            if branch is None:
                point = fork_point(store, session)
                if point is None:
                    continue
                if point["branch_id"] is None:
                    waiting.update(pending)
                    continue
                thread_id = db.execute(
                    "SELECT thread_id FROM branches WHERE id = ?", (point["branch_id"],)
                ).fetchone()[0]
                position = db.execute(
                    "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()[0]
                branch_id = db.execute(
                    "INSERT INTO branches(thread_id, title, position, from_event) VALUES (?, ?, ?, ?)",
                    (thread_id, _clean(session["title"] or "fork", 30), position, point["id"]),
                ).lastrowid
                db.execute(
                    "UPDATE sessions SET fork_branch = ? WHERE id = ?", (branch_id, session["id"])
                )
            else:
                branch_id = branch["id"]
            db.executemany(
                "UPDATE events SET branch_id = ? WHERE id = ?", [(branch_id, e) for e in pending]
            )
    return waiting


def _place_batches(
    store: Store, call: Call, project_id: int, progress: Progress, exclude: set[int]
) -> None:
    def pending() -> list[dict[str, Any]]:
        return [e for e in _unplaced(store, project_id) if e["id"] not in exclude]

    todo = pending()
    size = PLACE_BATCH
    if todo:
        progress.set("threads", (len(todo) + size - 1) // size)
    while todo:
        batch = {e["id"] for e in todo[:size]}
        tokens = min(32_000, 2_000 + 16 * len(batch))
        try:
            output = call(
                THREAD_SYSTEM, render_structure(store, project_id, batch), THREAD_SCHEMA, tokens
            )
        except Truncated:
            if size <= 20:
                raise
            size //= 2
            continue
        apply_placement(store, project_id, output, batch)
        progress.step()
        todo = pending()


def place(store: Store, call: Call, project_id: int, progress: Progress) -> None:
    waiting = attach_forks(store, project_id)
    _place_batches(store, call, project_id, progress, exclude=waiting)
    attach_forks(store, project_id)
    _place_batches(store, call, project_id, progress, exclude=set())


# ---------- 重新整理 ----------


def reset(store: Store, project_id: int) -> None:
    with store.tx() as db:
        db.execute(
            "DELETE FROM events WHERE project_id = ? AND edited = 0 AND hidden = 0", (project_id,)
        )
        db.execute(
            "DELETE FROM branches WHERE edited = 0 AND thread_id IN "
            "(SELECT id FROM threads WHERE project_id = ?)",
            (project_id,),
        )
        db.execute(
            "DELETE FROM threads WHERE project_id = ? AND edited = 0 AND id NOT IN "
            "(SELECT thread_id FROM branches)",
            (project_id,),
        )
        db.execute(
            "UPDATE sessions SET extracted_upto = copied, fork_branch = NULL WHERE project_id = ?",
            (project_id,),
        )


def organize(
    store: Store,
    call: Call | None,
    project_id: int,
    roots: list[str],
    progress: Progress,
    full: bool = False,
) -> None:
    progress.set("sync")
    if full:
        reset(store, project_id)
    sync_project(store, project_id, roots)
    project = store.one("SELECT model_allowed FROM projects WHERE id = ?", (project_id,))
    progress.estimate = estimate(store, project_id)
    if not project or not project["model_allowed"]:
        progress.set("consent")
        return
    if call is None:
        progress.error("还没有设置 API Key")
        progress.set("error")
        return
    extract(store, call, project_id, progress)
    try:
        place(store, call, project_id, progress)
    except ModelError as error:
        progress.error(f"归入研究线失败：{error}")
    with store.tx() as db:
        db.execute("UPDATE projects SET organized_at = ? WHERE id = ?", (now(), project_id))
    progress.estimate = estimate(store, project_id)
    progress.set("error" if progress.errors else "done")


class Jobs:
    """每个项目同一时间只跑一个整理任务，在后台线程里执行。"""

    def __init__(
        self,
        store: Store,
        roots: Callable[[], list[str]],
        model: Callable[[], Call | None],
    ):
        self.store = store
        self.roots = roots
        self.model = model
        self.progress: dict[int, Progress] = {}
        self.lock = threading.Lock()

    def status(self, project_id: int) -> dict[str, Any]:
        with self.lock:
            progress = self.progress.get(project_id)
        return progress.view() if progress else Progress().view()

    def start(self, project_id: int, full: bool = False) -> bool:
        with self.lock:
            current = self.progress.get(project_id)
            if current and current.running:
                return False
            progress = Progress(running=True)
            self.progress[project_id] = progress
        thread = threading.Thread(target=self._run, args=(project_id, full, progress), daemon=True)
        thread.start()
        return True

    def _run(self, project_id: int, full: bool, progress: Progress) -> None:
        try:
            organize(self.store, self.model(), project_id, self.roots(), progress, full)
        except Exception as error:  # 后台任务的任何失败都要显示给用户，而不是静默结束。
            progress.error(f"整理中断：{type(error).__name__}: {error}")
            progress.set("error")
        finally:
            with progress.lock:
                progress.running = False
                progress.finished_at = now()
