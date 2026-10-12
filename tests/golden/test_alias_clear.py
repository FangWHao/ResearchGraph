from __future__ import annotations

import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from rg.ingest.scanner import scan_file
from rg.query.reader import Reader
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, now
from rg.store.fragments import assign
from rg.store.ownership import historical_source
from rg.store.science_migration import migrate
from tests.golden.test_fragment_assignments import intent
from tests.golden.test_fragment_dependencies import claim, entity
from tests.golden.test_ingestion import lines, record
from tests.golden.test_project_fragments import codex, user
from tests.golden.test_science_migration import request

PRIVATE = "SYNTHETIC_ALIAS_ERASE_20261012"


def scene(tmp_path, tool="claude"):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        a, b = store.project("公开别名原项目", []), store.project("公开别名目标", [])
        shared = "公开别名保留原话"
        header = codex("session_meta", {"id": "synthetic-alias-clear"})
        common = (
            [record(shared, uuid="synthetic-shared")]
            if tool == "claude"
            else [header, user(shared)]
        )
        private = record(PRIVATE, uuid="synthetic-private") if tool == "claude" else user(PRIVATE)
        original, mirror = tmp_path / "original.jsonl", tmp_path / "mirror.jsonl"
        lines(original, [*common, private])
        lines(mirror, common)
        scan_file(store, original, tool, a)
        scan_file(store, mirror, tool, a)
        rows = list(store.db.execute("SELECT * FROM raw_events WHERE file_instance_id=2"))
        before = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")]
        result = assign(
            store, intent(store, b, first=rows[0]["event_id"], last=rows[-1]["event_id"], source=2)
        )
        assert result["records_assigned"] == len(rows)
        assert [
            tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")
        ] == before
        mirror_event = rows[-1]["event_id"]
        assert rows[-1]["alias_of"] is not None
        assert (
            store.db.execute(
                "SELECT alias_of FROM effective_raw_events WHERE event_id=?", (mirror_event,)
            ).fetchone()[0]
            is None
        )
    return root, a, b, mirror_event, original, mirror


def erase(root, project):
    info = clear.preview(root, project)
    assert info["blockers"] == []
    return clear.execute(root, project, str(uuid4()), info["preview_sha256"])


