from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from rg.derive.records import Event, arguments, block, bound_path, calls, cwd
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

EXEC_TOOLS = {
    "Bash",
    "exec_command",
    "functions.exec_command",
    "shell",
    "functions.shell",
    "shell_command",
    "functions.shell_command",
}
POLL_TOOLS = {"write_stdin", "functions.write_stdin"}
HEADER = re.compile(
    r"\A(?:Chunk ID: [^\n]+\n)?Wall time: [0-9.]+ seconds\n"
    r"(?P<status>(?:Process exited with code -?\d+\n)?"
    r"(?:Process running with session ID \d+\n)?)"
    r"(?:Original token count: \d+\n)?(?:Final output|Output):(?:\n|$)"
)


@dataclass(frozen=True)
class Result:
    state: str = "unknown"
    exit_code: int | None = None
    executor_session_id: int | None = None
    reason: str | None = "execution_metadata_missing"


def execution_result(output: Any, *, trusted_fields: bool = False) -> Result:
    """只读原生结构字段或正文前的执行器头，不搜索 stdout 中的退出码。"""
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            output = parsed
        else:
            match = HEADER.match(output)
            if not match:
                return Result()
            code = re.search(r"Process exited with code (-?\d+)", match["status"])
            session = re.search(r"Process running with session ID (\d+)", match["status"])
            output = {
                "exit_code": int(code[1]) if code else None,
                "session_id": int(session[1]) if session else None,
            }
            trusted_fields = True
    if not isinstance(output, dict):
        return Result()
    if "body" in output:
        return execution_result(output["body"])
    if isinstance(output.get("metadata"), dict) and isinstance(output.get("output"), str):
        output = output["metadata"]
        trusted_fields = True
    if not trusted_fields:
        wall = output.get("wall_time_seconds")
        if (
            not isinstance(output.get("output"), str)
            or not isinstance(wall, (int, float))
            or isinstance(wall, bool)
            or not math.isfinite(wall)
            or wall < 0
        ):
            return Result()
    code = output.get("exit_code", output.get("exitCode"))
    handle = output.get("session_id")
    if code is not None and (type(code) is not int or not -(2**31) <= code < 2**31):
        return Result(reason="invalid_exit_code")
    if handle is not None and (type(handle) is not int or handle < 0):
        return Result(reason="invalid_executor_session")
    if (
        output.get("exit_code") is not None
        and output.get("exitCode") is not None
        and output["exit_code"] != output["exitCode"]
    ):
        return Result(reason="conflicting_exit_code")
    if code is not None:
        return Result("exited", code, handle, None)
    if handle is not None:
        return Result("started", None, handle, None)
    return Result()


def result_for(value: Event) -> Result:
    item = block(value)
    if value["tool"] == "claude":
        content = value["record"].get("message", {}).get("content", [])
        if (
            not isinstance(content, list)
            or sum(isinstance(part, dict) and part.get("type") == "tool_result" for part in content)
            != 1
        ):
            return Result(reason="ambiguous_tool_metadata")
        reported = value["record"].get("toolUseResult")
        # is_error 标记工具错误，不等于进程退出码。
        return (
            execution_result(reported, trusted_fields=True)
            if isinstance(reported, dict)
            else Result()
        )
    if value["record"].get("type") == "event_msg" and item.get("type") == "exec_command_end":
        return execution_result(item, trusted_fields=True)
    return execution_result(item.get("output"))


def run_id(value: Event) -> str:
    identity = value["call_id"] or f"event:{value['event_id']}"
    return "l1:" + digest(dumps([value["session_pk"], identity]).encode())


def observe(
    store: Store, identity: str, value: Event, result: Result, details: dict[str, Any] | None = None
) -> None:
    key = digest(dumps([identity, value["event_id"], result.__dict__, details]).encode())
    store.db.execute(
        "INSERT OR IGNORE INTO run_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            identity,
            value["event_id"],
            result.state,
            result.exit_code,
            result.executor_session_id,
            result.reason,
            dumps(details or {}),
            value["occurred_at"],
            now(),
        ),
    )


