"""一个本机入口管理界面与后台工作；空闲时释放数据目录占用。"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Any

from rg.snapshot.files import WorkspaceFiles
from rg.store.database import Store, dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import atomic_write
from rg.store.paths import reject_links

ROLES = ("scan", "files", "extract")


def status(root: Path) -> dict[str, Any]:
    result = {}
    for role in ROLES:
        path = root / "runtime" / f"{role}.json"
        try:
            reject_links(root, path)
            # Windows 读句柄共享删除，频繁查看健康状态不能阻止后台原子替换。
            with (
                WorkspaceFiles(path.parent) as files,
                os.fdopen(files.open(path.name), "rb") as stream,
            ):
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError("运行状态超过读取上限")
            record = json.loads(raw)
            if not isinstance(record, dict) or record.get("role") != role:
                raise ValueError("运行状态格式无效")
            # 文件只证明最后一次观测；不能用保存的 PID 或状态声明进程仍存活。
            result[role] = record | {"liveness": "unknown"}
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            result[role] = {"state": "unavailable", "liveness": "unknown"}
    return result


def _write(root: Path, role: str, run_id: str, state: str, **values: Any) -> None:
    path = root / "runtime" / f"{role}.json"
    reject_links(root, path)
    raw = dumps(
        {"role": role, "run_id": run_id, "state": state, "recorded_at": now(), **values}
    ).encode()
    if len(raw) > 16384:
        raise ValueError("运行回执超过上限")
    atomic_write(path, raw)


def _report(root: Path, role: str, run_id: str, state: str, **values: Any) -> None:
    try:
        _write(root, role, run_id, state, **values)
    except (OSError, ValueError):
        # 回执失败不替代任务结果，也不能让处理已有错误的工作进程再次崩溃。
        pass


def cycle(root: Path, role: str, config: dict[str, Any]) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError("无效的后台角色")
    with closing(Store(root)) as store:
        if role == "scan":
            from rg.ingest.watch import cycle as scan

            return scan(store)
        if role == "files":
            from rg.artifacts.worker import Worker

            return Worker(store).run(config["limit"])
        from rg.extract.pipeline import Pipeline
        from rg.extract.provider import Provider, load_key
        from rg.extract.worker import Worker

        path = Path(config["key_file"]) if config.get("key_file") else None
        provider = Provider(config["base_url"], config["model"], load_key(path))
        try:
            worker = Worker(
                store,
                provider,
                input_budget=config["input_budget"],
                daily_budget=config["daily_budget"],
            )
            return Pipeline(worker, config.get("project")).run(config["limit"])
        finally:
            provider.close()


class Runtime:
    """只管理自己创建的进程；模型等待与完整文件摘要不阻塞本地采集。"""

    def __init__(self, root: Path, config: dict[str, Any]):
        self.root, self.config = root.resolve(), config
        interval = config["interval"]
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("后台轮询间隔必须为有限正数")
        if type(config["limit"]) is not int or not 1 <= config["limit"] <= 200:
            raise ValueError("每轮任务上限需为 1 到 200")
        self.roles = ["scan", "files"] + (["extract"] if config.get("extract") else [])
        self.run_id = str(uuid.uuid4())
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock: Any = None

    def _spawn(self, role: str) -> None:
        environment = dict(os.environ)
        if role != "extract":
            for key in ("RG_API_KEY", "RG_BASE_URL", "RG_MODEL"):
                environment.pop(key, None)
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", "from rg.runtime import child_main; child_main()"],
            cwd=self.root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        self.processes[role] = process
        configuration = (
            self.config
            if role == "extract"
            else {key: self.config[key] for key in ("interval", "limit")}
        )
        assert process.stdin is not None
        process.stdin.write(
            dumps(
                {
                    "root": str(self.root),
                    "role": role,
                    "run_id": self.run_id,
                    "config": configuration,
                }
            ).encode()
            + b"\n"
        )
        process.stdin.flush()

    def _supervise(self) -> None:
        while not self.stopping.wait(1):
            for role, process in list(self.processes.items()):
                code = process.poll()
                if code is None:
                    continue
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    _write(self.root, role, self.run_id, "restarting", exit_code=code)
                    self._spawn(role)
                except (OSError, ValueError):
                    # 下一次轮询接续；不打印可能含来源路径或凭据的异常正文。
                    continue

    def __enter__(self) -> Runtime:
        path = self.root / "locks/runtime.lock"
        reject_links(self.root, path)
        self.lock = exclusive(path, "该数据目录已有统一启动入口")
        self.lock.__enter__()
        try:
            for role in self.roles:
                _write(self.root, role, self.run_id, "starting")
                self._spawn(role)
            if "extract" not in self.roles:
                _write(self.root, "extract", self.run_id, "disabled")
            self.thread = threading.Thread(target=self._supervise, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
        for process in self.processes.values():
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
        for role, process in self.processes.items():
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            try:
                _write(self.root, role, self.run_id, "stopped", exit_code=process.returncode)
            except (OSError, ValueError):
                pass
        if self.lock is not None:
            self.lock.__exit__(kind, value, traceback)
            self.lock = None


def child_main() -> None:
    raw = sys.stdin.buffer.readline(16385)
    if len(raw) > 16384 or not raw.endswith(b"\n"):
        raise ValueError("后台配置超过上限或不完整")
    body = json.loads(raw)
    root, role, run_id, config = Path(body["root"]), body["role"], body["run_id"], body["config"]
    if role not in ROLES:
        raise ValueError("无效的后台角色")

    def disconnected() -> None:
        while os.read(sys.stdin.fileno(), 1):
            pass
        # 正在运行的持久任务保留原始尝试，由下次实际持锁工作进程接管。
        os._exit(0)

    threading.Thread(target=disconnected, daemon=True).start()
    while True:
        started = time.monotonic()
        interval = config["interval"]
        try:
            _report(root, role, run_id, "working")
            counts = cycle(root, role, config)
            _report(root, role, run_id, "idle", counts=counts)
        except TaskBusy:
            _report(root, role, run_id, "waiting", reason="store_or_worker_busy")
            interval = min(interval, 1)
        except (OSError, ValueError, RuntimeError) as error:
            _report(root, role, run_id, "error", error_type=type(error).__name__)
        time.sleep(max(interval - (time.monotonic() - started), 0.05))
