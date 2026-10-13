from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api import view_state
from rg.api.server import LocalServer
from rg.api.views import NotFound
from rg.store import migrations
from rg.store.backup import backup as make_backup
from rg.store.clear import execute, preview
from rg.store.database import ConflictError, Store, dumps
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, append, original, review
from tests.test_migrations import legacy_store


def node(entity, scope=SCOPE):
    return (
        entity
        + "::"
        + (json.dumps(list(scope.items()), ensure_ascii=False) if scope else "unknown")
    )


def body(store, project, entity, claim, *, user="本机甲", scope=SCOPE):
    key = node(entity, scope)
    return {
        "project_id": project,
        "user": user,
        "expected_view_id": None,
        "view": {
            "format_version": 1,
            "reading": {
                "project_id": project,
                "revision": store.revision(),
                "occurred_until": T2,
                "known_until": T2,
            },
            "show_candidates": True,
            "positions": {key: {"x": 321.25, "y": -12}},
            "viewport": {"x": 17, "y": -44, "zoom": 0.6},
            "selected_versions": {key: claim},
            "process_group": None,
        },
    }


def setup(store, name="合成个人视图"):
    project = store.project(name, [])
    entity, claim = original(store, project, kind="finding")
    return project, entity, claim


def facts(store):
    return {
        table: [tuple(row) for row in store.db.execute("SELECT * FROM " + table)]
        for table in ("claims", "raw_events", "review_actions", "entities", "graph_clock")
    }


def test_saved_view_survives_reopen_and_readonly_without_changing_research(store, monkeypatch):
    project, entity, claim = setup(store)
    before = facts(store)
    monkeypatch.setattr(Store, "raw", lambda *a: pytest.fail("视图不读原文"))
    request = body(store, project, entity, claim)
    request_before = copy.deepcopy(request)
    saved = view_state.save(store, request)
    assert request == request_before and saved["view_id"] > 0
    assert saved["view"]["viewport"] == request["view"]["viewport"]
    assert facts(store) == before
    with closing(Store(store.root, readonly=True)) as readonly:
        read = view_state.read(readonly, {"project": project, "user": "本机甲"})
        assert read == saved and readonly.db.total_changes == 0
    newer = copy.deepcopy(request)
    newer["expected_view_id"] = saved["view_id"]
    newer["view"]["positions"][node(entity)]["x"] = 555
    updated = view_state.save(store, newer)
    assert updated["view_id"] > saved["view_id"] and facts(store) == before
    assert store.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 2
    with pytest.raises(ConflictError, match="其它页面"):
        view_state.save(store, newer)
    assert view_state.read(store, {"project": project, "user": "本机甲"}) == updated


def test_users_projects_and_sql_text_are_separate(store):
    p, e, c = setup(store)
    q, f, d = setup(store, "另一合成项目")
    injection = "本机' OR 1=1 --"
    saved = [
        view_state.save(store, body(store, project, entity, claim, user=user))
        for project, entity, claim, user in [
            (p, e, c, injection),
            (p, e, c, "乙"),
            (q, f, d, injection),
        ]
    ]
    assert len({s["view_id"] for s in saved}) == 3
    for result in saved:
        assert (
            view_state.read(store, {"project": result["project_id"], "user": result["user"]})
            == result
        )
    assert view_state.read(store, {"project": p, "user": "尚未保存"})["view"] is None
    with pytest.raises(NotFound):
        view_state.read(store, {"project": "不存在", "user": injection})


def test_actual_concurrent_first_saves_only_one_wins(store):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    barrier = threading.Barrier(2)
    results = []

    def writer():
        with closing(Store(store.root)) as connection:
            barrier.wait()
            try:
                results.append(view_state.save(connection, copy.deepcopy(request)))
            except ConflictError:
                results.append("conflict")

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(results) == 2 and results.count("conflict") == 1
    assert store.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 1


def test_read_uses_one_snapshot_while_another_writer_saves_and_reviews(store, monkeypatch):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    saved = view_state.save(store, request)
    latest = view_state._latest
    once = False

    def during(connection, project, user):
        nonlocal once
        if not once:
            once = True
            with closing(Store(store.root)) as writer:
                changed = copy.deepcopy(request)
                changed["expected_view_id"] = saved["view_id"]
                view_state.save(writer, changed)
                review(writer, c, "confirm", T3)
        return latest(connection, project, user)

    monkeypatch.setattr(view_state, "_latest", during)
    with closing(Store(store.root, readonly=True)) as reader:
        assert view_state.read(reader, {"project": p, "user": "本机甲"}) == saved
    current = view_state.read(store, {"project": p, "user": "本机甲"})
    assert current["view_id"] > saved["view_id"]
    assert current["current_revision"] > saved["current_revision"]
    assert current["view"]["reading"]["revision"] == saved["view"]["reading"]["revision"]


