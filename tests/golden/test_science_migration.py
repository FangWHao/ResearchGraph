from __future__ import annotations

import pytest

from rg.query.reader import Reader
from rg.store.database import ConflictError, now
from rg.store.fragment_dependencies import preview
from rg.store.fragments import assign
from rg.store.ownership import entity_owner, event_owner
from rg.store.science_migration import migrate
from tests.golden.test_fragment_assignments import intent
from tests.golden.test_fragment_dependencies import claim, entity, extraction
from tests.golden.test_project_fragments import scene


def request(store, project, **options):
    body = intent(store, project, **options)
    plan = preview(store, body)
    return body | {"expected_plan_sha256": plan["plan_sha256"], "include_required_records": True}


def physical(store):
    tables = (
        "raw_events",
        "claims",
        "entities",
        "review_actions",
        "evidence_spans",
        "claim_evidence",
        "extraction_runs",
    )
    return {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }


def test_migration_preserves_science_and_review_states_and_history(store, tmp_path):
    a, b, *_ = scene(store, tmp_path)
    target = entity(store, a)
    old = claim(store, target, 4)
    store.review(old, "confirm", "human:公开验收", store.revision())
    new = claim(store, target, 4, replaces=old)
    before, at = physical(store), now()
    data = request(store, b)
    result = migrate(store, data)
    assert result["migration_applied"] and result["scientific_records_rewritten"] == 0
    assert result["model_calls"] == 0 and result["records_assigned"] == 2
    assert physical(store) == before
    assert entity_owner(store.db, target) == b and event_owner(store.db, 4) == b
    assert entity_owner(store.db, target, at) == a and event_owner(store.db, 4, at) == a
    assert store.claim_state(old) == "confirmed" and store.claim_state(new) == "candidate"
    assert {r["claim_id"] for r in Reader(store, b, {}).claims()} == {old, new}
    assert Reader(store, a, {}).claims() == []
    assert {r["claim_id"] for r in Reader(store, a, {"known_until": at}).claims()} == {old, new}
    assert Reader(store, b, {"known_until": at}).claims() == []
    assert Reader(store, a, {"known_until": at}).references(old)["total"] == 1
    historical = Reader(store, a, {"known_until": at, "event_id": 4}).evidence()
    assert historical["event_id"] == 4
    assert Reader(store, a, {"known_until": at, "source": "events"}).search("公开乙")["total"] == 1
    assert Reader(store, b, {"source": "events"}).search("公开乙")["total"] == 1
    assert result["revision"] == store.revision()
    revision = store.revision()
    assert migrate(store, data)["replayed"] and store.revision() == revision
    with pytest.raises(ConflictError):
        migrate(store, data | {"actor": "human:另一人"})
    with pytest.raises(ConflictError):
        assign(store, {k: data[k] for k in intent(store, b)})
    assert physical(store) == before


def test_migration_includes_shared_versions_context_and_external_records(store, tmp_path):
    a, b, *_ = scene(store, tmp_path)
    one, two = entity(store, a), entity(store, a)
    claim(store, one, 4)
    claim(store, two, 2)
    relation = claim(
        store, one, 4, payload={"claim_type": "relation", "source": one, "target": two}
    )
    run = extraction(store, [2, 4], [one, two])
    claim(store, one, 4, run=run)
    before = physical(store)
    result = migrate(store, request(store, b))
    assert result["required_records_included"] == 1 and result["records_assigned"] == 3
    assert result["dependencies_migrated"]["entities"] == 2
    assert entity_owner(store.db, one) == entity_owner(store.db, two) == b
    assert event_owner(store.db, 2) == event_owner(store.db, 4) == b
    assert any(row["claim_id"] == relation for row in Reader(store, b, {}).claims())
    assert physical(store) == before


def test_invalid_stale_plan_and_missing_consent_are_atomic(store, tmp_path):
    a, b, *_ = scene(store, tmp_path)
    target = entity(store, a)
    claim(store, target, 4)
    data = request(store, b)
    before = list(store.db.iterdump())
    for altered in (
        {"expected_plan_sha256": "0" * 64},
        {"include_required_records": False},
        {"project_id": None},
        {"actor": "model:自动"},
    ):
        with pytest.raises(ValueError):
            migrate(store, data | altered)
        assert list(store.db.iterdump()) == before
    claim(store, target, 4)
    changed = list(store.db.iterdump())
    with pytest.raises(ConflictError):
        migrate(store, data)
    assert list(store.db.iterdump()) == changed