@pytest.mark.parametrize("tool", ["claude", "codex"])
@pytest.mark.parametrize("original_first", [True, False])
def test_inactive_native_alias_clear_both_orders_preserves_other_raw_and_review(
    tmp_path, tool, original_first
):
    root, a, b, event, original, mirror = scene(tmp_path, tool)
    removed, retained = (a, b) if original_first else (b, a)
    files = [original.read_bytes(), mirror.read_bytes()]
    with closing(Store(root)) as store:
        target = entity(store, b)
        confirmed = claim(store, target, event)
        store.review(confirmed, "confirm", "human:公开验收", store.revision())
        candidate = claim(store, target, event, replaces=confirmed)
        kept = [
            tuple(r)
            for r in store.db.execute(
                "SELECT r.* FROM raw_events r JOIN effective_raw_events e USING(event_id) "
                "JOIN sessions s ON s.session_pk=e.session_pk "
                "WHERE s.project_id=? ORDER BY r.event_id",
                (retained,),
            )
        ]
        science = {
            t: [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in ("claims", "review_actions", "claim_evidence", "evidence_spans")
        }
        shared_sha = store.db.execute(
            "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
        ).fetchone()[0]
        raw = store.raw(event)
        at = now()
    assert erase(root, removed)["state"] == "complete"
    with closing(Store(root)) as store:
        assert [
            tuple(r) for r in store.db.execute("SELECT * FROM raw_events ORDER BY event_id")
        ] == kept
        assert store.objects.get(shared_sha) == raw
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        if original_first:
            assert PRIVATE not in "\n".join(store.db.iterdump())
            assert {
                t: [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                for t in science
            } == science
            assert (
                store.claim_state(confirmed) == "confirmed"
                and store.claim_state(candidate) == "candidate"
            )
            assert Reader(store, b, {"event_id": event}).evidence()["event_id"] == event
            view = historical_source(
                store.db,
                dict(
                    store.db.execute(
                        "SELECT * FROM effective_raw_events WHERE event_id=?", (event,)
                    ).fetchone()
                ),
                at,
            )
            assert view["alias_of"] is None and view["exclude_reason"] is None
            assert scan_file(store, mirror, tool, b) == {}
        destination = tmp_path / "after-backup"
        backup(store, destination)
    with closing(Store(destination)) as restored:
        assert [
            tuple(r) for r in restored.db.execute("SELECT * FROM raw_events ORDER BY event_id")
        ] == kept
        assert restored.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert erase(root, retained)["state"] == "complete"
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
        assert not store.objects.path(shared_sha).exists()
    assert [original.read_bytes(), mirror.read_bytes()] == files


@pytest.mark.parametrize("foreign_keys", [0, 1])
def test_migration34_transaction_preserves_rows_views_indexes_and_protection(
    tmp_path, monkeypatch, foreign_keys
):
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 33)
        from rg.store.clear_selection import TABLES

        legacy.setattr(
            "rg.store.clear_selection.TABLES",
            TABLES - {"project_assignment_actions", "automatic_assignments"},
        )
        legacy.setattr(
            "rg.store.fragment_dependency_contract.SCIENCE_SCHEMA_SHA256",
            "04234f1624949c022fdb4d6e6e9f8d3d159416e081869afef845bf4d9462914d",
        )
        root, a, b, event, *_ = scene(tmp_path)
        store = Store(root)
    try:
        store.db.execute("CREATE INDEX synthetic_alias_index ON raw_events(alias_of)")
        store.db.execute("PRAGMA foreign_keys=" + str(foreign_keys))
        before = list(store.db.iterdump())
        raw = store.raw(event)
        schema = [
            tuple(r)
            for r in store.db.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type IN ('view','trigger','index') "
                "AND sql IS NOT NULL ORDER BY name"
            )
        ]
        with monkeypatch.context() as damaged:
            damaged.setitem(migrations.MIGRATIONS, 34, (*migrations.MIGRATIONS[34], "INVALID SQL"))
            with pytest.raises(sqlite3.OperationalError):
                migrations.migrate(store.db)
        assert list(store.db.iterdump()) == before
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == foreign_keys
        migrations.migrate(store.db)
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == foreign_keys
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.raw(event) == raw
        actual = {
            tuple(r)
            for r in store.db.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type IN ('view','trigger','index') "
                "AND sql IS NOT NULL"
            )
        }
        assert set(schema) <= actual
        for sql in ("UPDATE raw_events SET alias_of=NULL", "DELETE FROM raw_events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store.db.execute(sql)
        assert clear.preview(root, a)["blockers"] == []
        before = list(store.db.iterdump())
        migrations.migrate(store.db)
        assert list(store.db.iterdump()) == before
    finally:
        store.close()


def test_deleted_original_id_cannot_be_reused_or_referenced_by_new_alias(tmp_path):
    root, a, b, event, *_ = scene(tmp_path)
    erase(root, a)
    with closing(Store(root)) as store:
        old = store.db.execute(
            "SELECT alias_of FROM raw_events WHERE event_id=?", (event,)
        ).fetchone()[0]
        assert old is not None
        template = dict(
            store.db.execute("SELECT * FROM raw_events WHERE event_id=?", (event,)).fetchone()
        )
        del template["event_id"]
        template.update(byte_start=10000, byte_end=10001, seq=10000)
        columns = ",".join(template)
        statement = f"INSERT INTO raw_events({columns}) VALUES ({','.join('?' for _ in template)})"
        with pytest.raises(sqlite3.IntegrityError, match="alias target is missing"):
            store.db.execute(statement, tuple(template.values()))
        template["alias_of"] = None
        new = store.db.execute(statement, tuple(template.values())).lastrowid
        assert new > event and new != old
        assert (
            store.db.execute(
                "SELECT alias_of FROM effective_raw_events WHERE event_id=?", (event,)
            ).fetchone()[0]
            is None
        )


@pytest.mark.parametrize("unverified", ["unknown", "receipt", "native_id", "excluded"])
def test_unverified_inactive_alias_remains_a_clear_blocker(tmp_path, unverified):
    root, a, b, event, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        # 仅构造损坏的合成旧库；普通生产入口的 L0 保护始终禁止这些改写。
        triggers = list(
            store.db.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='trigger' "
                "AND tbl_name IN ('raw_events','parser_records')"
            )
        )
        for name, _ in triggers:
            store.db.execute('DROP TRIGGER "' + name + '"')
        if unverified == "receipt":
            store.db.execute("DELETE FROM parser_records WHERE file_instance_id=2")
        else:
            column, value = {
                "unknown": ("kind", "unknown"),
                "native_id": ("native_id", "synthetic-conflict"),
                "excluded": ("exclude_reason", None),
            }[unverified]
            store.db.execute(f"UPDATE raw_events SET {column}=? WHERE event_id=?", (value, event))
            if unverified == "native_id":
                store.db.execute(
                    "UPDATE raw_events SET line_sha256='synthetic-conflict' WHERE event_id=?",
                    (event,),
                )
        for _, sql in triggers:
            store.db.execute(sql)
    info = clear.preview(root, a)
    assert "保留记录仍引用待清除记录：raw_events.alias_of" in info["blockers"]
    with pytest.raises(ConflictError, match="归属阻碍"):
        clear.execute(root, a, str(uuid4()), info["preview_sha256"])
    assert not (root / clear.ACTIVE).exists()


