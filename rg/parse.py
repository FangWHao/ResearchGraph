"""会话解析：流式读取 Claude Code / Codex 的 JSONL，只留下研究相关的文字。

原始文件只读。工具调用压缩成一行，结果只留开头；思考、压缩回放、系统注入和元数据跳过。
不认识的记录类型计入 `unknown`，不报错。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_TEXT = 20_000
TOOL_ARG = 160
TOOL_RESULT = 200
TITLE_CHARS = 60

# 客户端或插件注入到用户消息里的块，不属于用户原话。
INJECTED_TAGS = (
    "system-reminder",
    "rg-context",
    "local-command-stdout",
    "local-command-stderr",
    "local-command-caveat",
    "command-message",
    "codex_internal_context",
    "environment_context",
    "external_codex_apps_open_page",
    "recommended_plugins",
    "user_instructions",
    "permissions instructions",
    "INSTRUCTIONS",
    "turn_aborted",
)
_TAG_NAMES = "|".join(re.escape(t) for t in INJECTED_TAGS)
_INJECTED = re.compile(rf"<({_TAG_NAMES})\b[^>]*>.*?(?:</\1>|\Z)", re.S)
_INSTRUCTION_PREFIXES = ("# AGENTS.md instructions", "<INSTRUCTIONS>")
_AGENTS_HEADING = re.compile(r"^# AGENTS\.md instructions[^\n]*$", re.M)
# Codex 在 IDE 里会把当前文件、打开的标签页拼在用户的话前面，真正的请求在“## My request…”之后
_IDE_CONTEXT = "# Context from my IDE setup"
_IDE_REQUEST = re.compile(r"^## My request[^\n]*\n?", re.M)
_IDE_ACTIVE = re.compile(r"^## Active file:\s*(\S+)", re.M)
# 解析规则变了就加一，已导入的会话会重新读
PARSER_VERSION = 2
# Claude Code 的斜杠命令：<command-name>/clear</command-name><command-args>…</command-args>
_COMMAND_TAGS = re.compile(r"</?command-(?:name|args)>")
_SLASH_COMMAND = re.compile(r"^/[\w:-]+(\s|$)")

CLAUDE_IGNORED = {
    "attachment",
    "last-prompt",
    "mode",
    "permission-mode",
    "queue-operation",
    "file-history-snapshot",
    "file-history-delta",
    "summary",
    "progress",
    "system",
    "cost-state",
    "bridge-session",
    "atis-latch",
}
CLAUDE_SKIPPED_TOOLS = {
    "Read",
    "Grep",
    "Glob",
    "LS",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "ToolSearch",
    "BashOutput",
    "KillShell",
    "ExitPlanMode",
}
CODEX_IGNORED = {
    "session_meta",
    "turn_context",
    "compacted",
    "token_usage_record",
    "world_state",
    "inter_agent_communication_metadata",
}
CODEX_SKIPPED_TOOLS = {
    "update_plan",
    "sleep",
    "wait",
    "wait_agent",
    "list_agents",
    "view_image",
    "write_stdin",
}
CODEX_SKIPPED_ITEMS = {"reasoning", "compaction", "ghost_snapshot", "web_search_call"}


class NotASession(ValueError):
    pass


@dataclass
class Message:
    role: str  # user / assistant / tool
    text: str
    ts: str | None = None
    source: str = ""  # Codex 用来区分镜像消息


@dataclass
class Session:
    tool: str
    messages: list[Message] = field(default_factory=list)
    native_id: str | None = None
    cwd: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    subagent: bool = False
    forked_from: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None
    unknown: Counter[str] = field(default_factory=Counter)

    @property
    def title(self) -> str | None:
        if self.custom_title or self.ai_title:
            return _one_line(self.custom_title or self.ai_title or "", TITLE_CHARS)
        first = next(
            (
                m.text
                for m in self.messages
                if m.role == "user" and m.text and not _SLASH_COMMAND.match(m.text)
            ),
            None,
        )
        return _one_line(first, TITLE_CHARS) if first else None

    def touch(self, ts: Any) -> None:
        if not isinstance(ts, str) or not ts:
            return
        if self.started_at is None or ts < self.started_at:
            self.started_at = ts
        if self.ended_at is None or ts > self.ended_at:
            self.ended_at = ts


def _one_line(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.8)
    return text[:head] + " …（略）… " + text[-(limit - head) :]


def _strip_ide_context(text: str) -> str:
    if not text.lstrip().startswith(_IDE_CONTEXT):
        return text
    match = _IDE_REQUEST.search(text)
    if not match:
        return ""  # 只有 IDE 上下文，没有用户的话
    request = text[match.end() :].strip()
    active = _IDE_ACTIVE.search(text[: match.start()])
    if request and active:
        request += f"（当前文件 {re.split(r'[\\/]', active[1])[-1]}）"
    return request


def clean_user_text(text: str) -> str:
    if text.lstrip().startswith(_INSTRUCTION_PREFIXES):
        return ""
    text = _strip_ide_context(text)
    text = _AGENTS_HEADING.sub("", _INJECTED.sub("", text))
    return " ".join(_COMMAND_TAGS.sub(" ", text).split()) if "<command-" in text else text.strip()


def _blocks_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict) and item.get("type") in {"image", "input_image"}:
                parts.append("[图片]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def detect_tool(record: dict[str, Any]) -> str | None:
    kind = record.get("type")
    if kind in {"session_meta", "response_item", "event_msg", "turn_context"}:
        return "codex"
    if "sessionId" in record or kind in {"user", "assistant", "summary", "ai-title"}:
        return "claude"
    return None


class _Parser:
    def __init__(self, session: Session):
        self.s = session
        self.calls: dict[str, Message] = {}

    def add(self, role: str, text: str, ts: Any, source: str = "") -> Message | None:
        text = text.strip()
        if not text:
            return None
        message = Message(role, clip(text, MAX_TEXT), ts if isinstance(ts, str) else None, source)
        self.s.messages.append(message)
        return message

    def result(self, call_id: Any, text: str, error: bool = False) -> None:
        call = self.calls.pop(call_id, None) if isinstance(call_id, str) else None
        if call is None:
            return
        preview = _one_line(text, TOOL_RESULT)
        if preview:
            call.text += (" → 出错：" if error else " → ") + preview

    def feed(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    def finish(self) -> None:
        pass


class _Claude(_Parser):
    def feed(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        s = self.s
        s.native_id = s.native_id or record.get("sessionId")
        if isinstance(record.get("cwd"), str):
            s.cwd = s.cwd or record["cwd"]
        if kind == "custom-title":
            s.custom_title = record.get("customTitle") or s.custom_title
            return
        if kind == "ai-title":
            s.ai_title = record.get("aiTitle") or s.ai_title
            return
        if kind in CLAUDE_IGNORED:
            return
        if kind not in {"user", "assistant"}:
            s.unknown[f"claude:{kind}"] += 1
            return
        if record.get("isSidechain") or record.get("isMeta") or record.get("isCompactSummary"):
            return
        ts = record.get("timestamp")
        s.touch(ts)
        content = (record.get("message") or {}).get("content", "")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            s.unknown["claude:content"] += 1
            return
        for block in content:
            self.block(kind, block, ts)

    def block(self, kind: str, block: Any, ts: Any) -> None:
        if not isinstance(block, dict):
            self.s.unknown["claude:block"] += 1
            return
        btype = block.get("type")
        if btype == "text":
            text = str(block.get("text", ""))
            if kind == "user":
                self.add("user", clean_user_text(text), ts)
            else:
                self.add("assistant", text, ts)
        elif btype == "tool_use":
            line = claude_call(str(block.get("name", "")), block.get("input") or {})
            message = self.add("tool", line, ts) if line else None
            if message and isinstance(block.get("id"), str):
                self.calls[block["id"]] = message
        elif btype == "tool_result":
            self.result(
                block.get("tool_use_id"),
                _blocks_text(block.get("content", "")),
                bool(block.get("is_error")),
            )
        elif btype == "image" and kind == "user":
            self.add("user", "[图片]", ts)
        elif btype not in {"thinking", "redacted_thinking", "document", "fallback"}:
            self.s.unknown[f"claude:block:{btype}"] += 1


def claude_call(name: str, args: dict[str, Any]) -> str | None:
    if name in CLAUDE_SKIPPED_TOOLS:
        return None
    if name == "Bash":
        return "运行 " + _one_line(str(args.get("command", "")), TOOL_ARG)
    if name in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return "编辑 " + str(args.get("file_path") or args.get("notebook_path") or "")
    if name in {"Task", "Agent"}:
        return "子 Agent：" + _one_line(
            str(args.get("description") or args.get("prompt", "")), TOOL_ARG
        )
    if name == "WebSearch":
        return "搜索：" + _one_line(str(args.get("query", "")), TOOL_ARG)
    if name == "WebFetch":
        return "读取网页 " + str(args.get("url", ""))
    return f"{name} " + _one_line(json.dumps(args, ensure_ascii=False), TOOL_ARG)


class _Codex(_Parser):
    def feed(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        payload = record.get("payload")
        s = self.s
        if kind == "session_meta" and isinstance(payload, dict):
            s.native_id = s.native_id or payload.get("id")
            s.cwd = s.cwd or payload.get("cwd")
            source = payload.get("source")
            if payload.get("parent_thread_id") or (
                isinstance(source, dict) and "subagent" in source
            ):
                s.subagent = True
            elif isinstance(payload.get("forked_from_id"), str):
                s.forked_from = payload["forked_from_id"]
            s.touch(payload.get("timestamp") or record.get("timestamp"))
            return
        if kind == "turn_context" and isinstance(payload, dict):
            s.cwd = s.cwd or payload.get("cwd")
            return
        if kind in CODEX_IGNORED:
            return
        if not isinstance(payload, dict):
            s.unknown[f"codex:{kind}"] += 1
            return
        ts = record.get("timestamp")
        if kind == "response_item":
            s.touch(ts)
            self.item(payload, ts)
        elif kind == "event_msg":
            # 旧版本只在 event_msg 里写对话；新版本同时写 response_item，结束时去重。
            ptype = payload.get("type")
            if ptype in {"user_message", "agent_message"}:
                s.touch(ts)
            if ptype == "user_message":
                self.add("user", clean_user_text(str(payload.get("message", ""))), ts, "event")
            elif ptype == "agent_message":
                self.add("assistant", str(payload.get("message", "")), ts, "event")
        else:
            s.unknown[f"codex:{kind}"] += 1

    def item(self, p: dict[str, Any], ts: Any) -> None:
        ptype = p.get("type")
        if ptype == "message":
            role = p.get("role")
            text = _blocks_text(p.get("content", []))
            if role == "user":
                self.add("user", clean_user_text(text), ts, "item")
            elif role == "assistant":
                self.add("assistant", text, ts, "item")
        elif ptype in {"function_call", "custom_tool_call", "local_shell_call"}:
            name = str(p.get("name") or ptype)
            args = p.get("arguments") if "arguments" in p else p.get("input", p.get("action", ""))
            line = codex_call(name, args)
            message = self.add("tool", line, ts) if line else None
            if message and isinstance(p.get("call_id"), str):
                self.calls[p["call_id"]] = message
        elif ptype in {
            "function_call_output",
            "custom_tool_call_output",
            "local_shell_call_output",
        }:
            self.result(p.get("call_id"), _codex_output(p.get("output", "")))
        elif ptype == "agent_message":
            author, recipient = p.get("author", "?"), p.get("recipient", "?")
            text = _one_line(_blocks_text(p.get("content", [])), TOOL_RESULT)
            self.add("tool", f"Agent 消息 {author}→{recipient}：{text}", ts)
        elif ptype not in CODEX_SKIPPED_ITEMS:
            self.s.unknown[f"codex:item:{ptype}"] += 1

    def finish(self) -> None:
        if any(m.source == "item" for m in self.s.messages):
            self.s.messages = [m for m in self.s.messages if m.source != "event"]


def _codex_output(value: Any) -> str:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return value
        if isinstance(data, dict) and isinstance(data.get("output"), str):
            return data["output"]
        return value
    return _blocks_text(value)


def codex_call(name: str, args: Any) -> str | None:
    short = name.rsplit(".", 1)[-1]
    if short in CODEX_SKIPPED_TOOLS:
        return None
    text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    if short == "apply_patch":
        files = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", text, re.M)
        return "编辑 " + ", ".join(f.strip() for f in files[:5]) if files else "编辑文件"
    if short in {"exec_command", "shell", "local_shell", "exec", "local_shell_call"}:
        command: Any = text
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            command = data.get("cmd") or data.get("command") or text
        if isinstance(command, list):
            command = " ".join(str(x) for x in command)
        return "运行 " + _one_line(str(command), TOOL_ARG)
    return f"{name} " + _one_line(text, TOOL_ARG)


def _records(lines: Iterable[str], session: Session) -> Iterable[dict[str, Any]]:
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            session.unknown["bad_line"] += 1
            continue
        if isinstance(record, dict):
            yield record
        else:
            session.unknown["not_object"] += 1


def parse_lines(lines: Iterable[str], tool: str | None = None) -> Session:
    holder = Session(tool or "")
    parser: _Parser | None = None
    for record in _records(lines, holder):
        if parser is None:
            detected = tool or detect_tool(record)
            if detected is None:
                holder.unknown["before_detect"] += 1
                if holder.unknown["before_detect"] > 20:
                    raise NotASession("不是 Claude Code 或 Codex 会话")
                continue
            holder.tool = detected
            parser = _Claude(holder) if detected == "claude" else _Codex(holder)
        parser.feed(record)
    if parser is None:
        raise NotASession("文件里没有可识别的会话记录")
    parser.finish()
    return holder


def parse_file(path: Path, tool: str | None = None, max_lines: int | None = None) -> Session:
    with path.open(encoding="utf-8", errors="replace") as handle:
        if max_lines is None:
            return parse_lines(handle, tool)
        return parse_lines((line for _, line in zip(range(max_lines), handle, strict=False)), tool)
