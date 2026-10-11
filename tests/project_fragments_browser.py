"""物理片段归属的公开合成来源；只使用真实扫描和生产 API。"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from rg.api.server import Handler, LocalServer
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from tests.golden.test_read_tools import original, review

TOKEN = "synthetic-project-fragments-token"


def seed(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    root_a, root_b = directory / "public-alpha", directory / "public-beta"
    root_a.mkdir()
    root_b.mkdir()
    paths = [directory / "public-codex.jsonl", directory / "public-claude.jsonl"]
    codex = [{"type": "session_meta", "payload": {"id": str(uuid4()), "cwd": str(root_a)}}]
    for n in range(1, 24):
        if n == 10:
            codex.append({"type": "turn_context", "payload": {"cwd": str(root_b)}})
        else:
            codex.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"公开片段甲 {n}"}],
                    },
                }
            )
    native = str(uuid4())
    claude = [
        {
            "type": "assistant",
            "uuid": str(uuid4()),
            "sessionId": native,
            "cwd": str(root_a if n < 2 else root_b),
            "message": {
                "content": [
                    {"type": "text", "text": f"公开片段乙 {n} 第一块"},
                    {"type": "text", "text": f"公开片段乙 {n} 第二块 <script>字面</script>"},
                ]
            },
        }
        for n in range(5)
    ]
    for path, records in zip(paths, [codex, claude], strict=True):
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
    with closing(Store(directory / "store")) as store:
        first = store.project("验收物理片段项目甲", [root_a])
        second = store.project("验收物理片段项目乙", [root_b])
        for tool, path in zip(["codex", "claude"], paths, strict=True):
            scan_file(store, path, tool, first)
        # 与选定原文无依赖的公开候选/人工确认，核对归属操作不改科研史。
        _, candidate = original(store, first)
        _, confirmed = original(store, second)
        review(store, confirmed, "confirm")
        sources = [
            dict(row)
            for row in store.db.execute(
                "SELECT file_instance_id,session_pk,path,parser FROM source_files "
                "WHERE parser IN ('codex','claude') ORDER BY file_instance_id"
            )
        ]
        for source in sources:
            source["events"] = [
                dict(row)
                for row in store.db.execute(
                    "SELECT event_id,byte_start,byte_end,object_sha256 FROM raw_events "
                    "WHERE file_instance_id=? AND record_index=0 ORDER BY byte_start",
                    (source["file_instance_id"],),
                )
            ]
        index = {
            "first_project": first,
            "second_project": second,
            "sources": sources,
            "candidate": candidate,
            "confirmed": confirmed,
        }
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(directory: Path) -> dict:
    with closing(Store(directory / "store")) as store:
        tables = (
            "raw_events",
            "claims",
            "entities",
            "review_actions",
            "evidence_spans",
            "claim_evidence",
            "model_attempts",
        )
        rows = {
            name: [list(r) for r in store.db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in tables
        }
        result = {
            "research_sha256": sha256(dumps(rows).encode()).hexdigest(),
            "counts": {name: len(items) for name, items in rows.items()},
            "revision": store.revision(),
            "claim_states": [
                [r[0], store.claim_state(r[0])]
                for r in store.db.execute("SELECT claim_id FROM claims ORDER BY claim_id")
            ],
            "objects": {
                row[0]: sha256(store.raw(row[1])).hexdigest()
                for row in store.db.execute(
                    "SELECT object_sha256,min(event_id) FROM raw_events GROUP BY object_sha256"
                )
            },
        }
    result["input_files"] = {
        path.name: sha256(path.read_bytes()).hexdigest() for path in directory.glob("*.jsonl")
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9809)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    if args.prepare:
        index = seed(args.fixture_dir)
        (cache / "frontend-project-fragments-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("两个公开项目、Codex 24记录、Claude 5多块记录已真实扫描。", flush=True)
        return
    before = audit(args.fixture_dir)

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-fragments/audit":
                return super().do_GET()
            if self._authorized():
                self._json(200, audit(args.fixture_dir))

    server = LocalServer(args.fixture_dir / "store", args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        after = audit(args.fixture_dir)
        protected = ["research_sha256", "counts", "claim_states", "objects", "input_files"]
        intact = all(before[key] == after[key] for key in protected)
        (cache / "frontend-project-fragments-audit.json").write_text(
            json.dumps(
                {"服务前": before, "服务后": after, "原件候选审核未改写": intact},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not intact:
            raise RuntimeError("原件、候选或审核历史被改写")


if __name__ == "__main__":
    main()
