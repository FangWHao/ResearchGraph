from __future__ import annotations

import os
import subprocess
import sys
import threading
from types import TracebackType

from rg.store.database import Store


class Service:
    """由实际子进程句柄管理后台摘要；父进程断开管道后子进程退出。"""

    def __init__(self, store: Store):
        self.store = store
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Service:
        return self

    def tick(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                return
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process = None
        if not self.store.db.execute("SELECT 1 FROM workspace_snapshots LIMIT 1").fetchone():
            return
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"RG_API_KEY", "RG_MODEL", "RG_BASE_URL"}
        }
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                "from rg.artifacts.service import child_main; child_main()",
                "--data-dir",
                str(self.store.root.resolve()),
                "hash-files",
                "--watch",
            ],
            cwd=self.store.root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)


def child_main() -> None:
    def disconnected() -> None:
        while sys.stdin.buffer.read(1):
            pass
        # 未完成任务由下一持锁进程接管；不用父 PID 或超时推测进程存活。
        os._exit(0)

    threading.Thread(target=disconnected, daemon=True).start()
    from rg.cli.main import main

    main()
