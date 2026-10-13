"""旧独立规则资格界面验收：仅公开合成资料，生产 API 查询只读。"""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from rg.api.server import LocalServer
from rg.derive import claim_sources, eligibility, rule_references
from rg.ingest.scanner import scan_file
from rg.ingest.spans import event_span
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from tests.golden.test_ingestion import record
from tests.golden.test_read_tools import T1, T2, T3, append
from tests.golden.test_rule_confirmation import decision, save

SCOPE = {"dataset_version": "data_v2"}
OTHER = {"dataset_version": "data_v3"}
NAMES = [
    "pending_newer",
    "pending_older",
    "verified",
    "invalid",
    "unverifiable",
    "unavailable",
    "human_override",
]


def definition(store: Store, project: str, label: str, *, kind="approach", scope=SCOPE):
    entity = str(uuid4())
    store.db.execute("INSERT INTO entities VALUES(?,?,?,NULL,?)", (entity, project, kind, now()))
    claim = append(
        store,
        entity,
        "entity_version",
        {
            "claim_type": "entity_version",
            "temp_id": entity,
            "kind": kind,
            "label": label,
            "content": "公开合成定义",
            "scope": scope,
        },
        scope=scope,
        state="confirmed",
        recorded=now(),
    )
    return entity, claim


def source(store: Store, directory: Path, project: str, text: str, time: str):
    path = directory / (str(uuid4()) + ".jsonl")
    entry = record(text, str(uuid4()), sessionId=str(uuid4()), timestamp=time, version="2.1.280")
    path.write_text(dumps(entry) + "\n", encoding="utf-8")
    scan_file(store, path, "claude", project)
    event = store.db.execute(
        "SELECT event_id FROM raw_events ORDER BY event_id DESC LIMIT 1"
    ).fetchone()[0]
    raw = store.raw(event)
    bounds = event_span(raw, "claude", 0)
    assert bounds is not None and bounds[2]
    a, b, _ = bounds
    return {"event_id": event, "byte_start": a, "byte_end": b, "quote": raw[a:b].decode()}


def legacy(store: Store, project: str, entity: str, span: dict, *, evidence=False):
    payload = (
        {
            "claim_type": "evidence_event",
            "target": entity,
            "state": "refuted",
            "reason": "用户明确否定",
            "scope": SCOPE,
            "evidence": [span],
        }
        if evidence
        else decision(entity, span, "withdrawn", "用户明确撤回")
    )
    with patch("rg.extract.validate.confirm_explicit", return_value=0):
        claim = save(store, project, [payload], {entity})[0]
    run = store.db.execute(
        "SELECT extraction_run_id FROM claims WHERE claim_id=?", (claim,)
    ).fetchone()[0]
    # 公开旧运行的实际测试工作集；不借缺记录猜对象曾在其中。
    store.db.execute(
        "UPDATE extraction_runs SET working_set_ids=? WHERE extraction_run_id=?",
        (dumps([entity]), run),
    )
    proof = {
        "rule_version": "explicit-user-v1",
        "basis": "direct_record",
        "scope": SCOPE,
        "target_ids": [entity],
        "reason_source": "verified-action-description",
        **{k: span[k] for k in ("event_id", "byte_start", "byte_end")},
        "quote_sha256": digest(span["quote"].encode()),
    }
    review = store.db.execute(
        "INSERT INTO review_actions(claim_id,action,reason,actor,expected_revision,recorded_at) "
        "VALUES(?,'confirm',?,'rule:explicit-user-v1',?,?)",
        (claim, dumps(proof), store.revision(), now()),
    ).lastrowid
    assert review is not None
    return claim, review


