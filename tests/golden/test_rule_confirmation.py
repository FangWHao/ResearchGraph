from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rg.extract.differences import confirmed_differences
from rg.extract.provider import ModelResult
from rg.extract.report import report
from rg.extract.rules import RULE_VERSION, confirm_explicit
from rg.extract.validate import persist
from rg.extract.worker import Worker
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.ingest.spans import event_span
from rg.store.database import Store, dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record
from tests.golden.test_links_overview import add_object
from tests.test_worker import PipelineProvider


def seed(store: Store, tmp_path: Path, kind: str = "approach"):
    project = store.project("规则合成项目", [tmp_path])
    claim, _ = add_object(store, tmp_path, project, "seed", kind=kind)
    target = store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (claim,)).fetchone()[
        0
    ]
    return project, target


def source(store: Store, tmp_path: Path, project: str, text: str, mode: str = "user"):
    name = "source-" + str(store.health()["events"])
    path = tmp_path / (name + ".jsonl")
    entry = record(text, name, sessionId=name)
    tool = "claude"
    if mode == "assistant":
        entry["type"] = "assistant"
    elif mode == "tool":
        entry["message"]["content"] = [{"type": "tool_result", "content": text}]
    elif mode == "blocks":
        entry["message"]["content"] = [
            {"type": "text", "text": text},
            {"type": "text", "text": "等等，先不要采用。"},
        ]
    elif mode == "codex":
        tool = "codex"
        entry = {"type": "event_msg", "payload": {"type": "user_message", "message": text}}
    if mode == "escaped":
        path.write_text(json.dumps(entry, ensure_ascii=True) + "\n", encoding="utf-8")
    else:
        lines(path, [entry])
    scan_file(store, path, tool, project)
    event = store.db.execute(
        "SELECT min(event_id) FROM raw_events WHERE file_instance_id=("
        "SELECT max(file_instance_id) FROM source_files)"
    ).fetchone()[0]
    raw = store.raw(event)
    bounds = event_span(raw, tool, 0)
    assert bounds is not None
    a, b, _ = bounds
    return {"event_id": event, "byte_start": a, "byte_end": b, "quote": raw[a:b].decode()}


def decision(target: str, evidence: dict, action: str = "accepted", reason: str = "用户明确采用"):
    return {
        "claim_type": "decision_event",
        "target": target,
        "action": action,
        "reason": reason,
        "speaker": "user",
        "explicitness": "explicit",
        "referent_unique": True,
        "scope": {"dataset_version": "data_v2"},
        "evidence": [evidence],
    }


def save(store: Store, project: str, claims: list[dict], allowed: set[str], **kwargs):
    kwargs.setdefault("expected_scope", {"dataset_version": "data_v2"})
    output = {"segment_id": "rule-case", "claims": claims, "lookup_terms": [], "unresolved": []}
    events = {e["event_id"] for claim in claims for e in claim["evidence"]}
    run = store.db.execute(
        "INSERT INTO extraction_runs (job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES (?,'pass2',?,'validated',?,?)",
        (
            "rule-" + str(store.db.execute("SELECT count(*) FROM extraction_runs").fetchone()[0]),
            dumps(sorted(events)),
            dumps(output),
            now(),
        ),
    ).lastrowid
    return persist(store, output, project, run, allowed, events, "rule-case", **kwargs)


@pytest.mark.parametrize(
    "action,text,reason",
    [
        ("accepted", "采用方法甲。", "用户明确采用"),
        ("accepted", "我决定选择方法甲。", "用户明确选择"),
        ("accepted", "采用方法甲，因为误差更小。", "误差更小"),
        ("accepted", "We adopt 方法甲.", "We adopt 方法甲"),
        ("withdrawn", "撤回方法甲。", "用户明确撤回"),
        ("rejected", "拒绝方法甲。", "用户明确拒绝"),
    ],
)
def test_verified_user_action_appends_rule_review(
    store: Store, tmp_path: Path, action, text, reason
):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, text)
    item = decision(target, span, action, reason)
    before = store.revision()
    claim = save(store, project, [item], {target})[0]
    original = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim,)).fetchone()
    assert original["claim_state"] == "candidate" and original["actor"].startswith("model:")
    assert store.claim_state(claim) == "confirmed"
    review = store.db.execute("SELECT * FROM review_actions WHERE claim_id=?", (claim,)).fetchone()
    proof = json.loads(review["reason"])
    assert review["actor"] == "rule:" + RULE_VERSION
    assert proof["target_ids"] == [target] and proof["scope"] == item["scope"]
    assert proof["event_id"] == span["event_id"] and proof["basis"] == "direct_record"
    assert store.revision() == before + 2
    assert confirm_explicit(store, [claim], project, {target}) == 0
    assert store.revision() == before + 2


