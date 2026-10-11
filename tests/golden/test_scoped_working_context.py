from __future__ import annotations

import json
from copy import deepcopy

import pytest

from rg.extract import worker as worker_module
from rg.extract.provider import ModelResult
from rg.extract.redact import model_input
from rg.extract.schemas import PASS1_SCHEMA, PASS2_SCHEMA
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim, persist, validate
from rg.extract.worker import Worker
from rg.extract.working_scopes import context, groups
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.store.database import dumps, now
from tests.conftest import FakeProvider
from tests.golden.test_extraction import new_run
from tests.golden.test_ingestion import record


def model_result(value):
    return ModelResult(dumps(value), 10, 10, "stop")


class ScopeProvider(FakeProvider):
    def __init__(self, claims=None, lookup=False):
        super().__init__(count=10)
        self.claims = claims or []
        self.lookup = lookup
        self.inputs = []

    def generate(self, request):
        content = json.loads(request["input"])
        if isinstance(content, list):
            output = {
                "candidates": [
                    {
                        "event_id": item["event_id"],
                        "byte_range": item["byte_range"],
                        "cue": "decision",
                    }
                    for item in content
                    if not item.get("context_only")
                ]
            }
        else:
            self.inputs.append(content)
            output = {
                "segment_id": content["segment_id"],
                "claims": self.claims,
                "lookup_terms": self.lookup if isinstance(self.lookup, list) else [],
                "unresolved": [],
            }
        self.calls += 1
        return model_result(output)


def source_object(store, tmp_path):
    project = store.project("公开范围项目", [tmp_path / "project"])
    path = tmp_path / "input.jsonl"
    path.write_bytes(
        (dumps(record("采用方法甲，用于 data_v2；回退代码后方法继续采用。")) + "\n").encode()
    )
    scan_file(store, path, "claude", project)
    raw, quote = store.raw(1), "采用方法甲".encode()
    start = raw.index(quote)
    output = {
        "segment_id": "test-segment",
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": "approach",
                "label": "方法甲",
                "content": "公开方法",
                "scope": {"dataset_version": "data_v2"},
                "evidence": [
                    {
                        "event_id": 1,
                        "byte_start": start,
                        "byte_end": start + len(quote),
                        "quote": quote.decode(),
                    }
                ],
            }
        ],
        "lookup_terms": [],
        "unresolved": [],
    }
    run = new_run(store, output)
    claim = persist(store, output, project, run, set(), {1}, "test-segment")[0]
    store.review(claim, "confirm", "human:公开复核", store.revision())
    entity = store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (claim,)).fetchone()[
        0
    ]
    return project, entity, output, claim


def another_scope(store, entity, original, scope, replaces=None):
    payload = deepcopy(original)
    payload.pop("evidence")
    payload["scope"] = scope
    payload["temp_id"] = entity
    return store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at,replaces_claim) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "entity_version",
            entity,
            dumps(payload),
            dumps(scope),
            "manual",
            "human:公开记录",
            "confirmed",
            now(),
            now(),
            replaces,
        ),
    ).lastrowid