def test_stale_graph_is_not_saved_but_old_view_remains_available(store):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    saved = view_state.save(store, request)
    review(store, c, "confirm", T3)
    request["expected_view_id"] = saved["view_id"]
    with pytest.raises(ConflictError, match="研究图已变化"):
        view_state.save(store, request)
    loaded = view_state.read(store, {"project": p, "user": "本机甲"})
    assert loaded["view"] == saved["view"] and loaded["view_id"] == saved["view_id"]
    assert loaded["current_revision"] > loaded["view"]["reading"]["revision"]


@pytest.mark.parametrize(
    "failure",
    [
        "foreign",
        "future",
        "late",
        "dismissed",
        "replaced",
        "wrong_scope",
        "wrong_version",
        "alias",
        "duplicate_scope",
    ],
)
def test_invisible_or_ambiguous_node_and_version_never_saved(store, failure):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    if failure == "foreign":
        q, f, d = setup(store, "其它项目")
        request["view"]["positions"][node(f)] = {"x": 0, "y": 0}
    elif failure in {"future", "late", "wrong_scope", "wrong_version"}:
        d = append(
            store,
            e,
            "entity_version",
            {"label": "不可见显示版本"},
            occurred=T3 if failure == "future" else T1,
            recorded=T3 if failure == "late" else T1,
            scope={"data": "v2"} if failure == "wrong_scope" else SCOPE,
        )
        request["view"]["selected_versions"][node(e)] = (
            d if failure != "wrong_version" else c + 1000
        )
    elif failure == "dismissed":
        review(store, c, "dismiss", T1)
    elif failure == "replaced":
        append(store, e, "entity_version", {"label": "新版本"}, replaces=c)
    elif failure == "alias":
        request["view"]["positions"][node(e, dict(reversed(list(SCOPE.items()))))] = {
            "x": 0,
            "y": 0,
        }
    else:
        request["view"]["positions"] = {e + '::[["data","v1"],["data","v1"]]': {"x": 0, "y": 0}}
    request["view"]["reading"]["revision"] = store.revision()
    with pytest.raises(ValueError):
        view_state.save(store, request)
    assert store.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 0


def test_chinese_scope_order_unknown_scope_and_active_versions(store):
    p = store.project("中文范围合成", [])
    scope = {"步骤": "甲", "data": "v1"}
    e, c = original(store, p, scope=scope)
    request = body(store, p, e, c, scope=dict(reversed(list(scope.items()))))
    request["view"]["reading"]["known_until"] = "2026-01-02T08:00:00+08:00"
    saved = view_state.save(store, request)
    assert saved["view"]["reading"]["known_until"] == "2026-01-02T00:00:00+00:00"
    unknown, u = original(store, p, scope=None)
    request = body(store, p, unknown, u, user="未知范围", scope=None)
    assert view_state.save(store, request)["view"]["positions"] == request["view"]["positions"]


@pytest.mark.parametrize(
    "failure",
    [
        "bool_coord",
        "huge_int",
        "nan",
        "infinite",
        "zoom",
        "naive",
        "wrong_project",
        "extra",
        "bool_revision",
        "group_hidden",
        "group_one",
        "group_duplicate",
    ],
)
def test_invalid_parameters_rejected_atomically(store, failure):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    view = request["view"]
    if failure in {"bool_coord", "huge_int", "nan", "infinite"}:
        view["positions"][node(e)]["x"] = {
            "bool_coord": True,
            "huge_int": 10**400,
            "nan": float("nan"),
            "infinite": float("inf"),
        }[failure]
    elif failure == "zoom":
        view["viewport"]["zoom"] = 2
    elif failure == "naive":
        view["reading"]["known_until"] = "2026-01-02T00:00:00"
    elif failure == "wrong_project":
        view["reading"]["project_id"] = "其它项目"
    elif failure == "extra":
        view["facts"] = {"adopted": True}
    elif failure == "bool_revision":
        view["reading"]["revision"] = True
    else:
        f, _ = original(store, p, kind="finding")
        view["reading"]["revision"] = store.revision()
        view["process_group"] = {"name": "过程", "members": [node(e), node(f)]}
        if failure == "group_hidden":
            view["show_candidates"] = False
        if failure == "group_one":
            view["process_group"]["members"] = [node(e)]
        if failure == "group_duplicate":
            view["process_group"]["members"] = [node(e), node(e)]
    before = facts(store)
    with pytest.raises(ValueError):
        view_state.save(store, request)
    assert facts(store) == before
    assert store.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 0


