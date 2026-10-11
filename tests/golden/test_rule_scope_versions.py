from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from rg.extract import worker as worker_module
from rg.extract.provider import ModelResult
from rg.extract.schemas import PASS1_SCHEMA, PASS2_SCHEMA
from rg.extract.segmenter import Segment
from rg.extract.validate import persist
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.ingest.spans import event_span
from rg.store.database import Store, dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_extraction import new_run
from tests.golden.test_ingestion import record
from tests.golden.test_scoped_working_context import another_scope, model_result
from tests.golden.test_scoped_working_context import source_object as original_source_object


def source_object(store, tmp_path):
    # 只固定公开fixture的实体ID；不让随机手机号形状触发正文遮盖。
    identity = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    with patch("rg.extract.validate.uuid", SimpleNamespace(uuid4=lambda: identity)):
        return original_source_object(store, tmp_path)


class ActionProvider(FakeProvider):
    def __init__(self, target: str, evidence: dict):
        super().__init__(count=10)
        self.target = target
        self.evidence = evidence

    def generate(self, request):
        self.calls += 1
        content = json.loads(request["input"])
        if isinstance(content, list):
            output = {
                "candidates": [
                    {"event_id": x["event_id"], "byte_range": x["byte_range"], "cue": "decision"}
                    for x in content
                    if not x.get("context_only")
                ]
            }
        else:
            output = {
                "segment_id": content["segment_id"],
                "claims": [
                    {
                        "claim_type": "decision_event",
                        "target": self.target,
                        "action": "accepted",
                        "reason": "用户明确采用",
                        "speaker": "user",
                        "explicitness": "explicit",
                        "referent_unique": True,
                        "scope": {"dataset_version": "data_v2"},
                        "evidence": [self.evidence],
                    }
                ],
                "lookup_terms": [],
                "unresolved": [],
            }
        return ModelResult(dumps(output), 10, 10, "stop")


def action_source(store: Store, tmp_path: Path, project: str, text: str):
    path = tmp_path / "action.jsonl"
    value = record(text, "public-action", sessionId="public-action", timestamp=now())
    path.write_bytes((dumps(value) + "\n").encode())
    scan_file(store, path, "claude", project)
    event = store.db.execute("SELECT * FROM raw_events ORDER BY event_id DESC LIMIT 1").fetchone()
    raw = store.raw(event["event_id"])
    bounds = event_span(raw, "claude", 0)
    assert bounds and bounds[2]
    a, b, _ = bounds
    return event["session_pk"], {
        "event_id": event["event_id"],
        "byte_start": a,
        "byte_end": b,
        "quote": raw[a:b].decode(),
    }


@pytest.mark.parametrize("latest", ["data_v2", "data_v3"])
@pytest.mark.parametrize("mode", ["label-default", "label-explicit", "id-default", "id-explicit"])
def test_scope_ambiguity_does_not_depend_on_version_insertion_order(
    store: Store,
    tmp_path: Path,
    latest: str,
    mode: str,
):
    project, entity, output, _ = source_object(store, tmp_path)
    original = deepcopy(output["claims"][0])
    another_scope(store, entity, original, {"dataset_version": "data_v3"})
    if latest == "data_v2":
        another_scope(store, entity, original, {"dataset_version": "data_v2"})
    text = f"采用[{entity}]。" if mode.startswith("id-") else "采用方法甲。"
    session, evidence = action_source(store, tmp_path, project, text)
    before = [tuple(r) for r in store.db.execute("SELECT * FROM claims ORDER BY claim_id")]
    raw = store.raw(evidence["event_id"])
    provider = ActionProvider(entity, evidence)
    scope = {"dataset_version": "data_v2"} if mode.endswith("-explicit") else None
    result = Worker(store, provider).process(session, scope)
    assert result["claims"] == 1 and result["manual"] == 0
    row = store.db.execute(
        "SELECT * FROM claims WHERE claim_type='decision_event' ORDER BY claim_id DESC LIMIT 1"
    ).fetchone()
    assert row["claim_state"] == "candidate"
    assert store.claim_state(row["claim_id"]) == (
        "candidate" if mode.endswith("-default") else "confirmed"
    )
    assert [tuple(r) for r in store.db.execute("SELECT * FROM claims ORDER BY claim_id")][
        :-1
    ] == before
    assert store.raw(evidence["event_id"]) == raw
    calls = provider.calls
    assert Worker(store, provider).process(session, scope)["claims"] == 0
    assert provider.calls == calls