def test_repeated_moves_keep_current_and_historical_ownership(store, tmp_path):
    a, b, *_ = scene(store, tmp_path)
    c = store.project("公开第三项目", [])
    target = entity(store, a)
    claim(store, target, 4)
    first_time = now()
    migrate(store, request(store, b))
    second_time = now()
    migrate(store, request(store, c))
    assert entity_owner(store.db, target) == c
    assert entity_owner(store.db, target, second_time) == b
    assert entity_owner(store.db, target, first_time) == a
    assert event_owner(store.db, 4) == c
    assert event_owner(store.db, 4, second_time) == b
    assert event_owner(store.db, 4, first_time) == a
    assert Reader(store, c, {}).claims()[0]["claim_state"] == "candidate"


def test_plain_assign_does_not_bypass_science(store, tmp_path):
    a, b, *_ = scene(store, tmp_path)
    claim(store, entity(store, a), 4)
    with pytest.raises(ConflictError, match="科研依赖"):
        assign(store, intent(store, b))
    assert store.db.execute("SELECT count(*) FROM science_migrations").fetchone()[0] == 0


def test_native_manual_receipt_is_moved_without_new_confirmation(store, tmp_path):
    from uuid import uuid4

    from rg.record.question import question

    a, b, *_ = scene(store, tmp_path)
    saved = question(
        store,
        {
            "project_id": a,
            "text": "公开历史问题",
            "actor": "human:公开验收",
            "scope": None,
            "request_id": str(uuid4()),
            "expected_revision": store.revision(),
        },
    )
    target = entity(store, a)
    claim(
        store,
        target,
        4,
        payload={
            "claim_type": "relation",
            "source": saved["entity_id"],
            "target": target,
        },
    )
    before = physical(store)
    migrate(store, request(store, b))
    assert physical(store) == before
    assert store.claim_state(saved["claim_id"]) == "confirmed"
    assert saved["claim_id"] in {r["claim_id"] for r in Reader(store, b, {}).claims()}
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0
    assert store.db.execute("SELECT project_id FROM effective_explicit_records").fetchone()[0] == b


def test_pending_queue_uses_new_identity_and_target_remote_permission(store, tmp_path):
    from rg.extract.queue import Queue
    from rg.extract.worker import Worker
    from tests.golden.test_incremental import RecordingProvider

    a, b, *_ = scene(store, tmp_path)
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (a,))
    provider = RecordingProvider()
    provider.remote = True
    queue = Queue(Worker(store, provider))
    assert queue._discover(False)["enqueued"] == 1
    before = [tuple(r) for r in store.db.execute("SELECT * FROM extraction_queue")]
    first = migrate(store, request(store, b))
    assert first["records_assigned"] == 4
    assert [tuple(r) for r in store.db.execute("SELECT * FROM extraction_queue")] == before
    task = store.db.execute("SELECT * FROM effective_extraction_queue").fetchone()
    assert task["project_id"] == b and task["session_pk"] != 1
    assert queue.run()["remote_disabled"] == 1
    assert provider.calls == 0
    assert store.db.execute("SELECT state FROM extraction_queue").fetchone()[0] == "blocked"
    c = store.project("公开第二次接收", [])
    migrate(store, request(store, c))
    again = store.db.execute("SELECT * FROM effective_extraction_queue").fetchone()
    assert again["project_id"] == c and again["session_pk"] != task["session_pk"]
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("name", ("extract-queue", "postprocess", "derive", "artifacts"))
def test_active_worker_lock_rejects_migration_atomically(store, tmp_path, name):
    from rg.store.locking import TaskBusy, exclusive

    a, b, *_ = scene(store, tmp_path)
    claim(store, entity(store, a), 4)
    data = request(store, b)
    before = list(store.db.iterdump())
    with exclusive(store.root / "locks" / f"{name}.lock"):
        with pytest.raises(TaskBusy):
            migrate(store, data)
    assert list(store.db.iterdump()) == before


