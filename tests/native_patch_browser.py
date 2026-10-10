"""原生补丁前端验收合成服务；只导入合成记录，不读取或执行研究资料。"""

from __future__ import annotations

import argparse
import json
import tempfile
from copy import deepcopy
from pathlib import Path

from rg.api.server import LocalServer
from rg.store.database import Store
from tests.golden.test_l1 import ingest
from tests.golden.test_native_patch import records


def seed(store: Store, temp: Path) -> tuple[str, dict[str, dict[str, int | None]]]:
    project = store.project("验收原生补丁项目", [Path("/synthetic/project")])
    data = {
        "add": {
            "新增候选.py": {
                "type": "add",
                "content": "原生新增全文\r\n一\u2028二<script>synthetic</script>",
            }
        },
        "delete": {"删除候选.py": {"type": "delete", "content": "原生删除前全文\r\n无尾换行"}},
        "update": {
            "旧路径.py": {
                "type": "update",
                "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                "move_path": "新路径.py",
            }
        },
        "failed": {
            "失败新增.py": {"type": "add", "content": "失败未写入全文"},
            "失败删除.py": {"type": "delete", "content": "失败删除仍有原文"},
        },
        "conflict": {"矛盾候选.py": {"type": "add", "content": "开始文本"}},
        "waiting": {"缺少开始.py": {"type": "add", "content": "不得推断成功"}},
        "empty": {"空全文.py": {"type": "delete", "content": ""}},
    }
    identities = {}
    for name, changes in data.items():
        items = records(deepcopy(changes))
        items[0]["payload"]["id"] = f"native-browser-{name}"
        items[-1]["payload"]["stdout"] = f"合成场景 {name}"
        if name == "failed":
            items[-1]["payload"].update(status="failed", success=False)
        if name == "conflict":
            items[-1]["payload"]["changes"]["矛盾候选.py"]["content"] = "结束矛盾文本"
        if name == "waiting":
            items.pop(1)
        ingest(store, temp, items, "codex", project, f"{name}.jsonl")
        rows = store.db.execute(
            "SELECT r.event_id,r.kind FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE s.native_session_id=? ORDER BY r.seq",
            (f"native-browser-{name}",),
        ).fetchall()
        identities[name] = {
            "begin": next(
                (
                    row["event_id"]
                    for row in rows
                    if row["kind"] == "meta" and row["event_id"] != rows[0]["event_id"]
                ),
                None,
            ),
            "end": rows[-1]["event_id"],
        }
    return project, identities


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9795)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-native-patch-") as temp:
        root = Path(temp) / "store"
        store = Store(root)
        try:
            project, identities = seed(store, Path(temp))
            print(
                json.dumps({"project": project, "cases": identities}, ensure_ascii=False),
                flush=True,
            )
        finally:
            store.close()
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-native-patch-token")
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