def test_oversize_unavailable_legacy_and_transaction_rollback(store, monkeypatch):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    too_big = copy.deepcopy(request)
    too_big["view"]["positions"] = {"合成超限" * 16000: {"x": 0, "y": 0}}
    with pytest.raises(ValueError, match="65536"):
        view_state.save(store, too_big)
    store.db.execute(
        "INSERT INTO view_states(user,project_id,payload,graph_revision,updated_at) "
        "VALUES (?,?,?,?,?)",
        ("本机甲", p, "{坏JSON", 0, T1),
    )
    old = view_state.read(store, {"project": p, "user": "本机甲"})
    assert old["view"] is None and old["unavailable_reason"] and old["view_id"] > 0
    request["expected_view_id"] = old["view_id"]
    valid = view_state.save(store, request)
    assert valid["view"] and valid["unavailable_reason"] is None
    request["expected_view_id"] = valid["view_id"]
    monkeypatch.setattr(
        view_state, "_result", lambda *a: (_ for _ in ()).throw(RuntimeError("合成中断"))
    )
    with pytest.raises(RuntimeError):
        view_state.save(store, request)
    assert store.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 2


def test_backup_and_whole_project_clear_cover_all_personal_views(store, tmp_path):
    p, e, c = setup(store)
    q, f, d = setup(store, "保留合成项目")
    saved = view_state.save(store, body(store, p, e, c))
    kept = view_state.save(store, body(store, q, f, d))
    backup = tmp_path / "视图备份"
    make_backup(store, backup)
    with closing(Store(backup, readonly=True)) as restored:
        assert view_state.read(restored, {"project": p, "user": "本机甲"}) == saved
    root = store.root
    store.close()
    plan = preview(root, p)
    execute(root, p, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root, readonly=True)) as cleared:
        assert (
            cleared.db.execute(
                "SELECT count(*) FROM view_states WHERE project_id=?", (p,)
            ).fetchone()[0]
            == 0
        )
        after = view_state.read(cleared, {"project": q, "user": "本机甲"})
        assert after["view"] == kept["view"] and after["view_id"] == kept["view_id"]
        assert after["current_revision"] > kept["current_revision"]
    with closing(Store(root)) as reopened:
        current = body(reopened, q, f, d)
        current["expected_view_id"] = kept["view_id"]
        next_view = view_state.save(reopened, current)
        assert next_view["view_id"] > kept["view_id"]
        reopened.db.execute("DELETE FROM view_states")
        new_view = view_state.save(reopened, body(reopened, q, f, d))
        assert new_view["view_id"] > next_view["view_id"]


def test_legacy_view_upgrade_is_atomic_preserves_identity_and_never_reads_raw(
    tmp_path, monkeypatch
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=23)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        db.execute(
            "INSERT INTO view_states(rowid,user,project_id,payload,graph_revision,updated_at) "
            "VALUES (30,?,?,?,?,?)",
            ("旧身份", "旧项目", "旧显示正文", 7, T1),
        )
        db.commit()
    with pytest.raises(ValueError, match="升级"):
        Store(root, readonly=True)
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 24, (*migrations.MIGRATIONS[24], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 23
        assert db.execute("SELECT rowid,* FROM view_states").fetchone() == (
            30,
            "旧身份",
            "旧项目",
            "旧显示正文",
            7,
            T1,
        )
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='view_states_legacy'"
        ).fetchone()
    with monkeypatch.context() as patch:
        patch.setattr(Store, "raw", lambda *a: pytest.fail("升级不得读取原文"))
        with closing(Store(root)) as upgraded:
            assert tuple(upgraded.db.execute("SELECT * FROM view_states").fetchone()) == (
                30,
                "旧身份",
                "旧项目",
                "旧显示正文",
                7,
                T1,
            )
            upgraded.db.execute("VACUUM")
            assert upgraded.db.execute("SELECT view_id FROM view_states").fetchone()[0] == 30
    with closing(Store(root)) as repeated:
        assert repeated.raw(1) == raw
        assert repeated.db.execute("SELECT count(*) FROM view_states").fetchone()[0] == 1


def test_real_token_http_and_duplicate_json_rejection(store, tmp_path):
    p, e, c = setup(store)
    request = body(store, p, e, c)
    (tmp_path / "index.html").write_text("合成本机页面")
    server = LocalServer(store.root, tmp_path, 0, token="synthetic-personal-view-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    headers = {"Authorization": "Bearer synthetic-personal-view-token"}
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert (
                client.get("/api/view-state", params={"project": p, "user": "本机甲"}).status_code
                == 401
            )
            response = client.post("/api/view-state", json=request, headers=headers)
            assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
            assert client.post("/api/view-state", json=request, headers=headers).status_code == 409
            got = client.get(
                "/api/view-state", params={"project": p, "user": "本机甲"}, headers=headers
            )
            assert got.json() == response.json()
            assert (
                client.get(
                    "/api/view-state",
                    params={"project": p, "user": "本机甲"},
                    headers=headers | {"Host": "evil.invalid"},
                ).status_code
                == 403
            )
            duplicate = dumps(request).replace(
                '"format_version":1', '"format_version":1,"format_version":1'
            )
            assert (
                client.post(
                    "/api/view-state",
                    content=duplicate,
                    headers=headers | {"Content-Type": "application/json"},
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/view-state",
                    params={"project": p, "user": "本机甲", "extra": "1"},
                    headers=headers,
                ).status_code
                == 400
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
