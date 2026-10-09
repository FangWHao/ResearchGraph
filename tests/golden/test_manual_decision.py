from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator

from rg.api import views
from rg.api.server import LocalServer
from rg.extract.schemas import CLAIM_SCHEMA
from rg.extract.validate import persist
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.record.decide import ACTIONS, decide
from rg.record.question import question
from rg.record.resolve import resolve
from rg.record.targets import targets
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record
from tests.golden.test_manual_question import counts as old_counts

SCOPE = {"data": "synthetic_v1", "step": "合成决定"}


def objects(store: Store, project: str, items: list[tuple[str, dict]]) -> list[tuple[str, int]]:
    path = store.root / (str(uuid4()) + ".jsonl")
    before = store.db.execute("SELECT COALESCE(max(event_id),0) FROM raw_events").fetchone()[0]
    lines(path, [record(label, uuid=str(uuid4()), sessionId=str(uuid4())) for label, _ in items])
    scan_file(store, path, "claude", project)
    events = store.db.execute(
        "SELECT event_id FROM raw_events WHERE event_id>? ORDER BY event_id", (before,)
    ).fetchall()
    claims = []
    for index, ((label, scope), event) in enumerate(zip(items, events, strict=True)):
        raw = store.raw(event[0])
        start = raw.index(label.encode())
        claims.append(
            {
                "claim_type": "entity_version",
                "temp_id": f"new:{index}",
                "kind": "approach",
                "label": label,
                "content": "仅用于合成固定案例。",
                "scope": scope,
                "evidence": [
                    {
                        "event_id": event[0],
                        "byte_start": start,
                        "byte_end": start + len(label.encode()),
                        "quote": label,
                    }
                ],
            }
        )
    key = str(uuid4())
    output = {"segment_id": key, "claims": claims, "lookup_terms": [], "unresolved": []}
    run = store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES (?,'pass2',?,'validated',?,?)",
        (key, dumps([event[0] for event in events]), dumps(output), now()),
    ).lastrowid
    assert run is not None
    ids = persist(store, output, project, run, set(), {event[0] for event in events}, key)
    return [
        (
            store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (claim,)).fetchone()[
                0
            ],
            claim,
        )
        for claim in ids
    ]


def body(store: Store, project: str, **changes) -> dict:
    return {
        "project_id": project,
        "selector": "合成方案",
        "action": "accept",
        "why": '  合成理由：保留 "引号"、\\路径和\n换行。  ',
        "scope": SCOPE,
        "actor": "human:合成记录者",
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    } | changes


def choice(store: Store, target: str, **changes) -> dict:
    return {
        "target_id": target,
        "actor": "human:合成复核者",
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    } | changes


def counts(store: Store) -> dict:
    return old_counts(store) | {
        table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("decision_requests", "decision_resolutions")
    }


def setup(store: Store, labels: list[tuple[str, dict]] | None = None):
    project = store.project("人工决定合成项目", [])
    return project, objects(store, project, labels or [("合成方案", SCOPE)])


@pytest.mark.parametrize("action", list(ACTIONS))
def test_explicit_decision_confirms_only_decision_not_target_or_evidence(store: Store, action):
    project, [(target, target_claim)] = setup(store)
    data = body(store, project, action=action, occurred_at="2026-09-01T12:00:00+08:00")
    result = decide(store, data)
    value = views.claim(store, result["claim_id"])["claim"]
    assert result["target_id"] == value["entity_id"] == value["payload"]["target"] == target
    assert value["effective_state"] == value["claim_state"] == "confirmed"
    assert value["basis"] == "manual" and value["confirmation_source"] == "human"
    assert (
        value["payload"]["action"] == ACTIONS[action] and value["payload"]["reason"] == data["why"]
    )
    assert value["payload"]["scope"] == value["scope"] == SCOPE
    assert (
        value["occurred_at"] == "2026-09-01T04:00:00+00:00"
        and value["recorded_at"] != value["occurred_at"]
    )
    assert store.claim_state(target_claim) == "candidate"
    raw = store.raw(result["event_id"])
    assert json.loads(raw)["why"] == data["why"]
    assert json.loads(raw)["input_occurred_at"] == data["occurred_at"]
    for span in value["evidence"]:
        assert digest(raw[span["byte_start"] : span["byte_end"]]) == span["quote_sha256"]
    assert counts(store)["review_actions"] == counts(store)["model_attempts"] == 0
    assert store.search("合成理由")[-1]["event_id"] == result["event_id"]


