"""模型记录来源资格界面验收：公开合成临时库，查询过程只读。"""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from rg.api.server import LocalServer
from rg.derive.claim_sources import assess, recorded
from rg.store.database import Store, dumps, now
from tests.golden.test_links_overview import add_object
from tests.golden.test_read_tools import T1, T2, T3, append, review
from tests.injected_sources import legacy_candidate


def model_decision(store: Store, base, target: str, action: str, occurred: str, scope: dict) -> int:
    event = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at,extraction_run_id) "
        "VALUES ('decision_event',?,?,?,'model_inference','model:synthetic','candidate',?,?,?)",
        (
            target,
            dumps(
                {
                    "claim_type": "decision_event",
                    "target": target,
                    "action": action,
                    "reason": "公开合成模型决定",
                    "speaker": "user",
                    "explicitness": "explicit",
                    "referent_unique": True,
                }
            ),
            dumps(scope),
            occurred,
            now(),
            base["extraction_run_id"],
        ),
    ).lastrowid
    assert event is not None
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (event, base["claim_id"]),
    )
    review(store, event, "confirm", recorded=now())
    return event


def seed(store: Store, directory: Path) -> dict:
    originals = {}
    for key in ["excluded", "pending_newer", "pending_older", "unavailable"]:
        project, claim, event = legacy_candidate(store, "claude", "2.1.280-native-mcp", True)
        store.db.execute(
            "UPDATE projects SET name=? WHERE project_id=?", ("验收模型来源 " + key, project)
        )
        originals[key] = (project, claim, event)
    project = originals["unavailable"][0]
    good, good_event = add_object(store, directory, project, str(uuid4()), label="公开合成正常来源")
    # 所有扫描先结束；后续不再触发后台轮转，才能稳定观察尚未核验的模型记录。
    cases = {}
    for key, (project, claim, event) in originals.items():
        base = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim,)).fetchone()
        assert base is not None
        assert assess(store, claim, refresh=True)["status"] == "excluded"
        entity = base["entity_id"]
        scope = json.loads(base["scope"])
        manual = None
        if key != "excluded":
            manual = append(
                store,
                entity,
                "decision_event",
                {
                    "claim_type": "decision_event",
                    "target": entity,
                    "action": "accepted",
                    "reason": "公开合成人工决定",
                    "speaker": "user",
                    "explicitness": "explicit",
                    "referent_unique": True,
                },
                state="confirmed",
                scope=scope,
                occurred=T3 if key == "pending_older" else T1,
                recorded=now(),
            )
        proof = (
            store.db.execute("SELECT * FROM claims WHERE claim_id=?", (good,)).fetchone()
            if key == "unavailable"
            else base
        )
        assert proof is not None
        model = model_decision(
            store,
            proof,
            entity,
            "accepted" if key == "excluded" else "withdrawn",
            T2 if key == "pending_older" else T3,
            scope,
        )
        if key == "excluded":
            assert assess(store, model)["status"] == "excluded"
        elif key == "unavailable":
            assert assess(store, model)["status"] == "not_excluded"
            digest = store.db.execute(
                "SELECT object_sha256 FROM raw_events WHERE event_id=?", (good_event,)
            ).fetchone()[0]
            store.objects.path(digest).unlink()
            assert assess(store, model, refresh=True)["status"] == "unavailable"
        span = store.db.execute(
            "SELECT e.* FROM claim_evidence c JOIN evidence_spans e USING(span_id) "
            "WHERE c.claim_id=?",
            (claim,),
        ).fetchone()
        assert span is not None
        cases[key] = {
            "project_id": project,
            "base_claim_id": claim,
            "entity_id": entity,
            "model_claim_id": model,
            "manual_claim_id": manual,
            "scope": scope,
            "event_id": event,
            "byte_start": span["byte_start"],
            "byte_end": span["byte_end"],
            "quote_sha256": span["quote_sha256"],
        }
    for key, case in cases.items():
        expected = "pending" if key.startswith("pending") else key
        assert recorded(store, case["model_claim_id"])["status"] == expected
    return {"cases": cases, "synthetic_only": True}


def fingerprint(store: Store) -> str:
    return sha256("\n".join(store.db.iterdump()).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9805)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="researchgraph-claim-source-") as folder:
        root = Path(folder) / "store"
        with closing(Store(root)) as store:
            index = seed(store, Path(folder))
            before = fingerprint(store)
        (cache / "frontend-claim-source-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(index, ensure_ascii=False), flush=True)
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-claim-source-token")
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
            (cache / "frontend-claim-source-audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(audit, ensure_ascii=False), flush=True)
            if before != after:
                raise RuntimeError("只读来源资格验收改变了合成库")


if __name__ == "__main__":
    main()
