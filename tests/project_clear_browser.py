"""整项目清除的独立合成 HTTP 服务；不读取既有浏览器库或真实资料。"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from uuid import uuid4

from rg.api.server import LocalServer
from rg.store import clear
from rg.store.database import Store
from tests.golden.test_read_tools import T1, append
from tests.graph_browser import evidence


def seed(store: Store) -> tuple[str, str]:
    recovery = ""
    busy = ""
    for name in (
        "保留对照项目",
        "验收清除成功项目",
        "验收清除过期项目",
        "验收清除迟到项目",
        "验收清除重试项目",
        "验收清除鉴权项目",
        "验收清除恢复项目",
        "验收清除阻碍项目",
        "验收清除忙碌项目",
    ):
        project = store.project(name, [])
        entity = str(uuid4())
        store.db.execute(
            "INSERT INTO entities VALUES (?,?,?,NULL,?)", (entity, project, "finding", T1)
        )
        claim = append(
            store,
            entity,
            "entity_version",
            {
                "claim_type": "entity_version",
                "temp_id": entity,
                "kind": "finding",
                "label": "清除验收合成记录",
                "content": "纯合成正文；不得执行 <script>window.clearInjected=true</script>",
            },
            state="confirmed",
        )
        evidence(store, project, claim, "清除验收共享合成原文，不含真实个人资料。")
        evidence(
            store,
            project,
            claim,
            name + "独有原文：<script>window.clearInjected=true</script>，只用于临时验收。",
        )
        if name == "验收清除恢复项目":
            recovery = project
        if name == "验收清除忙碌项目":
            busy = project
        if name == "验收清除阻碍项目":
            # 明确跨项目行依赖：归属阻碍须由真实规划器报告，不伪造接口。
            store.db.execute(
                "INSERT INTO claim_evidence SELECT 1,span_id,'support' "
                "FROM claim_evidence WHERE claim_id=?",
                (claim,),
            )
    return recovery, busy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9792)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-clear-browser-") as directory:
        root = Path(directory) / "store"
        store = Store(root)
        try:
            recovery, busy_project = seed(store)
        finally:
            store.close()
        finish = clear._finish
        execute = clear.execute
        interrupted = False
        occupied = False

        def synthetic_interrupt(root, value, db):
            nonlocal interrupted
            if value["project_id"] == recovery and not interrupted:
                interrupted = True
                raise OSError("合成清除中断：已提交数据库，尚未完成管理文件清理")
            return finish(root, value, db)

        # 仅此独立合成进程注入一次中断；HTTP 状态/恢复仍走生产实现。
        clear._finish = synthetic_interrupt

        def synthetic_occupancy(root, project, request_id, proof):
            nonlocal occupied
            if project == busy_project and not occupied:
                occupied = True
                holder = Store(root)
                try:
                    return execute(root, project, request_id, proof)
                finally:
                    holder.close()
            return execute(root, project, request_id, proof)

        clear.execute = synthetic_occupancy
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-browser-token")
        print("独立清除合成服务就绪；未读取真实资料。", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
