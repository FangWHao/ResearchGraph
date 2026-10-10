"""父线程已存原文补记的独立合成服务，控制器仅运行生产扫描循环。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from rg.api.server import Handler, LocalServer
from rg.ingest.scanner import scan_file
from rg.ingest.watch import cycle
from rg.store import migrations
from rg.store.database import Store
from tests.session_parent_browser import header, identity

TOKEN = "synthetic-parent-backfill-token"


def body(text: str, **fields) -> dict:
    return {
        "type": "response_item",
        "timestamp": "2026-10-11T00:01:00Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
        **fields,
    }


def add(store: Store, directory: Path, project: str, name: str, records: list[dict]) -> dict:
    path = directory / (name + ".jsonl")
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8"
    )
    scan_file(store, path, "codex", project)
    events = store.db.execute(
        "SELECT r.event_id,r.session_pk,r.kind FROM raw_events r JOIN source_files f "
        "USING(file_instance_id) WHERE f.path=? ORDER BY r.event_id",
        (str(path),),
    ).fetchall()
    return {
        "event_id": next(event["event_id"] for event in events if event["kind"] == "user_msg"),
        "session_pk": events[0]["session_pk"],
        "event_ids": [event["event_id"] for event in events],
        "heads": [event["event_id"] for event in events if event["kind"] == "meta"],
    }


def seed(directory: Path) -> None:
    root = directory / "store"
    cases = {}
    latest = migrations.LATEST_VERSION
    try:
        migrations.LATEST_VERSION = 20
        store = Store(root)
        try:
            project = store.project("验收父线程补记项目", [])
            other = store.project("验收父线程补记项目二", [])
            for name in ("stable-parent", "other-parent", "duplicate-parent"):
                cases[name] = add(
                    store, directory, project, name, [header(name), body("合成父原文" + name)]
                )
            cases["stable"] = add(
                store,
                directory,
                project,
                "stable",
                [header("stable", identity("stable-parent"))]
                + [body("合成积压原文" + str(i)) for i in range(259)],
            )
            cases["conflicting"] = add(
                store,
                directory,
                project,
                "conflicting",
                [header("conflicting", identity("stable-parent"))]
                + [body("合成后来冲突原文" + str(i)) for i in range(258)]
                + [header("conflicting", identity("other-parent"))],
            )
            cases["identity"] = add(
                store,
                directory,
                project,
                "identity-child",
                [header("identity-child", identity("duplicate-parent")), body("等待全库身份核对")],
            )
            cases["duplicate"] = add(
                store,
                directory,
                other,
                "identity-backlog",
                [
                    body(
                        "另一项目的旧身份原文" + str(i),
                        sessionId=identity("duplicate-parent"),
                        agentId="synthetic-duplicate",
                    )
                    for i in range(259)
                ]
                + [{**header("duplicate-parent"), "agentId": "synthetic-duplicate"}],
            )
        finally:
            store.close()
    finally:
        migrations.LATEST_VERSION = latest
    paths = list(directory.glob("*.jsonl"))
    for path in paths:
        path.unlink()
    store = Store(root)
    try:
        assert latest >= 23
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == latest
        offsets = store.db.execute(
            "SELECT parent_observed_offset,committed_offset FROM source_files"
        ).fetchall()
        assert len(offsets) == len(paths)
        assert all(row["parent_observed_offset"] == 0 < row["committed_offset"] for row in offsets)
    finally:
        store.close()
    result = {"project": project, "cases": cases, "removed_sources": len(paths)}
    (directory / "fixture.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    print("已准备旧库并删除自身合成原日志", len(paths), flush=True)


def serve(directory: Path, port: int, web: Path) -> None:
    root = directory / "store"
    fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-parent-backfill/cases":
                return super().do_GET()
            if self._authorized():
                self._json(200, fixture)

        def do_POST(self):
            if urlsplit(self.path).path != "/synthetic-parent-backfill/cycle":
                return super().do_POST()
            self._unread_body = True
            if not self._authorized():
                return
            if self.headers.get("Content-Length") != "2":
                self._json(400, {"error": "合成控制器仅允许空对象"})
                return
            data = self.rfile.read(2)
            self._unread_body = False
            if data != b"{}":
                self._json(400, {"error": "合成控制器请求无效"})
                return
            store = Store(root)
            try:
                result = cycle(store)
            finally:
                store.close()
            self._json(200, result)

    server = LocalServer(root, web, port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    print("合成补记服务已监听", port, flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9800)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    parser.add_argument("--fixture-dir", type=Path)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.fixture_dir:
        if args.prepare:
            seed(args.fixture_dir)
        else:
            serve(args.fixture_dir, args.port, args.web_dir)
        return
    with tempfile.TemporaryDirectory(prefix="researchgraph-parent-backfill-") as temp:
        directory = Path(temp)
        seed(directory)
        serve(directory, args.port, args.web_dir)


if __name__ == "__main__":
    main()
