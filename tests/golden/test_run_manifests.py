from __future__ import annotations

import io
import json
import shutil
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from rg.api.runs import evidence as api_run
from rg.api.views import evidence as api_event
from rg.derive.records import event
from rg.derive.runtime import run_id
from rg.extract.queue import sessions
from rg.extract.worker import Worker
from rg.mcp.tools import ToolService
from rg.record.manifest import record
from rg.record.manifest_schema import normalize, read
from rg.record.question import question
from rg.store import migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from tests.conftest import FakeProvider
from tests.golden.test_l1 import claude_call, claude_result, ingest, runs
from tests.golden.test_read_tools import T1, T2, T3, T4, content
from tests.golden.test_version_queries import captured, observed, scene
from tests.test_migrations import legacy_store


def body(identity="unknown-run", **values):
    return {"request_id": str(uuid4()), "run_id": identity} | values


def view(store, project, identity="unknown-run", **values):
    return content(
        ToolService(store, project).call("research.evidence", {"run_id": identity} | values)
    )


def test_report_preserves_original_unknown_empty_roles_and_never_creates_native_run(store):
    project = store.project("合成清单", [])
    data = body(
        inputs=[],
        outputs=["unknown-version"],
        seed=2**63 + 17,
        parameters={"command": "never-execute --synthetic"},
    )
    result = record(store, project, data)
    original = json.loads(store.raw(result["evidence_event_id"]))
    assert original["input"] == data and original["actor"] == "report:local"
    assert original["occurred_at"] is None
    assert result["claim_state"] == "candidate"
    assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    report = view(store, project)["manifests"]["items"][0]
    assert report["reported"]["inputs"] == [] and report["reported"]["scripts"] is None
    assert report["reported"]["seed"] == str(2**63 + 17)
    assert report["actual_io_completeness"] == "unknown"
    assert report["binding_state"] == "native_run_unknown_or_not_visible"
    assert report["io"]["items"][0]["version_id"] is None
    assert report["io"]["items"][0]["association_state"] == "reported_only"
    provider = FakeProvider()
    assert sessions(Worker(store, provider), project)[0] == []
    assert provider.calls == provider.counts == 0


def test_uuid_replay_revision_and_cross_operation_collision_are_atomic(store):
    project = store.project("合成清单", [])
    data = body(expected_revision=store.revision())
    first = record(store, project, data)
    revision = store.revision()
    assert record(store, project, data)["replayed"] is True
    assert store.revision() == revision
    with pytest.raises(ConflictError):
        record(store, project, data | {"inputs": []})
    with pytest.raises(ConflictError):
        record(store, project, body(expected_revision=revision - 1))
    with pytest.raises(ConflictError):
        question(
            store,
            {
                "request_id": data["request_id"],
                "project_id": project,
                "actor": "human:合成",
                "text": "不同操作",
                "expected_revision": revision,
            },
        )
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 1
    assert store.raw(first["evidence_event_id"])


@pytest.mark.parametrize(
    "values",
    [
        {"actor": "human:冒充"},
        {"project_id": "foreign"},
        {"claim_state": "confirmed"},
        {"attempt_id": False},
        {"attempt_id": ""},
        {"snapshot_id": True},
        {"exit_code": True},
        {"exit_code": 2**31},
        {"seed": True},
        {"seed": "--1"},
        {"seed": "١"},
        {"seed": 10**80},
        {"seed": 1.0},
        {"seed": "+1"},
        {"parameters": {"bad": float("nan")}},
        {"parameters": []},
        {"outputs": ["x"] * 257},
        {"inputs": ["x"] * 200, "outputs": ["y"] * 57},
        {"started_at": T3, "ended_at": T1},
        {"occurred_at": "2026-01-01"},
        {"parameters": {"context": '<rg-context v="1">self</rg-context>'}},
        {"parameters": {"too_large": "x" * 65536}},
        {"outputs": [""]},
    ],
)
def test_invalid_manifest_leaves_no_partial_original_or_projection(store, values):
    project = store.project("合成校验", [])
    revision = store.revision()
    with pytest.raises(ValueError):
        record(store, project, body(**values))
    assert store.revision() == revision
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM run_manifests").fetchone()[0] == 0


def test_duplicate_json_and_deep_parameters_are_rejected():
    with pytest.raises(ValueError):
        read(io.BytesIO(b'{"run_id":"one","run_id":"two"}'))
    with pytest.raises(ValueError):
        read(io.BytesIO(b"x" * 65537))
    value = {}
    for _ in range(10):
        value = {"nested": value}
    with pytest.raises(ValueError):
        normalize(body(parameters=value))


