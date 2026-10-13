"""个人视图的持久临时合成 HTTP 服务；只使用测试资料，支持重启同一临时库。"""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from rg.api.server import Handler, LocalServer
from rg.store.database import Store, dumps, now
from tests.golden.test_read_tools import review
from tests.graph_browser import evidence
from tests.research_graph_browser import add_entity, seed, version

TOKEN = "synthetic-personal-view-token"
SECONDARY = "验收个人视图项目二"


def prepare(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cases = seed(directory / "store", directory)
    with closing(Store(directory / "store")) as store:
        project = store.project(SECONDARY, [])
        entity = add_entity(store, project, "finding")
        claim = version(store, entity, "个人视图次项目发现", state="confirmed")
        evidence(store, project, claim, "个人视图次项目独立合成原话")
        store.db.commit()
        cases["view_secondary"] = project
        cases["secondary_claim"] = claim
    (directory / "cases.json").write_text(dumps(cases), encoding="utf-8")
    print("个人视图合成库已独立准备；正式记录与原文在重启时不会重新生成。", flush=True)


def audit(root: Path) -> dict:
    with closing(Store(root)) as store:
        tables = ("raw_events", "claims", "review_actions", "claim_evidence", "graph_clock")
        content = {}
        for table in tables:
            content[table] = [list(row) for row in store.db.execute(f"SELECT * FROM {table}")]
        encoded = dumps(content).encode()
        return {
            "research_sha256": hashlib.sha256(encoded).hexdigest(),
            "revision": store.revision(),
            "views": store.db.execute("SELECT count(*) FROM view_states").fetchone()[0],
            "model_attempts": store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9802)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    if args.prepare:
        prepare(args.fixture_dir)
        return
    root = args.fixture_dir / "store"
    cases = json.loads((args.fixture_dir / "cases.json").read_text(encoding="utf-8"))

    class FixtureHandler(Handler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in ("/synthetic-personal-view/cases", "/synthetic-personal-view/audit"):
                return super().do_GET()
            if self._authorized():
                self._json(200, cases if path.endswith("/cases") else audit(root))

        def do_POST(self):
            if urlsplit(self.path).path != "/synthetic-personal-view/advance":
                return super().do_POST()
            self._unread_body = True
            if not self._authorized():
                return
            if self.headers.get("Content-Length") != "2" or self.rfile.read(2) != b"{}":
                self._json(400, {"error": "合成控制器仅允许空对象"})
                return
            self._unread_body = False
            with closing(Store(root)) as store:
                review(store, cases["historical"], "confirm", now())
                store.db.commit()
                result = {"revision": store.revision()}
            self._json(200, result)

    server = LocalServer(root, args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    print("个人视图合成服务监听127.0.0.1；数据库沿原临时目录恢复。", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