@pytest.mark.parametrize("mode", ["codex", "escaped"])
def test_raw_parser_and_escaped_bytes_supply_proof(store: Store, tmp_path: Path, mode):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。", mode)
    claim = save(store, project, [decision(target, span)], {target})[0]
    assert store.claim_state(claim) == "confirmed"


@pytest.mark.parametrize(
    "text",
    [
        "如果测试成功，就采用方法甲。",
        "建议采用方法甲。",
        "可能采用方法甲。",
        "采用它。",
        "采用方法甲吗？",
        "不要采用方法甲。",
        "先不采用方法甲。",
        "他说采用方法甲。",
        "示例：采用方法甲。",
        "采用方法甲。算了，撤回。",
        "采用方法甲，因为误差小，不过现在不要用了。",
    ],
)
def test_ambiguous_or_quoted_statement_stays_candidate(store: Store, tmp_path: Path, text):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, text)
    claim = save(store, project, [decision(target, span)], {target})[0]
    assert store.claim_state(claim) == "candidate"
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["assistant", "tool", "blocks"])
def test_model_speaker_flags_cannot_override_original_speaker(store: Store, tmp_path: Path, mode):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。", mode)
    claim = save(store, project, [decision(target, span)], {target})[0]
    assert store.claim_state(claim) == "candidate"


@pytest.mark.parametrize("reason", ["模型认为效果更好", "通过临床验证", "误差显著降低"])
def test_model_reason_not_present_in_original_is_not_confirmed(
    store: Store, tmp_path: Path, reason
):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    claim = save(store, project, [decision(target, span, reason=reason)], {target})[0]
    assert store.claim_state(claim) == "candidate"


@pytest.mark.parametrize(
    "field,value",
    [("speaker", "assistant"), ("explicitness", "implicit"), ("referent_unique", False)],
)
def test_rule_does_not_confirm_inconsistent_payload_metadata(
    store: Store, tmp_path: Path, field, value
):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    item = decision(target, span)
    item[field] = value
    claim = save(store, project, [item], {target})[0]
    assert store.claim_state(claim) == "candidate"


