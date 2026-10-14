"""整理流程：同步会话 → 分块提取事件 → 归入研究线和分支。

模型只负责提议；编号、时间先后和“每个事件只放一处”都由程序校验。
人工改过（edited=1）或删掉（hidden=1）的内容不会被自动整理覆盖。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rg.model import AuthError, Call, ModelError, Truncated, metered
from rg.parse import PARSER_VERSION, Message, NotASession, clip, parse_file
from rg.sources import SessionInfo, normalize_cwd, scan, under
from rg.store import Store, now
from rg.usage import recorder

KINDS = ("attempt", "decision", "result")
KIND_LABEL = {
    "attempt": "尝试",
    "decision": "决定",
    "result": "结果",
    "question": "问题",  # 旧版本提取的，不再产生
}
OUTCOMES = ("success", "failed", "open")
OUTCOME_LABEL = {"success": "成功", "failed": "失败", "open": "未定"}
IMPORTANCE = (1, 2, 3)  # 1 细节，2 关键，3 里程碑（复审研究线时挑出来，提取时不给）
STATUSES = ("active", "done", "dropped")
MILESTONE_MAX = 15
ROLE_LABEL = {"user": "用户", "assistant": "Agent", "tool": "工具"}
CHUNK_CHARS = 80_000
PROMPT_MSG_CHARS = 3_000
CONTEXT_MSGS = 3
EVENT_TOKENS = 4_000
PLACE_BATCH = 300
WORKERS = 6
MISC_TITLE = "未归类"

EVENT_SYSTEM = """你在帮一位研究者整理他和编程 Agent（Claude Code 或 Codex）的对话，目标是画出项目推进的时间线。
他回头看时只关心三件事：试了什么、成功还是失败；做了什么决定、为什么；得到了什么结果。只记录这三类步骤：
- attempt 尝试：为了回答一个问题或验证一种做法而动手试的方法、分析、实验或实现，事先不确定能不能行。outcome 写结果：success 成功（行得通、效果达到预期、被采用），failed 失败（走不通、效果不好、被放弃），open 还没有结论。尝试和它的结果在这段对话里都出现时，合成一个步骤，结果写进 summary。按部就班做完的任务不是尝试。
- decision 决定：采用、放弃、改用、暂缓某个方案，确定参数、范围、主线或结论的说法。summary 写清楚理由。
- result 结果：拿到手的关键数字、结论，或者重要的成品（论文初稿、主图定稿、跑通的完整流程、交付的软件版本）。已经写进某个尝试的结果不要再单独记。
importance 写重要性：2 关键（值得在组会上提一句的尝试、决定和结果），1 细节（小调整、局部修补、例行产出，以及下面这些辅助性工作）。
辅助性工作——整理目录、归档、备份、打包上传、写说明文档或交接包、给别的模型准备材料、复查核对、图的字号裁切配色图例等版式修补——一般不记；如果整段对话都在做这类事，最多记一个总的步骤，importance 给 1。
不要记录：
- 提问、讨论和请 Agent 评估本身，除非它直接带来了一个决定
- 让 Agent 读代码、盘点项目、解释结构、汇报进度、检查状态；复查核对本身也不记，查出的问题带来了决定的，记那个决定
- 配环境、装依赖、修报错、调路径、改格式、重命名，除非它改变了研究做法（比如因为显存不够改用小模型）
- 发现并修好的 bug 不是失败的尝试。只有改变了结果或结论的 bug 才记，记成 result，写清修复前后的差别
- Agent 的自我说明和闲聊
如果这段对话的目标本身就是这类事（比如专门排查电脑磁盘占用、专门开发一个工具），它的关键尝试、决定和结论照样要记，按正常标准给重要性。
宁缺毋滥：几十条消息的对话通常 0 到 3 个步骤，很长的一段也很少超过 5 个。同一件事反复调整时，只记最后的做法和结果。
每个步骤：
- title：14 个字以内，写清对象和结论，比如“Harmony 去批次可用”“改用 atlas_v3 参考”“放弃 BBKNN”
- summary：一两句话，不超过 60 个字。只写最关键的数字和理由，不要罗列文件名、路径和步骤
- refs：支持这一步的消息编号，只能用本段出现的 #编号，最重要的放第一个
用对话的主要语言书写。标为 [上文] 的消息只帮助理解，不要为它们生成步骤。
用 submit 工具提交。"""

THREAD_SYSTEM = """下面是一个研究项目里按时间排列的关键步骤，来自多个对话。请为标了 ★ 的新步骤安排位置，把项目整理成几条研究线。
- 研究线是一个相对独立的研究方向或问题，比如“细胞注释”“生存分析”“论文图 3”。
- 按研究问题划分，不按对话划分：同一个方向常常跨好几个对话、好几天，一个对话也可能涉及几条线。不要每个对话单开一条研究线。
- 按研究问题判断每个 ★ 步骤属于哪个方向：属于已有研究线的就放进去（主线或分支）；出现了新的研究方向、并且这一批里至少有 5 个步骤属于它，就新建研究线，不要把不同方向的步骤堆进同一条线的主线。只有几个步骤的小方向放进最相关的研究线当分支。研究线多了之后程序会再合并，这里宁可分得细一点。
- 工程环境、工具开发、目录整理、文档报告这些辅助工作放进同一条研究线，不要拆成好几条。
- 每条研究线的第一条分支是主线，按时间推进，保留最后被采用的路线。研究中同时试另一种做法、临时岔开处理一个子问题，或者几个连续的步骤在试一条后来放弃的路时，开一个分支：from 是它从哪个步骤分出来；后来结果被合并、比较后采用或者回到了主线，into 是汇入的步骤，没有汇回就是 null。from 和 into 可以指向别的研究线里的步骤。单个失败的尝试留在原处即可。
- 已有的研究线（T…）和分支（B…）保持不变，已经放好的步骤不要动，也不要再列出来。
输出：
- placements：每个 ★ 步骤写一条 {event, branch}。branch 写放进哪里：已有分支的 B 编号；已有研究线的 T 编号（放进它的主线）；或者下面新建的研究线、分支的 key。
- new_threads：要新建的研究线 {key, title, summary}，key 用 N1、N2…。新研究线的主线不用建分支，把步骤直接放进它的 N key。
- new_branches：要新建的分支 {key, thread, title, from, into}，key 用 NB1、NB2…，thread 写所属研究线（T 编号或 N key）。
- merges：已有分支后来汇回了，写 {branch, into}。
- 只写 E12、T3、B7 这样的编号，不要抄写步骤内容。研究线和分支的标题要短（12 个字以内），用步骤的主要语言。
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
                    "outcome": {
                        "type": ["string", "null"],
                        "enum": [*OUTCOMES, None],
                        "description": "只有 attempt 填，其余填 null",
                    },
                    "importance": {"type": "integer", "enum": [1, 2]},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["kind", "outcome", "importance", "title", "summary", "refs"],
            },
        }
    },
    "required": ["events"],
}