def seed(store: Store, directory: Path):
    bases = {}
    for name in NAMES:
        project = store.project("验收旧规则资格 " + name, [directory / name])
        entity, definition_id = definition(store, project, "方法甲")
        if name == "unverifiable":
            append(
                store,
                entity,
                "entity_version",
                {
                    "claim_type": "entity_version",
                    "temp_id": entity,
                    "kind": "approach",
                    "label": "方法甲",
                    "content": "另一完整范围",
                    "scope": OTHER,
                },
                scope=OTHER,
                state="confirmed",
                recorded=now(),
            )
        finding = (
            definition(store, project, "证据发现甲", kind="finding")
            if name == "pending_newer"
            else None
        )
        bases[name] = {
            "project_id": project,
            "entity_id": entity,
            "definition_id": definition_id,
            "finding": finding,
        }
    # 先完成全部扫描；后面不再让后台轮转补查刻意保留的 pending。
    for name, item in bases.items():
        item["span"] = source(
            store,
            directory,
            item["project_id"],
            "采用方法甲。" if name == "invalid" else "撤回方法甲。",
            T2 if name == "pending_older" else T3,
        )
        if item["finding"]:
            item["finding_span"] = source(
                store, directory, item["project_id"], "否定证据发现甲。", T3
            )
    cases = {}
    for name, item in bases.items():
        entity, project, span = item["entity_id"], item["project_id"], item["span"]
        manual = append(
            store,
            entity,
            "decision_event",
            {
                "claim_type": "decision_event",
                "target": entity,
                "action": "accepted",
                "reason": "公开合成人工采用",
                "speaker": "user",
                "explicitness": "explicit",
                "referent_unique": True,
            },
            state="confirmed",
            scope=SCOPE,
            occurred=T3 if name == "pending_older" else T1,
            recorded=now(),
        )
        if name == "unverifiable":
            append(
                store,
                entity,
                "decision_event",
                {
                    "claim_type": "decision_event",
                    "target": entity,
                    "action": "accepted",
                    "reason": "另一个范围的人工采用",
                },
                state="confirmed",
                scope=OTHER,
                occurred=T1,
                recorded=now(),
            )
        claim, review = legacy(store, project, entity, span)
        assert claim_sources.assess(store, claim)["status"] == "not_excluded"
        if name == "unavailable":
            # 只移除本临时合成库复制的对象，来源已保存诊断保留；规则独立实读失败。
            object_hash = store.db.execute(
                "SELECT object_sha256 FROM raw_events WHERE event_id=?", (span["event_id"],)
            ).fetchone()[0]
            store.objects.path(object_hash).unlink()
        expected = name if name in {"invalid", "unverifiable", "unavailable"} else "verified"
        if not name.startswith("pending"):
            assert rule_references.assess(store, claim)["status"] == expected
        if name == "human_override":
            store.review(claim, "confirm", "human:公开人工覆盖", store.revision())
        case = {
            **{k: item[k] for k in ("project_id", "entity_id", "definition_id")},
            "claim_id": claim,
            "rule_review_id": review,
            "manual_claim_id": manual,
            "scope": SCOPE,
            "span": span,
            "quote_sha256": digest(span["quote"].encode()),
        }
        if item["finding"]:
            finding, definition_id = item["finding"]
            append(
                store,
                finding,
                "evidence_event",
                {
                    "claim_type": "evidence_event",
                    "target": finding,
                    "state": "supported",
                    "reason": "公开合成人工支持",
                },
                state="confirmed",
                scope=SCOPE,
                occurred=T1,
                recorded=now(),
            )
            evidence_claim, evidence_review = legacy(
                store, project, finding, item["finding_span"], evidence=True
            )
            assert claim_sources.assess(store, evidence_claim)["status"] == "not_excluded"
            case["finding"] = {
                "entity_id": finding,
                "definition_id": definition_id,
                "claim_id": evidence_claim,
                "rule_review_id": evidence_review,
            }
        cases[name] = case
    for name, item in cases.items():
        status = (
            "pending"
            if name.startswith("pending")
            else "not_required"
            if name == "human_override"
            else name
        )
        result = eligibility.recorded(store, item["claim_id"])
        assert result["rule_qualification"]["status"] == status
        assert result["source_qualification"]["status"] == "not_excluded"
        assert result["input_eligible"] == (status in {"verified", "not_required"})
    return {"cases": cases, "synthetic_only": True}


def fingerprint(store: Store):
    return sha256("\n".join(store.db.iterdump()).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9806)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="researchgraph-rule-qualification-") as folder:
        root = Path(folder) / "store"
        with closing(Store(root)) as store:
            index = seed(store, Path(folder))
            before = fingerprint(store)
        (cache / "frontend-rule-qualification-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(index, ensure_ascii=False), flush=True)
        server = LocalServer(
            root, args.web_dir, args.port, token="synthetic-rule-qualification-token"
        )
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            with closing(Store(root)) as store:
                after = fingerprint(store)
            audit = {
                "查询前数据库摘要": before,
                "查询后数据库摘要": after,
                "逐表导出相同": before == after,
            }
            (cache / "frontend-rule-qualification-audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(audit, ensure_ascii=False), flush=True)
            assert before == after, "界面只读查询改变了公开合成库"


if __name__ == "__main__":
    main()
