"""独立公开笔记验收服务，生产记录/API；不联网、不调用模型。"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from rg.api.server import Handler, LocalServer
from rg.record.note import note, propose_note
from rg.record.question import question
from rg.store.database import Store, dumps

TOKEN = "synthetic-note-token"


def seed(directory: Path) -> dict:
    with closing(Store(directory / "store")) as store:
        project = store.project("验收研究笔记项目", [])
        second = store.project("验收研究笔记项目二", [])
        target = question(
            store,
            {
                "project_id": project,
                "text": "合成关联研究问题",
                "scope": {"data": "公开合成v1", "step": "笔记验收"},
                "actor": "human:公开种子",
                "request_id": str(uuid4()),
                "expected_revision": store.revision(),
            },
        )
        other = question(
            store,
            {
                "project_id": second,
                "text": "另一项目的关联对象",
                "scope": None,
                "actor": "human:公开种子",
                "request_id": str(uuid4()),
                "expected_revision": store.revision(),
            },
        )
        candidates = {}
        for name in ("confirm", "edit"):
            text = "Agent 候选笔记 " + name + "\n<script>window.noteInjected=true</script>"
            candidates[name] = propose_note(
                store,
                project,
                {
                    "text": text,
                    "about": target["entity_id"],
                    "scope": None,
                    "request_id": str(uuid4()),
                    "expected_revision": store.revision(),
                },
            ) | {"text": text}
        for number in range(51):
            note(
                store,
                {
                    "project_id": project,
                    "text": f"合成分页笔记 {number:02}",
                    "scope": None,
                    "actor": "human:公开种子",
                    "request_id": str(uuid4()),
                    "expected_revision": store.revision(),
                },
            )
        index = {
            "project_id": project,
            "second_project": second,
            "target": target,
            "other_target": other,
            "candidates": candidates,
            "max_initial_claim": store.db.execute("SELECT max(claim_id) FROM claims").fetchone()[0],
            "max_initial_event": store.db.execute(
                "SELECT max(event_id) FROM raw_events"
            ).fetchone()[0],
        }
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(root: Path, index: dict) -> dict:
    with closing(Store(root)) as store:
        initial = {
            "raw_events": [
                list(r)
                for r in store.db.execute(
                    "SELECT * FROM raw_events WHERE event_id<=? ORDER BY event_id",
                    (index["max_initial_event"],),
                )
            ],
            "claims": [
                list(r)
                for r in store.db.execute(
                    "SELECT * FROM claims WHERE claim_id<=? ORDER BY claim_id",
                    (index["max_initial_claim"],),
                )
            ],
            "claim_evidence": [
                list(r)
                for r in store.db.execute(
                    "SELECT * FROM claim_evidence WHERE claim_id<=? ORDER BY claim_id,span_id",
                    (index["max_initial_claim"],),
                )
            ],
        }
        return {
            "initial_history_sha256": sha256(dumps(initial).encode()).hexdigest(),
            "revision": store.revision(),
            "model_attempts": store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0],
            "note_requests": store.db.execute("SELECT count(*) FROM note_requests").fetchone()[0],
            "claims": store.db.execute("SELECT count(*) FROM claims").fetchone()[0],
            "events": store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0],
            "reviews": store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9807)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    if args.prepare:
        index = seed(args.fixture_dir)
        (cache / "frontend-notes-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("公开两项目笔记库已通过真实记录入口准备。", flush=True)
        return
    root = args.fixture_dir / "store"
    index = json.loads((args.fixture_dir / "index.json").read_text(encoding="utf-8"))
    before = audit(root, index)

    class FixtureHandler(Handler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path != "/synthetic-notes/audit":
                return super().do_GET()
            if self._authorized():
                self._json(200, audit(root, index))

    server = LocalServer(root, args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        after = audit(root, index)
        result = {
            "准备完成时": before,
            "服务关闭时": after,
            "原始事件与候选内容未改写": before["initial_history_sha256"]
            == after["initial_history_sha256"],
        }
        (cache / "frontend-notes-audit.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not result["原始事件与候选内容未改写"]:
            raise RuntimeError("合成原始历史被改写")


if __name__ == "__main__":
    main()