_EVENT_REF = {"type": ["string", "null"], "description": "步骤编号，例如 E12；没有就填 null"}


def _items(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "object", "properties": properties, "required": list(properties)},
    }


THREAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "placements": _items(
            {
                "event": {"type": "string", "description": "步骤编号，例如 E12"},
                "branch": {"type": "string", "description": "B 编号、T 编号或新建的 key"},
            }
        ),
        "new_threads": _items(
            {
                "key": {"type": "string", "description": "N1、N2…"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
            }
        ),
        "new_branches": _items(
            {
                "key": {"type": "string", "description": "NB1、NB2…"},
                "thread": {"type": "string", "description": "T 编号或新建研究线的 key"},
                "title": {"type": "string"},
                "from": _EVENT_REF,
                "into": _EVENT_REF,
            }
        ),
        "merges": _items(
            {
                "branch": {"type": "string", "description": "已有分支的 B 编号"},
                "into": {"type": "string", "description": "汇入的步骤编号"},
            }
        ),
    },
    "required": ["placements", "new_threads", "new_branches", "merges"],
}

Key = tuple[str, int, int, int]


# ---------- 进度 ----------


@dataclass
class Progress:
    # idle / sync / consent / confirm / extract / threads / consolidate / review / done / error
    phase: str = "idle"
    done: int = 0
    total: int = 0
    running: bool = False
    auto: bool = False
    errors: list[str] = field(default_factory=list)
    estimate: dict[str, int] | None = None
    finished_at: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, phase: str, total: int = 0) -> None:
        with self.lock:
            self.phase, self.done, self.total = phase, 0, total

    def step(self, remaining: int = 0) -> None:
        """remaining：还要再做几步。比原先估计的多（比如归类时补问漏掉的事件）就把总数加上去。"""
        with self.lock:
            self.done += 1
            self.total = max(self.total, self.done + remaining)

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
                "auto": self.auto,
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


def step_label(event: dict[str, Any]) -> str:
    label = KIND_LABEL.get(event["kind"]) or str(event["kind"])
    if event.get("outcome") in OUTCOME_LABEL:
        label += f"·{OUTCOME_LABEL[event['outcome']]}"
    return label + ("·里程碑" if event.get("importance") == 3 else "")


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


def followed(store: Store, project: dict[str, Any], roots: list[str]) -> list[SessionInfo]:
    """跟随的工作目录里的会话，去掉用户移出过项目的。"""
    if not (project["follow"] and project["workspace"]):
        return []
    excluded = {
        r["path"]
        for r in store.q("SELECT path FROM excluded WHERE project_id = ?", (project["id"],))
    }
    return [
        i
        for i in scan(store, roots)
        if under(i.cwd, project["workspace"]) and i.path not in excluded
    ]