def test_unknown_scope_is_literal_and_cannot_change_known_scope_adoption(store: Store):
    project, [(target, _)] = setup(store)
    result = decide(store, body(store, project, scope=None))
    value = views.claim(store, result["claim_id"])["claim"]
    assert value["effective_state"] == "confirmed" and value["scope"] is None
    assert value["payload"]["scope"] is None
    original = store.raw(result["event_id"])
    assert working_set(store, project, target, FakeProvider())[0]["state"] == "proposed"
    assert list(
        Draft202012Validator(CLAIM_SCHEMA).iter_errors(
            value["payload"]
            | {"evidence": [{"event_id": 1, "byte_start": 0, "byte_end": 1, "quote": "x"}]}
        )
    )
    edited = views.edit(
        store,
        result["claim_id"],
        {
            "payload": value["payload"],
            "scope": SCOPE,
            "actor": "human:补范围",
            "expected_revision": store.revision(),
        },
    )
    assert views.claim(store, edited["claim_id"])["claim"]["scope"] == SCOPE
    assert working_set(store, project, target, FakeProvider())[0]["state"] == "accepted"
    assert store.raw(result["event_id"]) == original


def test_long_label_preview_preserves_exact_search_beyond_preview(store: Store):
    project = store.project("长对象合成项目", [])
    text = "汉" * 3000 + "末尾精确检索"
    created = question(
        store,
        {
            "project_id": project,
            "text": text,
            "actor": "human:合成",
            "scope": None,
            "request_id": str(uuid4()),
            "expected_revision": store.revision(),
        },
    )
    listing = targets(store, {"project": project, "q": "末尾精确检索"})
    assert listing["total"] == 1
    target = listing["targets"][0]
    assert target["label_truncated"] is True and target["label_total_bytes"] == len(text.encode())
    assert len(target["label"].encode()) <= 8000 and target["label"] == text[: len(target["label"])]
    result = decide(store, body(store, project, selector=created["entity_id"], scope=None))
    assert result["target_id"] == created["entity_id"]


def test_human_edits_of_model_decisions_keep_original_speaker_and_support_later_edits(store: Store):
    project, [(target, target_claim)] = setup(store)
    source = views.claim(store, target_claim)["claim"]["evidence"][0]
    raw = store.raw(source["event_id"])
    span = {key: source[key] for key in ("event_id", "byte_start", "byte_end", "quote_sha256")}
    span["quote"] = raw[span["byte_start"] : span["byte_end"]].decode()
    payload = {
        "claim_type": "decision_event",
        "target": target,
        "action": "proposed",
        "reason": "模型提出的合成建议",
        "scope": SCOPE,
        "speaker": "assistant",
        "explicitness": "implicit",
        "referent_unique": False,
    }
    key = str(uuid4())
    output = {
        "segment_id": key,
        "claims": [payload | {"evidence": [span]}],
        "lookup_terms": [],
        "unresolved": [],
    }
    run = store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES (?,'pass2',?,'validated',?,?)",
        (key, dumps([source["event_id"]]), dumps(output), now()),
    ).lastrowid
    assert run is not None
    [claim] = persist(store, output, project, run, {target}, {source["event_id"]}, key)
    for reason in ("人工第一次补充", "人工第二次补充"):
        current = views.claim(store, claim)["claim"]
        edited = views.edit(
            store,
            claim,
            {
                "payload": current["payload"] | {"reason": reason},
                "scope": SCOPE,
                "actor": "human:人工复核",
                "expected_revision": store.revision(),
            },
        )
        claim = edited["claim_id"]
        value = views.claim(store, claim)["claim"]
        assert (
            value["payload"]["speaker"] == "assistant"
            and value["payload"]["referent_unique"] is False
        )
        assert value["payload"]["reason"] == reason and value["effective_state"] == "confirmed"


