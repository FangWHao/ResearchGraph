"""问答界面合成种子及明确模拟的模型协议；不连接任何远程服务。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from rg.api.qa import QAConfig
from rg.extract.validate import persist
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from tests.golden.test_ingestion import lines, record
from tests.golden.test_read_tools import T1, T2, T3, append, decision

SCOPE = {"dataset_version": "qa_fixture_v1", "analysis_step": "问答验收"}


def seed_qa(store: Store, directory: Path) -> QAConfig:
    for suffix in ("", "二"):
        project = store.project("验收问答项目" + suffix, [])
        labels = ["问答方案甲", "问答已确认", "问答已忽略", "问答旧版", "问答另一范围"]
        messages = [f"{label}{suffix}的合成原文，旧版因验证不足暂缓。" for label in labels]
        messages.append(
            "隐私问答合成邮件 qa-fixture@example.invalid。"
            "<script>window.qaInjected = true</script>"
            "这些文字仅用于验证本地原文显示与发送遮盖。"
        )
        source = directory / ("qa-browser" + suffix + ".jsonl")
        lines(
            source,
            [
                record(
                    text,
                    uuid=f"qa-browser-{suffix}-{index}",
                    sessionId="qa-browser-" + suffix,
                    timestamp=T1,
                )
                for index, text in enumerate(messages)
            ],
        )
        scan_file(store, source, "claude", project)
        events = store.db.execute(
            "SELECT r.event_id FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE s.project_id=? ORDER BY r.seq",
            (project,),
        ).fetchall()
        claims = []
        for index, label in enumerate(labels):
            event = events[index][0]
            raw = store.raw(event)
            start = raw.index(messages[index].encode())
            scope = SCOPE if index != 4 else SCOPE | {"dataset_version": "qa_fixture_v2"}
            claims.append(
                {
                    "claim_type": "entity_version",
                    "temp_id": f"new:{index}",
                    "kind": "approach",
                    "label": label + suffix,
                    "content": "合成问答状态样例",
                    "scope": scope,
                    "evidence": [
                        {
                            "event_id": event,
                            "byte_start": start,
                            "byte_end": start + len(messages[index].encode()),
                            "quote": messages[index],
                        }
                    ],
                }
            )
        output = {"segment_id": project, "claims": claims, "lookup_terms": [], "unresolved": []}
        run = store.db.execute(
            "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,created_at) "
            "VALUES (?,'pass2',?,'validated',?)",
            (project, dumps([row[0] for row in events]), now()),
        ).lastrowid
        assert run is not None
        ids = persist(store, output, project, run, set(), {row[0] for row in events}, project)
        store.review(ids[1], "confirm", "human:合成问答", store.revision())
        store.review(ids[2], "dismiss", "human:合成问答", store.revision())
        # 合成替换版仅作为历史读取验收，原候选与原件保持不变。
        old = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (ids[3],)).fetchone()
        replacement = append(
            store,
            old["entity_id"],
            "entity_version",
            claims[3] | {"label": "问答修正版" + suffix},
            scope=SCOPE,
            state="confirmed",
            replaces=ids[3],
            occurred=T2,
            recorded=now(),
        )
        entity = store.db.execute(
            "SELECT entity_id FROM claims WHERE claim_id=?", (ids[0],)
        ).fetchone()[0]
        rejected = decision(
            store, entity, "rejected", scope=SCOPE, state="confirmed", occurred=T2, recorded=now()
        )
        accepted = decision(store, entity, "accepted", scope=SCOPE, occurred=T3, recorded=now())
        for target, evidence_source in (
            (replacement, ids[3]),
            (rejected, ids[0]),
            (accepted, ids[0]),
        ):
            store.db.execute(
                "INSERT INTO claim_evidence(claim_id,span_id,role) "
                "SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
                (target, evidence_source),
            )
    return QAConfig(
        "https://synthetic-model.invalid",
        "synthetic-qa-model",
        "synthetic-key-only",
        transport=httpx.MockTransport(protocol),
    )


def protocol(request: httpx.Request) -> httpx.Response:
    data = json.loads(request.content)
    if request.url.path == "/responses/input_tokens":
        if "模拟计数不可用" in data["input"]:
            return httpx.Response(503, json={"error": "合成计数故障"})
        return httpx.Response(200, json={"input_tokens": 10})
    assert request.url.path == "/responses"
    sent = json.loads(data["input"])
    question = sent["question"]
    source = sent["sources"][0]
    answer = {
        "status": "answered",
        "statements": [
            {
                "text": "这是合成协议的模型解释；候选、采用与证据状态分别保留。",
                "citations": [
                    {
                        "id": "E99999" if "模拟问答失败" in question else source["citation_id"],
                        "quote": source["text"][:60],
                    }
                ],
            }
        ],
        "caveats": ["仅验证合成协议与界面，不证明研究解释正确。"],
    }
    truncated = "模拟截断" in question
    return httpx.Response(
        200,
        json={
            "status": "incomplete" if truncated else "completed",
            "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": dumps(answer)}]}
            ],
        },
    )
