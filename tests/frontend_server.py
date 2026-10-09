"""浏览器验收专用合成库；不读取任何真实会话或凭据。"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from rg.api.server import LocalServer
from rg.extract.validate import persist
from rg.ingest.scanner import scan_file
from rg.ingest.sources import register as register_source
from rg.ingest.spool import enqueue
from rg.ingest.spool import register as register_spool
from rg.store.database import Store, dumps, now
from tests.golden.test_ingestion import lines, record


def seed(store: Store, directory: Path) -> None:
    project = store.project("合成研究 · 界面信号验证", [Path("/synthetic/research")])
    empty_project = store.project("合成空项目", [Path("/synthetic/empty")])
    messages = [
        "哪些局部界面信号值得继续验证？",
        "界面分层分析",
        "分层方案复查",
        "比较合成队列 A 与 B",
        "相邻区域呈现不同模式",
        "综合不同队列证据",
        "先提出界面分层分析，再采用；暂缓后拒绝，撤回后再次采用。",
        "仅建议核查是否撤回界面分层分析，尚无最终决定。",
    ]
    source = directory / "synthetic-session.jsonl"
    lines(
        source,
        [
            record(
                text,
                uuid=f"synthetic-{index}",
                sessionId="browser-synthetic",
                timestamp=f"2026-10-09T10:0{index}:00Z",
            )
            for index, text in enumerate(messages)
        ],
    )
    scan_file(store, source, "claude", project)
    scope = {"dataset_version": "synthetic_v1", "analysis_step": "界面验证"}
    quotes = []
    for index, message in enumerate(messages):
        raw = store.raw(index + 1)
        start = raw.index(message.encode())
        quotes.append(
            {
                "event_id": index + 1,
                "byte_start": start,
                "byte_end": start + len(message.encode()),
                "quote": message,
            }
        )
    items = []
    kinds = ["question", "approach", "approach", "attempt", "finding", "join"]
    for index, kind in enumerate(kinds):
        items.append(
            {
                "claim_type": "entity_version",
                "temp_id": f"new:{index}",
                "kind": kind,
                "label": messages[index],
                "content": "这是浏览器验收的合成研究记录，仅用于验证交互与证据追溯。",
                "scope": scope,
                "evidence": [quotes[index]],
            }
        )
    relationships = [
        ("new:3", "new:1", "part_of"),
        ("new:1", "new:2", "supersedes"),
        ("new:2", "new:0", "part_of"),
        ("new:1", "new:0", "part_of"),
        ("new:4", "new:0", "supports"),
    ]
    for index, (source_id, target_id, relation) in enumerate(relationships):
        items.append(
            {
                "claim_type": "relation",
                "source": source_id,
                "target": target_id,
                "relation": relation,
                "scope": scope,
                "evidence": [quotes[index]],
            }
        )
    actions = ["proposed", "accepted", "deferred", "rejected", "withdrawn", "accepted"]
    for action in actions:
        items.append(
            {
                "claim_type": "decision_event",
                "target": "new:1",
                "action": action,
                "reason": "合成时间线中的明确人工决定",
                "speaker": "user",
                "explicitness": "explicit",
                "referent_unique": True,
                "scope": scope,
                "evidence": [quotes[6]],
            }
        )
    items.append(
        {
            "claim_type": "join_ports",
            "target": "new:5",
            "semantics": "evidence_synthesis",
            "inputs": [{"port": "模式", "ref": "new:4"}, {"port": "比较", "ref": "new:3"}],
            "selected": None,
            "scope": scope,
            "evidence": [quotes[5]],
        }
    )
    items.append(
        {
            "claim_type": "decision_event",
            "target": "new:1",
            "action": "withdrawn",
            "reason": "模型提出撤回候选，需与已有人工确认逐项核对",
            "speaker": "assistant",
            "explicitness": "implicit",
            "referent_unique": False,
            "scope": scope,
            "evidence": [quotes[7]],
        }
    )
    output = {
        "segment_id": "browser-segment",
        "claims": items,
        "lookup_terms": [],
        "unresolved": [],
    }
    run = store.db.execute(
        "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES ('synthetic-browser','pass2',?,'validated',?,?)",
        (dumps(list(range(1, 9))), dumps(output), now()),
    ).lastrowid
    assert run is not None
    ids = persist(store, output, project, run, set(), set(range(1, 9)), "browser-segment")
    for index, claim_id in enumerate(ids):
        if index not in {4, 10, 18}:
            store.review(claim_id, "confirm", "human:合成验收", store.revision())
    # 同一合成事件含完整动作序列；为各步另存发生时间，L0 不改写。
    for offset, claim_id in enumerate(ids[11:17]):
        # claims 只追加，所以以人工替换版赋予合成时间，而不 UPDATE 原候选。
        row = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        cursor = store.db.execute(
            "INSERT INTO claims (claim_type,entity_id,payload,scope,basis,actor,claim_state, "
            "replaces_claim,occurred_at,recorded_at) VALUES (?,?,?,?,?,'human:合成验收',"
            "'confirmed',?,?,?)",
            (
                row["claim_type"],
                row["entity_id"],
                row["payload"],
                row["scope"],
                "manual",
                claim_id,
                f"2026-10-09T10:{10 + offset}:00Z",
                now(),
            ),
        )
        store.db.execute(
            "INSERT INTO claim_evidence (claim_id,span_id,role) "
            "SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
            (cursor.lastrowid, claim_id),
        )
        store.db.execute(
            "INSERT INTO review_actions (claim_id,action,new_claim_id,actor,expected_revision,"
            "recorded_at) VALUES (?,'edit',?,'human:合成验收',?,?)",
            (claim_id, cursor.lastrowid, store.revision(), now()),
        )
    store.db.execute("UPDATE coverage SET segment_id='browser-segment' WHERE stage='pass2'")
    batch_project = store.project("验收批量项目", [Path("/synthetic/batch")])
    batch_source = directory / "synthetic-batch.jsonl"
    batch_quote = "以下 211 条是合成批量复核记录，仅用于跨页确认验收。"
    lines(batch_source, [record(batch_quote, uuid="batch-only", sessionId="browser-batch")])
    scan_file(store, batch_source, "claude", batch_project)
    event_id = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
    raw = store.raw(event_id)
    start = raw.index(batch_quote.encode())
    batch_evidence = {
        "event_id": event_id,
        "byte_start": start,
        "byte_end": start + len(batch_quote.encode()),
        "quote": batch_quote,
    }
    batch = {
        "segment_id": "batch-segment",
        "lookup_terms": [],
        "unresolved": [],
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": f"new:batch-{index}",
                "kind": "approach",
                "label": f"批量候选 {index + 1:02d}",
                "content": "合成跨页复核候选",
                "scope": scope,
                "evidence": [batch_evidence],
            }
            for index in range(211)
        ],
    }
    batch_run = store.db.execute(
        "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES ('synthetic-batch','pass2',?,'validated',?,?)",
        (dumps([event_id]), dumps(batch), now()),
    ).lastrowid
    assert batch_run is not None
    persist(store, batch, batch_project, batch_run, set(), {event_id}, "batch-segment")
    store.db.execute(
        "UPDATE coverage SET segment_id='batch-segment' WHERE event_id=? AND stage='pass2'",
        (event_id,),
    )
    seed_health(store, project, empty_project, batch_project, source, batch_source)
    seed_l1(store, project, directory)


def seed_l1(store: Store, project: str, directory: Path) -> None:
    """真实 HTTP 的运行/编辑验收输入；命令仅入库，永不执行。"""
    records = [
        {
            "type": "session_meta",
            "payload": {"id": "browser-l1-runtime", "cwd": "/synthetic/research"},
        }
    ]
    for identity, label, result in (
        ("requested", "合成运行仅请求", None),
        ("started", "合成运行中", {"session_id": 88}),
        ("zero", "合成运行退出零", {"exit_code": 0}),
        ("two", "合成运行退出二", {"exit_code": 2}),
        ("unknown", "合成运行退出未知", {}),
    ):
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": identity,
                    "arguments": dumps({"cmd": label}),
                },
            }
        )
        if result is not None:
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": identity,
                        "output": {
                            "wall_time_seconds": 0.1,
                            "output": "合成stdout exit_code: 9",
                            **result,
                        },
                    },
                }
            )
    source = directory / "synthetic-l1-runtime.jsonl"
    lines(source, records)
    scan_file(store, source, "codex", project)
    source = directory / "synthetic-l1-edit.jsonl"
    lines(
        source,
        [
            {
                "type": "assistant",
                "uuid": "synthetic-l1-edit-request",
                "sessionId": "browser-l1-edit",
                "cwd": "/synthetic/research",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "full-edit",
                            "name": "Edit",
                            "input": {
                                "file_path": "/synthetic/research/合成完整编辑.py",
                                "old_string": "old",
                                "new_string": "新",
                            },
                        }
                    ]
                },
            },
            {
                "type": "user",
                "uuid": "synthetic-l1-edit-result",
                "sessionId": "browser-l1-edit",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "full-edit",
                            "content": "合成完整编辑已报告",
                        }
                    ]
                },
                "toolUseResult": {
                    "filePath": "/synthetic/research/合成完整编辑.py",
                    "originalFile": "old\n",
                    "structuredPatch": [
                        {
                            "oldStart": 1,
                            "oldLines": 1,
                            "newStart": 1,
                            "newLines": 1,
                            "lines": ["-old", "+新"],
                        }
                    ],
                },
            },
        ],
    )
    scan_file(store, source, "claude", project)
    source = directory / "synthetic-l1-patch.jsonl"
    lines(
        source,
        [
            {
                "type": "session_meta",
                "payload": {"id": "browser-l1-patch", "cwd": "/synthetic/research"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "call_id": "patch-only",
                    "input": (
                        "*** Begin Patch\n*** Update File: 合成仅补丁.py\n"
                        "@@\n-old\n+new\n*** End Patch\n"
                    ),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "patch-only",
                    "output": "Success. Updated the following files:\nM 合成仅补丁.py\n",
                },
            },
        ],
    )
    scan_file(store, source, "codex", project)


def seed_health(
    store: Store,
    project: str,
    empty_project: str,
    batch_project: str,
    source: Path,
    batch_source: Path,
) -> None:
    """合成统计边界；快照行不代表实际拍摄或引擎成功验收。"""
    register_source(store, source, "claude", project)
    register_source(store, batch_source, "claude", batch_project)
    for state in ("queued", "running", "failed"):
        path = enqueue(
            store.root,
            "claude",
            dumps({"hook_event_name": "SessionStart", "session_id": f"synthetic-{state}"}).encode(),
        )
        register_spool(store)
        store.db.execute(
            "UPDATE jobs SET state=?,error=? WHERE job_id="
            "(SELECT job_id FROM spool_receipts WHERE filename=?)",
            (state, "合成失败边界" if state == "failed" else None, path.name),
        )
    omitted = [{"path": "synthetic-large.bin", "reason": "large_file"}]
    rows = [
        (project, "synthetic-timeout", 1, {"omitted_files": omitted}),
        (project, None, 1, {"omitted_files": omitted}),
        (project, None, None, None),
        (project, None, 0, {"omitted_files": []}),
        (empty_project, "synthetic-unavailable", 0, {"omitted_files": []}),
    ]
    for index, (owner, skipped, async_race, metadata) in enumerate(rows):
        store.db.execute(
            "INSERT INTO workspace_snapshots "
            "(project_id,trigger,skipped,taken_at,snapshot_key,async_race,metadata,recorded_at) "
            "VALUES (?,'synthetic_browser',?,?,?,?,?,?)",
            (
                owner,
                skipped,
                "2026-10-09T12:00:00Z",
                f"synthetic-health-{index}",
                async_race,
                dumps(metadata) if metadata is not None else None,
                now(),
            ),
        )
    seed_model_observations(store, project)


def seed_model_observations(store: Store, project: str) -> None:
    """构造未知与零值的合成尝试账本；不执行计数或生成请求。"""
    for stage, sent, status, tokens in (
        ("pass1", 0, "pending", None),
        ("pass2", 1, "invalid", 0),
    ):
        timestamp = now()
        run = store.db.execute(
            "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,created_at) "
            "VALUES (?,?,'[]',?,?)",
            (f"synthetic-health-{stage}", stage, status, timestamp),
        ).lastrowid
        store.db.execute(
            "INSERT INTO model_attempts "
            "(extraction_run_id,project_id,stage,provider,model,segment_id,input_budget,"
            "output_budget,measured_input_tokens,input_tokens,output_tokens,sent,status,"
            "failure_kind,created_at,finished_at) "
            "VALUES (?,?,?,'synthetic_fixture','synthetic_model',?,2000,100,?,?,?,?,?,?,?,?)",
            (
                run,
                project,
                stage,
                f"synthetic-health-{stage}",
                tokens,
                tokens,
                tokens,
                sent,
                status,
                "citation" if status == "invalid" else None,
                timestamp,
                timestamp if status == "invalid" else None,
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-browser-") as temp:
        directory = Path(temp)
        store = Store(directory / "store")
        seed(store, directory)
        store.project("验收新增人工记录项目", [Path("/synthetic/manual-question")])
        store.project("验收新增人工记录项目二", [Path("/synthetic/manual-question-two")])
        store.close()
        server = LocalServer(
            directory / "store", args.web_dir, args.port, token="synthetic-browser-token"
        )
        print("合成浏览器验收服务已启动；仅含合成记录。", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