def sync_project(store: Store, project_id: int, roots: list[str]) -> None:
    project = store.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise KeyError(project_id)
    add_sessions(store, project_id, followed(store, project, roots))
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
    current = row["parser"] == PARSER_VERSION
    if current and stat.st_size == row["parsed_size"] and stat.st_mtime == row["parsed_mtime"]:
        return
    try:
        session = parse_file(path, row["tool"] or None)
    except (NotASession, OSError, UnicodeError):
        # 记下大小和时间：文件不再变化时，自动更新不会反复来读它。
        with store.tx() as db:
            db.execute(
                "UPDATE sessions SET missing = 1, parsed_size = ?, parsed_mtime = ? WHERE id = ?",
                (stat.st_size, stat.st_mtime, row["id"]),
            )
        return
    messages = session.messages
    times = _fill_times([m.ts for m in messages], session.started_at)
    copied, fork_of = row["copied"], row["fork_of"]
    if row["parsed_size"] < 0 or row["message_count"] == 0:
        copied, fork_of = find_fork(store, row, session.forked_from, messages)
    upto = min(max(row["extracted_upto"], copied), len(messages))
    reread = not current and row["parsed_size"] >= 0 and _changed(store, row["id"], messages)
    with store.tx() as db:
        if reread:
            # 解析规则变了、消息编号可能移动：自动提取的事件作废，这个会话从头再提取。
            # 人工改过或删掉的事件保留（它们引用的编号可能已经对不上）。
            db.execute(
                "DELETE FROM events WHERE session_id = ? AND edited = 0 AND hidden = 0",
                (row["id"],),
            )
            upto = copied
        db.execute("DELETE FROM messages WHERE session_id = ?", (row["id"],))
        db.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            [(row["id"], i, m.role, times[i], m.text) for i, m in enumerate(messages)],
        )
        db.execute(
            "UPDATE sessions SET tool = ?, native_id = ?, cwd = ?, title = ?, started_at = ?, "
            "ended_at = ?, parsed_size = ?, parsed_mtime = ?, message_count = ?, "
            "extracted_upto = ?, copied = ?, fork_of = ?, missing = 0, unknown = ?, parser = ? "
            "WHERE id = ?",
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
                PARSER_VERSION,
                row["id"],
            ),
        )
        if reread:
            cleanup(db, row["project_id"])


def _changed(store: Store, session_id: int, messages: list[Message]) -> bool:
    stored = store.q(
        "SELECT role, text FROM messages WHERE session_id = ? ORDER BY idx", (session_id,)
    )
    return [(r["role"], r["text"]) for r in stored] != [(m.role, m.text) for m in messages]


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


