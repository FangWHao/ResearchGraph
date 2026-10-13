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
from rg.cli.main import parser, run
from rg.extract.schemas import CLAIM_SCHEMA
from rg.ingest.scanner import mark_deleted
from rg.ingest.sources import sources
from rg.ingest.watch import cycle
from rg.record.question import question
from rg.slim.slimmer import slim_session
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from rg.store.objects import digest
from tests.conftest import FakeProvider


def body(store: Store, **changes) -> dict:
    project = store.project("人工问题合成项目", [])
    return {
        "project_id": project,
        "text": '  为什么选这一方法？\n保留 "引号" 和 \\路径。  ',
        "actor": "human:合成测试者",
        "scope": None,
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    } | changes


def counts(store: Store) -> dict:
    return {
        table: store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "raw_events",
            "claims",
            "explicit_records",
            "entities",
            "sessions",
            "source_files",
            "evidence_spans",
            "event_search",
            "review_actions",
            "model_attempts",
        )
    }


def test_manual_question_keeps_literal_text_original_evidence_and_four_states(store: Store):
    input_body = body(store)
    result = question(store, input_body)
    value = views.claim(store, result["claim_id"])["claim"]
    assert value["basis"] == "manual" and value["actor"] == input_body["actor"]
    assert value["claim_state"] == value["effective_state"] == "confirmed"
    assert value["confirmation_source"] == "human"
    assert value["payload"]["content"] == value["payload"]["label"] == input_body["text"]
    assert value["scope"] is None and value["payload"]["scope"] is None
    assert value["occurred_at"] == value["recorded_at"]
    raw = store.raw(result["event_id"])
    assert json.loads(raw)["text"] == input_body["text"]
    assert json.loads(raw)["input_occurred_at"] is None
    for span in value["evidence"]:
        assert digest(raw[span["byte_start"] : span["byte_end"]]) == span["quote_sha256"]
        evidence = views.evidence(
            store,
            span["event_id"],
            {"start": str(span["byte_start"]), "end": str(span["byte_end"])},
        )
        assert evidence["event"]["quote_sha256"] == span["quote_sha256"]
    graph = views.graph(store, input_body["project_id"])
    assert graph["claims"][0]["entity_id"] == result["entity_id"]
    assert graph["run_states"] == []
    assert [row[0] for row in store.db.execute("SELECT claim_type FROM claims")] == [
        "entity_version"
    ]
    assert counts(store)["model_attempts"] == counts(store)["review_actions"] == 0
    assert store.search("引号")[0]["text"] == input_body["text"]


def test_complete_scope_is_preserved_and_backfill_time_is_separate(store: Store):
    input_body = body(
        store, scope={"data": "v2", "step": "cnv"}, occurred_at="2026-09-01T12:00:00+08:00"
    )
    result = question(store, input_body)
    value = views.claim(store, result["claim_id"])["claim"]
    assert value["scope"] == value["payload"]["scope"] == input_body["scope"]
    assert value["occurred_at"] == "2026-09-01T04:00:00+00:00"
    assert value["recorded_at"] != value["occurred_at"]
    assert (
        json.loads(store.raw(result["event_id"]))["input_occurred_at"] == input_body["occurred_at"]
    )


def test_exact_retry_ignores_stale_revision_but_different_intent_conflicts(store: Store):
    input_body = body(store)
    first = question(store, input_body)
    initial = counts(store)
    second = question(store, input_body | {"expected_revision": 0})
    assert second == first | {"replayed": True}
    assert counts(store) == initial
    for change in (
        {"text": "新问题"},
        {"scope": {"data": "v3"}},
        {"actor": "human:另一位"},
        {"occurred_at": "2026-09-01T00:00:00+00:00"},
        {"project_id": store.project("别的项目", [])},
    ):
        with pytest.raises(ConflictError):
            question(store, input_body | change)
        assert counts(store) == initial
    other = question(
        store, input_body | {"request_id": str(uuid4()), "expected_revision": store.revision()}
    )
    assert other["claim_id"] != first["claim_id"]
    repeated = question(store, input_body)
    assert (
        repeated["revision"] == store.revision() == 2 and repeated["claim_id"] == first["claim_id"]
    )


def test_simultaneous_same_intent_has_one_original_and_claim(store: Store):
    input_body = body(store)
    ready = threading.Barrier(2)

    def write():
        independent = Store(store.root)
        try:
            ready.wait(timeout=10)
            return question(independent, input_body)
        finally:
            independent.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(item["replayed"] for item in results) == [False, True]
    assert results[0]["claim_id"] == results[1]["claim_id"]
    assert (
        counts(store)["raw_events"]
        == counts(store)["explicit_records"]
        == counts(store)["claims"]
        == 1
    )


