from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api import views
from rg.api.server import LocalServer
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import query
from rg.query.reader import Reader
from rg.record.question import question
from rg.slim.slimmer import slim_session
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from rg.store.fragments import assign
from rg.store.locking import TaskBusy, exclusive
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record
from tests.golden.test_project_fragments import codex, scene, user, view
from tests.test_migrations import legacy_store
from tests.test_worker import PipelineProvider


def intent(store, project, first=3, last=4, source=1, session=1):
    page = query(store, {"session": str(session), "source": str(source)})
    return {
        "request_id": str(uuid4()),
        "session": session,
        "source": source,
        "first_event": first,
        "last_event": last,
        "project_id": project,
        "actor": "human:公开验收",
        "expected_revision": page["revision"],
        "expected_scope_key": page["scope_key"],
        "snapshot": page["snapshot"],
        "observation_snapshot": page["observation_snapshot"],
    }


def owners(store):
    return [
        tuple(r)
        for r in store.db.execute(
            "SELECT e.event_id,s.project_id,e.session_pk FROM effective_raw_events e "
            "JOIN sessions s USING(session_pk) ORDER BY e.event_id"
        )
    ]


def test_assignment_preserves_raw_and_isolates_search_evidence_and_slim(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    objects = [store.raw(i) for i in range(1, 5)]
    result = assign(store, intent(store, second))
    assert result["records_assigned"] == 2 and result["partitions_created"] == 2
    assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == raw
    assert [store.raw(i) for i in range(1, 5)] == objects
    assert [r[1] for r in owners(store)] == [first, first, second, second]
    assert views.search(store, {"q": "公开乙", "project": first})["total"] == 0
    assert views.search(store, {"q": "公开乙", "project": second})["total"] == 1
    assert len(views.health(store, {"project": second}, 500000)["sources"]) == 1
    assert views.health(store, {"project": second}, 500000)["sources"][0]["session_pk"] == 1
    evidence = views.evidence(store, 4, {"context": "5"})
    assert all(x["event_id"] >= 3 for x in evidence["before"])
    with pytest.raises(views.NotFound):
        Reader(store, first, {"event_id": 4}).evidence()
    second_session = owners(store)[-1][2]
    slim = slim_session(store, second_session, FakeProvider())
    assert {r["event_id"] for r in slim} <= {3, 4}
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0


def test_uuid_replay_precedes_revision_and_scope_checks(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    first = assign(store, body)
    store.db.execute("UPDATE graph_clock SET revision=revision+3")
    before = list(store.db.iterdump())
    repeated = assign(store, body | {"expected_revision": 0, "expected_scope_key": "0" * 64})
    assert repeated == first | {"replayed": True}
    assert list(store.db.iterdump()) == before
    with pytest.raises(ConflictError, match="不同归属"):
        assign(store, body | {"project_id": None})


@pytest.mark.parametrize("first_operation", ["fragment", "question"])
def test_uuid_cannot_be_reused_for_another_record_operation(store, tmp_path, first_operation):
    first, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    note = {
        "request_id": body["request_id"],
        "project_id": first,
        "actor": "human:公开验收",
        "text": "公开问题",
        "expected_revision": store.revision(),
    }
    if first_operation == "fragment":
        assign(store, body)
        with pytest.raises(ConflictError):
            question(store, note)
    else:
        question(store, note)
        fresh = intent(store, second) | {"request_id": body["request_id"]}
        with pytest.raises(ConflictError):
            assign(store, fresh)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_event", True),
        ("source", 0),
        ("last_event", 2),
        ("project_id", "missing"),
        ("actor", "model:提取器"),
        ("snapshot", -1),
    ],
)
def test_invalid_intent_has_no_partial_write(store, tmp_path, field, value):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        assign(store, body | {field: value})
    assert list(store.db.iterdump()) == before


