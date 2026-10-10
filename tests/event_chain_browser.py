"""Claude事件父链界面的独立真实扫描服务；资料仅在临时目录生成。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from rg.api.server import Handler, LocalServer
from rg.ingest.scanner import scan_file
from rg.store import migrations
from rg.store.database import Store

TOKEN = "synthetic-event-chain-token"


def identity(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "researchgraph-synthetic-claude-chain:" + name))


def row(name: str, text: str, **fields) -> dict:
    return {
        "type": "user",
        "uuid": identity(name),
        "timestamp": "2026-10-11T00:00:00Z",
        "version": "2.1.214",
        "message": {"content": text},
        **fields,
    }


def add(store: Store, directory: Path, project: str, name: str, records: list[dict]) -> dict:
    path = directory / (name + ".jsonl")
    path.write_text(
        "".join(
            json.dumps({"sessionId": identity("session-" + name), **record}, ensure_ascii=False)
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    scan_file(store, path, "claude", project)
    events = store.db.execute(
        "SELECT r.event_id,r.session_pk,r.file_instance_id,r.alias_of FROM raw_events r "
        "JOIN source_files f USING(file_instance_id) WHERE f.path=? ORDER BY r.event_id",
        (str(path),),
    ).fetchall()
    return {
        "event_ids": [event["event_id"] for event in events],
        "session_pk": events[0]["session_pk"],
        "file_instance_id": events[0]["file_instance_id"],
        "aliases": [event["alias_of"] for event in events],
    }


def seed(directory: Path) -> dict:
    root = directory / "store"
    cases = {}
    latest = migrations.LATEST_VERSION
    try:
        migrations.LATEST_VERSION = 21
        store = Store(root)
        try:
            project = store.project("验收事件父链项目", [])
            records = [
                row("legacy-" + str(i), "旧记录" + str(i), parentUuid=None) for i in range(260)
            ]
            cases["backlog"] = add(store, directory, project, "legacy", records)
        finally:
            store.close()
    finally:
        migrations.LATEST_VERSION = latest
    store = Store(root)
    try:
        other = store.project("验收事件父链项目二", [])
        for suffix in ("a", "b"):
            parent = "fork-parent-" + suffix
            records = [
                row(parent, "不同fork的父记录" + suffix, parentUuid=None, isSidechain=False),
                row("fork-child", "相同UUID与正文的来源变体", parentUuid=identity(parent)),
            ]
            cases["fork_" + suffix] = add(store, directory, project, "fork-" + suffix, records)
        cases["multiblock"] = add(
            store,
            directory,
            project,
            "multiblock",
            [
                row("block-parent", "多块父记录", parentUuid=None, isSidechain=False),
                row(
                    "blocks",
                    "未使用",
                    parentUuid=identity("block-parent"),
                    isSidechain=True,
                    message={
                        "content": [
                            {"type": "text", "text": "第一内容块"},
                            {
                                "type": "text",
                                "text": "第二块<script>window.chainInjected=true</script>",
                            },
                        ]
                    },
                ),
            ],
        )
        cases["late"] = add(
            store,
            directory,
            project,
            "late",
            [row("late-child", "迟到父记录", parentUuid=identity("late-parent"))],
        )
        cases["missing_field"] = add(
            store, directory, project, "missing", [row("missing", "未声明")]
        )
        cases["null_parent"] = add(
            store, directory, project, "null", [row("null", "明确空父", parentUuid=None)]
        )
        cases["invalid_sidechain"] = add(
            store,
            directory,
            project,
            "invalid-sidechain",
            [row("invalid-sidechain", "非法旁支", parentUuid=None, isSidechain="false")],
        )
        cases["invalid_parent"] = add(
            store,
            directory,
            project,
            "invalid-parent",
            [row("invalid-parent", "非法父ID", parentUuid="synthetic-invalid")],
        )
        cases["uuid_unavailable"] = add(
            store,
            directory,
            project,
            "invalid-uuid",
            [row("invalid-uuid", "无效记录ID", uuid="synthetic-invalid", parentUuid=None)],
        )
        cases["conflicting"] = add(
            store,
            directory,
            project,
            "conflicting",
            [
                row("conflicting", "冲突正文", parentUuid=identity("fork-parent-a")),
                row("conflicting", "冲突正文", parentUuid=identity("fork-parent-b")),
            ],
        )
        for suffix in ("a", "b"):
            add(
                store,
                directory,
                project,
                "ambiguous-parent-" + suffix,
                [row("ambiguous-parent", "不等价父正文" + suffix, parentUuid=None)],
            )
        cases["ambiguous"] = add(
            store,
            directory,
            project,
            "ambiguous",
            [row("ambiguous-child", "不唯一父记录", parentUuid=identity("ambiguous-parent"))],
        )
        for index in range(21):
            add(
                store,
                directory,
                project,
                "copy-" + str(index),
                [row("copy-parent", "等价父正文", parentUuid=None, isSidechain=False)],
            )
        cases["copies"] = add(
            store,
            directory,
            project,
            "copies-child",
            [row("copy-child", "多个父副本", parentUuid=identity("copy-parent"))],
        )
        cases["cycle"] = add(
            store,
            directory,
            project,
            "cycle",
            [
                row("cycle-a", "循环甲", parentUuid=identity("cycle-b")),
                row("cycle-b", "循环乙", parentUuid=identity("cycle-a")),
            ],
        )
        records = [
            row(
                "depth-" + str(index),
                "祖先元数据" + str(index),
                parentUuid=identity("depth-" + str(index + 1)) if index < 64 else None,
            )
            for index in range(65)
        ]
        cases["depth"] = add(store, directory, project, "depth", records)
        cases["outside_parent"] = add(
            store,
            directory,
            other,
            "outside-parent",
            [row("outside-parent", "其它项目", parentUuid=None)],
        )
        cases["outside"] = add(
            store,
            directory,
            project,
            "outside",
            [row("outside-child", "跨项目父链", parentUuid=identity("outside-parent"))],
        )
        result = {"project": project, "cases": cases}
        (directory / "fixture.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        return result
    finally:
        store.close()


def serve(directory: Path, port: int, web: Path) -> None:
    root = directory / "store"
    fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-chain-fixture/cases":
                return super().do_GET()
            if self._authorized():
                self._json(200, fixture)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in {
                "/synthetic-chain-fixture/append-parent",
                "/synthetic-chain-fixture/backfill",
            }:
                return super().do_POST()
            self._unread_body = True
            if not self._authorized():
                return
            if self.headers.get("Content-Length") != "2":
                self._json(400, {"error": "合成控制器仅允许空对象"})
                return
            body = self.rfile.read(2)
            self._unread_body = False
            if body != b"{}":
                self._json(400, {"error": "合成控制器请求无效"})
                return
            store = Store(root)
            try:
                if path.endswith("append-parent"):
                    result = add(
                        store,
                        directory,
                        fixture["project"],
                        "late-parent",
                        [row("late-parent", "实际迟到父原文", parentUuid=None)],
                    )
                else:
                    result = scan_file(
                        store, directory / "legacy.jsonl", "claude", fixture["project"]
                    )
            finally:
                store.close()
            self._json(200, result)

    server = LocalServer(root, web, port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    print("合成父链服务已监听", port, flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9799)
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
    with tempfile.TemporaryDirectory(prefix="researchgraph-event-chain-") as temp:
        directory = Path(temp)
        seed(directory)
        serve(directory, args.port, args.web_dir)


if __name__ == "__main__":
    main()
