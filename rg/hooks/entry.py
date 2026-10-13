from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rg.ingest.spool import EVENTS, MAX_BYTES, enqueue, enqueue_stream
from rg.snapshot.capture import capture
from rg.snapshot.config import choose, load_registry
from rg.store.lease import lease
from rg.store.objects import digest
from rg.store.paths import reject_links


def _error(root: Path, stage: str, failure: BaseException) -> None:
    try:
        directory = root / "logs"
        reject_links(root, directory / "hook-errors.log")
        directory.mkdir(parents=True, exist_ok=True)
        raw = f"{datetime.now(UTC).isoformat()} {stage} {type(failure).__name__}\n".encode()
        if os.name == "nt":
            from rg.hooks.windows_log import append

            append(directory / "hook-errors.log", raw)
            return
        descriptor = os.open(
            directory / "hook-errors.log",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(raw)
    except BaseException:
        pass


def process(
    root: Path,
    tool: str,
    event: str,
    raw: bytes,
    *,
    detached: bool = False,
    source_sha256: str | None = None,
    requested_at: str | None = None,
) -> None:
    with lease(root, writable=True):
        _process(
            root,
            tool,
            event,
            raw,
            detached=detached,
            source_sha256=source_sha256,
            requested_at=requested_at,
        )


def _process(
    root: Path,
    tool: str,
    event: str,
    raw: bytes,
    *,
    detached: bool,
    source_sha256: str | None,
    requested_at: str | None,
) -> None:
    if not detached:
        enqueue(root, tool, raw)
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("hook_event_name") != event:
        raise ValueError("钩子参数与原始事件不符")
    if event != "UserPromptSubmit":
        return
    selected = choose(root, payload.get("cwd"))
    asynchronous = selected["snapshot_mode"] == "async" or detached
    requested_at = requested_at or datetime.now(UTC).isoformat()
    source_sha256 = source_sha256 or digest(raw)
    if asynchronous and not detached:
        # 固定运行本软件的子进程；绝不执行 payload 中的命令。
        command = [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            tool,
            event,
            "--data-dir",
            str(root),
            "--detached",
            "--source-sha256",
            source_sha256,
            "--requested-at",
            requested_at,
        ]
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if child.stdin is not None:
            child.stdin.write(raw)
            child.stdin.close()
        return
    capture(
        root,
        selected["project_id"],
        selected["root_id"],
        Path(selected["path"]),
        tool=tool,
        trigger=event,
        source_sha256=source_sha256,
        native_session_id=payload.get("session_id")
        if isinstance(payload.get("session_id"), str)
        else None,
        prompt_id=payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None,
        asynchronous=asynchronous,
        requested_at=requested_at,
        seconds=1.8,
    )


class SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("钩子参数非法")

    def print_help(self, file: Any = None) -> None:
        pass


def main() -> int:
    root = Path(os.environ.get("RG_DATA_DIR", "~/.researchgraph")).expanduser()
    try:
        parser = SilentParser(add_help=False)
        parser.add_argument("tool", choices=["claude", "codex"])
        parser.add_argument("event", choices=sorted(EVENTS))
        parser.add_argument("--data-dir", type=Path, default=root)
        parser.add_argument("--detached", action="store_true")
        parser.add_argument("--source-sha256")
        parser.add_argument("--requested-at")
        parser.add_argument("--scope-root", type=Path)
        parser.add_argument("--scope-project")
        args = parser.parse_args()
        if (args.scope_root is None) != (args.scope_project is None):
            raise ValueError("项目钩子范围参数不完整")
        if args.scope_root is not None and not args.scope_root.is_absolute():
            raise ValueError("项目钩子范围必须为绝对路径")
        root = args.data_dir.expanduser().resolve()
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if args.scope_root is not None:
            # 项目配置可能被子目录继承；明确属于其它工作区的事件不采集。
            # 无法解析的输入仍按原规则保留，不能伪装成已判断归属。
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            cwd = payload.get("cwd") if isinstance(payload, dict) else None
            if isinstance(cwd, str) and Path(cwd).is_absolute():
                if not Path(cwd).resolve().is_relative_to(args.scope_root.resolve()):
                    return 0
                try:
                    matches = [
                        value
                        for value in load_registry(root)
                        if Path(cwd).resolve().is_relative_to(Path(value["path"]).resolve())
                    ]
                    depth = max((len(Path(value["path"]).parts) for value in matches), default=0)
                    owners = {
                        value["project_id"]
                        for value in matches
                        if len(Path(value["path"]).parts) == depth
                    }
                    if len(owners) == 1 and args.scope_project not in owners:
                        return 0
                except (ValueError, KeyError, OSError):
                    pass
        if len(raw) > MAX_BYTES:
            # 超大输入仍保存原件；不解析、不拍快照，由消费端显示 held 缺口。
            enqueue_stream(root, args.tool, sys.stdin.buffer, raw)
            raise ValueError("钩子输入超过解析上限")
        process(
            root,
            args.tool,
            args.event,
            raw,
            detached=args.detached,
            source_sha256=args.source_sha256,
            requested_at=args.requested_at,
        )
    except BaseException as failure:
        _error(root, "hook", failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