def test_stale_page_and_partition_change_cannot_be_combined(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    old = view(store, limit="1")
    body = intent(store, second)
    assign(store, body)
    with pytest.raises(ConflictError):
        assign(store, body | {"request_id": str(uuid4())})
    with pytest.raises(ConflictError):
        view(
            store,
            after_event=str(old["next_after_event"]),
            snapshot=str(old["snapshot"]),
            observation_snapshot=str(old["observation_snapshot"]),
            expected_scope_key=old["scope_key"],
        )


def test_only_whole_claude_record_can_move(store, tmp_path):
    first, second = store.project("甲", []), store.project("乙", [])
    item = record("公开完整记录")
    item["message"]["content"] = [
        {"type": "text", "text": "公开第一块"},
        {"type": "text", "text": "公开第二块"},
    ]
    path = tmp_path / "claude.jsonl"
    lines(path, [item])
    scan_file(store, path, "claude", first)
    with pytest.raises(ValueError, match="完整物理"):
        assign(store, intent(store, second, 2, 2))
    result = assign(store, intent(store, second, 1, 1))
    assert result["records_assigned"] == 1
    assert [r[1] for r in owners(store)] == [second, second]


def test_repartitioning_middle_keeps_two_a_contexts_separate(store, tmp_path):
    first, second, path, *_ = scene(store, tmp_path)
    lines(
        path,
        [
            codex("session_meta", {"id": "public-thread"}),
            user("公开一"),
            user("公开二"),
            user("公开三"),
            user("公开四"),
        ],
    )
    # 新文件使用独立会话，避免把覆盖源原件的轮转当作同一物理实例。
    fresh = tmp_path / "fresh.jsonl"
    lines(
        fresh,
        [
            codex("session_meta", {"id": "fresh-thread"}),
            user("公开一"),
            user("公开二"),
            user("公开三"),
            user("公开四"),
        ],
    )
    scan_file(store, fresh, "codex", first)
    assign(store, intent(store, first, 5, 9, source=2, session=2))
    assign(store, intent(store, second, 7, 7, source=2, session=2))
    mapping = owners(store)[4:]
    assert [r[1] for r in mapping] == [first, first, second, first, first]
    assert mapping[0][2] != mapping[-1][2]
    assert [r["event_id"] for r in views.evidence(store, 6, {"context": "5"})["after"]] == []


def test_append_is_inbox_and_rescan_does_not_restore_whole_session_project(store, tmp_path):
    first, second, path, *_ = scene(store, tmp_path)
    assign(store, intent(store, second))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(user("公开新增未归属"), ensure_ascii=False) + "\n")
    scan_file(store, path, "codex", first)
    assert owners(store)[-1][1:] == (None, 1)
    assert (
        store.db.execute("SELECT project_basis FROM sessions WHERE session_pk=1").fetchone()[0]
        == "fragment_container"
    )
    before = list(store.db.iterdump())
    assert scan_file(store, path, "codex", first) == {}
    assert list(store.db.iterdump()) == before


def test_extract_sends_only_the_assigned_partition(store, tmp_path):
    first, second = store.project("甲", []), store.project("乙", [])
    path = tmp_path / "extract.jsonl"
    lines(path, [record("甲公开采用方法甲", "a"), record("乙公开采用方法甲", "b")])
    scan_file(store, path, "claude", first)
    assign(store, intent(store, second, 2, 2))
    session = owners(store)[-1][2]

    class Audited(PipelineProvider):
        def __init__(self):
            super().__init__()
            self.inputs = []

        def generate(self, request):
            self.inputs.append(copy.deepcopy(request))
            assert "甲公开" not in request["input"]
            return super().generate(request)

    provider = Audited()
    assert Worker(store, provider).process(session)["claims"] == 1
    assert provider.inputs and provider.calls >= 2
    assert store.db.execute("SELECT DISTINCT project_id FROM entities").fetchone()[0] == second
    assert store.db.execute("SELECT claim_state FROM claims").fetchone()[0] == "candidate"
    before = list(store.db.iterdump())
    with pytest.raises(ConflictError, match="科研依赖"):
        assign(store, intent(store, first, 2, 2))
    assert list(store.db.iterdump()) == before


