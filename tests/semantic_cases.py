"""§15 A 的公开合成输入与身份清单；拓扑、状态和折叠预期来自独立 JSON。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from rg.api.views import edit
from rg.extract.provider import ModelResult
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.query.tokenizer import LocalCounter
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from tests.golden.test_ingestion import lines, record
from tests.golden.test_read_tools import append

FIXTURE = Path(__file__).parents[1] / "fixtures/golden/semantic_cases.json"


def cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())["cases"]


def day(number: int) -> str:
    return f"2026-01-{number:02d}T00:00:00+00:00"


def quote(case: dict[str, Any], key: str, description: str) -> str:
    return f"§15 {case['id']} · {key}：{description}"


def _message(
    tool: str, text: str, key: str, identity: str, timestamp: str, role: str = "user"
) -> dict[str, Any]:
    if tool == "claude":
        return record(text, uuid=key, sessionId=identity, timestamp=timestamp, type=role)
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": f"{role if role == 'user' else 'agent'}_message", "message": text},
    }


def _native(case: dict[str, Any]) -> list[dict[str, Any]]:
    identity = case["id"]
    if case.get("native") == "rollback":
        # 原生执行器报告代码回退；只导入报告，绝不执行补丁或读取这个路径。
        changes = {
            "/synthetic/analysis.py": {
                "type": "update",
                "unified_diff": "@@ -1 +1 @@\n-new_analysis()\n+previous_analysis()\n",
                "move_path": None,
            }
        }
        return [
            {
                "timestamp": day(2),
                "type": "event_msg",
                "payload": {
                    "type": kind,
                    "call_id": identity + "-rollback",
                    "turn_id": identity + "-turn",
                    "changes": changes,
                    **extra,
                },
            }
            for kind, extra in (
                ("patch_apply_begin", {"auto_approved": True}),
                ("patch_apply_end", {"success": True, "status": "completed"}),
            )
        ]
    if case.get("native") == "missing_result":
        return [
            {
                "timestamp": day(2),
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": identity + "-missing",
                    "arguments": dumps({"cmd": "synthetic --never-execute"}),
                },
            }
        ]
    return []


def seed_case(store: Store, directory: Path, case: dict[str, Any]) -> dict[str, Any]:
    identity = case["id"]
    project = store.project(case["project_name"], [])
    nodes: dict[str, dict[str, Any]] = {}
    for key, node in case["nodes"].items():
        entity = nodes[node["entity"]]["entity_id"] if "entity" in node else str(uuid4())
        scope = node.get("scope", case["scope"])
        nodes[key] = {"entity_id": entity, "scope": scope, "label": node["label"]}
        if "entity" not in node:
            store.db.execute(
                "INSERT INTO entities VALUES (?,?,?,NULL,?)",
                (entity, project, node["kind"], day(1)),
            )

    # 清单只关联输入语义 key 与运行时身份，不调用语义投影或折叠函数。
    descriptors: list[dict[str, Any]] = []
    for key, node in case["nodes"].items():
        descriptors.append(
            {
                "key": "v:" + key,
                "node": key,
                "type": "entity_version",
                "day": 1,
                "payload": {
                    "kind": node["kind"],
                    "label": node["label"],
                    "content": node["label"],
                    "temp_id": nodes[key]["entity_id"],
                },
                "description": node["label"],
            }
        )
    for relation in case["relations"]:
        descriptors.append(
            {
                "key": relation["key"],
                "node": relation["source"],
                "type": "relation",
                "day": 1,
                "payload": {
                    "source": nodes[relation["source"]]["entity_id"],
                    "target": nodes[relation["target"]]["entity_id"],
                    "relation": relation["relation"],
                },
                "description": (
                    f"声明 {relation['source']} {relation['relation']} {relation['target']}"
                ),
            }
        )
    for join in case["joins"]:
        descriptors.append(
            {
                "key": join["key"],
                "node": join["target"],
                "type": "join_ports",
                "day": 1,
                "payload": {
                    "target": nodes[join["target"]]["entity_id"],
                    "semantics": join["semantics"],
                    "inputs": [
                        {"port": port["port"], "ref": nodes[port["ref"]]["entity_id"]}
                        for port in join["inputs"]
                    ],
                    "selected": nodes[join["selected"]]["entity_id"] if join["selected"] else None,
                },
                "description": f"声明命名端口与 {join['semantics']}，选择 {join['selected']}",
            }
        )
    for event in case["events"]:
        field = "action" if event["type"] == "decision_event" else "state"
        payload = {
            "target": nodes[event["target"]]["entity_id"],
            field: event["value"],
            "reason": "合成明确记录",
        }
        if field == "action":
            payload |= {"speaker": "user", "explicitness": "explicit", "referent_unique": True}
        descriptors.append(
            {
                "key": event["key"],
                "node": event["target"],
                "type": event["type"],
                "day": event["day"],
                "payload": payload,
                "description": f"明确记录 {event['target']} {event['value']}",
            }
        )

    native = case.get("native")
    tool = "codex" if native in {"rollback", "mirror", "missing_result"} else "claude"
    source = directory / (identity + ".jsonl")
    records: list[dict[str, Any]] = []
    if tool == "codex":
        records.append({"type": "session_meta", "payload": {"id": identity, "cwd": "/synthetic"}})
    texts = {d["key"]: quote(case, d["key"], d["description"]) for d in descriptors}
    for descriptor in descriptors:
        if native == "late" and descriptor["key"] == "late":
            continue
        text = texts[descriptor["key"]]
        records.append(
            _message(tool, text, identity + descriptor["key"], identity, day(descriptor["day"]))
        )
        if native == "mirror" and descriptor["key"] == "v:B":
            records.extend(
                [
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    },
                    {
                        "type": "compacted",
                        "payload": {
                            "replacement_history": [{"role": "user", "content": text}],
                            "encrypted_content": "synthetic-only",
                        },
                    },
                ]
            )
    if native == "reextract":
        for key, description, number in (("initial", "初始模型猜测拒绝", 2),):
            texts[key] = quote(case, key, description)
            records.append(
                _message(tool, texts[key], identity + key, identity, day(number), "assistant")
            )
    records.extend(_native(case))
    lines(source, records)
    before_source = source.read_bytes()
    scan_file(store, source, tool, project)
    known_before_late = now()
    rows = store.db.execute(
        "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
        "WHERE s.project_id=? AND r.exclude_reason IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM dedupe_links d WHERE d.alias_id=r.event_id) "
        "ORDER BY r.event_id",
        (project,),
    ).fetchall()
    citations: dict[str, dict[str, Any]] = {}
    for key, text in texts.items():
        if native == "late" and key == "late":
            continue
        raw_row = next(row for row in rows if text.encode() in store.raw(row["event_id"]))
        raw = store.raw(raw_row["event_id"])
        start = raw.index(text.encode())
        citations[key] = {
            "event_id": raw_row["event_id"],
            "byte_start": start,
            "byte_end": start + len(text.encode()),
            "quote": text,
            "quote_sha256": digest(text.encode()),
            "recorded_at": raw_row["recorded_at"],
        }
    claim_index: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        key, node = descriptor["key"], nodes[descriptor["node"]]
        if native == "late" and key == "late":
            # 先形成已经可读的当前决定，再实际导入旧会话，截止不回填记录时间。
            known_before_late = now()
            late_source = directory / (identity + "-late.jsonl")
            lines(
                late_source,
                [_message(tool, texts[key], identity + "late", identity + "-old", day(1))],
            )
            scan_file(store, late_source, tool, project)
            row = store.db.execute(
                "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
                "WHERE s.project_id=? ORDER BY event_id DESC LIMIT 1",
                (project,),
            ).fetchone()
            raw = store.raw(row["event_id"])
            start = raw.index(texts[key].encode())
            citations[key] = {
                "event_id": row["event_id"],
                "byte_start": start,
                "byte_end": start + len(texts[key].encode()),
                "quote": texts[key],
                "quote_sha256": digest(texts[key].encode()),
                "recorded_at": row["recorded_at"],
            }
        payload = {
            "claim_type": descriptor["type"],
            **descriptor["payload"],
            "scope": node["scope"],
        }
        citation = citations[key]
        claim = append(
            store,
            node["entity_id"],
            descriptor["type"],
            payload,
            state="confirmed",
            scope=node["scope"],
            occurred=day(descriptor["day"]),
            recorded=now(),
        )
        span = store.db.execute(
            "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
            "VALUES (?,?,?,?)",
            tuple(citation[k] for k in ("event_id", "byte_start", "byte_end", "quote_sha256")),
        ).lastrowid
        store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim, span))
        claim_index[key] = {"claim_id": claim, "span_id": span, **citation}

    if native == "reextract":
        _reextract(store, project, nodes["B"], citations, claim_index)
    assert source.read_bytes() == before_source
    return {
        "project_id": project,
        "nodes": nodes,
        "claims": claim_index,
        "source_paths": [str(source)],
        "known_before_late": known_before_late,
    }


class ReplayProvider:
    """离线固定输出走真实 Worker；本地精确计数，不连接任何模型服务。"""

    provider = "synthetic-offline"
    remote = False

    def __init__(self, model: str, node: dict[str, Any], citation: dict[str, Any], action: str):
        self.model, self.node, self.citation, self.action = model, node, citation, action
        self.counter = LocalCounter()
        self.calls = 0
        self.pass2_calls = 0

    def count_text(self, text: str) -> int:
        return self.counter.count(text)

    def count_request(self, request: dict[str, Any]) -> int:
        return self.counter.count(dumps(request))

    def request(self, instructions: str, content: str, schema: dict[str, Any], output_budget=4000):
        return {
            "instructions": instructions,
            "input": content,
            "schema": schema,
            "max_tokens": output_budget,
        }

    def generate(self, request: dict[str, Any]) -> ModelResult:
        self.calls += 1
        if "candidates" in request["schema"]["properties"]:
            available = {item["event_id"] for item in json.loads(request["input"])}
            output = {
                "candidates": [
                    {
                        "event_id": self.citation["event_id"],
                        "byte_range": [self.citation["byte_start"], self.citation["byte_end"]],
                        "cue": "decision",
                    }
                ]
                if self.citation["event_id"] in available
                else []
            }
        else:
            self.pass2_calls += 1
            content = json.loads(request["input"])
            assert self.node["entity_id"] in {node["id"] for node in content["working_set"]}
            output = {
                "segment_id": content["segment_id"],
                "claims": [
                    {
                        "claim_type": "decision_event",
                        "target": self.node["entity_id"],
                        "action": self.action,
                        "reason": self.citation["quote"],
                        "speaker": "assistant",
                        "explicitness": "implicit",
                        "referent_unique": False,
                        "scope": self.node["scope"],
                        "evidence": [
                            {
                                k: self.citation[k]
                                for k in (
                                    "event_id",
                                    "byte_start",
                                    "byte_end",
                                    "quote",
                                    "quote_sha256",
                                )
                            }
                        ],
                    }
                ],
                "lookup_terms": [],
                "unresolved": [],
            }
        text = dumps(output)
        return ModelResult(text, self.count_request(request), self.count_text(text), "stop")


def _reextract(
    store: Store,
    project: str,
    node: dict[str, Any],
    citations: dict[str, Any],
    claim_index: dict[str, Any],
) -> None:
    before = {
        row["event_id"]: store.raw(row["event_id"])
        for row in store.db.execute(
            "SELECT r.event_id FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE s.project_id=?",
            (project,),
        )
    }
    for key, model, action in (
        ("initial", "synthetic-model-v1", "rejected"),
        ("reextracted", "synthetic-model-v2", "accepted"),
    ):
        # 两个模型重新解释相同事件窗口，确保模型变化而非新增消息造成新候选。
        citation = citations["initial"]
        provider = ReplayProvider(model, node, citation, action)
        session = store.db.execute(
            "SELECT session_pk FROM raw_events WHERE event_id=?", (citation["event_id"],)
        ).fetchone()[0]
        result = Worker(store, provider).process(
            session, node["scope"], max_event_id=citation["event_id"]
        )
        assert result["claims"] == 1 and result["manual"] == 0
        assert provider.calls >= 2 and provider.pass2_calls == 1
        calls_before_cache = provider.calls
        assert (
            Worker(store, provider).process(
                session, node["scope"], max_event_id=citation["event_id"]
            )["claims"]
            == 0
        )
        assert provider.pass2_calls == 1 and provider.calls == calls_before_cache
        ids = [
            row[0]
            for row in store.db.execute(
                "SELECT c.claim_id FROM claims c JOIN extraction_runs r USING(extraction_run_id) "
                "WHERE r.model=?",
                (model,),
            )
        ]
        assert len(ids) == 1 and store.claim_state(ids[0]) == "candidate"
        span = store.db.execute(
            "SELECT span_id FROM claim_evidence WHERE claim_id=?", (ids[0],)
        ).fetchone()[0]
        claim_index[key] = {"claim_id": ids[0], "span_id": span, **citation}
        if key == "initial":
            payload = json.loads(
                store.db.execute(
                    "SELECT payload FROM claims WHERE claim_id=?", (ids[0],)
                ).fetchone()[0]
            )
            receipt = edit(
                store,
                ids[0],
                {
                    "payload": payload | {"action": "deferred", "reason": "人工更正为暂停"},
                    "scope": node["scope"],
                    "actor": "human:合成A10",
                    "expected_revision": store.revision(),
                },
            )
            claim_index["correction"] = {**claim_index[key], "claim_id": receipt["claim_id"]}
    assert all(store.raw(identity) == raw for identity, raw in before.items())


def seed_semantic_cases(store: Store, directory: Path) -> dict[str, Any]:
    return {case["id"]: seed_case(store, directory, case) for case in cases()}