@pytest.mark.parametrize("unknown", ["unknown", "未知", " 未确定 ", "不详", "Unspecified", "?"])
def test_unknown_scope_values_never_become_adoption(store: Store, unknown):
    scope = {"data": unknown, "step": "合成"}
    project, [(target, _)] = setup(store, [("合成方案", scope)])
    result = decide(store, body(store, project, scope=scope))
    assert result["effective_state"] == "confirmed"
    assert working_set(store, project, target, FakeProvider())[0]["state"] == "unknown_scope"


def test_full_scope_disambiguates_and_partial_or_other_scope_never_falls_back(store: Store):
    other = {"data": "synthetic_v2", "step": "合成决定"}
    project, rows = setup(store, [("合成方案", SCOPE), ("合成方案", other)])
    assert decide(store, body(store, project))["target_id"] == rows[0][0]
    assert decide(store, body(store, project, scope=other))["target_id"] == rows[1][0]
    for scope in (None, {"data": "synthetic_v1"}, SCOPE | {"cohort": "synthetic"}):
        result = decide(store, body(store, project, scope=scope))
        assert result["target_id"] is None and result["effective_state"] == "candidate"


def test_exact_id_has_priority_and_same_entity_across_scopes_is_one_identity(store: Store):
    project, [(target, original)] = setup(store)
    objects(store, project, [(target, SCOPE)])
    # 为同一个对象追加不同完整范围版本，保留原模型行和原文。
    row = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (original,)).fetchone()
    payload = json.loads(row["payload"]) | {"scope": {"data": "v2"}, "label": "另一范围名"}
    store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "recorded_at) "
        "VALUES ('entity_version',?,?,?,'model_inference','model:synthetic','candidate',?)",
        (target, dumps(payload), dumps(payload["scope"]), now()),
    )
    assert decide(store, body(store, project, selector=target, scope=None))["target_id"] == target


