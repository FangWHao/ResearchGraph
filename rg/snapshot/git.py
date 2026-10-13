from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class Deadline(RuntimeError):
    pass


class Git:
    def __init__(
        self,
        worktree: Path,
        deadline: float,
        repository: Path | None = None,
        index: Path | None = None,
    ):
        self.worktree = worktree
        self.deadline = deadline
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_ALLOW_PROTOCOL": "",
            }
        )
        if repository:
            self.environment["GIT_DIR"] = str(repository)
            self.environment["GIT_WORK_TREE"] = str(worktree)
        if index:
            self.environment["GIT_INDEX_FILE"] = str(index)
        self.options = [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
        ]

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Deadline("快照超过时间预算")
        return remaining

    def run(self, *arguments: str, data: bytes | None = None, optional: bool = False) -> bytes:
        try:
            result = subprocess.run(
                ["git", *self.options, "-C", str(self.worktree), *arguments],
                input=data,
                capture_output=True,
                env=self.environment,
                timeout=self.remaining(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise Deadline("Git 超过快照时间预算") from None
        if result.returncode and not optional:
            # 不把配置、文件名或用户原文放入异常日志。
            raise RuntimeError("影子 Git 操作失败")
        self.remaining()
        return result.stdout if result.returncode == 0 else b""

    def metadata(self, captured_paths: set[bytes], omitted: bool) -> dict[str, object]:
        tracked = self.run("ls-files", "--stage", "-z", optional=True).split(b"\0")
        tracked_paths = {part.split(b"\t", 1)[1] for part in tracked if b"\t" in part}
        if omitted or not tracked_paths <= captured_paths:
            # status 可能重新读取已被忽略或超 5 MB 的 tracked 文件；只读 Git 对象元数据。
            head = self.run("rev-parse", "--verify", "HEAD", optional=True).decode().strip()
            branch = (
                self.run("symbolic-ref", "--short", "-q", "HEAD", optional=True).decode().strip()
            )
            staged = (
                self.run(
                    "diff-index",
                    "--cached",
                    "--name-only",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--ignore-submodules=all",
                    "HEAD",
                    optional=True,
                )
                if head
                else b""
            )
            return {
                "head_commit": head or None,
                "branch": branch or None,
                "dirty": True if staged else None,
                "dirty_unavailable_reason": "omitted_or_ignored_tracked_files",
            }
        # status 可调用 clean/process 过滤器。先禁用实际配置的所有过滤器，禁止执行仓库脚本。
        names = (
            self.run("config", "--name-only", "--get-regexp", r"^filter\.", optional=True)
            .decode()
            .splitlines()
        )
        for name in names:
            if name.endswith((".clean", ".process", ".smudge")):
                self.options += ["-c", f"{name}="]
            elif name.endswith(".required"):
                self.options += ["-c", f"{name}=false"]
        raw = self.run(
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=all",
            optional=True,
        )
        head, branch, changes = None, None, []
        headers = True
        for part in raw.split(b"\0"):
            if headers and part.startswith(b"# "):
                if part.startswith(b"# branch.oid "):
                    value = part.removeprefix(b"# branch.oid ").decode()
                    head = None if value == "(initial)" else value
                elif part.startswith(b"# branch.head "):
                    value = part.removeprefix(b"# branch.head ").decode()
                    branch = None if value == "(detached)" else value
            elif part:
                headers = False
                changes.append(part)
        dirty = bool(changes) if raw else None
        return {
            "head_commit": head,
            "branch": branch or None,
            "dirty": None if names else dirty,
            "dirty_unavailable_reason": "configured_filters" if names else None,
        }