def test_prepared_model_input_cannot_cross_a_later_assignment(store, tmp_path, monkeypatch):
    first, second, *_ = scene(store, tmp_path)
    provider = FakeProvider(count=10)
    worker = Worker(store, provider)
    body = intent(store, second)
    # 模型能力核对结束后、pending输入登记前发生合法重归属。
    monkeypatch.setattr(worker, "_check_model_limits", lambda: assign(store, body))
    item = Segment("public-prepared-input", [{"event_id": 4}])
    with pytest.raises(InvalidClaim, match="归属已变化"):
        worker.invoke("qa", item, first, "公开旧准备内容", {"type": "object"}, [])
    assert provider.counts == provider.calls == 0
    assert store.db.execute("SELECT count(*) FROM extraction_runs").fetchone()[0] == 0
    assert owners(store)[-1][1] == second


@pytest.mark.parametrize("project_index", [0, 1])
def test_partial_project_clear_preserves_other_raw_and_restorable_backup(tmp_path, project_index):
    root = tmp_path / "store"
    with closing(Store(root)) as store:
        first, second, path, *_ = scene(store, tmp_path)
        assign(store, intent(store, second))
        projects = first, second
        target, retained = projects[project_index], projects[1 - project_index]
        retained_raw = {r[0]: store.raw(r[0]) for r in owners(store) if r[1] == retained}
    plan = clear.preview(root, target)
    assert plan["blockers"] == [] and plan["rows"]["raw_events"] == 2
    clear.execute(root, target, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root)) as store:
        assert {r[0] for r in owners(store)} == set(retained_raw)
        assert all(store.raw(event) == raw for event, raw in retained_raw.items())
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None
        backup(store, tmp_path / "backup")
    with closing(Store(tmp_path / "backup")) as restored:
        assert all(restored.raw(event) == raw for event, raw in retained_raw.items())
    # 最后一部分清除移除物理容器、游标和分区账本，外部日志始终保留。
    last = clear.preview(root, retained)
    assert last["blockers"] == []
    clear.execute(root, retained, str(uuid4()), last["preview_sha256"])
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM source_files").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert path.exists()


def test_assignment_locks_prevent_provider_and_source_races(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    before = list(store.db.iterdump())
    with exclusive(store.root / "locks" / "sessions" / "1.lock"):
        with pytest.raises(TaskBusy):
            assign(store, body)
    assert list(store.db.iterdump()) == before


def test_missing_legacy_first_block_cannot_lose_existing_ownership(store, tmp_path):
    first, second, *_ = scene(store, tmp_path)
    store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end,"
        "object_sha256,seq,kind,recorded_at,line_sha256) "
        "SELECT session_pk,file_instance_id,1,999,1000,object_sha256,99,kind,recorded_at,"
        "line_sha256 FROM raw_events WHERE event_id=2"
    )
    body = intent(store, second)
    before = list(store.db.iterdump())
    with pytest.raises(ConflictError, match="缺少完整物理首块"):
        assign(store, body)
    assert list(store.db.iterdump()) == before
    assert all(r[1] == first for r in owners(store))


def test_migration31_restores_fk_indices_and_rolls_back_without_reading_objects(
    tmp_path, monkeypatch
):
    root, _ = legacy_store(tmp_path, monkeypatch, version=30)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        db.execute("CREATE INDEX synthetic_session_index ON sessions(project_id)")
        original = list(db.iterdump())
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 31, (*migrations.MIGRATIONS[31], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert list(db.iterdump()) == original
    with closing(Store(root)) as store:
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None
        assert store.db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='synthetic_session_index'"
        ).fetchone()


def test_http_token_duplicate_fields_and_uuid_replay_use_actual_routes(store, tmp_path):
    _, second, *_ = scene(store, tmp_path)
    body = intent(store, second)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("公开测试", encoding="utf-8")
    server = LocalServer(store.root, web, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = server.origin
    try:
        with httpx.Client(base_url=base, trust_env=False) as client:
            assert client.post("/api/project-fragments/assign", json=body).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.post("/api/project-fragments/assign", json=body)
            assert response.status_code == 200, response.text
            repeated = client.post("/api/project-fragments/assign", json=body)
            assert repeated.json() == response.json() | {"replayed": True}
            duplicate = json.dumps(body)[:-1] + ',"session":1}'
            assert (
                client.post(
                    "/api/project-fragments/assign",
                    content=duplicate,
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 400
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
