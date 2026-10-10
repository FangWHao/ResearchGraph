"""完整研究图界面的独立合成服务；只处理临时库和合成原件。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from rg.api.server import Handler, LocalServer
from rg.derive.worker import derive
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from tests.golden.test_read_tools import T1, T2, T3, T4, append, review
from tests.graph_browser import evidence, seed_graph

TOKEN = "synthetic-research-graph-token"
PRIMARY = "验收语义图项目"
SECONDARY = "验收语义图缺口项目"


def add_entity(store: Store, project: str, kind: str) -> str:
    identity = str(uuid4())
    store.db.execute("INSERT INTO entities VALUES (?,?,?,NULL,?)", (identity, project, kind, T1))
    return identity


def version(store: Store, entity: str, label: str, **kwargs) -> int:
    return append(
        store,
        entity,
        "entity_version",
        {"claim_type": "entity_version", "temp_id": entity, "label": label, "content": label},
        **kwargs,
    )


def native(store: Store, directory: Path, project: str) -> dict:
    session = str(uuid4())
    body = {
        "type": "user",
        "uuid": str(uuid4()),
        "sessionId": session,
        "timestamp": T1,
        "message": {"content": [{"type": "text", "text": "镜像合成原话"}]},
    }
    events = []
    for suffix in ("original", "mirror"):
        path = directory / (suffix + ".jsonl")
        path.write_text(dumps(body) + "\n", encoding="utf-8")
        scan_file(store, path, "claude", project)
        events.append(store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0])
    mirror = store.db.execute(
        "SELECT alias_of FROM raw_events WHERE event_id=?", (events[1],)
    ).fetchone()
    assert mirror is not None and mirror[0] == events[0], "镜像原件保留，正文由明确别名归于同一事件"
    request = {
        "type": "assistant",
        "uuid": str(uuid4()),
        "sessionId": session,
        "timestamp": T2,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "synthetic-no-result",
                    "name": "Bash",
                    "input": {"command": "synthetic-never-execute # 只回退代码，方法继续使用"},
                }
            ]
        },
    }
    path = directory / "missing-result.jsonl"
    path.write_text(dumps(request) + "\n", encoding="utf-8")
    scan_file(store, path, "claude", project)
    derive(store)
    run = store.db.execute("SELECT * FROM runs WHERE call_id='synthetic-no-result'").fetchone()
    assert run is not None
    return {"event_id": run["request_event_id"], "run_id": run["run_id"], "mirror": events}


def seed(root: Path, directory: Path) -> dict:
    store = Store(root)
    try:
        seed_graph(store)
        project = store.db.execute(
            "SELECT project_id FROM projects WHERE name=?", (PRIMARY,)
        ).fetchone()[0]
        secondary = store.db.execute(
            "SELECT project_id FROM projects WHERE name=?", (SECONDARY,)
        ).fetchone()[0]
        rows = store.db.execute(
            "SELECT c.* FROM claims c JOIN entities e USING(entity_id) WHERE e.project_id=?",
            (project,),
        ).fetchall()
        chain = [
            r
            for r in rows
            if r["claim_type"] == "entity_version"
            and json.loads(r["payload"]).get("label")
            in ("原始合成观察", "需保留撤回的中间发现", "阴性实验记录", "合成后续结论")
        ]
        boundary = next(
            r
            for r in rows
            if r["claim_type"] == "relation"
            and json.loads(r["payload"]).get("target") == chain[1]["entity_id"]
        )
        span = store.db.execute(
            "SELECT s.* FROM evidence_spans s JOIN claim_evidence ce USING(span_id) "
            "WHERE ce.claim_id=?",
            (boundary["claim_id"],),
        ).fetchone()
        for _ in range(34):
            identity = store.db.execute(
                "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
                "VALUES (?,?,?,?)",
                (span["event_id"], span["byte_start"], span["byte_end"], span["quote_sha256"]),
            ).lastrowid
            store.db.execute(
                "INSERT INTO claim_evidence VALUES (?,?,'support')",
                (boundary["claim_id"], identity),
            )
        filler = add_entity(store, project, "approach")
        version(store, filler, "大量候选的独立方案", state="confirmed")
        # 同一次合成导入事务，避免每条记录单独持久化拖住监听准备。
        with store.transaction():
            for index in range(2005):
                append(
                    store,
                    filler,
                    "decision_event",
                    {
                        "claim_type": "decision_event",
                        "target": filler,
                        "action": "accepted",
                        "reason": f"合成候选 {index}，不得当采用",
                        "speaker": "assistant",
                        "explicitness": "implicit",
                        "referent_unique": True,
                    },
                )
        historical = add_entity(store, project, "approach")
        original = version(store, historical, "历史更正方案 · 原候选")
        evidence(
            store,
            project,
            original,
            "原候选<script>window.graphInjected=true</script>；只回退代码不撤方法。",
        )
        event = store.db.execute(
            "SELECT r.* FROM raw_events r JOIN evidence_spans s USING(event_id) "
            "JOIN claim_evidence ce USING(span_id) WHERE ce.claim_id=?",
            (original,),
        ).fetchone()
        review(store, original, "confirm", T3)
        replacement = version(
            store,
            historical,
            "历史更正方案 · 人工更正",
            recorded=T4,
            state="confirmed",
            replaces=original,
        )
        evidence(store, project, replacement, "明确人工更正，原件继续保留")
        reextract = version(
            store, historical, "更换模型后的候选内容", recorded="2026-01-05T00:00:00Z"
        )
        evidence(store, project, reextract, "重新提取仍为候选，不自动代替人工更正")
        attempts = []
        for action, occurred in (
            ("proposed", T1),
            ("rejected", "2026-01-01T01:00:00Z"),
            ("accepted", T2),
        ):
            identity = append(
                store,
                historical,
                "decision_event",
                {
                    "claim_type": "decision_event",
                    "target": historical,
                    "action": action,
                    "reason": "先拒绝后重新采用；代码回退没有撤回方法",
                    "speaker": "user",
                    "explicitness": "explicit",
                    "referent_unique": True,
                },
                state="confirmed",
                occurred=occurred,
            )
            evidence(store, project, identity, "明确研究决定：" + action)
            attempts.append(identity)
        late_raw = dumps({"content": "后来才收到的原文，不得混入旧上下文"}).encode()
        store.db.execute(
            "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,object_sha256,"
            "seq,kind,role,occurred_at,recorded_at,line_sha256) "
            "VALUES (?,?,?,?,?,1,'user_text','user',?,?,?)",
            (
                event["session_pk"],
                event["file_instance_id"],
                event["byte_end"],
                event["byte_end"] + len(late_raw),
                store.objects.put(late_raw),
                T1,
                T3,
                digest(late_raw),
            ),
        )
        execution = native(store, directory, project)
        store.db.commit()
        return {
            "project": project,
            "secondary": secondary,
            "chain": [r["claim_id"] for r in chain],
            "boundary": boundary["claim_id"],
            "historical": original,
            "replacement": replacement,
            "reextract": reextract,
            "history_event": event["event_id"],
            "decisions": attempts,
            "reading": {"occurred_until": T2, "known_until": T2},
            "execution": execution,
            "total": store.db.execute(
                "SELECT count(*) FROM claims c JOIN entities e USING(entity_id) WHERE project_id=?",
                (project,),
            ).fetchone()[0],
        }
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9801)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-full-graph-") as temporary:
        directory = Path(temporary)
        root = directory / "store"
        cases = seed(root, directory)

        class FixtureHandler(Handler):
            def do_GET(self):
                if urlsplit(self.path).path != "/synthetic-full-graph/cases":
                    return super().do_GET()
                if self._authorized():
                    self._json(200, cases)

            def do_POST(self):
                if urlsplit(self.path).path != "/synthetic-full-graph/advance":
                    return super().do_POST()
                self._unread_body = True
                if not self._authorized():
                    return
                if self.headers.get("Content-Length") != "2" or self.rfile.read(2) != b"{}":
                    self._json(400, {"error": "合成控制器仅允许空对象"})
                    return
                self._unread_body = False
                store = Store(root)
                try:
                    review(store, cases["historical"], "confirm", now())
                    store.db.commit()
                    result = {"revision": store.revision()}
                finally:
                    store.close()
                self._json(200, result)

        server = LocalServer(root, args.web_dir, args.port, token=TOKEN)
        server.RequestHandlerClass = FixtureHandler
        print(dumps(cases), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