def test_retained_science_can_move_again_after_original_alias_target_was_erased(tmp_path):
    root, a, b, event, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        target = entity(store, b)
        candidate = claim(store, target, event)
    erase(root, a)
    with closing(Store(root)) as store:
        c = store.project("公开再次迁移", [])
        before = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
        data = request(store, c, first=event, last=event, source=2)
        result = migrate(store, data)
        assert result["migration_applied"] and result["model_calls"] == 0
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == before
        assert store.claim_state(candidate) == "candidate"
        assert [r["claim_id"] for r in Reader(store, c, {}).claims()] == [candidate]
        assert Reader(store, c, {"event_id": event}).evidence()["event_id"] == event
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_active_native_alias_between_projects_is_still_protected(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        a, b = store.project("公开原项目", []), store.project("公开保留项目", [])
        for project, session in ((a, "synthetic-original"), (b, "synthetic-mirror")):
            path = tmp_path / (session + ".jsonl")
            lines(path, [record("公开相同原话", uuid="synthetic-shared", sessionId=session)])
            scan_file(store, path, "claude", project)
        assert (
            store.db.execute(
                "SELECT alias_of FROM effective_raw_events WHERE event_id=2"
            ).fetchone()[0]
            == 1
        )
    for project in (a, b):
        info = clear.preview(root, project)
        assert any("raw_events.alias_of" in issue for issue in info["blockers"])
        with pytest.raises(ConflictError, match="归属阻碍"):
            clear.execute(root, project, str(uuid4()), info["preview_sha256"])


def test_inactive_alias_clear_resumes_after_filesystem_interruption(tmp_path, monkeypatch):
    root, a, b, event, *_ = scene(tmp_path)
    info, request_id = clear.preview(root, a), str(uuid4())

    def fail(*args):
        raise OSError("synthetic alias clear interruption")

    with monkeypatch.context() as broken:
        broken.setattr(clear, "_managed_path", fail)
        with pytest.raises(OSError, match="synthetic alias clear interruption"):
            clear.execute(root, a, request_id, info["preview_sha256"])
    assert clear.status(root, request_id)["state"] == "pending"
    assert clear.resume(root, request_id)["state"] == "complete"
    assert clear.resume(root, request_id)["state"] == "complete"
    with closing(Store(root)) as store:
        assert Reader(store, b, {"event_id": event}).evidence()["event_id"] == event
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration34_rejects_preexisting_broken_alias_without_erasing_violation(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 33)
        store = Store(tmp_path / "legacy")
        path = tmp_path / "public.jsonl"
        lines(path, [record("公开损坏旧库验收")])
        scan_file(store, path, "claude")
    try:
        store.db.execute("PRAGMA foreign_keys=OFF")
        trigger = store.db.execute(
            "SELECT sql FROM sqlite_schema WHERE name='raw_no_update'"
        ).fetchone()[0]
        store.db.execute("DROP TRIGGER raw_no_update")
        store.db.execute("UPDATE raw_events SET alias_of=10000")
        store.db.execute(trigger)
        before = list(store.db.iterdump())
        with pytest.raises(sqlite3.IntegrityError, match="原数据库外键校验失败"):
            migrations.migrate(store.db)
        assert list(store.db.iterdump()) == before
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is not None
    finally:
        store.close()