def test_ambiguity_has_no_fake_entity_version_and_cannot_be_confirmed_or_edited(store: Store):
    project, rows = setup(store, [("合成方案", SCOPE), ("合成方案", SCOPE)])
    result = decide(store, body(store, project))
    value = views.claim(store, result["claim_id"])["claim"]
    assert value["pending_decision"]["requires_resolution"] is True
    assert value["payload"]["target"] is None and value["payload"]["referent_unique"] is False
    assert value["entity_id"] not in {row[0] for row in rows}
    assert (
        store.db.execute(
            "SELECT count(*) FROM claims WHERE entity_id=? AND claim_type='entity_version'",
            (value["entity_id"],),
        ).fetchone()[0]
        == 0
    )
    before = counts(store)
    with pytest.raises(ValueError, match="选择对象"):
        store.review(result["claim_id"], "confirm", "human:复核者", store.revision())
    with pytest.raises(ValueError, match="选择对象"):
        views.review(
            store,
            {
                "claim_ids": [rows[0][1], result["claim_id"]],
                "action": "confirm",
                "actor": "human:复核者",
                "expected_revision": store.revision(),
            },
        )
    with pytest.raises(ValueError, match="选择对象"):
        views.edit(
            store,
            result["claim_id"],
            {
                "payload": value["payload"] | {"target": rows[0][0]},
                "scope": SCOPE,
                "actor": "human:复核者",
                "expected_revision": store.revision(),
            },
        )
    assert counts(store) == before and store.claim_state(rows[0][1]) == "candidate"
    with pytest.raises(sqlite3.IntegrityError, match="resolved"):
        store.db.execute(
            "INSERT INTO review_actions(claim_id,action,actor,expected_revision,recorded_at) "
            "VALUES (?,'confirm','human:test',?,?)",
            (result["claim_id"], store.revision(), now()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="candidate"):
        store.db.execute(
            "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
            "recorded_at) "
            "SELECT claim_type,entity_id,payload,scope,basis,actor,'confirmed',recorded_at "
            "FROM claims WHERE claim_id=?",
            (result["claim_id"],),
        )


def test_resolution_appends_original_decision_and_choice_preserving_time_and_scope(store: Store):
    project, rows = setup(store, [("合成方案", SCOPE), ("合成方案", SCOPE)])
    data = body(store, project, occurred_at="2026-09-01T12:00:00+08:00")
    result = decide(store, data)
    old_raw = store.raw(result["event_id"])
    old_claim = views.claim(store, result["claim_id"])["claim"]
    selection = choice(store, rows[1][0])
    saved = resolve(store, result["claim_id"], selection)
    value = views.claim(store, saved["claim_id"])["claim"]
    assert value["effective_state"] == "confirmed" and value["entity_id"] == rows[1][0]
    assert value["replaces_claim"] == result["claim_id"]
    assert value["actor"] == selection["actor"] and value["basis"] == "manual"
    assert (
        value["occurred_at"] == old_claim["occurred_at"]
        and value["recorded_at"] > old_claim["recorded_at"]
    )
    assert value["payload"] == old_claim["payload"] | {
        "target": rows[1][0],
        "referent_unique": True,
    }
    assert {span["event_id"] for span in value["evidence"]} == {
        result["event_id"],
        saved["event_id"],
    }
    assert store.raw(result["event_id"]) == old_raw
    assert json.loads(store.raw(saved["event_id"]))["target_id"] == rows[1][0]
    original = views.claim(store, result["claim_id"])["claim"]
    assert (
        original["effective_state"] == "dismissed"
        and original["pending_decision"]["resolved_claim_id"] == saved["claim_id"]
    )
    assert original["payload"]["target"] is None
    replay = resolve(store, result["claim_id"], selection)
    assert replay["replayed"] is True and replay["claim_id"] == saved["claim_id"]
    assert decide(store, data)["resolved_claim_id"] == saved["claim_id"]


def test_unknown_selector_can_only_choose_object_and_keeps_unknown_scope(store: Store):
    project, [(target, _)] = setup(store)
    result = decide(store, body(store, project, selector="原话里的缩写？", scope=None))
    with pytest.raises(ValueError, match="只能选择对象"):
        resolve(store, result["claim_id"], choice(store, target, scope=SCOPE))
    saved = resolve(store, result["claim_id"], choice(store, target))
    assert views.claim(store, saved["claim_id"])["claim"]["scope"] is None
    assert working_set(store, project, target, FakeProvider())[0]["state"] == "proposed"


@pytest.mark.parametrize("invalid", ["other_project", "other_scope", "dismissed", "missing"])
def test_resolution_rejects_wrong_or_inactive_object_without_writes(store: Store, invalid):
    project, rows = setup(store, [("合成方案", SCOPE), ("合成方案", SCOPE)])
    if invalid == "other_project":
        other_project = store.project("其他合成项目", [])
        [(target, _)] = objects(store, other_project, [("合成方案", SCOPE)])
    elif invalid == "other_scope":
        [(target, _)] = objects(store, project, [("另一范围", {"data": "synthetic_v2"})])
    elif invalid == "dismissed":
        target, claim_id = rows[1]
        store.review(claim_id, "dismiss", "human:测试", store.revision())
    else:
        target = str(uuid4())
    result = decide(store, body(store, project, selector="歧义名称"))
    before = counts(store)
    with pytest.raises(ValueError, match="有效对象"):
        resolve(store, result["claim_id"], choice(store, target))
    assert counts(store) == before


def test_dismissed_pending_decision_and_replaced_confirmed_record_cannot_be_revived(store: Store):
    project, [(target, _)] = setup(store)
    result = decide(store, body(store, project, selector="歧义"))
    store.review(result["claim_id"], "dismiss", "human:驳回", store.revision())
    with pytest.raises(ValueError, match="驳回"):
        resolve(store, result["claim_id"], choice(store, target))
    data = body(store, project)
    direct = decide(store, data)
    value = views.claim(store, direct["claim_id"])["claim"]
    views.edit(
        store,
        direct["claim_id"],
        {
            "payload": value["payload"] | {"reason": "合成补充"},
            "scope": SCOPE,
            "actor": "human:修改",
            "expected_revision": store.revision(),
        },
    )
    before = counts(store)
    with pytest.raises(ValueError, match="修改版"):
        store.review(direct["claim_id"], "confirm", "human:复核", store.revision())
    assert decide(store, data)["effective_state"] == "dismissed"
    assert counts(store) == before


def test_retries_are_global_immutable_intents_and_never_reverse_review(store: Store):
    project, [(target, _)] = setup(store)
    data = body(store, project)
    result = decide(store, data)
    store.review(result["claim_id"], "dismiss", "human:驳回", store.revision())
    before = counts(store)
    assert decide(store, data)["replayed"] is True
    assert decide(store, data)["effective_state"] == "dismissed"
    for field, value in [
        ("why", "新理由"),
        ("action", "withdraw"),
        ("selector", target),
        ("scope", None),
        ("actor", "human:其他"),
    ]:
        with pytest.raises(ConflictError):
            decide(store, data | {field: value})
    with pytest.raises(ConflictError):
        question(
            store,
            {
                "project_id": project,
                "text": "合成问题",
                "scope": SCOPE,
                "actor": data["actor"],
                "request_id": data["request_id"],
                "expected_revision": store.revision(),
            },
        )
    pending = decide(store, body(store, project, selector="歧义"))
    with pytest.raises(ConflictError):
        resolve(store, pending["claim_id"], choice(store, target, request_id=data["request_id"]))
    assert counts(store)["decision_resolutions"] == 0
    assert counts(store)["claims"] == before["claims"] + 1


def test_same_intent_concurrency_and_resolution_replay_survive_later_dismiss(store: Store):
    project, [(target, _)] = setup(store)
    data = body(store, project, selector="歧义")

    def submit():
        connection = Store(store.root)
        try:
            return decide(connection, data)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0]["claim_id"] == results[1]["claim_id"]
    assert {value["replayed"] for value in results} == {True, False}
    selection = choice(store, target)

    def select():
        connection = Store(store.root)
        try:
            return resolve(connection, results[0]["claim_id"], selection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        selected = list(pool.map(lambda _: select(), range(2)))
    assert selected[0]["claim_id"] == selected[1]["claim_id"]
    assert {value["replayed"] for value in selected} == {True, False}
    store.review(selected[0]["claim_id"], "dismiss", "human:后来驳回", store.revision())
    before = counts(store)
    assert resolve(store, results[0]["claim_id"], selection)["replayed"] is True
    assert store.claim_state(selected[0]["claim_id"]) == "dismissed" and counts(store) == before


@pytest.mark.parametrize(
    "change",
    [
        {"selector": " "},
        {"selector": "汉" * 1334},
        {"why": ""},
        {"why": "汉" * 5334},
        {"action": "accepted"},
        {"action": []},
        {"actor": "model:synthetic"},
        {"actor": "human: "},
        {"scope": {}},
        {"scope": []},
        {"scope": {"data": " "}},
        {"scope": {"data": 1}},
        {"request_id": "bad"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"occurred_at": "2026-10-09T12:00:00"},
        {"project_id": "missing"},
        {"evidence": []},
    ],
)
def test_invalid_input_has_no_partial_original_or_claim(store: Store, change):
    project, _ = setup(store)
    before = counts(store)
    with pytest.raises(ValueError):
        decide(store, body(store, project, **change))
    assert counts(store) == before


def test_revision_conflicts_and_faults_rollback_both_creation_and_resolution(
    store: Store, monkeypatch
):
    project, [(target, _)] = setup(store)
    before = counts(store)
    with pytest.raises(ConflictError):
        decide(store, body(store, project, expected_revision=store.revision() + 1))

    def fail(*args, **kwargs):
        raise ValueError("合成写入故障")

    with monkeypatch.context() as patch:
        patch.setattr("rg.record.decide.validate_decision", fail)
        with pytest.raises(ValueError, match="故障"):
            decide(store, body(store, project))
    assert counts(store) == before
    pending = decide(store, body(store, project, selector="歧义"))
    before = counts(store)
    with pytest.raises(ConflictError):
        resolve(store, pending["claim_id"], choice(store, target, expected_revision=0))
    with monkeypatch.context() as patch:
        patch.setattr("rg.record.resolve.evidence", fail)
        with pytest.raises(ValueError, match="故障"):
            resolve(store, pending["claim_id"], choice(store, target))
    assert counts(store) == before and store.claim_state(pending["claim_id"]) == "candidate"


def test_long_reason_and_scope_are_exact_bounded_utf8_evidence(store: Store):
    project, _ = setup(store)
    why = '汉🙂\\"\n' * 1100
    result = decide(store, body(store, project, why=why))
    claim = views.claim(store, result["claim_id"])["claim"]
    assert claim["payload"]["reason"] == why and len(claim["evidence"]) > 4
    raw = store.raw(result["event_id"])
    for span in claim["evidence"]:
        quote = raw[span["byte_start"] : span["byte_end"]]
        assert len(quote) <= 8000 and digest(quote) == span["quote_sha256"]
        quote.decode()


def test_object_lookup_uses_entire_project_and_pages_literal_labels(store: Store):
    project, rows = setup(
        store, [(f"对象 {index:04d}", SCOPE) for index in range(2001)] + [("对象 0000", SCOPE)]
    )
    assert views.graph(store, project)["partial"] is True
    result = decide(store, body(store, project, selector="对象 0000"))
    assert result["target_id"] is None  # 第二个同名对象在图的 2000 条上限之外。
    page = targets(store, {"project": project, "q": "对象", "limit": "200"})
    assert page["total"] == 2002 and page["next_offset"] == 200 and len(page["targets"]) == 200
    tail = targets(store, {"project": project, "q": "对象", "limit": "200", "offset": "2000"})
    assert tail["next_offset"] is None and len(tail["targets"]) == 2
    saved = resolve(store, result["claim_id"], choice(store, rows[-1][0]))
    assert views.claim(store, saved["claim_id"])["claim"]["entity_id"] == rows[-1][0]
    assert targets(store, {"project": project, "q": '" OR *'})["total"] == 0
    assert (
        targets(store, {"project": project, "scope": dumps(SCOPE | {"extra": "none"})})["total"]
        == 0
    )


def test_lookup_prefers_confirmed_version_and_excludes_edited_or_dismissed_versions(store: Store):
    project, [(target, original)] = setup(store)
    store.review(original, "confirm", "human:人工", store.revision())
    row = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (original,)).fetchone()
    candidate_payload = json.loads(row["payload"]) | {"label": "较新候选名字"}
    candidate = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "recorded_at) "
        "VALUES ('entity_version',?,?,?,'model_inference','model:synthetic','candidate',?)",
        (target, dumps(candidate_payload), row["scope"], now()),
    ).lastrowid
    assert candidate is not None
    assert decide(store, body(store, project))["target_id"] == target
    assert targets(store, {"project": project, "q": "较新"})["total"] == 0
    value = views.claim(store, original)["claim"]
    edit = views.edit(
        store,
        original,
        {
            "payload": value["payload"] | {"label": "人工新名字"},
            "scope": SCOPE,
            "actor": "human:人工",
            "expected_revision": store.revision(),
        },
    )
    assert targets(store, {"project": project, "q": "合成方案"})["total"] == 0
    assert (
        targets(store, {"project": project, "q": "人工新名字"})["targets"][0]["claim_id"]
        == edit["claim_id"]
    )
    store.review(candidate, "dismiss", "human:人工", store.revision())
    store.review(edit["claim_id"], "dismiss", "human:人工", store.revision())
    assert targets(store, {"project": project})["total"] == 0