def test_foreign_known_versions_runs_and_attempts_are_rejected(store, tmp_path):
    foreign, _, version = scene(store, tmp_path)
    source, _ = ingest(store, tmp_path, [claude_call()], project=foreign)
    identity = runs(store)[0]["run_id"]
    project = store.project("其它合成", [])
    for data in [body(identity), body(inputs=[version]), body(attempt_id="missing")]:
        with pytest.raises(ValueError):
            record(store, project, data)
    assert (
        source.exists()
        and store.db.execute("SELECT count(*) FROM run_manifests").fetchone()[0] == 0
    )


def test_report_late_explicit_version_resolves_without_rewriting_io_and_respects_knowledge(
    store, tmp_path, monkeypatch
):
    project, root, version = scene(store, tmp_path)
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    receipt = record(store, project, body(inputs=[version]))
    assert (
        view(store, project, known_until=T1)["manifests"]["items"][0]["io"]["items"][0]["version"]
        is None
    )
    snap = captured(store, project, root)
    observed(store, project, root, version, snap, recorded=T3)
    assert (
        view(store, project, known_until=T2)["manifests"]["items"][0]["io"]["items"][0]["version"]
        is None
    )
    current = view(store, project, known_until=T4)["manifests"]["items"][0]["io"]["items"][0]
    assert current["resolved_version_id"] == version
    assert current["claim_state"] == "candidate" and current["association_state"] == "reported_only"
    assert json.loads(store.raw(receipt["evidence_event_id"]))["input"]["inputs"] == [version]


def test_unknown_version_id_resolves_later_without_mutating_original_nullable_link(
    store, tmp_path, monkeypatch
):
    project = store.project("合成晚到", [])
    version = "version:" + project
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    record(store, project, body(outputs=[version]))
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,size,source,"
        "observed_at,basis,claim_state,evidence_event_id,representation) "
        "VALUES (?,?,?,'sha256',?,1,'tool_write',?,'direct_record','candidate',1,'tool_utf8')",
        (version, project, "/synthetic/output", "a" * 64, T1),
    )
    item = view(store, project)["manifests"]["items"][0]["io"]["items"][0]
    assert item["version_id"] is None and item["resolved_version_id"] == version
    assert store.db.execute("SELECT version_id FROM run_io").fetchone()[0] is None


