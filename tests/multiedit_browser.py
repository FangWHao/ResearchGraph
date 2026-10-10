"""多步编辑界面验收服务；仅使用合成已存原件与生产只读接口。"""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from rg.api.server import LocalServer
from rg.store.database import Store
from tests.multiedit import seed_multiedit_store


def fingerprint(store: Store) -> str:
    return sha256("\n".join(store.db.iterdump()).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9803)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    parser.add_argument("--index", type=Path, default=Path(".cache/frontend-multiedit-index.json"))
    parser.add_argument("--audit", type=Path, default=Path(".cache/frontend-multiedit-audit.json"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-multiedit-browser-") as folder:
        directory = Path(folder)
        root = directory / "store"
        with closing(Store(root)) as store:
            project, cases = seed_multiedit_store(store, directory)
            before = fingerprint(store)
            index = {"project_id": project, "cases": cases, "synthetic_only": True}
        args.index.parent.mkdir(parents=True, exist_ok=True)
        args.index.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(index, ensure_ascii=False), flush=True)
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-multiedit-token")
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            with closing(Store(root)) as store:
                after = fingerprint(store)
            audit = {
                "合成库查询前摘要": before,
                "合成库查询后摘要": after,
                "合成库逐表导出未改变": before == after,
            }
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(audit, ensure_ascii=False), flush=True)
            if before != after:
                raise RuntimeError("只读多步编辑验收改变了合成库")


if __name__ == "__main__":
    main()
