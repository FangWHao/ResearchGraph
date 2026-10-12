from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api import views
from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import query
from rg.store import clear, migrations, roots
from rg.store.assignment_policy import configure, listing
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, now
from rg.store.fragments import assign
from tests.golden.test_fragment_assignments import intent
from tests.golden.test_ingestion import lines, record
from tests.golden.test_project_fragments import codex, user


def enable(store, project, enabled=True):
    page = listing(store, project)
    return configure(
        store,
        {
            "project_id": project,
            "enabled": enabled,
            "actor": "human:公开验收",
            "expected_revision": page["revision"],
            "expected_scope_key": page["scope_key"],
        },
    )


def owners(store):
    return [
        row[0]
        for row in store.db.execute(
            "SELECT s.project_id FROM effective_raw_events e JOIN sessions s USING(session_pk) "
            "ORDER BY e.event_id"
        )
    ]


def scene(store, tmp_path):
    a, b = tmp_path / "甲", tmp_path / "乙"
    return store.project("甲", [a]), store.project("乙", [b]), a, b


def test_once_enabled_routes_future_context_switch_preserves_raw(store, tmp_path):
    first, second, a, b = scene(store, tmp_path)
    enable(store, first)
    enable(store, second)
    path = tmp_path / "public.jsonl"
    records = [
        codex("session_meta", {"id": "public", "cwd": str(a)}),
        user("公开甲内容"),
        codex("turn_context", {"cwd": str(b)}),
        user("公开乙内容"),
    ]
    lines(path, records)
    scan_file(store, path, "codex")
    assert owners(store) == [first, first, second, second]
    assert {row[0] for row in store.db.execute("SELECT session_pk FROM raw_events")} == {1}
    assert [json.loads(store.raw(i)) for i in range(1, 5)] == records
    assert views.search(store, {"q": "公开乙内容", "project": first})["total"] == 0
    assert views.search(store, {"q": "公开乙内容", "project": second})["total"] == 1
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    page = query(store, {"session": "1", "source": "1"})
    assert all(r["assignment"]["basis"] == "path_rule" for r in page["records"])
    before = list(store.db.execute("SELECT * FROM automatic_assignments"))
    assert scan_file(store, path, "codex") == {}
    assert list(store.db.execute("SELECT * FROM automatic_assignments")) == before


def test_default_off_no_backfill_and_disable_keeps_history(store, tmp_path):
    first, _, a, _ = scene(store, tmp_path)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)}), user("旧记录")])
    scan_file(store, path, "codex")
    old = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    assert owners(store) == [None, None]
    assert enable(store, first)["policy"]["after_event_id"] == 2
    assert scan_file(store, path, "codex") == {}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(user("未来新记录"), ensure_ascii=False) + "\n")
    scan_file(store, path, "codex")
    assert owners(store) == [None, None, first]
    enable(store, first, False)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(user("停止后新记录"), ensure_ascii=False) + "\n")
    scan_file(store, path, "codex")
    assert owners(store) == [None, None, first, None]
    assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events WHERE event_id<=2")] == old


def test_explicit_manual_project_beats_enabled_rule(store, tmp_path):
    first, second, _, b = scene(store, tmp_path)
    enable(store, second)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(b)}), user("人工明确归属")])
    scan_file(store, path, "codex", first)
    assert owners(store) == [first, first]
    assert store.db.execute("SELECT count(*) FROM automatic_assignments").fetchone()[0] == 0


@pytest.mark.parametrize(
    "context,reason",
    [
        (codex("turn_context", {"cwd": "../相对路径"}), "invalid_cwd"),
        ({"type": "future_record", "cwd": "/不能猜"}, "unknown_record"),
        (codex("turn_context", {}), "missing_cwd"),
        (codex("response_item", {"type": "future_payload"}), "unknown_record"),
    ],
)
def test_invalid_unknown_context_blocks_inheritance(store, tmp_path, context, reason):
    first, _, a, _ = scene(store, tmp_path)
    enable(store, first)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)}), context, user("缺口后")])
    scan_file(store, path, "codex")
    assert owners(store) == [first, None, None]
    assert [
        r[0]
        for r in store.db.execute(
            "SELECT reason FROM automatic_assignments WHERE first_event_id>1 "
            "ORDER BY first_event_id"
        )
    ] == [reason, reason]
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 3