@pytest.mark.parametrize("confirmed", [False, True])
def test_unqualified_newer_label_cannot_supply_rule_identity(store, tmp_path, confirmed):
    from rg.extract.working_set import working_set

    project, entity, output, _ = source_object(store, tmp_path)
    payload = deepcopy(output["claims"][0])
    payload.update(label="方法乙", content="来源缺失的旧模型版本")
    payload.pop("evidence")
    old = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at) VALUES('entity_version',?,?,?,'model_inference',"
        "'model:public-legacy','candidate',?,?)",
        (entity, dumps(payload), dumps(payload["scope"]), now(), now()),
    ).lastrowid
    if confirmed:
        store.review(old, "confirm", "human:公开旧复核", store.revision())
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    assert [card["label"] for card in cards] == ["方法甲"]
    _, evidence = action_source(store, tmp_path, project, "采用方法乙。")
    provider = ActionProvider(entity, evidence)
    result = json.loads(
        provider.generate(
            provider.request("", dumps({"segment_id": "identity"}), PASS2_SCHEMA)
        ).text
    )
    run = store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,output_json,created_at) "
        "VALUES ('identity','pass2',?,'validated',?,?)",
        (dumps([evidence["event_id"]]), dumps(result), now()),
    ).lastrowid
    assert run is not None
    added = persist(
        store,
        result,
        project,
        run,
        {entity},
        {evidence["event_id"]},
        "identity",
        working_cards=cards,
    )
    assert store.claim_state(added[0]) == "candidate"
    assert store.claim_state(old) == ("confirmed" if confirmed else "candidate")


def test_replaced_definition_does_not_make_current_label_ambiguous(store, tmp_path):
    from rg.extract.working_set import working_set

    project, original, output, old = source_object(store, tmp_path)
    another_scope(
        store, original, output["claims"][0], {"dataset_version": "data_v3"}, replaces=old
    )
    definition = deepcopy(output)
    definition["claims"][0]["temp_id"] = "new:current"
    run = new_run(store, definition, "current")
    new = persist(store, definition, project, run, set(), {1}, "test-segment")[0]
    entity = store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (new,)).fetchone()[0]
    session, evidence = action_source(store, tmp_path, project, "采用方法甲。")
    cards = working_set(
        store, project, "方法甲", FakeProvider(), scope={"dataset_version": "data_v2"}
    )
    assert [card["id"] for card in cards] == [entity]
    result = Worker(store, ActionProvider(entity, evidence)).process(
        session, {"dataset_version": "data_v2"}
    )
    assert result["claims"] == 1 and result["manual"] == 0
    latest = store.db.execute(
        "SELECT claim_id FROM claims WHERE claim_type='decision_event'"
    ).fetchone()[0]
    assert store.claim_state(latest) == "confirmed"


def test_rule_version_invalidates_pass2_but_preserves_pass1_cache(store, tmp_path, monkeypatch):
    project, _, _, _ = source_object(store, tmp_path)
    output = {"segment_id": "rule-cache", "claims": [], "lookup_terms": [], "unresolved": []}
    provider = FakeProvider(
        [model_result({"candidates": []}), model_result(output), model_result(output)], count=10
    )
    worker = Worker(store, provider)
    item = Segment("rule-cache", [{"event_id": 1}])
    first = worker.invoke("pass1", item, project, "[]", PASS1_SCHEMA, [])
    second = worker.invoke("pass2", item, project, "{}", PASS2_SCHEMA, [])
    config = worker.configuration()
    monkeypatch.setattr(worker_module, "RULE_VERSION", "public-future-rule")
    assert worker.configuration() != config
    assert worker.invoke("pass1", item, project, "[]", PASS1_SCHEMA, [])[0] == first[0]
    assert worker.invoke("pass2", item, project, "{}", PASS2_SCHEMA, [])[0] != second[0]
    assert provider.calls == 3


def test_independent_manual_label_remains_authoritative(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    updated = deepcopy(output["claims"][0])
    updated["label"] = "方法乙"
    another_scope(store, entity, updated, {"dataset_version": "data_v2"})
    session, evidence = action_source(store, tmp_path, project, "采用方法乙。")
    result = Worker(store, ActionProvider(entity, evidence)).process(session)
    assert result["claims"] == 1 and result["manual"] == 0
    latest = store.db.execute(
        "SELECT claim_id FROM claims WHERE claim_type='decision_event'"
    ).fetchone()[0]
    assert store.claim_state(latest) == "confirmed"


def test_exact_id_can_confirm_when_its_complete_scope_is_unique(store, tmp_path):
    project, entity, _, _ = source_object(store, tmp_path)
    session, evidence = action_source(store, tmp_path, project, f"采用[{entity}]。")
    result = Worker(store, ActionProvider(entity, evidence)).process(session)
    assert result["claims"] == 1 and result["manual"] == 0
    latest = store.db.execute(
        "SELECT claim_id FROM claims WHERE claim_type='decision_event'"
    ).fetchone()[0]
    assert store.claim_state(latest) == "confirmed"
