"""文件差异的独立合成 HTTP 服务；真实快照与离线文件处理，不读取真实资料。"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from rg.api.server import LocalServer
from rg.artifacts.worker import Worker
from rg.snapshot.capture import MAX_FILE_BYTES, capture
from rg.store.database import Store
from tests.golden.test_snapshots_hooks import queued


def snapshot(store: Store, project: str, root: str, work: Path) -> None:
    result = capture(store.root, project, root, work)
    if result["skipped"] is not None:
        raise RuntimeError("合成快照未保存：" + str(result["skipped"]))
    queued(store)
    counts = Worker(store, project).run(1000)
    if counts.get("failed", 0) or counts.get("paused", 0):
        raise RuntimeError("合成物理版本处理失败：" + str(counts))


def seed(store: Store, directory: Path) -> None:
    work = directory / "synthetic-primary"
    work.mkdir()
    project = store.project("验收文件差异项目", [work])
    root = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (project,)
    ).fetchone()[0]
    before = {
        "compare.txt": (
            "标题\r\n旧数据\u2028正文\r\n<script>window.versionDiffInjected=true</script>\r\n"
        ).encode(),
        "mode.txt": b"before mode\n",
        "binary.bin": b"before\0binary",
        "invalid.txt": b"before\xff",
        "bytes.txt": b"a" * 35000,
        "lines.txt": b"a\n" * 2001,
        "output.txt": (b"a" * 18 + b"\n") * 1600,
        "empty.txt": b"",
        "current.bin": b"x" * (MAX_FILE_BYTES + 100),
    }
    for name, raw in before.items():
        (work / name).write_bytes(raw)
    (work / "link").symlink_to("unopened-synthetic-target-before")
    snapshot(store, project, root, work)
    # 单独捕获分页填充，避免与真正after同一时间并列后按随机版本ID挤出第一页。
    for index in range(28):
        (work / f"a-fill-{index:02}.txt").write_bytes(f"合成分页文件{index}\n".encode())
    snapshot(store, project, root, work)
    after = {
        "compare.txt": (
            "标题\n新数据\u2028正文\n<script>window.versionDiffInjected=true</script>"
        ).encode(),
        "mode.txt": b"after mode\n",
        "binary.bin": b"after\0binary",
        "invalid.txt": b"after\xff",
        "bytes.txt": b"b" * 35000,
        "lines.txt": b"b\n" * 2001,
        "output.txt": (b"b" * 18 + b"\n") * 1600,
        "empty.txt": "非空文件\n".encode(),
    }
    for name, raw in after.items():
        (work / name).write_bytes(raw)
    (work / "mode.txt").chmod(0o755)
    (work / "link").unlink()
    (work / "link").symlink_to("unopened-synthetic-target-after")
    snapshot(store, project, root, work)
    other = directory / "synthetic-secondary"
    other.mkdir()
    (other / "compare.txt").write_bytes("次项目独立合成保存字节\n".encode())
    project2 = store.project("验收文件差异项目二", [other])
    root2 = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (project2,)
    ).fetchone()[0]
    snapshot(store, project2, root2, other)
    # 已登记原文/快照和对象保留，研究工作区实际删除后再提供只读接口。
    shutil.rmtree(work)
    shutil.rmtree(other)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9793)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-version-diff-browser-") as temp:
        directory = Path(temp)
        store = Store(directory / "store")
        try:
            seed(store, directory)
            count = store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0]
            models = store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0]
            print(f"合成已保存版本 {count} 个，模型调用 {models}；工作区已删除。", flush=True)
        finally:
            store.close()
        server = LocalServer(
            directory / "store", args.web_dir, args.port, token="synthetic-browser-token"
        )
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