def test_conflicting_roots_and_disabled_project_do_not_guess(store, tmp_path):
    outer = tmp_path / "工作区"
    first = store.project("外层", [outer])
    second = store.project("内层", [outer / "内层"])
    enable(store, first)
    assert listing(store, first)["roots"][0]["reason"] == "overlapping_projects"
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(outer / "内层")})])
    scan_file(store, path, "codex")
    assert owners(store) == [None]
    assert (
        store.db.execute("SELECT reason FROM automatic_assignments").fetchone()[0]
        == "ambiguous_path"
    )
    other = tmp_path / "仅乙"
    roots.register(store, second, other, "data")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(codex("turn_context", {"cwd": str(other)})) + "\n")
    scan_file(store, path, "codex")
    assert owners(store) == [None, None]
    assert (
        store.db.execute(
            "SELECT reason FROM automatic_assignments WHERE first_event_id=2"
        ).fetchone()[0]
        == "policy_disabled"
    )


def test_later_registered_directory_enters_existing_permission(store, tmp_path):
    first, _, _, _ = scene(store, tmp_path)
    enable(store, first)
    new = tmp_path / "新数据"
    roots.register(store, first, new, "data")
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(new)})])
    scan_file(store, path, "codex")
    assert owners(store) == [first]
    assert listing(store, first)["future_roots"] is True


def test_policy_stale_revision_and_root_digest_never_write(store, tmp_path):
    first, _, _, _ = scene(store, tmp_path)
    page = listing(store, first)
    body = {
        "project_id": first,
        "enabled": True,
        "actor": "human:公开",
        "expected_revision": page["revision"],
        "expected_scope_key": page["scope_key"],
    }
    # 旧 project() 接口不增加图修订；完整根摘要仍阻止保存。
    store.project("新项目", [tmp_path / "新根"])
    with pytest.raises(ConflictError):
        configure(store, body)
    assert not listing(store, first)["enabled"]
    enable(store, first)
    with pytest.raises(ConflictError):
        configure(store, body)


def test_complete_pagination_requires_same_project_registry(store, tmp_path):
    first, _, _, _ = scene(store, tmp_path)
    for n in range(3):
        roots.register(store, first, tmp_path / str(n), "data")
    page = listing(store, first, limit=2)
    second = listing(store, first, 2, page["next_offset"], page["revision"], page["scope_key"])
    assert len({r["root_id"] for r in page["roots"] + second["roots"]}) == 4
    assert second["next_offset"] is None
    with pytest.raises(ValueError):
        listing(store, first, 2, 2)
    enable(store, first)
    with pytest.raises(ConflictError):
        listing(store, first, 2, 2, page["revision"], page["scope_key"])


def test_scan_failure_rolls_back_receipt_ownership_and_cursor(store, tmp_path):
    first, _, a, _ = scene(store, tmp_path)
    enable(store, first)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)})])

    def fail():
        raise RuntimeError("公开故障")

    with pytest.raises(RuntimeError):
        scan_file(store, path, "codex", fault=fail)
    assert owners(store) == []
    assert store.db.execute("SELECT count(*) FROM automatic_assignments").fetchone()[0] == 0
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
    scan_file(store, path, "codex")
    assert owners(store) == [first]


def test_claude_known_native_directory_and_unknown_are_separate(store, tmp_path):
    first, _, a, _ = scene(store, tmp_path)
    enable(store, first)
    path = tmp_path / "public.jsonl"
    lines(
        path,
        [
            record("公开原话", cwd=str(a)),
            {"type": "future_type", "sessionId": "s", "cwd": str(a)},
        ],
    )
    scan_file(store, path, "claude")
    assert owners(store) == [first, None]