def test_retry_does_not_reverse_later_human_review(store: Store):
    input_body = body(store)
    first = question(store, input_body)
    store.review(first["claim_id"], "dismiss", "human:复核者", store.revision())
    before = counts(store)
    assert question(store, input_body)["replayed"] is True
    assert counts(store) == before
    assert store.claim_state(first["claim_id"]) == "dismissed"


@pytest.mark.parametrize(
    "change",
    [
        {"text": ""},
        {"text": " \n\t "},
        {"text": "中" * 5334},
        {"text": 123},
        {"actor": "model:test"},
        {"actor": "agent:client"},
        {"actor": "human: "},
        {"actor": "human:" + "a" * 121},
        {"scope": {}},
        {"scope": {"data": " "}},
        {"scope": {"": "v1"}},
        {"scope": {"data": 1}},
        {"scope": []},
        {"scope": {str(i): "v" for i in range(33)}},
        {"scope": {"x": "a" * 501}},
        {"occurred_at": "2026-01-01"},
        {"occurred_at": "不是时间"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": "0"},
        {"request_id": "bad"},
        {"request_id": 1},
        {"project_id": "not-present"},
        {"confirmed": True},
    ],
)
def test_invalid_manual_input_writes_nothing(store: Store, change):
    input_body = body(store) | change
    before = counts(store)
    with pytest.raises(ValueError):
        question(store, input_body)
    assert counts(store) == before and store.revision() == 0


def test_revision_conflict_does_not_save_any_original_or_receipt(store: Store):
    input_body = body(store)
    question(store, input_body)
    before = counts(store)
    with pytest.raises(ConflictError):
        question(store, input_body | {"request_id": str(uuid4())})
    assert counts(store) == before


def test_original_receipt_and_claim_cannot_be_rewritten(store: Store):
    question(store, body(store))
    for table in ("explicit_records", "raw_events", "claims"):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("UPDATE explicit_records SET kind='question'")


def test_failure_before_receipt_rolls_back_every_database_record(store: Store, monkeypatch):
    input_body = body(store)
    import rg.record.question as module

    original = module.validate_question

    def failure(payload):
        original(payload)
        raise RuntimeError("合成故障")

    monkeypatch.setattr(module, "validate_question", failure)
    before = counts(store)
    with pytest.raises(RuntimeError, match="合成故障"):
        question(store, input_body)
    assert counts(store) == before and store.revision() == 0
    monkeypatch.setattr(module, "validate_question", original)
    assert question(store, input_body)["replayed"] is False


def test_virtual_inputs_are_not_missing_sources_and_are_never_extracted_again(store: Store):
    input_body = body(store)
    result = question(store, input_body)
    assert sources(store) == []
    mark_deleted(store)
    assert store.db.execute("SELECT status FROM source_files").fetchone()[0] == "virtual"
    assert views.health(store, {}, 500000)["sources"] == []
    assert store.health()["ingest"]["known_source_paths"] == 0
    before = counts(store)
    cycle(store)
    assert counts(store) == before
    session = store.db.execute(
        "SELECT session_pk FROM raw_events WHERE event_id=?", (result["event_id"],)
    ).fetchone()[0]
    counter = FakeProvider()
    assert slim_session(store, session, counter) == [] and counter.counts == 0
    assert (
        store.db.execute("SELECT status FROM coverage").fetchone()[0]
        == "excluded:explicit_recorded"
    )


def test_long_escaped_text_has_bounded_exact_original_windows(store: Store):
    text = "中\n\t" * 2000
    result = question(store, body(store, text=text))
    value = views.claim(store, result["claim_id"])["claim"]
    raw = store.raw(result["event_id"])
    windows = value["evidence"]
    assert len(windows) > 1
    combined = b"".join(raw[span["byte_start"] : span["byte_end"]] for span in windows)
    assert json.loads(b'"' + combined + b'"') == text
    for span in windows:
        assert span["byte_end"] - span["byte_start"] <= 8000
        assert (
            views.evidence(
                store,
                result["event_id"],
                {"start": str(span["byte_start"]), "end": str(span["byte_end"])},
            )["event"]["quote_sha256"]
            == span["quote_sha256"]
        )


def test_backup_restores_original_and_retry_identity(store: Store, tmp_path: Path):
    input_body = body(store)
    result = question(store, input_body)
    raw = store.raw(result["event_id"])
    destination = tmp_path / "restored"
    assert backup(store, destination) == {"objects": 1, "events": 1}
    recovered = Store(destination)
    try:
        assert recovered.raw(result["event_id"]) == raw
        assert question(recovered, input_body)["replayed"] is True
        assert recovered.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        recovered.close()


def test_human_unknown_scope_edit_is_append_only_and_model_schema_stays_strict(store: Store):
    result = question(store, body(store))
    value = views.claim(store, result["claim_id"])["claim"]
    edited = views.edit(
        store,
        result["claim_id"],
        {
            "payload": value["payload"] | {"label": "修改的问题", "content": "人工补充"},
            "scope": None,
            "actor": "human:复核者",
            "expected_revision": store.revision(),
        },
    )
    latest = views.claim(store, edited["claim_id"])["claim"]
    assert latest["scope"] is None and latest["effective_state"] == "confirmed"
    assert latest["evidence"] == value["evidence"]
    assert (
        store.db.execute(
            "SELECT payload FROM claims WHERE claim_id=?", (result["claim_id"],)
        ).fetchone()[0]
        != store.db.execute(
            "SELECT payload FROM claims WHERE claim_id=?", (edited["claim_id"],)
        ).fetchone()[0]
    )
    probe = latest["payload"] | {
        "scope": None,
        "evidence": [{"event_id": 1, "byte_start": 0, "byte_end": 1, "quote": "x"}],
    }
    assert list(Draft202012Validator(CLAIM_SCHEMA).iter_errors(probe))
    assert question(store, body(store))["claim_id"] != edited["claim_id"]


def test_edit_cannot_clear_scope_by_omitting_it_or_relax_model_claims(store: Store):
    result = question(store, body(store, scope={"data": "v1"}))
    value = views.claim(store, result["claim_id"])["claim"]
    edit_body = {
        "payload": value["payload"],
        "actor": "human:复核者",
        "expected_revision": store.revision(),
    }
    with pytest.raises(ValueError):
        views.edit(store, result["claim_id"], edit_body)
    model_id = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,"
        "claim_state,recorded_at) "
        "SELECT claim_type,entity_id,payload,scope,'model_inference','model:synthetic',"
        "'candidate',recorded_at FROM claims WHERE claim_id=?",
        (result["claim_id"],),
    ).lastrowid
    assert model_id is not None
    before = counts(store)
    with pytest.raises(ValueError):
        views.edit(
            store, model_id, edit_body | {"scope": None, "expected_revision": store.revision()}
        )
    assert counts(store) == before


