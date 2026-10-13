from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.snapshot.git import Git
from rg.store import fragment_dependencies as dependencies
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.fragments import assign
from rg.store.objects import ObjectStore, digest
from tests.golden.test_fragment_assignments import intent
from tests.golden.test_ingestion import lines
from tests.golden.test_project_fragments import scene


def entity(store, project):
    value = str(uuid4())
    store.db.execute(
        "INSERT INTO entities VALUES (?,?, 'approach',NULL,?)", (value, project, now())
    )
    return value


def claim(store, target, event, *, payload=None, replaces=None, run=None):
    quote_sha = digest(store.raw(event)[:1])
    payload = payload or {
        "claim_type": "entity_version",
        "temp_id": target,
        "kind": "approach",
        "label": "公开方案",
        "content": "公开科研内容",
        "scope": {"data": "public"},
        "evidence": [{"event_id": event, "byte_start": 0, "byte_end": 1, "quote": "{"}],
    }
    value = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "replaces_claim,recorded_at,extraction_run_id) VALUES (?,?,?,'{}','manual',"
        "'human:公开验收','candidate',?,?,?)",
        (payload["claim_type"], target, dumps(payload), replaces, now(), run),
    ).lastrowid
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,0,1,?)",
        (event, quote_sha),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (value, span))
    return value


def extraction(store, inputs, working=()):
    return store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,working_set_ids,"
        "status,created_at) "
        "VALUES (?,'pass2',?,?,'validated',?)",
        (str(uuid4()), dumps(inputs), dumps(working), now()),
    ).lastrowid


def test_readonly_plan_preserves_raw_history_and_has_no_external_reads(
    store, tmp_path, monkeypatch
):
    first, second, *_ = scene(store, tmp_path)
    target = entity(store, first)
    old = claim(store, target, 4)
    store.review(old, "confirm", "human:公开验收", store.revision())
    new = claim(store, target, 4, replaces=old)
    body = intent(store, second)
    before = list(store.db.iterdump())
    monkeypatch.setattr(ObjectStore, "get", lambda *a: pytest.fail("不得读取原始正文"))
    monkeypatch.setattr(Git, "run", lambda *a, **k: pytest.fail("不得探测 Git"))
    with closing(Store(store.root, readonly=True)) as reader:
        result = dependencies.preview(reader, body)
        assert reader.db.execute("PRAGMA query_only").fetchone()[0] == 1
    assert result["complete"] and result["closure_closed"]
    assert result["dependencies"]["claims"] == 2
    assert result["dependencies"]["review_actions"] == 1
    assert result["write_mode"] == "dependency_migration"
    assert not result["assignment_available"] and not result["migration_applied"]
    assert "公开科研内容" not in dumps(result) and "human:" not in dumps(result)
    assert store.claim_state(old) == "confirmed" and store.claim_state(new) == "candidate"
    assert list(store.db.iterdump()) == before
    with pytest.raises(ConflictError, match="科研依赖"):
        assign(store, body)
    assert list(store.db.iterdump()) == before


