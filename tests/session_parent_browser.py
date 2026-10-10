"""父线程证据面板的独立合成服务；仅扫描临时构造的头记录。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from rg.api.server import Handler, LocalServer
from rg.ingest.scanner import scan_file
from rg.ingest.watch import cycle
from rg.store import migrations
from rg.store.database import Store


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "researchgraph-synthetic-parent:" + name))


def header(name: str, parent: str | None = None, *, both: bool = False) -> dict:
    payload: dict = {"id": identity(name), "cli_version": "0.120.0"}
    if parent:
        payload["parent_thread_id"] = parent
        if both:
            payload["source"] = {
                "subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}
            }
    return {"type": "session_meta", "timestamp": "2026-10-11T00:00:00Z", "payload": payload}


def add(store: Store, directory: Path, project: str, name: str, heads: list[dict], text: str):
    path = directory / (name + ".jsonl")
    items = heads + [
        {
            "type": "response_item",
            "timestamp": "2026-10-11T00:01:00Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        }
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in items), encoding="utf-8"
    )
    scan_file(store, path, "codex", project)
    rows = store.db.execute(
        "SELECT r.event_id,r.session_pk,r.kind FROM raw_events r JOIN source_files f "
        "USING(file_instance_id) WHERE f.path=? ORDER BY r.event_id",
        (str(path),),
    ).fetchall()
    return {
        "event_id": next(row["event_id"] for row in rows if row["kind"] == "user_msg"),
        "session_pk": rows[0]["session_pk"],
        "head_events": [row["event_id"] for row in rows if row["kind"] == "meta"],
    }


def seed(root: Path, directory: Path) -> tuple[str, dict]:
    cases = {}
    latest = migrations.LATEST_VERSION
    try:
        migrations.LATEST_VERSION = 20
        store = Store(root)
        try:
            project = store.project("验收父线程项目", [])
            cases["unobserved"] = add(
                store, directory, project, "legacy", [header("legacy")], "旧未观测"
            )
        finally:
            store.close()
    finally:
        migrations.LATEST_VERSION = latest
    store = Store(root)
    try:
        other = store.project("验收父线程项目二", [])
        cases["parent"] = add(store, directory, project, "parent", [header("parent")], "父头原文")
        cases["linked"] = add(
            store,
            directory,
            project,
            "linked",
            [header("linked", identity("parent"), both=True)],
            "同项目已关联<script>window.parentInjected=true</script>",
        )
        cases["late"] = add(
            store,
            directory,
            project,
            "late",
            [header("late", identity("late-parent"))],
            "父头尚未导入",
        )
        cases["none"] = add(store, directory, project, "none", [header("none")], "没有父声明")
        invalid = header("invalid", "not-a-uuid")
        cases["invalid"] = add(store, directory, project, "invalid", [invalid], "声明无效")
        heads = [header("conflicting", identity("different-parent"))]
        heads += [header("conflicting", identity("parent")) for _ in range(20)]
        cases["conflicting"] = add(
            store, directory, project, "conflicting", heads, "冲突藏在被省略的旧头中"
        )
        cases["outside_parent"] = add(
            store, directory, other, "outside-parent", [header("outside-parent")], "其他项目父原文"
        )
        cases["outside"] = add(
            store,
            directory,
            project,
            "outside",
            [header("outside", identity("outside-parent"))],
            "父线程在其他项目",
        )
        cases["cycle"] = add(
            store, directory, project, "cycle-a", [header("cycle-a", identity("cycle-b"))], "循环甲"
        )
        add(
            store, directory, project, "cycle-b", [header("cycle-b", identity("cycle-a"))], "循环乙"
        )
        for agent in ("a", "b"):
            row = header("duplicated-parent")
            row["agentId"] = "synthetic-agent-" + agent
            add(store, directory, project, "duplicate-" + agent, [row], "重复原生线程身份")
        cases["ambiguous"] = add(
            store,
            directory,
            project,
            "ambiguous",
            [header("ambiguous", identity("duplicated-parent"))],
            "父身份不唯一",
        )
        return project, cases
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9798)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-session-parent-") as temp:
        directory = Path(temp)
        root = directory / "store"
        project, cases = seed(root, directory)

        class FixtureHandler(Handler):
            def do_GET(self):
                if urlsplit(self.path).path != "/synthetic-parent-fixture/cases":
                    return super().do_GET()
                if self._authorized():
                    self._json(200, {"project": project, "cases": cases})

            def do_POST(self):
                path = urlsplit(self.path).path
                if path not in (
                    "/synthetic-parent-fixture/append-parent",
                    "/synthetic-parent-fixture/cycle",
                ):
                    return super().do_POST()
                self._unread_body = True
                if not self._authorized():
                    return
                if self.headers.get("Content-Length") != "2":
                    self._json(400, {"error": "仅允许合成控制器空对象"})
                    return
                body = self.rfile.read(2)
                self._unread_body = False
                if body != b"{}":
                    self._json(400, {"error": "合成控制器请求无效"})
                    return
                store = Store(root)
                try:
                    if path == "/synthetic-parent-fixture/cycle":
                        result = cycle(store)
                    else:
                        result = add(
                            store,
                            directory,
                            project,
                            "late-parent",
                            [header("late-parent")],
                            "迟到父头原文",
                        )
                finally:
                    store.close()
                self._json(200, result)

        server = LocalServer(root, args.web_dir, args.port, token="synthetic-session-parent-token")
        server.RequestHandlerClass = FixtureHandler
        print(json.dumps({"project": project, "cases": cases}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