def test_default_keeps_same_object_versions_in_each_complete_scope(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    another_scope(store, entity, output["claims"][0], {"dataset_version": "data_v3"})
    cards = working_set(store, project, entity, FakeProvider())
    assert len(cards) == 2
    assert {dumps(card["scope"]) for card in cards} == {
        dumps({"dataset_version": "data_v2"}),
        dumps({"dataset_version": "data_v3"}),
    }


def test_default_worker_refuses_existing_object_from_another_scope(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    bad = deepcopy(output["claims"][0])
    bad = {
        "claim_type": "decision_event",
        "target": entity,
        "action": "accepted",
        "reason": "公开候选",
        "speaker": "user",
        "explicitness": "explicit",
        "referent_unique": True,
        "scope": {"dataset_version": "data_v3"},
        "evidence": bad["evidence"],
    }
    provider = ScopeProvider([bad])
    before = store.db.execute("SELECT count(*) FROM claims").fetchone()[0]
    Worker(store, provider).process(1)
    assert provider.inputs
    assert not store.db.execute(
        "SELECT 1 FROM claims WHERE entity_id=? AND claim_type='decision_event' AND scope=?",
        (entity, dumps(bad["scope"])),
    ).fetchone()
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == before
    assert store.db.execute("SELECT 1 FROM extraction_runs WHERE status='invalid'").fetchone()


def test_default_worker_supplies_complete_scope_groups_without_inferred_filter(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    another_scope(store, entity, output["claims"][0], {"dataset_version": "data_v3"})
    provider = ScopeProvider()
    result = Worker(store, provider).process(1)
    assert result["manual"] == 0
    captured = provider.inputs
    assert captured and captured[0]["scope_filter"] is None
    groups = captured[0]["working_scope_groups"]
    assert {dumps(group["scope"]) for group in groups} == {
        dumps({"dataset_version": "data_v2"}),
        dumps({"dataset_version": "data_v3"}),
    }
    assert all(group["ids"] == [entity] for group in groups)


def decision(entity, evidence, scope):
    return {
        "claim_type": "decision_event",
        "target": entity,
        "action": "proposed",
        "reason": "公开候选",
        "speaker": "assistant",
        "explicitness": "implicit",
        "referent_unique": False,
        "scope": scope,
        "evidence": evidence,
    }


@pytest.mark.parametrize(
    "scope,allowed",
    [
        ({"dataset_version": "data_v2"}, True),
        ({"dataset_version": "data_v3"}, False),
        ({"dataset_version": "data_v2", "axis": "分析"}, False),
        ({"version": "data_v2"}, False),
        ({"dataset_version": "unknown"}, False),
    ],
)
def test_existing_reference_requires_complete_scope_and_never_rewrites_history(
    store,
    tmp_path,
    scope,
    allowed,
):
    project, entity, output, claim = source_object(store, tmp_path)
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    output["claims"] = [decision(entity, output["claims"][0]["evidence"], scope)]
    before = store.raw(1)
    run = new_run(store, output, "reference")
    if allowed:
        added = persist(
            store, output, project, run, {entity}, {1}, "test-segment", working_cards=cards
        )
        assert len(added) == 1 and store.claim_state(added[0]) == "candidate"
    else:
        with pytest.raises(InvalidClaim, match="完整工作集范围"):
            persist(store, output, project, run, {entity}, {1}, "test-segment", working_cards=cards)
        assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 1
    assert store.raw(1) == before and store.claim_state(claim) == "confirmed"


def test_latest_version_per_scope(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    original = output["claims"][0]
    updated = deepcopy(original)
    updated["label"] = "新版公开方法"
    another_scope(store, entity, updated, {"dataset_version": "data_v2"})
    another_scope(store, entity, original, {"dataset_version": "data_v3"})
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    assert len(cards) == 2
    assert (
        next(c for c in cards if c["scope"]["dataset_version"] == "data_v2")["label"]
        == "新版公开方法"
    )


def test_replaced_version_is_not_revived(store, tmp_path):
    project, entity, output, claim = source_object(store, tmp_path)
    original = output["claims"][0]
    another_scope(store, entity, original, {"dataset_version": "data_v3"}, replaces=claim)
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    assert [c["scope"] for c in cards] == [{"dataset_version": "data_v3"}]


@pytest.mark.parametrize("old_scope", [None, {}, {" ": "data_v2"}, {"version": " "}])
def test_invalid_old_scope_is_only_unknown_hint(store, tmp_path, old_scope):
    project, entity, output, claim = source_object(store, tmp_path)
    another_scope(store, entity, output["claims"][0], old_scope, replaces=claim)
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    assert len(cards) == 1 and cards[0]["state"] == "unknown_scope"
    assert groups(cards)[0]["known"] is False
    evidence = output["claims"][0]["evidence"]
    output["claims"] = [decision(entity, evidence, {"version": "data_v2"})]
    with pytest.raises(InvalidClaim, match="完整工作集范围"):
        validate(store, output, project, {entity}, {1}, "test-segment", working_cards=cards)
    output["claims"][0]["scope"] = {"version": "unknown"}
    validate(store, output, project, {entity}, {1}, "test-segment", working_cards=cards)


@pytest.mark.parametrize("scope", [{}, {"": "v2"}, {" ": "v2"}, {"version": " "}])
def test_model_cannot_publish_empty_scope_fields(store, tmp_path, scope):
    project, _, output, _ = source_object(store, tmp_path)
    output["claims"][0]["scope"] = scope
    with pytest.raises(InvalidClaim, match="schema"):
        validate(store, output, project, set(), {1}, "test-segment")


def test_grouped_working_set_counts_cards_and_groups_in_one_budget(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    another_scope(store, entity, output["claims"][0], {"dataset_version": "data_v3"})
    counter = FakeProvider()
    cards = working_set(store, project, entity, counter, grouped=True)
    card_budget = counter.count_text(dumps(cards[:1]))
    assert working_set(store, project, entity, counter, budget=card_budget, grouped=True) == []
    budget = counter.count_text(dumps(context(cards[:1])))
    selected = working_set(store, project, entity, counter, budget=budget, grouped=True)
    assert selected == cards[:1] and counter.count_text(dumps(context(selected))) <= budget
    assert working_set(store, project, "完全无关", counter, grouped=True) == []


def test_lookup_rebuilds_groups_and_stops_after_two_rounds(store, tmp_path):
    project, entity, output, _ = source_object(store, tmp_path)
    hidden = deepcopy(output["claims"][0])
    hidden["label"] = "LookupOnly"
    another_scope(store, entity, hidden, {"dataset_version": "data_v3"})
    provider = ScopeProvider(lookup=["LookupOnly"])
    result = Worker(store, provider).process(1)
    assert result["manual"] == 0 and provider.calls == 3 and len(provider.inputs) == 2
    assert len(provider.inputs[0]["working_scope_groups"]) == 1
    assert len(provider.inputs[1]["working_scope_groups"]) == 2
    assert all(
        FakeProvider().count_text(dumps(context(item["working_set"]))) <= 3000
        for item in provider.inputs
    )


def test_group_ids_keep_identity_while_scope_and_text_are_redacted():
    identity = "aaaaaaaa-bbbb-cccc-dddd-a13800138000"
    payload = context(
        [{"id": identity, "scope": {"cohort": "13800138000"}, "label": "号码13800138000"}]
    )
    safe = json.loads(model_input(dumps(payload), "pass2"))
    assert safe["working_scope_groups"][0]["ids"] == [identity]
    assert "13800138000" not in safe["working_scope_groups"][0]["scope"]["cohort"]
    assert "13800138000" not in safe["working_set"][0]["label"]


def test_scope_policy_invalidates_pass2_and_plan_but_preserves_pass1_cache(
    store,
    tmp_path,
    monkeypatch,
):
    project, _, _, _ = source_object(store, tmp_path)
    output = {"segment_id": "cache-scope", "claims": [], "lookup_terms": [], "unresolved": []}
    provider = FakeProvider(
        [model_result({"candidates": []}), model_result(output), model_result(output)], count=10
    )
    worker = Worker(store, provider)
    item = Segment("cache-scope", [{"event_id": 1}])
    first = worker.invoke("pass1", item, project, "[]", PASS1_SCHEMA, [])
    second = worker.invoke("pass2", item, project, "{}", PASS2_SCHEMA, [])
    configuration = worker.configuration()
    monkeypatch.setattr(worker_module, "working_scope_policy", lambda: {"working_scope_version": 2})
    assert worker.configuration() != configuration
    assert worker.invoke("pass1", item, project, "[]", PASS1_SCHEMA, [])[0] == first[0]
    assert worker.invoke("pass2", item, project, "{}", PASS2_SCHEMA, [])[0] != second[0]
    assert provider.calls == 3


@pytest.mark.parametrize("claim_type", ["relation", "merge", "join_ports"])
def test_every_existing_endpoint_requires_matching_scope(store, tmp_path, claim_type):
    project, entity, output, _ = source_object(store, tmp_path)
    cards = working_set(store, project, entity, FakeProvider(), grouped=True)
    evidence = output["claims"][0]["evidence"]
    definition = deepcopy(output["claims"][0])
    definition.update(temp_id="new:join", kind="join", scope={"dataset_version": "data_v3"})
    item = {"claim_type": claim_type, "scope": {"dataset_version": "data_v3"}, "evidence": evidence}
    if claim_type == "join_ports":
        item.update(
            target="new:join",
            semantics="compare_then_select",
            inputs=[{"port": "a", "ref": entity}, {"port": "b", "ref": entity}],
            selected=entity,
        )
        output["claims"] = [definition, item]
    else:
        item.update(source=entity, target=entity)
        if claim_type == "relation":
            item["relation"] = "supersedes"
        output["claims"] = [item]
    with pytest.raises(InvalidClaim, match="完整工作集范围"):
        validate(store, output, project, {entity}, {1}, "test-segment", working_cards=cards)