def test_relations_and_all_versions_require_outside_evidence(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    a, b = entity(store, first), entity(store, first)
    claim(store, a, 4)
    claim(store, b, 2)
    claim(store, a, 4, payload={"claim_type": "relation", "source": a, "target": b})
    data = dependencies.preview(store, intent(store, second))
    assert data["complete"] and not data["closure_closed"]
    assert data["dependencies"]["entities"] == 2 and data["dependencies"]["claims"] == 3
    assert [r["first_event_id"] for r in data["required_records"]] == [2]
    assert data["required_records"][0]["project_id"] == first


def test_model_working_set_and_context_are_dependencies(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    a = entity(store, first)
    claim(store, a, 2)
    extraction(store, [4], [a])
    store.db.execute(
        "INSERT INTO extraction_plans VALUES ('config','segment',1,?,?, 'pending',NULL,?,?)",
        (
            dumps([4]),
            dumps({"events": [{"event_id": 4}], "context_events": [{"event_id": 1}]}),
            now(),
            now(),
        ),
    )
    store.db.execute(
        "INSERT INTO jobs(kind,payload,state,updated_at) VALUES ('context_gap',?,'done',?)",
        (dumps({"segment_id": "segment"}), now()),
    )
    data = dependencies.preview(store, intent(store, second, 4, 4))
    assert data["complete"] and not data["closure_closed"]
    assert data["dependencies"]["extraction_runs"] == 1
    assert data["dependencies"]["extraction_plans"] == 1
    assert data["dependencies"]["jobs"] == 1
    # 已登记的提取边界保持完整，迁移也必须携带该逻辑会话中的目录记录。
    assert [r["first_event_id"] for r in data["required_records"]] == [1, 2, 3]


def test_native_run_request_result_and_edit_versions_expand_together(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    run, version = str(uuid4()), str(uuid4())
    store.db.execute(
        "INSERT INTO runs(run_id,project_id,session_pk,state,request_event_id) "
        "VALUES (?,?,1,'succeeded',2)",
        (run, first),
    )
    store.db.execute(
        "INSERT INTO run_observations(run_id,event_id,state,details,recorded_at) "
        "VALUES (?,4,'exited','{}',?)",
        (run, now()),
    )
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
        "evidence_event_id,observed_at) VALUES (?,?,'public.py','sha256',?,"
        "'agent_edit',2,?)",
        (version, first, "b" * 64, now()),
    )
    store.db.execute(
        "INSERT INTO run_io(run_id,version_id,direction,basis) VALUES (?,?,'out','manual')",
        (run, version),
    )
    store.db.execute(
        "INSERT INTO edit_records(edit_id,project_id,session_pk,request_event_id,result_event_id,"
        "path,operation,after_version,recorded_at) VALUES (?,?,1,2,4,'public.py','edit',?,?)",
        (str(uuid4()), first, version, now()),
    )
    data = dependencies.preview(store, intent(store, second, 4, 4))
    assert data["complete"] and not data["closure_closed"]
    assert all(
        data["dependencies"][t] == 1
        for t in ("runs", "run_observations", "run_io", "artifact_versions", "edit_records")
    )
    assert [r["first_event_id"] for r in data["required_records"]] == [2]


def test_plan_hash_detects_model_journal_change_without_graph_revision(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    run = extraction(store, [1, 2, 4])
    body = intent(store, second)
    first = dependencies.preview(store, body | {"limit": 1})
    assert first["next_offset"] == 1 and first["required_records_total"] == 2
    again = dependencies.preview(
        store, body | {"offset": 1, "limit": 1, "expected_plan_sha256": first["plan_sha256"]}
    )
    assert again["plan_sha256"] == first["plan_sha256"]
    assert again["required_records"][0]["first_event_id"] == 2
    revision = store.revision()
    store.db.execute("UPDATE extraction_runs SET status='failed' WHERE extraction_run_id=?", (run,))
    assert store.revision() == revision
    with pytest.raises(ConflictError, match="科研依赖已变化"):
        dependencies.preview(store, body | {"expected_plan_sha256": first["plan_sha256"]})


@pytest.mark.parametrize(
    "bad",
    ["{", '"not an array"', "null", "{}", "[true]", "[null]", '["4"]', "[9223372036854775808]"],
)
def test_bad_json_never_becomes_empty_dependency_plan(store, tmp_path, bad):
    _, second, *_ = scene(store, tmp_path)
    run = extraction(store, [4])
    store.db.execute(
        "UPDATE extraction_runs SET input_event_ids=? WHERE extraction_run_id=?", (bad, run)
    )
    data = dependencies.preview(store, intent(store, second))
    assert not data["complete"] and data["issues"]
    assert data["write_mode"] == "unverifiable" and not data["assignment_available"]
    with pytest.raises(ConflictError):
        assign(store, intent(store, second))


@pytest.mark.parametrize(
    "budget", ["MAX_NODES", "MAX_ROW_BYTES", "MAX_TOTAL_BYTES", "MAX_SQL_STEPS"]
)
def test_limits_are_visible_and_do_not_authorize_partial_plan(store, tmp_path, monkeypatch, budget):
    _, second, *_ = scene(store, tmp_path)
    extraction(store, [4])
    body = intent(store, second, 4, 4)
    monkeypatch.setattr(dependencies, budget, 1)
    if budget == "MAX_SQL_STEPS":
        for _ in range(200):
            extraction(store, [2])
    before = list(store.db.iterdump())
    data = dependencies.preview(store, body)
    assert not data["complete"] and data["issues"]
    assert not data["assignment_available"] and not data["closure_closed"]
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE future_science(value TEXT)",
        "ALTER TABLE claims ADD COLUMN future_reference TEXT",
    ],
)
def test_unknown_schema_is_explicit_and_no_data_is_modified(store, tmp_path, sql):
    _, second, *_ = scene(store, tmp_path)
    store.db.execute(sql)
    body = intent(store, second)
    before = list(store.db.iterdump())
    data = dependencies.preview(store, body)
    assert not data["complete"] and data["issues"] == ["schema_contract_changed"]
    assert list(store.db.iterdump()) == before


def test_no_science_is_direct_but_unrelated_origin_science_still_reports_protection(
    store, tmp_path
):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    data = dependencies.preview(store, body)
    assert data["complete"] and data["assignment_available"] and data["write_mode"] == "direct"
    extraction(store, [2])
    protected = dependencies.preview(store, body)
    assert protected["dependencies"] == {} and protected["closure_closed"]
    assert (
        not protected["assignment_available"] and protected["write_mode"] == "dependency_migration"
    )


def test_whole_physical_record_includes_all_content_blocks(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    path = tmp_path / "public-multiblock.jsonl"
    lines(
        path,
        [
            {
                "type": "user",
                "uuid": "public-multi",
                "sessionId": "public-multiblock",
                "message": {
                    "content": [
                        {"type": "text", "text": "公开首块"},
                        {"type": "text", "text": "公开第二块"},
                    ]
                },
            }
        ],
    )
    scan_file(store, path, "claude", first)
    ids = [r[0] for r in store.db.execute("SELECT event_id FROM raw_events WHERE session_pk=2")]
    assert len(ids) == 2
    claim(store, entity(store, first), ids[1])
    body = intent(store, second, ids[0], ids[0], source=2, session=2)
    result = dependencies.preview(store, body)
    assert result["complete"] and result["closure_closed"]
    assert result["selected_records"] == 1 and result["dependencies"]["claims"] == 1
    with pytest.raises(ValueError, match="完整物理记录首事件"):
        dependencies.preview(store, body | {"last_event": ids[1]})


def test_saved_source_qualification_keeps_associated_call_dependency(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    saved = claim(store, entity(store, first), 4)
    store.db.execute(
        "INSERT INTO claim_source_checks(claim_id,project_id,input_signature,payload,recorded_at) "
        "VALUES (?,?,?,?,?)",
        (
            saved,
            first,
            "a" * 64,
            dumps({"references": [{"event_id": 4, "associated_call": {"event_id": 2}}]}),
            now(),
        ),
    )
    result = dependencies.preview(store, intent(store, second, 4, 4))
    assert result["complete"] and not result["closure_closed"]
    assert result["dependencies"]["claim_source_checks"] == 1
    assert [r["first_event_id"] for r in result["required_records"]] == [2]


def test_missing_input_is_not_guessed_and_version_only_dependency_blocks_write(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    extraction(store, [4, 999999])
    result = dependencies.preview(store, intent(store, second))
    assert not result["complete"] and "missing_reference" in result["issues"]
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
        "evidence_event_id,observed_at) VALUES (?,?,'public.py','sha256',?,'agent_edit',2,?)",
        (str(uuid4()), first, "b" * 64, now()),
    )
    with pytest.raises(ConflictError, match="科研依赖"):
        assign(store, intent(store, second, 2, 2))


@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": 99},
        {"expected_scope_key": "a" * 64},
        {"offset": 1},
        {"limit": True},
        {"expected_plan_sha256": "bad"},
    ],
)
def test_invalid_or_stale_plan_is_rejected_without_mutation(store, tmp_path, change):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        dependencies.preview(store, body | change)
    assert list(store.db.iterdump()) == before


def test_cli_and_authenticated_http_are_readonly_and_do_not_reserve_uuid(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    before = list(store.db.iterdump())
    path = tmp_path / "public-request.json"
    path.write_text(dumps(body), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "project",
            "fragment-plan",
            "--request-file",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["assignment_available"]
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("公开测试", encoding="utf-8")
    with LocalServer(store.root, web, port=0, token="public-test-token") as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False
        ) as client:
            assert client.post("/api/project-fragments/dependencies", json=body).status_code == 401
            headers = {"Authorization": "Bearer public-test-token"}
            response = client.post(
                "/api/project-fragments/dependencies", json=body, headers=headers
            )
            assert response.status_code == 200, response.text
            assert response.json()["assignment_available"]
            duplicate = dumps(body)[:-1] + ',"session":1}'
            rejected = client.post(
                "/api/project-fragments/dependencies",
                content=duplicate,
                headers=headers | {"Content-Type": "application/json"},
            )
            assert rejected.status_code == 400
        server.shutdown()
        thread.join(5)
    assert list(store.db.iterdump()) == before
    assert assign(store, body)["assignments_applied"]