def pending(store: Store, project_id: int, roots: list[str]) -> dict[str, Any]:
    """有没有新内容。只看文件大小和修改时间，不解析会话，可以频繁调用。

    changed：已导入的会话文件变了；new：跟随的工作目录里出现了新会话；
    chars：已经读入但还没提取的字数（比如上次整理中途出错）；
    unplaced：已经提取、还没归入研究线的事件数（比如归类时出错）；
    quiet：最近一次文件改动距今的秒数，没有改动时为 None。
    """
    project = store.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise KeyError(project_id)
    rows = store.q(
        "SELECT path, parsed_size, parsed_mtime FROM sessions WHERE project_id = ?", (project_id,)
    )
    known = {r["path"] for r in rows}
    changed, new, latest = 0, 0, 0.0
    for row in rows:
        try:
            stat = Path(row["path"]).stat()
        except OSError:
            continue
        if row["parsed_size"] < 0:  # 刚加进项目、还没读过的会话
            new += 1
            latest = max(latest, stat.st_mtime)
        elif (stat.st_size, stat.st_mtime) != (row["parsed_size"], row["parsed_mtime"]):
            changed += 1
            latest = max(latest, stat.st_mtime)
    for info in followed(store, project, roots):
        if info.path not in known:
            new += 1
            latest = max(latest, info.mtime)
    return {
        "changed": changed,
        "new": new,
        "chars": estimate(store, project_id)["chars"],
        "unplaced": len(_unplaced(store, project_id)),
        "quiet": max(0, round(time.time() - latest)) if latest else None,
    }


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
        outcome = item.get("outcome")
        if item["kind"] != "attempt":
            outcome = None
        elif outcome not in OUTCOMES:
            outcome = "open"
        importance = item.get("importance")
        if isinstance(importance, bool) or importance not in (1, 2):
            importance = 2  # 里程碑只在复审研究线时给
        events.append(
            {
                "kind": item["kind"],
                "outcome": outcome,
                "importance": importance,
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


def extract_session(
    store: Store,
    call: Call,
    session: dict[str, Any],
    progress: Progress,
    stop: threading.Event | None = None,
) -> None:
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
        if stop is not None and stop.is_set():
            return
        events = extract_range(call, session, messages, a, b)
        fresh = [e for e in events if not any(set(e["refs"]) & kept for kept in retained)]
        with store.tx() as db:
            db.executemany(
                "INSERT INTO events(project_id, session_id, kind, outcome, importance, title, "
                "summary, ts, anchor, refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        session["project_id"],
                        session["id"],
                        e["kind"],
                        e["outcome"],
                        e["importance"],
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

    stop = threading.Event()
    failure: list[AuthError] = []

    def run(session: dict[str, Any]) -> None:
        if stop.is_set():
            return
        try:
            extract_session(store, call, session, progress, stop)
        except AuthError as error:
            # Key 不对时每个会话都会失败，只报一次，其余会话不再发请求。
            failure.append(error)
            stop.set()
        except ModelError as error:
            progress.error(f"「{session['title'] or session['id']}」提取失败：{error}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, pending))
    if failure:
        raise failure[0]


# ---------- 归入研究线 ----------


def _visible_events(store: Store, project_id: int) -> list[dict[str, Any]]:
    return store.q(
        "SELECT id, session_id, branch_id, kind, outcome, importance, title, summary, ts, anchor "
        "FROM events WHERE project_id = ? AND hidden = 0",
        (project_id,),
    )


def render_structure(store: Store, project_id: int, new_ids: set[int], retry: bool = False) -> str:
    # 不写步骤来自哪个对话：写了以后模型几乎总是一个对话一条研究线（本机实测 25 个对话分成
    # 15 条线，每个对话只落在一条线上）。fork 由程序接上，也不需要对话信息。
    # 只列已经放好的步骤和这一批新步骤。还没轮到的步骤不能列出来：它们不在任何分支里，
    # 模型会顺手把它们全部安排一遍，输出超长被截断（2490 个事件的项目上实际发生过）。
    events = sorted(
        (
            e
            for e in _visible_events(store, project_id)
            if e["branch_id"] is not None or e["id"] in new_ids
        ),
        key=event_key,
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
    lines = []
    if retry:
        # 补问：请求和上一次不同，不会从缓存里拿到同一个结果
        lines += ["注意：上一轮漏掉了一些 ★ 步骤。这次请为每一个 ★ 步骤都写一条 placement。", ""]
    lines.append("已有研究线：")
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
        text = f"E{e['id']} {short_time(e['ts'])} {step_label(e)}｜{e['title']}"
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


def _code(value: Any) -> str:
    return str(value or "").strip().upper()


def apply_placement(
    store: Store,
    project_id: int,
    output: dict[str, Any],
    new_ids: set[int],
    fallback: set[int] | None = None,
) -> None:
    """按模型的安排放新事件。编号、归属和时间先后都由程序校验，不合规的部分丢掉。
    fallback：模型漏掉时由程序兜底安排的事件；不在其中的漏掉事件留着，下一批再问一次。
    默认全部兜底。"""
    events = {e["id"]: e for e in _visible_events(store, project_id)}
    new_ids = {i for i in new_ids if i in events}
    key = {i: event_key(e) for i, e in events.items()}
    threads = {
        t["id"]: t for t in store.q("SELECT * FROM threads WHERE project_id = ?", (project_id,))
    }
    branches = {
        b["id"]: b
        for b in store.q(
            "SELECT b.* FROM branches b JOIN threads t ON t.id = b.thread_id "
            "WHERE t.project_id = ? ORDER BY b.position, b.id",
            (project_id,),
        )
    }
    main_of: dict[int, int] = {}
    for b in branches.values():
        main_of.setdefault(b["thread_id"], b["id"])
    members: dict[int, list[int]] = defaultdict(list)
    for e in events.values():
        if e["branch_id"] is not None and e["id"] not in new_ids:
            members[e["branch_id"]].append(e["id"])
    thread_specs = {
        _code(t.get("key")): t
        for t in _list(output.get("new_threads"))
        if isinstance(t, dict) and _code(t.get("key"))
    }
    branch_specs = {
        _code(b.get("key")): b
        for b in _list(output.get("new_branches"))
        if isinstance(b, dict) and _code(b.get("key"))
    }
    made_threads: dict[str, int] = {}
    made_branches: dict[str, int] = {}
    assigned: dict[int, int] = {}

    with store.tx() as db:

        def new_branch(tid: int, title: str) -> int:
            position = db.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?", (tid,)
            ).fetchone()[0]
            bid = int(
                db.execute(
                    "INSERT INTO branches(thread_id, title, position) VALUES (?, ?, ?)",
                    (tid, title, position),
                ).lastrowid
                or 0
            )
            branches[bid] = {"id": bid, "thread_id": tid, "into_event": None, "edited": 0}
            main_of.setdefault(tid, bid)
            return bid

        def new_thread(title: str, summary: str) -> int:
            tid = int(
                db.execute(
                    "INSERT INTO threads(project_id, title, summary) VALUES (?, ?, ?)",
                    (project_id, title or MISC_TITLE, summary),
                ).lastrowid
                or 0
            )
            threads[tid] = {"id": tid, "title": title or MISC_TITLE, "edited": 0}
            return tid

        by_title = {str(t["title"]).casefold(): tid for tid, t in threads.items()}

        def thread_for(ref: Any) -> int | None:
            code = _code(ref)
            if code in thread_specs:
                if code not in made_threads:
                    spec = thread_specs[code]
                    title = _clean(spec.get("title"), 30)
                    same = by_title.get(title.casefold()) if title else None
                    if same is not None:
                        made_threads[code] = same  # 和已有研究线同名，就是同一条
                    else:
                        tid = new_thread(title, _clean(spec.get("summary"), 200))
                        new_branch(tid, "")  # 先建主线，之后建的分支都排在它后面
                        made_threads[code] = tid
                        by_title[title.casefold()] = tid
                return made_threads[code]
            tid = _ref(code, "T") if code.startswith("T") else None
            return tid if tid in threads else None

        def branch_for(ref: Any) -> int | None:
            code = _code(ref)
            if code in branch_specs:
                if code not in made_branches:
                    tid = thread_for(branch_specs[code].get("thread"))
                    if tid is None:
                        return None
                    made_branches[code] = new_branch(
                        tid, _clean(branch_specs[code].get("title"), 30)
                    )
                return made_branches[code]
            if code.startswith("B"):
                bid = _ref(code, "B")
                return bid if bid in branches else None
            tid = thread_for(code)
            if tid is None:
                return None
            return main_of.get(tid) or new_branch(tid, "")

        for item in _list(output.get("placements")):
            if not isinstance(item, dict):
                continue
            eid = _ref(item.get("event"), "E")
            if eid is None or eid not in new_ids or eid in assigned:
                continue
            bid = branch_for(item.get("branch"))
            if bid is not None:
                assigned[eid] = bid
                members[bid].append(eid)

        # 新分支的分出点和汇入点：先后必须合理，否则不连
        for code, bid in made_branches.items():
            spec = branch_specs[code]
            source = _link(spec.get("from"), members[bid], key, after=False)
            target = _link(spec.get("into"), members[bid], key, after=True)
            db.execute(
                "UPDATE branches SET from_event = ?, into_event = ? WHERE id = ?",
                (source, target, bid),
            )
        for item in _list(output.get("merges")):
            if not isinstance(item, dict):
                continue
            bid = _ref(item.get("branch"), "B")
            row = branches.get(bid) if bid is not None else None
            if row is None or row["edited"] or row["into_event"] is not None:
                continue
            target = _link(item.get("into"), members[row["id"]], key, after=True)
            if target is not None:
                db.execute("UPDATE branches SET into_event = ? WHERE id = ?", (target, row["id"]))
                row["into_event"] = target

        misc: list[int] = []

        def misc_branch() -> int:
            if not misc:
                found = db.execute(
                    "SELECT b.id FROM branches b JOIN threads t ON t.id = b.thread_id "
                    "WHERE t.project_id = ? AND t.title = ? AND t.edited = 0 "
                    "ORDER BY b.position, b.id LIMIT 1",
                    (project_id, MISC_TITLE),
                ).fetchone()
                misc.append(found[0] if found else new_branch(new_thread(MISC_TITLE, ""), ""))
            return misc[0]

        def place_near(eid: int) -> int:
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
            return misc_branch()

        rescue = new_ids if fallback is None else new_ids & fallback
        for eid in sorted(rescue - assigned.keys(), key=key.__getitem__):
            assigned[eid] = place_near(eid)
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
    # 分出点、汇入点被删掉（或随会话移出项目）后，连线没有意义了。
    for column in ("from_event", "into_event"):
        db.execute(
            f"UPDATE branches SET {column} = NULL WHERE {column} IS NOT NULL AND thread_id IN "
            "(SELECT id FROM threads WHERE project_id = ?) AND "
            f"{column} NOT IN (SELECT id FROM events WHERE hidden = 0)",
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
        rows = store.q(
            "SELECT id, title, ts, anchor FROM events "
            "WHERE session_id = ? AND hidden = 0 AND branch_id IS NULL ORDER BY ts, anchor, id",
            (session["id"],),
        )
        pending = [r["id"] for r in rows]
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
                    # 分支用 fork 之后第一步的标题命名；会话标题往往是一句随口的话
                    (thread_id, _clean(rows[0]["title"] or "fork", 30), position, point["id"]),
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
    asked: set[int] = set()  # 已经问过一次的事件；模型再漏掉就由程序兜底
    if todo:
        progress.set("threads", (len(todo) + size - 1) // size)
    while todo:
        batch = {e["id"] for e in todo[:size]}
        tokens = min(32_000, 3_000 + 20 * len(batch))
        prompt = render_structure(store, project_id, batch, retry=bool(batch & asked))
        try:
            output = call(THREAD_SYSTEM, prompt, THREAD_SCHEMA, tokens)
        except Truncated:
            if size <= 20:
                raise
            size //= 2
            continue
        apply_placement(store, project_id, output, batch, fallback=batch & asked)
        asked |= batch
        todo = pending()
        progress.step((len(todo) + size - 1) // size)


def place(store: Store, call: Call, project_id: int, progress: Progress) -> None:
    waiting = attach_forks(store, project_id)
    _place_batches(store, call, project_id, progress, exclude=waiting)
    attach_forks(store, project_id)
    _place_batches(store, call, project_id, progress, exclude=set())


# ---------- 合并研究线 ----------

THREAD_TARGET = 8  # 研究线超过这么多条，就请模型提出合并

CONSOLIDATE_SYSTEM = """下面是一个研究项目现有的研究线：编号、标题、进展说明、步骤数、时间跨度和几个代表性步骤。研究线太多太碎，请把属于同一个研究方向的合并起来，让整个项目保留 4 到 8 条研究线。
- 每组写 {into, lines, title}：into 是保留的研究线编号（选最主要、步骤最多的那条），lines 是并进去的其他研究线编号，并进去的研究线会变成保留研究线里的分支。title 是保留研究线的新标题（12 个字以内，能概括合并后的内容）；不需要改就填 null。
- 工程环境、工具开发、目录整理、文档报告这些辅助工作合并成一条。
- 确实独立的研究方向才单独保留，不需要合并的研究线不用写。
用 submit 工具提交。"""

CONSOLIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": _items(
            {
                "into": {"type": "string", "description": "保留的研究线编号，例如 T3"},
                "lines": {"type": "array", "items": {"type": "string"}},
                "title": {"type": ["string", "null"]},
            }
        )
    },
    "required": ["groups"],
}


def merge_threads(db: Any, source: dict[str, Any], target: dict[str, Any], edited: bool) -> None:
    """把一条研究线并入另一条：它的各条分支搬过去，原来的主线成为一条以原名命名的分支。"""
    start = db.execute(
        "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?", (target["id"],)
    ).fetchone()[0]
    moved = db.execute(
        "SELECT id, title FROM branches WHERE thread_id = ? ORDER BY position, id", (source["id"],)
    ).fetchall()
    for offset, branch in enumerate(moved):
        title = branch["title"] or (source["title"] if offset == 0 else "")
        db.execute(
            "UPDATE branches SET thread_id = ?, position = ?, title = ?, "
            "edited = MAX(edited, ?) WHERE id = ?",
            (target["id"], start + offset, title[:30], int(edited), branch["id"]),
        )
    db.execute("DELETE FROM threads WHERE id = ?", (source["id"],))
    if edited:
        db.execute("UPDATE threads SET edited = 1 WHERE id = ?", (target["id"],))


def render_lines(store: Store, threads: list[dict[str, Any]]) -> str:
    lines = []
    for t in threads:
        rows = sorted(
            store.q(
                "SELECT id, kind, outcome, importance, title, summary, ts, session_id, anchor "
                "FROM events WHERE hidden = 0 AND branch_id IN "
                "(SELECT id FROM branches WHERE thread_id = ?)",
                (t["id"],),
            ),
            key=event_key,
        )
        if not rows:
            continue
        key_steps = [e for e in rows if e["importance"] >= 2] or rows
        picks = key_steps[:: max(1, len(key_steps) // 5)][:5]
        sample = "；".join(e["title"] for e in picks)
        lines.append(
            f"T{t['id']}「{t['title']}」{len(rows)} 步，{short_time(rows[0]['ts'])[:5]}→"
            f"{short_time(rows[-1]['ts'])[:5]}：{t['summary'] or '（无说明）'}｜代表步骤：{sample}"
        )
    return "\n".join(lines)


def consolidate_threads(store: Store, call: Call, project_id: int, progress: Progress) -> None:
    """研究线太多时请模型提出合并。人工改过的研究线不会被并走；“未归类”不参与。"""
    threads = store.q(
        "SELECT * FROM threads WHERE project_id = ? AND title != ? ORDER BY id",
        (project_id, MISC_TITLE),
    )
    if len(threads) <= THREAD_TARGET:
        return
    # 研究线没变（没有新开的线）就不再合并，免得每次更新结构都在变
    mark = f"consolidated:{project_id}"
    signature = _signature([t["id"] for t in threads])
    if store.setting(mark) == signature:
        return
    progress.set("consolidate", 1)
    try:
        output = call(CONSOLIDATE_SYSTEM, render_lines(store, threads), CONSOLIDATE_SCHEMA, 3_000)
    except Truncated:
        progress.error("合并研究线的输出被截断，保持原样")
        return
    known = {t["id"]: t for t in threads}
    used: set[int] = set()
    with store.tx() as db:
        for group in _list(output.get("groups")):
            if not isinstance(group, dict):
                continue
            target_id = _ref(group.get("into"), "T")
            if target_id is None or target_id not in known or target_id in used:
                continue
            target = known[target_id]
            merged = False
            for value in _list(group.get("lines")):
                source_id = _ref(value, "T")
                if source_id is None or source_id not in known or source_id in used:
                    continue
                source = known[source_id]
                if source_id == target_id or source["edited"]:
                    continue
                merge_threads(db, source, target, edited=False)
                used.add(source_id)
                merged = True
            used.add(target_id)
            title = _clean(group.get("title"), 30)
            if merged and title and not target["edited"]:
                db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, target_id))
    left = store.q(
        "SELECT id FROM threads WHERE project_id = ? AND title != ?", (project_id, MISC_TITLE)
    )
    store.set_setting(mark, _signature([t["id"] for t in left]))
    progress.step()


# ---------- 复审研究线 ----------

REVIEW_SYSTEM = """下面是一个研究项目里的一条研究线，按分支列出它的步骤（时间先后排列）。请做三件事：
1. milestones：挑出这条线的里程碑——以后讲这条线的历史时一定会提到的转折：换了方法、定了主线、得到核心结论、放弃一个方向、交付主要成果。个数见下面的说明，按重要性从高到低排列。辅助性工作（整理、打包、备份、文档、版式修补）不能当里程碑。只写编号（例如 E12）。
2. summary：两三句话写这条线走过的路和现在的状态，不超过 120 个字。写最关键的方法选择、转折和结论，不要罗列步骤。
3. status：active 还在推进，done 已经完成或定稿，dropped 已经放弃。
用这条线步骤的主要语言书写。用 submit 工具提交。"""

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "milestones": {
            "type": "array",
            "items": {"type": "string", "description": "步骤编号，例如 E12"},
        },
        "summary": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
    },
    "required": ["milestones", "summary", "status"],
}


def _signature(ids: list[int]) -> str:
    return ",".join(str(i) for i in sorted(ids))


def milestone_quota(key_steps: int) -> int:
    """里程碑个数：大约每 12 个关键步骤一个，至少 1 个、最多 15 个。"""
    return max(1, min(MILESTONE_MAX, round(key_steps / 12)))


def render_thread(
    thread: dict[str, Any], branches: list[dict[str, Any]], members: dict[int, list[dict[str, Any]]]
) -> str:
    key_steps = sum(1 for steps in members.values() for e in steps if e["importance"] >= 2)
    lines = [
        f"研究线「{thread['title']}」：{key_steps} 个关键步骤，请挑 {milestone_quota(key_steps)} 个里程碑。"
    ]
    for i, b in enumerate(branches):
        steps = members.get(b["id"], [])
        if not steps:
            continue
        name = b["title"] or ("主线" if i == 0 else "分支")
        lines += ["", f"{name}："]
        key = [e for e in steps if e["importance"] >= 2] or steps
        for e in key:
            lines.append(
                f"E{e['id']} {short_time(e['ts'])} {step_label(e)}｜{e['title']}｜{e['summary']}"
            )
    return "\n".join(lines)


def review_threads(store: Store, call: Call, project_id: int, progress: Progress) -> None:
    """每条研究线挑里程碑、写进展叙述和状态。研究线里的事件没变就不重复调用。
    人工改过的研究线不改名字和叙述；人工改过的事件不改重要性。"""
    threads = store.q(
        "SELECT * FROM threads WHERE project_id = ? AND title != ? ORDER BY id",
        (project_id, MISC_TITLE),
    )
    todo = []
    for t in threads:
        branches = store.q(
            "SELECT * FROM branches WHERE thread_id = ? ORDER BY position, id", (t["id"],)
        )
        members: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for e in sorted(
            store.q(
                "SELECT id, branch_id, kind, outcome, importance, title, summary, ts, anchor, "
                "session_id, edited FROM events WHERE hidden = 0 AND branch_id IN "
                "(SELECT id FROM branches WHERE thread_id = ?)",
                (t["id"],),
            ),
            key=event_key,
        ):
            members[e["branch_id"]].append(e)
        events = [e for steps in members.values() for e in steps]
        signature = _signature([e["id"] for e in events])
        if events and t["reviewed"] != signature:
            todo.append((t, branches, members, events, signature))
    if not todo:
        return
    progress.set("review", len(todo))
    for t, branches, members, events, signature in todo:
        try:
            output = call(REVIEW_SYSTEM, render_thread(t, branches, members), REVIEW_SCHEMA, 2_000)
        except Truncated:
            progress.error(f"「{t['title']}」复审输出被截断，跳过")
            progress.step()
            continue
        known = {e["id"]: e for e in events}
        cap = milestone_quota(sum(1 for e in events if e["importance"] >= 2))
        picked: list[int] = []
        for value in _list(output.get("milestones")):
            eid = _ref(value, "E")
            if eid is not None and eid in known and eid not in picked and len(picked) < cap:
                picked.append(eid)
        status = output.get("status") if output.get("status") in STATUSES else None
        summary = _clean(output.get("summary"), 400)
        with store.tx() as db:
            for e in events:
                if e["edited"]:
                    continue
                if e["id"] in picked and e["importance"] != 3:
                    db.execute("UPDATE events SET importance = 3 WHERE id = ?", (e["id"],))
                elif e["id"] not in picked and e["importance"] == 3:
                    db.execute("UPDATE events SET importance = 2 WHERE id = ?", (e["id"],))
            if t["edited"]:
                db.execute("UPDATE threads SET reviewed = ? WHERE id = ?", (signature, t["id"]))
            else:
                db.execute(
                    "UPDATE threads SET summary = COALESCE(NULLIF(?, ''), summary), "
                    "status = COALESCE(?, status), reviewed = ? WHERE id = ?",
                    (summary, status, signature, t["id"]),
                )
        progress.step()


# ---------- 搜索 ----------

SEARCH_TERMS = 5
SEARCH_MESSAGES = 60


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def snippet(text: str, terms: list[str], before: int = 30, after: int = 70) -> str:
    flat = " ".join(text.split())
    lower = flat.lower()
    hits = [i for i in (lower.find(t.lower()) for t in terms) if i >= 0]
    start = max(0, min(hits, default=0) - before)
    end = min(len(flat), start + before + after)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(store: Store, project_id: int, query: str) -> dict[str, Any]:
    """按空格分开的几个词都要出现（不区分英文大小写）。事件看标题和摘要；原话命中时，
    找它所属的事件：引用了这条消息的事件，否则是它之前最近的一个事件。"""
    terms = [t for t in query.split() if t][:SEARCH_TERMS]
    if not terms:
        return {"terms": [], "events": [], "messages": [], "more": False}
    likes = [_like(t) for t in terms]
    where = " AND ".join(["(e.title || ' ' || e.summary) LIKE ? ESCAPE '\\'"] * len(terms))
    matched = store.q(
        f"SELECT e.id FROM events e WHERE e.project_id = ? AND e.hidden = 0 AND {where} "
        "ORDER BY e.ts, e.id",
        (project_id, *likes),
    )
    where = " AND ".join(["m.text LIKE ? ESCAPE '\\'"] * len(terms))
    # fork 会话开头复制自原会话的部分不重复列出
    rows = store.q(
        "SELECT m.session_id, m.idx, m.role, m.ts, m.text FROM messages m "
        "JOIN sessions s ON s.id = m.session_id "
        f"WHERE s.project_id = ? AND m.idx >= s.copied AND {where} "
        "ORDER BY m.ts, m.session_id, m.idx LIMIT ?",
        (project_id, *likes, SEARCH_MESSAGES + 1),
    )
    owners: dict[int, list[dict[str, Any]]] = {}
    hits = []
    for row in rows[:SEARCH_MESSAGES]:
        sid = row["session_id"]
        if sid not in owners:
            owners[sid] = store.q(
                "SELECT id, anchor, refs FROM events WHERE session_id = ? AND hidden = 0 "
                "ORDER BY anchor, id",
                (sid,),
            )
        candidates = owners[sid]
        owner = next((e for e in candidates if row["idx"] in json.loads(e["refs"])), None)
        owner = owner or next((e for e in reversed(candidates) if e["anchor"] <= row["idx"]), None)
        owner = owner or (candidates[0] if candidates else None)
        if owner is None:
            continue
        hits.append(
            {
                "event": owner["id"],
                "session_id": sid,
                "idx": row["idx"],
                "role": row["role"],
                "ts": row["ts"],
                "snippet": snippet(row["text"], terms),
            }
        )
    return {
        "terms": terms,
        "events": [r["id"] for r in matched],
        "messages": hits,
        "more": len(rows) > SEARCH_MESSAGES,
    }


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


# 每个阶段用哪个模型：可以是同一个，也可以按阶段分开（extract / threads / review）
Calls = Call | Mapping[str, Call]


def _stage(call: Calls, stage: str) -> Call:
    return call[stage] if isinstance(call, Mapping) else call


def organize(
    store: Store,
    call: Calls | None,
    project_id: int,
    roots: list[str],
    progress: Progress,
    full: bool = False,
    limit: int | None = None,
) -> None:
    """limit：自动更新的单次上限（字数）。新内容超过它就停在 confirm，等用户点头。"""
    progress.set("sync")
    if full:
        reset(store, project_id)
    sync_project(store, project_id, roots)
    project = store.one("SELECT model_allowed FROM projects WHERE id = ?", (project_id,))
    progress.estimate = estimate(store, project_id)
    if not project or not project["model_allowed"]:
        progress.set("consent")
        return
    if limit is not None and progress.estimate["chars"] > limit:
        progress.set("confirm")
        return
    if call is None:
        progress.error("还没有设置 API Key")
        progress.set("error")
        return
    try:

        def use(stage: str) -> Call:
            return metered(_stage(call, stage), recorder(store, project_id, stage))

        extract(store, use("extract"), project_id, progress)
        place(store, use("threads"), project_id, progress)
        consolidate_threads(store, use("threads"), project_id, progress)
        review_threads(store, use("review"), project_id, progress)
    except AuthError as error:
        progress.error(f"{error}。请在设置里检测 Key，改好后点“重试”，已整理的部分不会丢")
        progress.estimate = estimate(store, project_id)
        progress.set("error")
        return
    except Truncated:
        progress.error(
            "归入研究线失败：模型输出太长被截断。打开思考时容易这样，"
            "可以在设置里把“归入研究线”的思考调低或关掉，再点“重试”"
        )
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
        model: Callable[[], Calls | None],
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

    def start(
        self, project_id: int, full: bool = False, auto: bool = False, limit: int | None = None
    ) -> bool:
        with self.lock:
            current = self.progress.get(project_id)
            if current and current.running:
                return False
            progress = Progress(running=True, auto=auto)
            self.progress[project_id] = progress
        thread = threading.Thread(
            target=self._run, args=(project_id, full, limit, progress), daemon=True
        )
        thread.start()
        return True

    def _run(self, project_id: int, full: bool, limit: int | None, progress: Progress) -> None:
        try:
            organize(self.store, self.model(), project_id, self.roots(), progress, full, limit)
        except Exception as error:  # 后台任务的任何失败都要显示给用户，而不是静默结束。
            progress.error(f"整理中断：{type(error).__name__}: {error}")
            progress.set("error")
        finally:
            with progress.lock:
                progress.running = False
                progress.finished_at = now()
