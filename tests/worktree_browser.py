"""公开临时 Git 工作树验收；生产 API 和扫描循环，不读真实项目。"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from rg.api.server import Handler, LocalServer
from rg.ingest.watch import cycle
from rg.record.question import question
from rg.store.database import Store, dumps
from rg.store.roots import register

TOKEN = "synthetic-worktrees-token"


def git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def repository(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Synthetic")
    git(path, "config", "user.email", "synthetic@example.invalid")
    (path / "README.txt").write_text("公开合成工作区正文，不作为采集输入。\n", encoding="utf-8")
    git(path, "add", "README.txt")
    git(path, "commit", "-qm", "public synthetic fixture")


def seed(directory: Path) -> dict:
    main, other = directory / "public-main", directory / "public-other"
    repository(main)
    repository(other)
    trees = [directory / f"public-tree-{n:02}" for n in range(53)]
    for tree in trees:
        git(main, "worktree", "add", "-q", "--detach", str(tree), "HEAD")
    # 留下清单第一页内的真实工作树，确保下一轮实际发现；不计算生产发现结果。
    unregistered = min(trees, key=lambda p: sha256(str(p).encode()).hexdigest())
    with closing(Store(directory / "store")) as store:
        project = store.project("验收工作树跟踪项目", [main])
        second = store.project("验收工作树跟踪项目二", [other])
        for tree in trees:
            if tree != unregistered:
                register(store, project, tree, "worktree")
        question(
            store,
            {
                "project_id": project,
                "text": "公开合成原始问题；工作树元数据不修改这条科研记录。",
                "scope": None,
                "actor": "human:公开种子",
                "request_id": str(uuid4()),
                "expected_revision": store.revision(),
            },
        )
        main_root = store.db.execute(
            "SELECT root_id FROM source_roots WHERE project_id=? AND path=?",
            (project, str(main)),
        ).fetchone()[0]
        index = {
            "project_id": project,
            "second_project": second,
            "main": str(main),
            "other": str(other),
            "new_tree": str(unregistered),
            "main_root": main_root,
            "initial_total": 53,
            "after_discovery_total": 54,
            "late_tree": str(directory / "public-late-tree"),
        }
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(root: Path, index: dict) -> dict:
    tables = ("raw_events", "claims", "evidence_spans", "claim_evidence", "model_attempts")
    with closing(Store(root)) as store:
        protected = {
            table: [list(r) for r in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables
        }
        result = {
            "research_sha256": sha256(dumps(protected).encode()).hexdigest(),
            "protected_counts": {table: len(rows) for table, rows in protected.items()},
            "revision": store.revision(),
            "roots": store.db.execute("SELECT count(*) FROM source_roots").fetchone()[0],
            "policies": store.db.execute("SELECT count(*) FROM worktree_follow_actions").fetchone()[
                0
            ],
            "observations": store.db.execute(
                "SELECT count(*) FROM worktree_discovery_observations"
            ).fetchone()[0],
        }
    values = {}
    for name in ("main", "other"):
        repo = Path(index[name])
        for relative in (".git/index", ".git/config", "README.txt"):
            values[f"{name}/{relative}"] = sha256((repo / relative).read_bytes()).hexdigest()
    result["git_index_config_body"] = values
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9808)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    if args.prepare:
        index = seed(args.fixture_dir)
        (cache / "frontend-worktrees-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("公开两个 Git 仓库、53个工作树及两项目已准备。", flush=True)
        return
    root = args.fixture_dir / "store"
    index = json.loads((args.fixture_dir / "index.json").read_text(encoding="utf-8"))
    before = audit(root, index)

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-worktrees/audit":
                return super().do_GET()
            if self._authorized():
                self._json(200, audit(root, index))

        def do_POST(self):
            path = urlsplit(self.path).path
            if not path.startswith("/synthetic-worktrees/"):
                return super().do_POST()
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            if path == "/synthetic-worktrees/cycle":
                results, failures = [], []

                def run():
                    try:
                        with closing(Store(root)) as store:
                            results.append(cycle(store))
                    except Exception as error:
                        failures.append(type(error).__name__)

                thread = threading.Thread(target=run)
                thread.start()
                thread.join()
                if failures:
                    self._json(500, {"error": failures[0]})
                else:
                    self._json(200, {"cycle": results[0], "audit": audit(root, index)})
            elif path == "/synthetic-worktrees/new":
                late = Path(index["late_tree"])
                if not late.exists():
                    git(Path(index["main"]), "worktree", "add", "-q", "--detach", str(late))
                self._json(200, {"path": str(late)})
            elif path == "/synthetic-worktrees/share":
                with closing(Store(root)) as store:
                    result = register(
                        store, index["second_project"], Path(index["late_tree"]), "worktree"
                    )
                self._json(200, result)
            else:
                self._json(404, {"error": "未知合成控制"})

    server = LocalServer(root, args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        after = audit(root, index)
        intact = (
            before["research_sha256"] == after["research_sha256"]
            and before["git_index_config_body"] == after["git_index_config_body"]
        )
        (cache / "frontend-worktrees-audit.json").write_text(
            json.dumps(
                {"准备完成时": before, "服务关闭时": after, "科研及Git原内容未改写": intact},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not intact:
            raise RuntimeError("合成科研或Git原内容被改写")


if __name__ == "__main__":
    main()