def test_cli_parsing_preserves_scope_and_installed_entry_accepts_literal_data(store: Store):
    input_body = body(store)
    args = parser().parse_args(
        [
            "question",
            input_body["text"],
            "--project",
            input_body["project_id"],
            "--scope",
            "data=v2",
            "step=cnv",
            "--scope",
            "cohort=test",
        ]
    )
    result = run(args, store)
    assert isinstance(result, dict)
    assert views.claim(store, result["claim_id"])["claim"]["scope"] == {
        "data": "v2",
        "step": "cnv",
        "cohort": "test",
    }
    command = [
        str(Path(sys.executable).parent / "rg"),
        "--data-dir",
        str(store.root),
        "question",
        "$(不执行) `不执行` ; '原话'",
        "--project",
        input_body["project_id"],
        "--request-id",
        str(uuid4()),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    output = json.loads(process.stdout)
    assert output["replayed"] is False
    assert json.loads(store.raw(output["event_id"]))["text"] == command[4]
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 0 and json.loads(repeated.stdout)["replayed"] is True


def test_real_http_requires_auth_and_revision_and_retries_exactly_once(
    store: Store, tmp_path: Path
):
    input_body = body(store)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>合成测试</html>")
    server = LocalServer(store.root, web, port=0, token="synthetic-question-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
            assert client.post("/api/records/question", json=input_body).status_code == 401
            assert counts(store)["raw_events"] == 0
            client.headers["Authorization"] = "Bearer " + server.token
            assert (
                client.post(
                    "/api/records/question",
                    json=input_body,
                    headers={"Origin": "https://external.invalid"},
                ).status_code
                == 403
            )
            response = client.post("/api/records/question", json=input_body)
            assert response.status_code == 200 and response.json()["replayed"] is False
            retry = client.post("/api/records/question", json=input_body)
            assert retry.status_code == 200 and retry.json()["replayed"] is True
            assert (
                client.post("/api/records/question", json=input_body | {"text": "改变"}).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/records/question", json=input_body | {"request_id": str(uuid4())}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/records/question",
                    json=input_body
                    | {"request_id": str(uuid4()), "expected_revision": 1, "actor": "model:test"},
                ).status_code
                == 400
            )
            assert counts(store)["raw_events"] == counts(store)["explicit_records"] == 1
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
