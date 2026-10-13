from __future__ import annotations

import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from rg.query.artifacts import version_record
from rg.query.reader import Reader
from rg.query.runs import native
from rg.store import migrations, roots
from rg.store.backup import backup
from rg.store.clear import execute, preview
from rg.store.database import ConflictError, Store, dumps
from rg.store.root_history import observation
from rg.store.root_history_schema import STAMP

FIRST = "2030-01-01T00:00:00+00:00"
CHANGE = "2030-01-01T00:00:10+00:00"
LATER = "2030-01-01T00:00:20+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"


def clock(store, at):
    # 固定公开合成登记阶段，不依赖机器墙钟单调；生产仍用 SQLite 的 UTC 时刻。
    store.db.create_function("strftime", 2, lambda *_: at)


def scene(store, tmp_path):
    clock(store, FIRST)
    project = store.project("公开登记历史", [tmp_path / "workspace"])
    root = store.db.execute("SELECT root_id FROM source_roots").fetchone()[0]
    return project, root


def test_registration_is_atomic_append_only_and_repeat_is_stable(store, tmp_path):
    project, root = scene(store, tmp_path)
    proof = observation(store.db, root)
    assert proof["basis"] == "registration"
    assert proof["occurred_at"] == proof["recorded_at"] == FIRST
    assert proof["declaration"]["project_id"] == project
    before = list(store.db.iterdump())
    assert not roots.register(store, project, tmp_path / "workspace")["created"]
    assert list(store.db.iterdump()) == before
    for sql in (
        "UPDATE source_root_observations SET recorded_at='invalid'",
        "DELETE FROM source_root_observations",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(sql)
    assert list(store.db.iterdump()) == before
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            roots.insert(db, project, roots.prepare(store, tmp_path / "failed"))
            raise RuntimeError("公开发布中断")
    assert list(store.db.iterdump()) == before


def test_metadata_change_preserves_old_identity_and_double_cutoff(store, tmp_path):
    project, root = scene(store, tmp_path)
    old = observation(store.db, root)
    revision = store.revision()
    clock(store, CHANGE)
    changed = dumps({"status": "registered", "git_common_dir": "/synthetic/new.git"})
    with store.transaction() as db:
        db.execute(
            "UPDATE source_roots SET git_common_dir=?,git_metadata=? WHERE root_id=?",
            ("/synthetic/new.git", changed, root),
        )
    assert store.revision() == revision + 1
    assert observation(store.db, root, known_until=FIRST) == old
    assert observation(store.db, root, known_until="2030-01-01T08:00:10+08:00")["basis"] == (
        "metadata_change"
    )
    assert observation(store.db, root, known_until="2030-01-01T00:00:09.999999+00:00") == old
    assert observation(store.db, root, occurred_until=FIRST, known_until=LATER) == old
    assert observation(store.db, root, known_until=LATER)["declaration"]["git_metadata"] == (
        {"status": "registered", "git_common_dir": "/synthetic/new.git"}
    )
    rows = list(store.db.execute("SELECT * FROM source_root_observations"))
    store.db.execute("UPDATE source_roots SET git_metadata=git_metadata WHERE root_id=?", (root,))
    assert list(store.db.execute("SELECT * FROM source_root_observations")) == rows
    with pytest.raises(ConflictError):
        roots.listing(store, project, expected_revision=revision)


@pytest.mark.parametrize("field", ["occurred_until", "known_until"])
def test_listing_excludes_future_registration_and_keeps_pagination(store, tmp_path, field):
    project, first = scene(store, tmp_path)
    clock(store, CHANGE)
    second = roots.register(store, project, tmp_path / "another", "data")["root"]["root_id"]
    values = {"occurred_until": FUTURE, "known_until": FUTURE, field: FIRST}
    view = roots.listing(store, project, **values)
    assert view["total"] == 1 and view["roots"][0]["root_id"] == first
    assert second not in dumps(view)
    values = {"occurred_until": FUTURE, "known_until": FUTURE, field: LATER}
    view = roots.listing(store, project, 1, **values)
    assert view["total"] == 2 and view["next_offset"] == 1
    assert roots.listing(store, project, 1, 1, **values)["next_offset"] is None


def test_cli_cutoffs_and_invalid_timezone_are_explicit(store, tmp_path):
    from rg.cli.main import parser, run

    project, _ = scene(store, tmp_path)
    args = parser().parse_args(
        [
            "project",
            "roots",
            "--project",
            project,
            "--known-until",
            FIRST,
            "--occurred-until",
            FUTURE,
        ]
    )
    assert run(args, store) == roots.listing(
        store, project, known_until=FIRST, occurred_until=FUTURE
    )
    with pytest.raises(ValueError, match="时区"):
        roots.listing(store, project, known_until="2030-01-01")
    with pytest.raises(ValueError, match="时区"):
        observation(store.db, "missing", occurred_until="2030-01-01")


def test_migration_adds_unknown_time_baseline_without_probing_or_rewriting(tmp_path, monkeypatch):
    data = tmp_path / "db"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 36)
        with closing(Store(data)) as store:
            project = store.project("公开旧登记", [tmp_path / "missing-root"])
            old = [tuple(row) for row in store.db.execute("SELECT * FROM source_roots")]

    def forbidden(*_, **__):
        raise AssertionError("升级和读取不得探测当前工作区")

    monkeypatch.setattr(roots, "_git_identity", forbidden)
    monkeypatch.setitem(
        migrations.MIGRATIONS,
        37,
        tuple(sql.replace(STAMP, repr(CHANGE)) for sql in migrations.MIGRATIONS[37]),
    )
    with closing(Store(data)) as store:
        root = old[0][0]
        proof = observation(store.db, root)
        assert proof["basis"] == "legacy_baseline" and proof["occurred_time_unknown"]
        assert proof["occurred_at"] is None and proof["recorded_at"] == CHANGE
        assert observation(store.db, root, known_until=FIRST)["state"] == "unknown"
        assert roots.listing(store, project, known_until=FIRST)["total"] == 0
        assert roots.listing(store, project, known_until=LATER)["total"] == 1
        assert [tuple(row) for row in store.db.execute("SELECT * FROM source_roots")] == old
    with closing(Store(data)) as store:
        assert store.db.execute("SELECT count(*) FROM source_root_observations").fetchone()[0] == 1