def test_append_only_and_backed_up_policy_receipts(store, tmp_path):
    first, _, a, _ = scene(store, tmp_path)
    enable(store, first)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)})])
    scan_file(store, path, "codex")
    for table in ("project_assignment_actions", "automatic_assignments"):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(f"UPDATE {table} SET recorded_at='篡改'")
    directory = tmp_path / "备份"
    backup(store, directory)
    with closing(Store(directory, readonly=True)) as saved:
        assert owners(saved) == [first]
        assert listing(saved, first)["enabled"]


def test_human_override_preserves_auto_receipt_then_clear_old_policy(store, tmp_path):
    first, second, a, _ = scene(store, tmp_path)
    enable(store, first)
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)}), user("独立迁移原文")])
    scan_file(store, path, "codex")
    receipt = [tuple(r) for r in store.db.execute("SELECT * FROM automatic_assignments")]
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    assign(store, intent(store, second, first=1, last=2))
    assert owners(store) == [second, second]
    page = query(store, {"session": "1", "source": "1"})
    assert all(r["assignment"]["basis"] == "manual" for r in page["records"])
    plan = clear.preview(store.root, first)
    assert plan["blockers"] == []
    store.close()
    clear.execute(store.root, first, str(uuid4()), plan["preview_sha256"])
    with closing(Store(store.root)) as saved:
        assert owners(saved) == [second, second]
        assert [tuple(r) for r in saved.db.execute("SELECT * FROM raw_events")] == raw
        assert [
            tuple(r) for r in saved.db.execute("SELECT * FROM automatic_assignments")
        ] == receipt
        assert saved.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_clear_removes_selected_receipts_and_policy_not_other_project(store, tmp_path):
    first, second, a, b = scene(store, tmp_path)
    enable(store, first)
    enable(store, second)
    path = tmp_path / "public.jsonl"
    lines(
        path,
        [
            codex("session_meta", {"id": "public", "cwd": str(a)}),
            codex("turn_context", {"cwd": str(b)}),
            user("保留乙"),
        ],
    )
    scan_file(store, path, "codex")
    plan = clear.preview(store.root, first)
    assert plan["blockers"] == []
    store.close()
    clear.execute(store.root, first, str(uuid4()), plan["preview_sha256"])
    with closing(Store(store.root)) as saved:
        assert owners(saved) == [second, second]
        assert saved.db.execute("SELECT count(*) FROM automatic_assignments").fetchone()[0] == 2
        assert listing(saved, second)["enabled"]
        assert saved.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_http_policy_strict_duplicate_keys_and_auth(store, tmp_path):
    first, _, _, _ = scene(store, tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("公开页面", encoding="utf-8")
    server = LocalServer(store.root, web, port=0, token="public-test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(
            base_url=url, headers={"Authorization": "Bearer public-test-token"}, trust_env=False
        ) as client:
            page = client.get("/api/project-assignment", params={"project": first}).json()
            body = {
                "project_id": first,
                "enabled": True,
                "actor": "human:公开",
                "expected_revision": page["revision"],
                "expected_scope_key": page["scope_key"],
            }
            response = client.post("/api/project-assignment/configure", json=body)
            assert response.status_code == 200 and response.json()["enabled"]
            assert client.post("/api/project-assignment/configure", json=body).status_code == 409
            assert (
                client.post(
                    "/api/project-assignment/configure",
                    content='{"enabled":true,"enabled":false}',
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/project-assignment",
                    params={"project": first},
                    headers={"Authorization": "Bearer wrong"},
                ).status_code
                == 401
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_tool_result_follows_unique_request_across_directory_switch(store, tmp_path):
    first, second, a, b = scene(store, tmp_path)
    enable(store, first)
    enable(store, second)
    path = tmp_path / "public.jsonl"
    lines(
        path,
        [
            codex("session_meta", {"id": "public", "cwd": str(a)}),
            codex(
                "response_item",
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "公开请求",
                    "arguments": '{"cmd":"公开命令资料"}',
                },
            ),
            codex("turn_context", {"cwd": str(b)}),
            codex(
                "response_item",
                {"type": "function_call_output", "call_id": "公开请求", "output": "公开已结束结果"},
            ),
        ],
    )
    scan_file(store, path, "codex")
    assert owners(store) == [first, first, second, first]
    event = store.db.execute("SELECT tool_name FROM raw_events WHERE event_id=4").fetchone()
    assert event[0] == "exec_command"
    assert (
        store.db.execute(
            "SELECT reason FROM automatic_assignments WHERE first_event_id=4"
        ).fetchone()[0]
        == "call_owner"
    )


def test_reused_call_identity_never_chooses_last_project(store, tmp_path):
    first, second, a, b = scene(store, tmp_path)
    enable(store, first)
    enable(store, second)
    path = tmp_path / "public.jsonl"
    call = codex(
        "response_item",
        {"type": "function_call", "name": "exec_command", "call_id": "重复请求", "arguments": "{}"},
    )
    lines(
        path,
        [
            codex("session_meta", {"id": "public", "cwd": str(a)}),
            call,
            codex("turn_context", {"cwd": str(b)}),
            call,
            codex(
                "response_item",
                {"type": "function_call_output", "call_id": "重复请求", "output": "不能猜"},
            ),
        ],
    )
    scan_file(store, path, "codex")
    assert owners(store)[-1] is None
    assert (
        store.db.execute(
            "SELECT reason FROM automatic_assignments WHERE first_event_id=5"
        ).fetchone()[0]
        == "ambiguous_call_owner"
    )


def test_privacy_gap_requires_new_native_directory_before_auto_assignment(store, tmp_path):
    first, second, a, b = scene(store, tmp_path)
    enable(store, first)
    enable(store, second)
    private = user("公开隐私清除标识-20261012")
    old = tmp_path / "old.jsonl"
    lines(old, [codex("session_meta", {"id": "old", "cwd": str(a)}), private])
    scan_file(store, old, "codex")
    plan = clear.preview(store.root, first)
    assert not plan["blockers"]
    store.close()
    clear.execute(store.root, first, str(uuid4()), plan["preview_sha256"])
    new = tmp_path / "new.jsonl"
    lines(
        new,
        [
            codex("session_meta", {"id": "new", "cwd": str(b)}),
            private,
            user("缺口后不可继承"),
            codex("turn_context", {"cwd": str(b)}),
            user("新声明后"),
        ],
    )
    with closing(Store(store.root)) as saved:
        assert scan_file(saved, new, "codex")["privacy_skipped_records"] == 1
        assert owners(saved) == [second, None, second, second]
        assert (
            saved.db.execute(
                "SELECT reason FROM automatic_assignments WHERE first_event_id=("
                "SELECT min(event_id) FROM raw_events WHERE kind='user_msg')"
            ).fetchone()[0]
            == "privacy_gap"
        )


def test_borrowed_science_root_is_diagnostic_not_new_permission(store, tmp_path):
    first, second, a, _ = scene(store, tmp_path)
    root = store.db.execute("SELECT root_id FROM source_roots WHERE project_id=?", (first,))
    root_id = root.fetchone()[0]
    snapshot = store.db.execute(
        "INSERT INTO workspace_snapshots(project_id,root_id,taken_at) VALUES (?,?,?)",
        (first, root_id, now()),
    ).lastrowid
    migration = store.db.execute(
        "INSERT INTO science_migrations(request_id,intent_sha256,plan_sha256,project_id,actor,"
        "response_json,recorded_at) VALUES (?,?,?,?,?,?,?)",
        (str(uuid4()), "0" * 64, "1" * 64, second, "human:公开", "{}", now()),
    ).lastrowid
    store.db.execute(
        "INSERT INTO science_ownership(migration_id,table_name,record_rowid,project_id,"
        "recorded_at) VALUES (?,?,?,?,?)",
        (migration, "workspace_snapshots", snapshot, second, now()),
    )
    enable(store, second)
    borrowed = next(r for r in listing(store, second)["roots"] if r["root_id"] == root_id)
    assert borrowed["reason"] == "borrowed_membership" and not borrowed["applicable"]
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)})])
    scan_file(store, path, "codex")
    assert owners(store) == [None]