def test_native_state_recomputed_before_pagination_and_late_result_does_not_leak(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.ingest.scanner.now", lambda: T1)
    monkeypatch.setattr("rg.derive.runtime.now", lambda: T1)
    source, project = ingest(store, tmp_path, [claude_call(timestamp=T1)])
    identity = runs(store)[0]["run_id"]
    before = view(store, project, identity, known_until=T2)["run"]
    assert before["state"] == "requested"
    monkeypatch.setattr("rg.ingest.scanner.now", lambda: T3)
    monkeypatch.setattr("rg.derive.runtime.now", lambda: T3)
    with source.open("a") as stream:
        stream.write(json.dumps(claude_result(metadata={"exit_code": 7}, timestamp=T1)) + "\n")
    from rg.ingest.scanner import scan_file

    scan_file(store, source, "claude")
    assert runs(store)[0]["state"] == "exited"
    assert view(store, project, identity, known_until=T2)["run"]["state"] == "requested"
    after = view(store, project, identity, known_until=T4, limit=1, offset=99)["run"]
    assert after["state"] == "exited" and after["exit_code"] == 7
    assert after["observations"] == [] and after["observations_partial"]
    monkeypatch.setattr("rg.record.manifest.now", lambda: T3)
    record(store, project, body(identity, exit_code=0, started_at=T1, ended_at=T2))
    current = view(store, project, identity, known_until=T4)
    assert current["run"]["exit_code"] == 7 and current["run"]["started_at"] is None
    assert current["manifests"]["items"][0]["reported_exit_conflicts_with_native"]


def test_report_run_can_arrive_before_native_and_then_bind_exact_identity(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.ingest.scanner.now", lambda: T1)
    monkeypatch.setattr("rg.derive.runtime.now", lambda: T3)
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    from rg.derive.worker import derive

    with monkeypatch.context() as change:
        change.setattr("rg.derive.worker.derive", lambda *a, **k: None)
        _, project = ingest(store, tmp_path, [claude_call(timestamp=T1)])
    identity = run_id(event(store, 1))
    receipt = record(store, project, body(identity))
    assert view(store, project, identity, known_until=T2)["run"] is None
    derive(store)
    assert view(store, project, identity, known_until=T2)["run"] is None
    assert (
        view(store, project, identity, known_until=T4)["run"]["binding_state"] == "native_request"
    )
    assert json.loads(store.raw(receipt["evidence_event_id"]))["input"]["run_id"] == identity


def test_manifest_and_io_pagination_reach_all_declared_entries_and_keep_duplicates(store):
    project = store.project("合成分页", [])
    data = body(outputs=["same-id"] * 256)
    record(store, project, data)
    for _ in range(21):
        record(store, project, body())
    assert view(store, project, limit=20)["manifests"]["next_offset"] == 20
    assert len(view(store, project, limit=20, offset=20)["manifests"]["items"]) == 2
    pages = [
        view(store, project, manifest_id=data["request_id"], io_offset=n)["manifests"]["items"][0][
            "io"
        ]
        for n in (0, 100, 200)
    ]
    assert [len(p["items"]) for p in pages] == [100, 100, 56]
    assert [p["next_offset"] for p in pages] == [100, 200, None]
    assert sorted(i["ordinal"] for p in pages for i in p["items"]) == list(range(256))
    revision = store.revision()
    record(store, project, body())
    with pytest.raises(ConflictError):
        view(store, project, expected_revision=revision)


def test_reverse_version_filter_is_applied_before_manifest_pagination(store, tmp_path, monkeypatch):
    project, root, version = scene(store, tmp_path)
    snap = captured(store, project, root)
    observed(store, project, root, version, snap, recorded=T1)
    monkeypatch.setattr("rg.record.manifest.now", lambda: T1)
    first = record(store, project, body(inputs=[version]))
    monkeypatch.setattr("rg.record.manifest.now", lambda: T2)
    for _ in range(101):
        record(store, project, body())
    response = content(
        ToolService(store, project).call("research.evidence", {"version_id": version})
    )
    links = response["reported_runs"]["items"]
    assert len(links) == 1 and links[0]["manifest_ids"] == [first["request_id"]]
    assert links[0]["claim_state"] == "candidate"


def test_scope_and_both_clocks_filter_reports_and_original_source(store, monkeypatch):
    project = store.project("合成历史", [])
    monkeypatch.setattr("rg.record.manifest.now", lambda: T3)
    record(store, project, body(occurred_at=T1, scope={"data": "a"}))
    record(store, project, body(occurred_at=T4, scope={"data": "b"}))
    with pytest.raises(ValueError):
        view(store, project, known_until=T2)
    assert view(store, project, known_until=T4, occurred_until=T2)["manifests"]["total"] == 1
    assert view(store, project, scope={"data": "b"})["manifests"]["total"] == 1


def test_append_only_guards_rollback_and_backup_without_workspace(store, tmp_path, monkeypatch):
    project = store.project("合成备份", [])
    original_put = store.objects.put

    def fail(raw):
        original_put(raw)
        raise OSError("合成落盘失败")

    with monkeypatch.context() as change:
        change.setattr(store.objects, "put", fail)
        with pytest.raises(OSError):
            record(store, project, body(outputs=["unknown"]))
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    receipt = record(store, project, body(outputs=["unknown"]))
    for sql in (
        "DELETE FROM run_manifests",
        "UPDATE run_manifests SET claim_state='confirmed'",
        "DELETE FROM run_io",
        "UPDATE run_io SET requested_version_id='changed'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(sql)
    target = tmp_path / "backup"
    backup(store, target)
    raw = store.raw(receipt["evidence_event_id"])
    store.close()
    shutil.rmtree(store.root)
    with closing(Store(target, readonly=True)) as restored:
        assert restored.raw(receipt["evidence_event_id"]) == raw
        assert view(restored, project)["manifests"]["total"] == 1
        assert restored.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v14_migration_is_atomic_preserves_legacy_io_and_does_not_promote_it(tmp_path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=14)
    db = sqlite3.connect(root / "rg.db")
    db.execute("INSERT INTO run_io VALUES ('legacy-run','legacy-version','in','time_match')")
    db.commit()
    db.close()
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 15, (*migrations.MIGRATIONS[15], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    db = sqlite3.connect(root / "rg.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 14
    assert db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 1
    db.close()
    with closing(Store(root)) as upgraded:
        assert upgraded.raw(1) == raw
        assert upgraded.db.execute("SELECT manifest_id,claim_state FROM run_io").fetchone()[:] == (
            None,
            None,
        )
        assert upgraded.db.execute("SELECT count(*) FROM run_manifests").fetchone()[0] == 0


def test_api_contract_strict_parameters_and_project_isolation(store):
    project = store.project("合成API", [])
    other = store.project("其它API", [])
    record(store, project, body())
    values = {"project": project, "run_id": "unknown-run"}
    assert api_run(store, values)["manifests"]["total"] == 1
    for invalid in (
        {"io_offset": "1.0"},
        {"io_offset": "-1"},
        {"offset": "2147483648"},
        {"bad": "x"},
        {"project": other},
        {"manifest_id": str(uuid4())},
    ):
        with pytest.raises(ValueError):
            api_run(store, values | invalid)
    assert api_event(store, 1, {})["l1"]["runs"] == []


def test_ambiguous_native_call_and_conflicting_exit_codes_are_not_success(store, tmp_path):
    _, project = ingest(
        store,
        tmp_path,
        [
            claude_call(),
            claude_result(metadata={"exit_code": 0}),
            claude_result(metadata={"exit_code": 2}, uuid="other-result"),
        ],
    )
    identity = runs(store)[0]["run_id"]
    assert view(store, project, identity)["run"]["state"] == "unknown"
    assert view(store, project, identity)["run"]["gap"] == "conflicting_execution_facts"
    ingest(
        store,
        tmp_path,
        [claude_call(uuid="other-call")],
        project=project,
        filename="duplicate.jsonl",
    )
    record(store, project, body(identity, exit_code=0))
    response = view(store, project, identity)
    assert response["run"]["binding_state"] == "native_request_ambiguous"
    assert response["run"]["exit_code"] is None
    assert response["manifests"]["items"][0]["claim_state"] == "candidate"


def test_late_foreign_native_id_never_discloses_other_project_through_report(store, tmp_path):
    # 显式缺失 ID 是候选报告；后来同 ID 被其它项目使用也不能跨项目读取。
    project = store.project("报告所属", [])
    identity = "foreign-late-run"
    receipt = record(store, project, body(identity))
    foreign = store.project("原生所属", [])
    store.db.execute(
        "INSERT INTO runs(run_id,project_id,command,state) VALUES (?,?,?,'unknown')",
        (identity, foreign, "private-other-project-command"),
    )
    response = view(store, project, identity)
    assert response["run"] is None
    assert "private-other-project-command" not in json.dumps(response)
    assert api_event(store, receipt["evidence_event_id"], {})["l1"]["runs"] == []


def test_attempt_association_is_candidate_and_filtered_before_paging(store, tmp_path):
    from rg.query.reader import Reader
    from rg.query.runs import associations
    from tests.golden.test_links_overview import add_object

    project = store.project("合成尝试", [])
    claim, _ = add_object(store, tmp_path, project, "attempt", "合成尝试对象", kind="attempt")
    attempt = store.db.execute(
        "SELECT entity_id FROM claims WHERE claim_id=?", (claim,)
    ).fetchone()[0]
    receipt = record(store, project, body(attempt_id=attempt))
    for _ in range(101):
        record(store, project, body())
    result = associations(Reader(store, project, {}), attempt=attempt)
    assert result["items"][0]["manifest_ids"] == [receipt["request_id"]]
    assert result["items"][0]["association_state"] == "reported_only"


def test_manifest_http_uses_existing_auth_origin_and_remains_readonly(store, tmp_path):
    import threading

    import httpx

    from rg.api.server import LocalServer

    project = store.project("合成网络", [])
    record(store, project, body(outputs=["unknown"] * 101))
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("合成页面")
    server = LocalServer(store.root, root, port=0, token="synthetic-run-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = store.revision()
    try:
        with httpx.Client(
            base_url=server.origin,
            trust_env=False,
            headers={"Authorization": "Bearer synthetic-run-token"},
        ) as client:
            params = {"project": project, "run_id": "unknown-run", "io_offset": 100}
            response = client.get("/api/run-evidence", params=params)
            assert response.status_code == 200
            assert response.json()["manifests"]["items"][0]["io"]["offset"] == 100
            assert (
                client.get(
                    "/api/run-evidence", params=params, headers={"Authorization": ""}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/api/run-evidence",
                    params=params,
                    headers={"Origin": "https://invalid.example"},
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/run-evidence", params=params | {"expected_revision": before - 1}
                ).status_code
                == 409
            )
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert store.revision() == before
