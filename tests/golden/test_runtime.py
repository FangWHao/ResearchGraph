from __future__ import annotations

import time
from contextlib import ExitStack, closing

import pytest

from rg.ingest.sources import register
from rg.runtime import Runtime, cycle, status
from rg.store.database import Store
from rg.store.lease import lease
from rg.store.locking import TaskBusy
from tests.golden.test_ingestion import lines, record


def wait_for(check, seconds=8, diagnostics=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("后台进程未在验收时间内完成：" + str(diagnostics() if diagnostics else {}))


def test_offline_cycle_ingests_append_without_model_or_original_changes(tmp_path):
    root = tmp_path / "data"
    logs = tmp_path / "public.jsonl"
    lines(logs, [record()])
    original = logs.read_bytes()
    with closing(Store(root)) as store:
        register(store, logs, "claude")
    assert cycle(root, "scan", {"limit": 20})["events"] == 1
    assert logs.read_bytes() == original
    with logs.open("ab") as stream:
        from rg.store.database import dumps

        stream.write(dumps(record("新增", uuid="second")).encode() + b"\n")
    assert cycle(root, "scan", {"limit": 20})["events"] == 1
    assert cycle(root, "scan", {"limit": 20}).get("events", 0) == 0
    with closing(Store(root)) as store:
        assert store.health()["events"] == 2
        assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_real_children_release_idle_lease_and_stop_with_parent(tmp_path):
    root = tmp_path / "中文 data"
    with closing(Store(root)):
        pass
    config = {"interval": 60, "limit": 20, "extract": False}
    with ExitStack() as running, ExitStack() as occupied:
        occupied.enter_context(lease(root, exclusive=True))
        runtime = running.enter_context(Runtime(root, config))
        wait_for(
            lambda: all(
                status(root).get(role, {}).get("state") == "waiting" for role in ("scan", "files")
            ),
            diagnostics=lambda: {
                "states": status(root),
                "process_exits": {
                    role: process.poll() for role, process in runtime.processes.items()
                },
            },
        )
        occupied.close()
        wait_for(
            lambda: all(
                status(root).get(role, {}).get("state") == "idle" for role in ("scan", "files")
            )
        )
        processes = list(runtime.processes.values())
        assert all(p.poll() is None for p in processes)
        assert status(root)["extract"]["state"] == "disabled"
        assert all(item["liveness"] == "unknown" for item in status(root).values())
        # 不持有空闲连接；清除流程能够获得实际排他存活锁。
        with lease(root, exclusive=True):
            pass
        with pytest.raises(TaskBusy, match="统一"):
            with Runtime(root, config):
                pass
    assert all(p.poll() == 0 for p in processes)
    assert all(status(root)[role]["state"] == "stopped" for role in ("scan", "files"))


def test_supervisor_recovers_actual_child_exit_and_control_pipe_eof(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)):
        pass
    with Runtime(root, {"interval": 0.05, "limit": 20, "extract": False}) as runtime:
        first = runtime.processes["scan"]
        assert first.stdin is not None
        first.stdin.close()
        wait_for(lambda: first.poll() == 0)
        wait_for(lambda: runtime.processes["scan"] is not first)
        replacement = runtime.processes["scan"]
        assert replacement.poll() is None
    assert replacement.poll() == 0


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_interval_spawns_nothing(tmp_path, interval):
    with pytest.raises(ValueError, match="有限正数"):
        Runtime(tmp_path, {"interval": interval, "limit": 20})
    assert not (tmp_path / "runtime").exists()


def test_status_has_bounded_read_and_does_not_create_missing_data(tmp_path):
    root = tmp_path / "absent"
    assert status(root) == {} and not root.exists()
    path = root / "runtime/scan.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b" " * 20000)
    assert status(root)["scan"]["state"] == "unavailable"