def test_global_registry_limit_does_not_match_truncated_roots(store, tmp_path):
    first, _, a, _ = scene(store, tmp_path)
    with store.transaction() as db:
        db.executemany(
            "INSERT INTO source_roots(root_id,project_id,host_id,path,kind) VALUES (?,?,?,?,?)",
            [
                (str(uuid4()), first, "local", str(tmp_path / f"公开-{n}"), "data")
                for n in range(2001)
            ],
        )
    enable(store, first)
    page = listing(store, first)
    assert page["total"] == 2002 and page["next_offset"] == 100
    assert all(r["reason"] == "root_registry_limit" for r in page["roots"])
    path = tmp_path / "public.jsonl"
    lines(path, [codex("session_meta", {"id": "public", "cwd": str(a)})])
    scan_file(store, path, "codex")
    assert owners(store) == [None]


@pytest.mark.parametrize("foreign_keys", [0, 1])
def test_migration35_rollback_then_preserve_original_records(tmp_path, monkeypatch, foreign_keys):
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 34)
        store = Store(tmp_path / "legacy")
        path = tmp_path / "public.jsonl"
        lines(path, [record("保留旧原文")])
        scan_file(store, path, "claude")
    try:
        store.db.execute("PRAGMA foreign_keys=" + str(foreign_keys))
        before = list(store.db.iterdump())
        raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
        with monkeypatch.context() as failure:
            failure.setitem(migrations.MIGRATIONS, 35, (*migrations.MIGRATIONS[35], "INVALID SQL"))
            with pytest.raises(sqlite3.OperationalError):
                migrations.migrate(store.db)
        assert list(store.db.iterdump()) == before
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 34
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == foreign_keys
        migrations.migrate(store.db)
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 35
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == raw
        assert json.loads(store.raw(1))["message"]["content"] == "保留旧原文"
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_partial_legacy_context_backfill_cannot_skip_unobserved_boundary(tmp_path, monkeypatch):
    root, path, workspace = tmp_path / "legacy", tmp_path / "public.jsonl", tmp_path / "工作区"
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 29)
        with closing(Store(root)) as store:
            project = store.project("旧库公开项目", [workspace])
            lines(
                path,
                [
                    codex("session_meta", {"id": "public", "cwd": str(workspace)}),
                    *[user(f"旧原文-{n}") for n in range(40)],
                ],
            )
            scan_file(store, path, "codex")
    with closing(Store(root)) as store:
        enable(store, project)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(user("目录尚未完整观测的未来记录")) + "\n")
        scan_file(store, path, "codex")
        assert (
            store.db.execute(
                "SELECT count(*) FROM project_context_observations WHERE event_id<=41"
            ).fetchone()[0]
            == 32
        )
        assert owners(store) == [None] * 42
        assert (
            store.db.execute(
                "SELECT reason FROM automatic_assignments WHERE first_event_id=42"
            ).fetchone()[0]
            == "metadata_not_observed"
        )
        receipt = tuple(
            store.db.execute(
                "SELECT * FROM automatic_assignments WHERE first_event_id=42"
            ).fetchone()
        )
        assert scan_file(store, path, "codex") == {}
        assert owners(store) == [None] * 42
        assert (
            tuple(
                store.db.execute(
                    "SELECT * FROM automatic_assignments WHERE first_event_id=42"
                ).fetchone()
            )
            == receipt
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(user("观测完整后新记录")) + "\n")
        scan_file(store, path, "codex")
        assert owners(store) == [None] * 42 + [project]