def test_failed_upgrade_rolls_back_journal_and_original_rows(tmp_path, monkeypatch):
    data = tmp_path / "db"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 36)
        with closing(Store(data)) as store:
            store.project("公开失败迁移", [tmp_path / "workspace"])
            before = list(store.db.iterdump())
    monkeypatch.setitem(
        migrations.MIGRATIONS, 37, (*migrations.MIGRATIONS[37], "INVALID MIGRATION SQL")
    )
    with pytest.raises(sqlite3.OperationalError):
        Store(data)
    with closing(sqlite3.connect(data / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 36
        assert list(db.iterdump()) == before


def test_readonly_backup_retains_history_and_clear_removes_it(store, tmp_path, monkeypatch):
    project, root = scene(store, tmp_path)
    target = tmp_path / "backup"
    backup(store, target)

    def forbidden(*_, **__):
        raise AssertionError("登记历史只读不得打开对象或工作区")

    with closing(Store(target, readonly=True)) as saved:
        monkeypatch.setattr(saved.objects, "get", forbidden)
        before = list(saved.db.iterdump())
        assert observation(saved.db, root)["recorded_at"] == FIRST
        assert roots.listing(saved, project, known_until=LATER, occurred_until=FUTURE)["total"] == 1
        assert list(saved.db.iterdump()) == before
    root_dir = store.root
    store.close()
    plan = preview(root_dir, project)
    assert plan["blockers"] == []
    execute(root_dir, project, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root_dir)) as cleared:
        assert (
            cleared.db.execute("SELECT count(*) FROM source_root_observations").fetchone()[0] == 0
        )
        assert cleared.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_l1_cards_use_saved_registration_as_of_query_without_reading_objects(
    store, tmp_path, monkeypatch
):
    from rg.derive.path_checks import check_batch
    from tests.golden.test_l1_path_repair import legacy, reader

    clock(store, FIRST)
    project, root, _, run = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    first = observation(store.db, root)
    clock(store, CHANGE)
    store.db.execute(
        "UPDATE source_roots SET git_metadata=? WHERE root_id=?",
        (
            dumps({"status": "not_git", "observation": "later"}),
            root,
        ),
    )

    def forbidden(*_, **__):
        raise AssertionError("历史元数据不能读取正文")

    monkeypatch.setattr(store.objects, "get", forbidden)
    old = reader(store, project, CHANGE, occurred_until=FIRST)
    latest = reader(store, project, LATER)
    assert native(old, run["run_id"])["root_registration"] == first
    assert native(latest, run["run_id"])["root_registration"]["basis"] == "metadata_change"
    for version in store.db.execute("SELECT * FROM artifact_versions WHERE root_id=?", (root,)):
        card = version_record(latest, dict(version))[0]
        assert card["root_registration"]["declaration"]["git_metadata"]["observation"] == "later"
    early = Reader(
        store, project, {"occurred_until": FUTURE, "known_until": "2029-01-01T00:00:00+00:00"}
    )
    assert early.root_identity(root)["state"] == "unknown"


def test_shared_root_history_survives_origin_clear_then_final_reference_clear(
    store, tmp_path, monkeypatch
):
    from rg.derive.path_checks import check_batch
    from rg.store.science_migration import migrate
    from tests.golden.test_l1_path_repair import legacy
    from tests.golden.test_science_migration import request

    clock(store, FIRST)
    origin, root, _, _ = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    target = store.project("公开登记接收", [])
    monkeypatch.setattr("rg.store.fragments.now", lambda: CHANGE)
    monkeypatch.setattr("rg.store.science_migration.now", lambda: CHANGE)
    migrate(store, request(store, target, first=1, last=4))
    assert roots.listing(store, target, known_until=FIRST, occurred_until=FUTURE)["total"] == 0
    assert (
        roots.listing(
            store, target, known_until="2030-01-01T08:00:00+08:00", occurred_until=FUTURE
        )["total"]
        == 0
    )
    shared = roots.listing(store, target, known_until=LATER, occurred_until=FUTURE)
    assert shared["roots"][0]["registration"]["declaration"]["project_id"] == origin
    saved = [
        tuple(row)
        for row in store.db.execute(
            "SELECT * FROM source_root_observations WHERE root_id=?", (root,)
        )
    ]
    data = store.root
    store.close()
    plan = preview(data, origin)
    assert plan["blockers"] == []
    execute(data, origin, str(uuid4()), plan["preview_sha256"])
    with closing(Store(data)) as remaining:
        assert [
            tuple(row)
            for row in remaining.db.execute(
                "SELECT * FROM source_root_observations WHERE root_id=?", (root,)
            )
        ] == saved
        assert (
            roots.listing(remaining, target, known_until=LATER, occurred_until=FUTURE)["total"] == 1
        )
    plan = preview(data, target)
    assert plan["blockers"] == []
    execute(data, target, str(uuid4()), plan["preview_sha256"])
    with closing(Store(data)) as remaining:
        assert (
            remaining.db.execute("SELECT count(*) FROM source_root_observations").fetchone()[0] == 0
        )
        assert remaining.db.execute("SELECT count(*) FROM source_roots").fetchone()[0] == 0
        assert remaining.db.execute("PRAGMA foreign_key_check").fetchall() == []