def test_v33_migration_rolls_back_and_preserves_original_rows(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    from rg.store import migrations
    from rg.store.database import Store
    from rg.store.ownership_schema import OWNED_TABLES
    from tests.test_migrations import legacy_store

    root, raw = legacy_store(tmp_path, monkeypatch, version=32)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        before = db.execute("SELECT * FROM raw_events").fetchall()
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 33, (*migrations.MIGRATIONS[33], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 32
        assert db.execute("SELECT * FROM raw_events").fetchall() == before
        assert not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='science_ownership'"
        ).fetchone()
    with closing(Store(root)) as recovered:
        assert recovered.raw(1) == raw
        assert [tuple(r) for r in recovered.db.execute("SELECT * FROM raw_events")] == before
        assert recovered.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert recovered.db.execute("SELECT count(*) FROM science_migrations").fetchone()[0] == 0
        for table in OWNED_TABLES:
            original = [r[1] for r in recovered.db.execute(f"PRAGMA table_info({table})")]
            projected = [
                r[1] for r in recovered.db.execute(f"PRAGMA table_info(effective_{table})")
            ]
            assert projected == original


def test_failed_local_qualification_rolls_back_every_ownership_record(store, tmp_path, monkeypatch):
    a, b, *_ = scene(store, tmp_path)
    claim(store, entity(store, a), 4)
    data = request(store, b)
    before = list(store.db.iterdump())

    def fault(*args, **kwargs):
        raise RuntimeError("公开中断")

    monkeypatch.setattr("rg.derive.eligibility.assess", fault)
    with pytest.raises(RuntimeError, match="公开中断"):
        migrate(store, data)
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("scoped", (False, True))
def test_rule_proof_keeps_original_namespace_at_review_time(store, tmp_path, scoped):
    from rg.derive import rule_references
    from tests.golden.test_rule_reference_qualification import legacy_action
    from tests.golden.test_rule_scope_versions import another_scope

    a, target, saved, _review, _definition, output = legacy_action(store, tmp_path, scoped=scoped)
    b = store.project("公开目标命名空间", [])
    other = entity(store, b)
    # 相同显示名在新项目出现，不能回写旧规则的唯一性判断。
    another_scope(store, other, output["claims"][0], {"dataset_version": "data_v2"})
    state = rule_references.assess(store, saved)["status"]
    event = store.db.execute(
        "SELECT e.event_id,r.session_pk,r.file_instance_id FROM "
        "claim_evidence c JOIN evidence_spans e USING(span_id) "
        "JOIN raw_events r USING(event_id) WHERE claim_id=?",
        (saved,),
    ).fetchone()
    before = physical(store)
    migrate(
        store, request(store, b, first=event[0], last=event[0], source=event[2], session=event[1])
    )
    assert physical(store) == before
    assert entity_owner(store.db, target) == b
    assert rule_references.recorded(store, saved)["status"] == state
    assert rule_references.assess(store, saved, refresh=True)["status"] == state
    row = next(r for r in Reader(store, b, {}).claims() if r["claim_id"] == saved)
    assert row["effective_state"] == "confirmed"
    assert row["input_eligible"] == (not scoped)


@pytest.mark.parametrize("interrupt_prune", (False, True))
def test_native_snapshot_io_remains_readable_after_move_and_origin_clear(
    store, tmp_path, monkeypatch, interrupt_prune
):
    import time
    from contextlib import closing
    from uuid import uuid4

    from rg.artifacts.worker import Worker
    from rg.query.runs import native
    from rg.snapshot.capture import capture
    from rg.snapshot.git import Git
    from rg.store.backup import backup
    from rg.store.clear import execute, resume
    from rg.store.clear import preview as clear_preview
    from rg.store.database import Store
    from tests.golden.test_l1 import claude_call, claude_result, ingest
    from tests.golden.test_snapshots_hooks import project, queued

    a, root_id, work = project(store, tmp_path)
    b = store.project("公开快照接收项目", [])
    one = claude_call(cwd=str(work))
    two = claude_result(metadata={"exit_code": 0})
    ingest(store, tmp_path, [one, two], project=a)
    old_content = b"public-old-only-unreferenced-by-target\n"
    (work / "old-only.txt").write_bytes(old_content)
    first = capture(store.root, a, root_id, work, tool="claude", native_session_id="l1-synthetic")
    queued(store)
    (work / "old-only.txt").unlink()
    (work / "source.txt").write_bytes(b"public-target-snapshot\n")
    second = capture(store.root, a, root_id, work, tool="claude", native_session_id="l1-synthetic")
    queued(store)
    assert first["shadow_commit"] and second["shadow_commit"]
    Worker(store).run()
    at = now()
    snap = store.db.execute(
        "SELECT snapshot_id FROM workspace_snapshots WHERE snapshot_key=?",
        (second["snapshot_key"],),
    ).fetchone()[0]
    store.db.execute("UPDATE runs SET snapshot_id=?", (snap,))
    native_before = {
        t: [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        for t in (
            "runs",
            "run_observations",
            "workspace_snapshots",
            "artifact_versions",
            "artifact_observations",
        )
    }
    migrate(store, request(store, b, first=1, last=2))
    for t, rows in native_before.items():
        assert [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")] == rows
    assert (
        store.db.execute(
            "SELECT project_id FROM effective_workspace_snapshots WHERE snapshot_id=?", (snap,)
        ).fetchone()[0]
        == b
    )
    assert Worker(store).run().get("failed", 0) == 0
    run_id = store.db.execute("SELECT run_id FROM runs").fetchone()[0]
    assert native(Reader(store, a, {"known_until": at}), run_id)["state"] == "exited"
    assert native(Reader(store, b, {}), run_id)["state"] == "exited"
    data_root = store.root
    store.close()
    plan = clear_preview(data_root, a)
    assert plan["blockers"] == []
    request_id = str(uuid4())
    if interrupt_prune:

        def fault(*args, **kwargs):
            raise RuntimeError("公开影子清理中断")

        with monkeypatch.context() as patch:
            patch.setattr("rg.store.shadow_cleanup.prune", fault)
            with pytest.raises(RuntimeError, match="公开影子清理中断"):
                execute(data_root, a, request_id, plan["preview_sha256"])
        assert (data_root / ".clear-active.json").is_file()
        assert resume(data_root, request_id)["state"] == "complete"
        assert resume(data_root, request_id)["state"] == "complete"
    else:
        execute(data_root, a, request_id, plan["preview_sha256"])
    with closing(Store(data_root)) as reopened:
        repository = data_root / "snapshots" / (a + ".git")
        git = Git(data_root, time.monotonic() + 30, repository)
        git.run("cat-file", "-e", second["shadow_commit"] + "^{commit}")
        old_blob = git.run("hash-object", "--stdin", data=old_content).decode().strip()
        assert git.run("cat-file", "--batch-check", data=(old_blob + "\n").encode()).endswith(
            b" missing\n"
        )
        commits = git.run("rev-list", "--all").decode().splitlines()
        assert set(commits) == {second["shadow_commit"]}
        assert reopened.db.execute("SELECT count(*) FROM active_projects").fetchone()[0] == 1
        assert Worker(reopened).run().get("failed", 0) == 0
        result = backup(reopened, tmp_path / "migrated-backup")
        assert result["events"] == 2
    plan = clear_preview(data_root, b)
    assert plan["blockers"] == []
    execute(data_root, b, str(uuid4()), plan["preview_sha256"])
    assert not (data_root / "snapshots" / (a + ".git")).exists()


@pytest.mark.parametrize("first_clear", ("origin", "target"))
def test_clear_uses_current_science_owner_and_removes_all_copies(store, tmp_path, first_clear):
    from contextlib import closing
    from uuid import uuid4

    from rg.store.clear import execute
    from rg.store.clear import preview as clear_preview
    from rg.store.database import Store

    a, b, *_ = scene(store, tmp_path)
    target = entity(store, a)
    saved = claim(store, target, 4)
    migrate(store, request(store, b))
    root = store.root
    store.close()
    owner = a if first_clear == "origin" else b
    plan = clear_preview(root, owner)
    assert plan["blockers"] == []
    result = execute(root, owner, str(uuid4()), plan["preview_sha256"])
    assert result["state"] == "complete"
    with closing(Store(root)) as reopened:
        assert reopened.db.execute("PRAGMA foreign_key_check").fetchall() == []
        if owner == a:
            assert entity_owner(reopened.db, target) == b
            assert Reader(reopened, b, {}).claims()[0]["claim_id"] == saved
            assert not reopened.db.execute(
                "SELECT 1 FROM active_projects WHERE project_id=?", (a,)
            ).fetchone()
        else:
            assert (
                reopened.db.execute("SELECT 1 FROM claims WHERE claim_id=?", (saved,)).fetchone()
                is None
            )
            assert reopened.db.execute("SELECT count(*) FROM science_ownership").fetchone()[0] == 0
    other = b if owner == a else a
    plan = clear_preview(root, other)
    assert plan["blockers"] == []
    execute(root, other, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root)) as reopened:
        assert reopened.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert reopened.db.execute("SELECT count(*) FROM source_roots").fetchone()[0] == 0
        assert reopened.db.execute("SELECT count(*) FROM science_migrations").fetchone()[0] == 0