def test_backup_restores_pending_resolution_receipts_and_exact_originals(
    store: Store, tmp_path: Path
):
    project, [(target, _)] = setup(store)
    data = body(store, project, selector="歧义")
    pending = decide(store, data)
    selection = choice(store, target)
    saved = resolve(store, pending["claim_id"], selection)
    raw = {event: store.raw(event) for event in (pending["event_id"], saved["event_id"])}
    root = tmp_path / "restored"
    backup(store, root)
    for path in store.objects.root.rglob("*.zst"):
        path.unlink()
    restored = Store(root)
    try:
        assert all(restored.raw(event) == value for event, value in raw.items())
        assert decide(restored, data)["resolved_claim_id"] == saved["claim_id"]
        assert resolve(restored, pending["claim_id"], selection)["replayed"] is True
        for table in ("decision_requests", "decision_resolutions"):
            for operation in ("UPDATE", "DELETE"):
                sql = (
                    f"DELETE FROM {table}"
                    if operation == "DELETE"
                    else f"UPDATE {table} SET recorded_at='changed'"
                )
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    restored.db.execute(sql)
    finally:
        restored.close()


def test_installed_cli_keeps_literal_text_and_resolves_without_executing_commands(store: Store):
    project, [(target, _)] = setup(store)
    command = [
        str(Path(sys.executable).parent / "rg"),
        "--data-dir",
        str(store.root),
        "decide",
        "withdraw",
        "$(不执行) `不执行` ; '原话'",
        "--why",
        "$(不执行理由)",
        "--project",
        project,
        "--scope",
        "data=synthetic_v1",
        "step=合成决定",
        "--request-id",
        str(uuid4()),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    pending = json.loads(result.stdout)
    assert pending["effective_state"] == "candidate"
    assert json.loads(store.raw(pending["event_id"]))["selector"] == command[5]
    resolved = subprocess.run(
        [
            command[0],
            "--data-dir",
            str(store.root),
            "resolve-decision",
            str(pending["claim_id"]),
            "--target",
            target,
            "--expected-revision",
            str(store.revision()),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resolved.returncode == 0, resolved.stderr
    assert (
        views.claim(store, json.loads(resolved.stdout)["claim_id"])["claim"]["payload"]["action"]
        == "withdrawn"
    )
    listing = subprocess.run(
        [
            command[0],
            "--data-dir",
            str(store.root),
            "decision-targets",
            "--project",
            project,
            "--query",
            "合成",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert (
        listing.returncode == 0 and json.loads(listing.stdout)["targets"][0]["entity_id"] == target
    )


def test_real_http_auth_revision_pending_resolution_and_literal_pagination(
    store: Store, tmp_path: Path
):
    project, [(target, _)] = setup(store)
    data = body(store, project, selector="歧义")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>合成</html>")
    server = LocalServer(store.root, web, port=0, token="synthetic-decision-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
            assert client.post("/api/records/decide", json=data).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            assert (
                client.post(
                    "/api/records/decide", json=data, headers={"Origin": "https://external.invalid"}
                ).status_code
                == 403
            )
            created = client.post("/api/records/decide", json=data)
            assert created.status_code == 200
            pending = created.json()
            assert client.post("/api/records/decide", json=data).json()["replayed"] is True
            assert (
                client.post(
                    "/api/records/decide", json=data | {"request_id": str(uuid4())}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/review",
                    json={
                        "claim_ids": [pending["claim_id"]],
                        "action": "confirm",
                        "actor": "human:复核",
                        "expected_revision": store.revision(),
                    },
                ).status_code
                == 400
            )
            listing = client.get(
                "/api/decision-targets",
                params={"project": project, "q": "合成", "scope": dumps(SCOPE), "limit": 1},
            ).json()
            assert listing["targets"][0]["entity_id"] == target
            choice_body = choice(store, target)
            url = f"/api/records/decisions/{pending['claim_id']}/resolve"
            assert client.post(url, json=choice_body | {"why": "不能改"}).status_code == 400
            response = client.post(url, json=choice_body)
            assert response.status_code == 200 and response.json()["replayed"] is False
            assert client.post(url, json=choice_body).json()["replayed"] is True
            assert (
                client.post(url, json=choice_body | {"target_id": str(uuid4())}).status_code == 409
            )
            assert (
                client.post(
                    "/api/records/decisions/999999/resolve", json=choice(store, target)
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/decision-targets", params={"project": project, "scope": "[]"}
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/decision-targets", params={"project": project, "q": '" OR *'}
                ).json()["total"]
                == 0
            )
    finally:
        server.shutdown()
        thread.join(5)
