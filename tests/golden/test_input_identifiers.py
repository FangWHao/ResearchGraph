from __future__ import annotations

import json
from pathlib import Path

from rg.extract import linker
from rg.extract.linker import Pair, link, pairs
from rg.extract.redact import model_input
from rg.extract.worker import Worker
from rg.store.database import Store, dumps
from tests.golden.test_links_overview import DerivedProvider, two_objects

HASH = "a13800138000f" + "b" * 51
UUID = "aaaaaaaa-bbbb-cccc-dddd-a13800138000"


def test_link_identity_is_not_masked_but_same_number_in_text_and_scope_is():
    payload = {
        "pair_id": HASH,
        "cards": [{"id": UUID, "label": "手机号13800138000", "scope": {"id": "13800138000"}}],
        "source_window": {"quote": '原文里 {"pair_id":"13800138000"}'},
    }
    safe = json.loads(model_input(dumps(payload), "link"))
    assert safe["pair_id"] == HASH and safe["cards"][0]["id"] == UUID
    assert "13800138000" not in safe["cards"][0]["label"]
    assert "13800138000" not in safe["cards"][0]["scope"]["id"]
    assert "13800138000" not in safe["source_window"]["quote"]


def test_pass2_and_overview_preserve_only_ids_in_program_paths():
    content = {
        "segment_id": HASH,
        "working_set": [{"id": UUID, "scope": {"id": "13800138000"}}],
        "slim": dumps([{"text": "13800138000", "event_id": 1}]),
    }
    safe = json.loads(model_input(dumps(content), "pass2"))
    assert safe["segment_id"] == HASH and safe["working_set"][0]["id"] == UUID
    assert "13800138000" not in safe["slim"]
    overview = {
        "records": [
            {
                "payload": {
                    "target": UUID,
                    "reason": "13800138000",
                    "scope": {"target": "13800138000"},
                }
            }
        ]
    }
    safe = json.loads(model_input(dumps(overview), "overview"))
    assert safe["records"][0]["payload"]["target"] == UUID
    assert "13800138000" not in safe["records"][0]["payload"]["reason"]
    assert "13800138000" not in safe["records"][0]["payload"]["scope"]["target"]


def test_opaque_hash_round_trip_cannot_randomly_fail_from_phone_pattern(
    store: Store,
    tmp_path: Path,
    monkeypatch,
):
    project, _, _ = two_objects(store, tmp_path)
    original = pairs(store, project)[0]
    pair = Pair(HASH, original.cards, original.basis, original.scope)
    monkeypatch.setattr(linker, "iter_pairs", lambda *args: iter([pair]))
    provider = DerivedProvider("none")
    result = link(Worker(store, provider), project)
    assert result["manual"] == 0 and result["paused"] == 0 and provider.calls == 1
    assert provider.inputs[0]["pair_id"] == HASH


def test_none_with_object_endpoints_is_invalid_in_schema_and_never_enters_claims(
    store: Store,
    tmp_path: Path,
):
    project, _, _ = two_objects(store, tmp_path)
    result = link(Worker(store, DerivedProvider("none", broken="none_endpoints")), project)
    assert result["manual"] == 1 and result["claims"] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 2
    assert (
        store.db.execute("SELECT error FROM extraction_runs WHERE stage='link'").fetchone()[0]
        == "输出 schema 校验失败"
    )