def request(store: Store, value: Event) -> str | None:
    if value["tool_name"] not in EXEC_TOOLS:
        return None
    args = arguments(value)
    command = (
        args.get("command") if value["tool"] == "claude" else args.get("cmd", args.get("command"))
    )
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        command = dumps(command)
    if not isinstance(command, str) or not command:
        raise ValueError("运行调用缺少明确命令")
    working_dir = cwd(store, value)
    root, _ = bound_path(store, value["project_id"], working_dir, None)
    identity = run_id(value)
    store.db.execute(
        "INSERT OR IGNORE INTO runs "
        "(run_id,project_id,session_pk,call_id,command,cwd,state,request_event_id,"
        "root_id,gap,requested_at) "
        "VALUES (?,?,?,?,?,?,'requested',?,?,?,?)",
        (
            identity,
            value["project_id"],
            value["session_pk"],
            value["call_id"],
            command,
            working_dir,
            value["event_id"],
            root,
            None if root else "cwd_root_unknown",
            value["occurred_at"],
        ),
    )
    observe(
        store,
        identity,
        value,
        Result("requested", reason=None),
        {"command_sha256": digest(command.encode()), "cwd": working_dir, "root_id": root},
    )
    reduce(store, identity, value)
    return identity


def reduce(store: Store, identity: str, value: Event) -> None:
    facts = store.db.execute(
        "SELECT * FROM run_observations WHERE run_id=? ORDER BY event_id", (identity,)
    ).fetchall()
    exits = {row["exit_code"] for row in facts if row["state"] == "exited"}
    ambiguity = len(calls(store, value)) > 1
    invalid = any(
        row["reason"] in {"invalid_exit_code", "conflicting_exit_code", "invalid_executor_session"}
        for row in facts
    )
    state = (
        "unknown"
        if ambiguity or invalid or len(exits) > 1
        else (
            "exited"
            if exits
            else "started"
            if any(row["state"] == "started" for row in facts)
            else "unknown"
            if any(row["state"] == "unknown" for row in facts)
            else "requested"
        )
    )
    ended = (
        next((row["occurred_at"] for row in facts if row["state"] == "exited"), None)
        if state == "exited"
        else None
    )
    gap = (
        "ambiguous_call_id"
        if ambiguity
        else "conflicting_execution_facts"
        if invalid or len(exits) > 1
        else next((row["reason"] for row in facts if row["state"] == "unknown"), None)
        if state == "unknown"
        else None
    )
    if gap is None:
        root = store.db.execute("SELECT root_id FROM runs WHERE run_id=?", (identity,)).fetchone()
        gap = None if root[0] else "cwd_root_unknown"
    store.db.execute(
        "UPDATE runs SET state=?,exit_code=?,ended_at=?,gap=? WHERE run_id=?",
        (state, next(iter(exits)) if state == "exited" else None, ended, gap, identity),
    )


def apply_result(store: Store, call: Event, value: Event) -> str | None:
    name = call["tool_name"]
    if name in EXEC_TOOLS:
        identity = request(store, call)
    elif name in POLL_TOOLS:
        handle = arguments(call).get("session_id")
        matches = (
            store.db.execute(
                "SELECT DISTINCT o.run_id FROM run_observations o JOIN runs r USING(run_id) "
                "WHERE r.session_pk=? AND o.executor_session_id=? AND o.state='started'",
                (call["session_pk"], handle),
            ).fetchall()
            if type(handle) is int
            else []
        )
        if len(matches) != 1:
            return "executor_session_unknown_or_ambiguous"
        identity = matches[0][0]
    else:
        return None
    assert identity is not None
    observe(store, identity, value, result_for(value), {"call_event_id": call["event_id"]})
    owner = store.db.execute(
        "SELECT request_event_id FROM runs WHERE run_id=?", (identity,)
    ).fetchone()
    from rg.derive.records import event

    reduce(store, identity, event(store, owner[0]))
    return None