def test_partial_quote_and_unknown_scope_stay_candidates(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    partial = copy.deepcopy(span)
    partial.update(byte_start=span["byte_start"] + len("采用".encode()), quote="方法甲。")
    claim = save(store, project, [decision(target, partial)], {target})[0]
    assert store.claim_state(claim) == "candidate"
    unknown = decision(target, span)
    unknown["scope"] = {"dataset_version": "unknown"}
    claim = save(store, project, [unknown], {target}, expected_scope=None)[0]
    assert store.claim_state(claim) == "candidate"


def test_duplicate_label_outside_working_set_is_still_ambiguous(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path)
    add_object(store, tmp_path, project, "duplicate")
    span = source(store, tmp_path, project, "采用方法甲。")
    claim = save(store, project, [decision(target, span)], {target})[0]
    assert store.claim_state(claim) == "candidate"
    explicit = source(store, tmp_path, project, f"采用[{target}]。")
    claim = save(store, project, [decision(target, explicit)], {target})[0]
    assert store.claim_state(claim) == "confirmed"


def test_same_label_other_project_or_scope_does_not_conflate(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path)
    add_object(store, tmp_path, project, "other-scope", scope="data_v3")
    other = store.project("另一个项目", [tmp_path / "other"])
    add_object(store, tmp_path, other, "other-project")
    span = source(store, tmp_path, project, "采用方法甲。")
    claim = save(store, project, [decision(target, span)], {target})[0]
    assert store.claim_state(claim) == "confirmed"
    ambiguous = save(store, project, [decision(target, span)], {target}, expected_scope=None)[0]
    assert store.claim_state(ambiguous) == "candidate"


def test_manual_confirmation_is_protected_and_manual_dismissal_wins(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    first = save(store, project, [decision(target, span)], {target})[0]
    store.review(first, "confirm", "human:tester", store.revision())
    later = source(store, tmp_path, project, "撤回方法甲。")
    second = save(store, project, [decision(target, later, "withdrawn", "用户明确撤回")], {target})[
        0
    ]
    assert store.claim_state(first) == "confirmed" and store.claim_state(second) == "candidate"
    comparison = confirmed_differences(store, second)
    assert len(comparison) == 1 and comparison[0]["claim_id"] == first
    assert "action" in comparison[0]["differing_fields"]
    assert comparison[0]["payload"]["action"] == "accepted"
    assert working_set(store, project, "方法甲", FakeProvider())[0]["state"] == "accepted"
    store.review(first, "dismiss", "human:tester", store.revision())
    assert confirm_explicit(store, [first], project, {target}) == 0
    assert store.claim_state(first) == "dismissed"
    assert confirmed_differences(store, second) == []
    assert working_set(store, project, "方法甲", FakeProvider())[0]["state"] == "proposed"


def test_refuted_finding_is_distinct_from_adoption(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path, kind="finding")
    span = source(store, tmp_path, project, "否定方法甲。")
    item = {
        "claim_type": "evidence_event",
        "target": target,
        "state": "refuted",
        "reason": "用户明确否定",
        "scope": {"dataset_version": "data_v2"},
        "evidence": [span],
    }
    claim = save(store, project, [item], {target})[0]
    assert store.claim_state(claim) == "confirmed"
    assert working_set(store, project, f"[{target}]", FakeProvider())[0]["state"] == "proposed"


def test_all_required_proves_each_input_port_and_target(store: Store, tmp_path: Path):
    project, join = seed(store, tmp_path, kind="join")
    ids = []
    for name in ["B", "C"]:
        claim, _ = add_object(store, tmp_path, project, name, label=name)
        ids.append(
            store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (claim,)).fetchone()[
                0
            ]
        )
    span = source(store, tmp_path, project, "B和C共同输入方法甲，所有输入都必需。")
    item = {
        "claim_type": "join_ports",
        "target": join,
        "semantics": "all_required",
        "inputs": [{"ref": ids[0], "port": "B"}, {"ref": ids[1], "port": "C"}],
        "selected": None,
        "scope": {"dataset_version": "data_v2"},
        "evidence": [span],
    }
    claim = save(store, project, [item], {join, *ids})[0]
    assert store.claim_state(claim) == "confirmed"
    bad = copy.deepcopy(item)
    bad["inputs"][0]["port"] = "不在原话的端口"
    bad_claim = save(store, project, [bad], {join, *ids})[0]
    assert store.claim_state(bad_claim) == "candidate"


def test_confirmation_rolls_back_with_candidate_transaction(
    store: Store, tmp_path: Path, monkeypatch
):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    before = store.revision()

    def fail(*args, **kwargs):
        confirm_explicit(*args, **kwargs)
        raise RuntimeError("模拟确认后的提交故障")

    monkeypatch.setattr("rg.extract.validate.confirm_explicit", fail)
    with pytest.raises(RuntimeError):
        save(store, project, [decision(target, span)], {target})
    assert store.revision() == before
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1


def test_rule_confirmation_runs_in_real_worker_and_updates_next_working_set(
    store: Store, tmp_path: Path
):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    session = store.db.execute(
        "SELECT session_pk FROM raw_events WHERE event_id=?", (span["event_id"],)
    ).fetchone()[0]

    class Provider(PipelineProvider):
        def generate(self, request):
            if "candidates" in request["schema"]["properties"]:
                return super().generate(request)
            self.calls += 1
            content = json.loads(request["input"])
            assert any(card["id"] == target for card in content["working_set"])
            raw = content["raw_windows"][0]
            evidence = {
                "event_id": raw["event_id"],
                "byte_start": raw["byte_start"],
                "byte_end": raw["byte_end"],
                "quote": raw["raw"],
            }
            output = {
                "segment_id": content["segment_id"],
                "claims": [decision(target, evidence)],
                "lookup_terms": [],
                "unresolved": [],
            }
            return ModelResult(dumps(output), 10, 20, "stop")

    provider = Provider()
    result = Worker(store, provider).process(session, {"dataset_version": "data_v2"})
    assert result == {"segments": 1, "claims": 1, "manual": 0} and provider.calls == 2
    assert store.health()["pending"] == 3  # seed 直接入库，没有走三阶段覆盖。
    claim = store.db.execute("SELECT max(claim_id) FROM claims").fetchone()[0]
    assert store.claim_state(claim) == "confirmed"
    assert working_set(store, project, "方法甲", FakeProvider())[0]["state"] == "accepted"
    assert Worker(store, provider).process(session, {"dataset_version": "data_v2"})["claims"] == 0
    assert provider.calls == 2


def test_report_displays_independent_confirmation_source(store: Store, tmp_path: Path):
    project, target = seed(store, tmp_path)
    span = source(store, tmp_path, project, "采用方法甲。")
    save(store, project, [decision(target, span)], {target})
    session = store.db.execute(
        "SELECT session_pk FROM raw_events WHERE event_id=?", (span["event_id"],)
    ).fetchone()[0]
    destination = tmp_path / "规则报告.md"
    report(store, session, destination)
    body = destination.read_text(encoding="utf-8")
    assert "rule:" + RULE_VERSION in body and '"basis":"direct_record"' in body
    # 人工修改版直接以人工身份确认，不能因没有第二次审核而被报告归成未复核。
    from rg.api.views import edit

    original = store.db.execute("SELECT * FROM claims ORDER BY claim_id DESC LIMIT 1").fetchone()
    result = edit(
        store,
        original["claim_id"],
        {
            "actor": "human:报告复核",
            "expected_revision": store.revision(),
            "scope": json.loads(original["scope"]),
            "payload": json.loads(original["payload"]) | {"reason": "人工核对的理由"},
        },
    )
    assert store.claim_state(result["claim_id"]) == "confirmed"
    report(store, session, destination)
    assert "审核来源：human:报告复核" in destination.read_text(encoding="utf-8")
