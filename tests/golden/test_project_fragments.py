from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest
import zstandard

from rg.api.server import LocalServer
from rg.ingest import project_context as contexts
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import _hint, query
from rg.snapshot.git import Git
from rg.store import clear, migrations, roots
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from rg.store.objects import ObjectStore
from tests.golden.test_ingestion import lines, record
from tests.test_migrations import legacy_store


def codex(kind: str, payload: dict):
    return {"type": kind, "payload": payload}


def user(text: str):
    return codex(
        "response_item",
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    )


def scene(store, tmp_path):
    a, b = tmp_path / "甲", tmp_path / "乙"
    first, second = store.project("甲项目", [a]), store.project("乙项目", [b])
    path = tmp_path / "source.jsonl"
    lines(
        path,
        [
            codex("session_meta", {"id": "public-thread", "cwd": str(a)}),
            user("公开甲内容"),
            codex("turn_context", {"cwd": str(b)}),
            user("公开乙内容"),
        ],
    )
    scan_file(store, path, "codex", first)
    return first, second, path, a, b


def view(store, **extra):
    return query(store, {"session": "1", "source": "1", **extra})


def continuation(first, **extra):
    return {
        "after_event": str(first["next_after_event"]),
        "snapshot": str(first["snapshot"]),
        "observation_snapshot": str(first["observation_snapshot"]),
        "expected_scope_key": first["scope_key"],
        **extra,
    }


def test_context_switch_is_only_a_hint_and_preserves_all_history(store, tmp_path, monkeypatch):
    first, second, _, a, b = scene(store, tmp_path)
    before = list(store.db.iterdump())
    original = [store.raw(i) for i in range(1, 5)]
    monkeypatch.setattr(ObjectStore, "get", lambda *a: pytest.fail("只读查询不得回读对象"))
    monkeypatch.setattr(Git, "run", lambda *a, **k: pytest.fail("只读查询不得探测Git"))
    data = view(store, project=first)
    assert data["assignments_applied"] is False and data["current_session_project"] == first
    assert data["observations_complete"] and data["records_observed"] == 4
    assert [r["context"]["cwd"] for r in data["records"]] == [str(a), str(a), str(b), str(b)]
    assert [r["project_hint"]["projects"] for r in data["records"]] == [
        [first],
        [first],
        [second],
        [second],
    ]
    assert all(r["project_hint"]["state"] == "candidate_path" for r in data["records"])
    assert [r["context"]["origin_event_id"] for r in data["records"]] == [1, 1, 3, 3]
    assert list(store.db.iterdump()) == before
    monkeypatch.undo()
    # 已冻结原件仍在对象库；项目与模型／科研状态未被线索改写。
    assert [store.raw(i) for i in range(1, 5)] == original
    assert store.db.execute("SELECT project_id FROM sessions").fetchone()[0] == first
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


@pytest.mark.parametrize(
    "cwd",
    [
        None,
        1,
        True,
        [],
        {},
        "",
        "relative/path",
        "/a/../b",
        "C:relative",
        "/bad\0path",
        "/bad\ud800path",
        "/" + "a" * 17000,
    ],
)
def test_invalid_directory_is_not_retained_or_confirmed(cwd):
    value = contexts.declaration("codex", codex("turn_context", {"cwd": cwd}), "parsed")
    assert value["state"] == "invalid" and value["cwd"] is None
    assert value["reason"] == "invalid_cwd"


def test_invalid_and_unknown_records_stop_inheritance(store, tmp_path):
    project, _, path, a, b = scene(store, tmp_path)
    lines(
        path,
        [
            codex("session_meta", {"id": "public-thread", "cwd": str(a)}),
            codex("turn_context", {"cwd": "relative"}),
            user("不继承目录"),
            codex("turn_context", {"cwd": str(b)}),
            codex("future_context", {"cwd": str(a)}),
            user("未知记录之后"),
            codex("turn_context", {}),
            user("缺字段之后"),
        ],
    )
    scan_file(store, path, "codex", project)
    source = store.db.execute("SELECT max(file_instance_id) FROM source_files").fetchone()[0]
    data = query(store, {"session": "1", "source": str(source)})
    states = [r["context"]["state"] for r in data["records"]]
    assert states == [
        "declared",
        "invalid",
        "invalid",
        "declared",
        "unavailable",
        "unavailable",
        "invalid",
        "invalid",
    ]
    assert all(not data["records"][i]["project_hint"]["projects"] for i in (1, 2, 4, 5, 6, 7))


def test_nested_replay_arguments_and_rg_context_cannot_change_project_hint(store, tmp_path):
    a, b = tmp_path / "one", tmp_path / "two"
    project = store.project("公开项目", [a])
    other = store.project("另一个公开项目", [b])
    path = tmp_path / "public.jsonl"
    lines(
        path,
        [
            codex("session_meta", {"id": "native", "cwd": str(a)}),
            codex("compacted", {"replacement_history": [codex("turn_context", {"cwd": str(b)})]}),
            codex(
                "response_item",
                {
                    "type": "function_call",
                    "name": "research.context",
                    "call_id": "c",
                    "arguments": json.dumps({"cwd": str(b)}),
                },
            ),
            user('<rg-context v="1">cwd=' + str(b) + "</rg-context>用户自己的公开文字"),
        ],
    )
    scan_file(store, path, "codex")
    data = view(store)
    assert all(r["project_hint"]["projects"] == [project] for r in data["records"])
    assert all(other not in r["project_hint"]["projects"] for r in data["records"])
    assert store.db.execute("SELECT project_id FROM sessions").fetchone()[0] is None


def test_source_files_in_same_session_never_share_context(store, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    project = store.project("合成目录", [tmp_path / "work"])
    first, second = a / "shared.jsonl", b / "shared.jsonl"
    lines(first, [record("文件一", cwd=str(tmp_path / "work"))])
    lines(second, [record("文件二", uuid="two")])
    scan_file(store, first, "claude")
    scan_file(store, second, "claude")
    assert view(store)["records"][0]["project_hint"]["projects"] == [project]
    data = query(store, {"session": "1", "source": "2"})
    assert data["records"][0]["context"]["state"] == "unobserved"
    assert data["records"][0]["project_hint"]["projects"] == []


def test_record_with_multiple_content_blocks_has_one_complete_boundary(store, tmp_path):
    path = tmp_path / "public.jsonl"
    item = record(uuid="multi", cwd=str(tmp_path))
    item["message"]["content"] = [
        {"type": "text", "text": "公开正文"},
        {"type": "tool_result", "tool_use_id": "c", "content": "公开结果"},
    ]
    lines(path, [item])
    scan_file(store, path, "claude")
    data = view(store)
    assert data["records_total"] == 1 and len(data["records"]) == 1
    observed = data["records"][0]
    assert observed["event_count"] == 2 and observed["first_seq"] == 1 and observed["last_seq"] == 2
    assert observed["byte_start"] == 0 and observed["byte_end"] == path.stat().st_size
    assert store.db.execute("SELECT count(*) FROM project_context_observations").fetchone()[0] == 1


def test_root_matching_preserves_cross_project_ambiguity(store, tmp_path):
    first = store.project("外层项目", [tmp_path / "root"])
    second = store.project("内层项目", [tmp_path / "root" / "inner"])
    path = tmp_path / "public.jsonl"
    lines(path, [record(cwd=str(tmp_path / "root" / "inner" / "leaf"))])
    scan_file(store, path, "claude")
    hint = view(store)["records"][0]["project_hint"]
    assert hint["state"] == "ambiguous_path" and set(hint["projects"]) == {first, second}
    assert len(hint["matching_root_ids"]) == 2
    assert store.db.execute("SELECT project_id FROM sessions").fetchone()[0] is None


def test_same_project_nested_roots_and_literal_components():
    registry = [
        {"root_id": "a", "project_id": "p", "path": "/public/root"},
        {"root_id": "b", "project_id": "p", "path": "/public/root/inner"},
    ]
    assert _hint({"state": "declared", "cwd": "/public/root/inner/leaf"}, registry)["projects"] == [
        "p"
    ]
    assert _hint({"state": "declared", "cwd": "/public/root-other"}, registry)["projects"] == []
    assert _hint({"state": "declared", "cwd": "C:\\public\\root"}, registry)["projects"] == []


def test_paging_freezes_raw_and_observation_cutoffs_and_resolves_prior_context(store, tmp_path):
    project, _, path, a, _ = scene(store, tmp_path)
    first = view(store, limit="1")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(user("后来新增")) + "\n")
    scan_file(store, path, "codex", project)
    second = view(store, limit="1", **continuation(first))
    assert second["records_total"] == 4 and second["records"][0]["context"]["cwd"] == str(a)
    assert second["records"][0]["first_event_id"] == 2
    assert view(store)["records_total"] == 5
    roots.register(store, project, tmp_path / "additional", "data")
    with pytest.raises(ConflictError):
        view(store, limit="1", **continuation(first))


def test_late_backfill_does_not_change_old_pages_or_bypass_unobserved_barriers(
    tmp_path, monkeypatch
):
    root, _ = legacy_store(tmp_path, monkeypatch, version=29)
    with closing(Store(root)) as store:
        first = view(store)
        assert first["records_observed"] == 0 and first["observations_complete"] is False
        assert contexts.backfill_file(store, 1) == 1
        frozen = view(
            store,
            snapshot=str(first["snapshot"]),
            observation_snapshot=str(first["observation_snapshot"]),
            expected_scope_key=first["scope_key"],
        )
        assert frozen["records_observed"] == 0
        assert view(store)["records_observed"] == 1


def test_rollback_and_reimport_keep_context_with_committed_raw_only(store, tmp_path):
    path = tmp_path / "public.jsonl"
    lines(path, [record(cwd=str(tmp_path))])

    def fail():
        raise RuntimeError("合成写入中断")

    with pytest.raises(RuntimeError):
        scan_file(store, path, "claude", fault=fail)
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM project_context_observations").fetchone()[0] == 0
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
    scan_file(store, path, "claude")
    before = list(store.db.iterdump())
    assert scan_file(store, path, "claude") == {}
    assert list(store.db.iterdump()) == before
    assert view(store)["records_observed"] == 1


def test_upgrade_does_not_read_objects_and_rolls_back_atomically(tmp_path, monkeypatch):
    root, original = legacy_store(tmp_path, monkeypatch, version=29)
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 30, (*migrations.MIGRATIONS[30], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 29
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_schema WHERE name='project_context_observations'"
            ).fetchone()[0]
            == 0
        )
    with monkeypatch.context() as patch:
        patch.setattr(ObjectStore, "get", lambda *a: pytest.fail("升级不得读原件"))
        with closing(Store(root)) as store:
            assert (
                store.db.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
            )
            assert view(store)["records_observed"] == 0
    with closing(Store(root)) as store:
        assert store.raw(1) == original


def test_append_only_and_project_clear_remove_owned_context_only(store, tmp_path):
    first = store.project("清除公开项目", [])
    second = store.project("保留公开项目", [])
    path, other = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    lines(path, [record(cwd=str(tmp_path / "first"))])
    lines(
        other, [record("保留公开原文", "second", sessionId="other", cwd=str(tmp_path / "second"))]
    )
    scan_file(store, path, "claude", first)
    scan_file(store, other, "claude", second)
    retained = list(
        store.db.execute("SELECT * FROM project_context_observations WHERE file_instance_id=2")
    )[0]
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("UPDATE project_context_observations SET cwd='/different'")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("DELETE FROM project_context_observations")
    root = store.root
    store.close()
    plan = clear.preview(root, first)
    assert plan["rows"]["project_context_observations"] == 1
    clear.execute(root, first, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root)) as recovered:
        assert tuple(
            recovered.db.execute("SELECT * FROM project_context_observations").fetchone()
        ) == tuple(retained)
        assert recovered.db.execute("PRAGMA foreign_key_check").fetchone() is None
        assert recovered.db.execute("SELECT project_id FROM sessions").fetchone()[0] == second


def test_deleted_source_can_be_backfilled_from_saved_objects_in_bounded_batches(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 29)
        with closing(Store(tmp_path / "old")) as old:
            path = tmp_path / "public.jsonl"
            lines(path, [record("公开文本", str(i), cwd=str(tmp_path)) for i in range(40)])
            scan_file(old, path, "claude")
            frozen = [tuple(r) for r in old.db.execute("SELECT * FROM raw_events")]
            original = old.raw(1)
    path.unlink()
    with closing(Store(tmp_path / "old")) as store:
        assert contexts.backfill_file(store, 1) == 32
        assert view(store)["records_observed"] == 32 and not view(store)["observations_complete"]
        assert contexts.backfill_file(store, 1) == 8
        assert contexts.backfill_file(store, 1) == 0
        assert view(store)["observations_complete"]
        assert [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == frozen
        assert store.raw(1) == original


def test_oversized_saved_record_is_not_decompressed(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 29)
        with closing(Store(tmp_path / "old")) as old:
            path = tmp_path / "public.jsonl"
            lines(path, [record("x" * (contexts.RECORD_BYTES + 1), cwd=str(tmp_path))])
            scan_file(old, path, "claude")
    with closing(Store(tmp_path / "old")) as store:
        monkeypatch.setattr(contexts, "_saved", lambda *a: pytest.fail("超限原件不得解压"))
        assert contexts.backfill_file(store, 1) == 1
        item = view(store)["records"][0]
        assert (
            item["context"]["state"] == "unavailable"
            and item["context"]["reason"] == "saved_record_limit"
        )


def test_saved_corruption_does_not_become_a_context_declaration(tmp_path, monkeypatch):
    root, _ = legacy_store(tmp_path, monkeypatch, version=29)
    with closing(Store(root)) as store:
        sha = store.db.execute("SELECT object_sha256 FROM raw_events").fetchone()[0]
        store.objects.path(sha).write_bytes(
            zstandard.ZstdCompressor().compress(b"corrupt replacement")
        )
        assert contexts.backfill_file(store, 1) == 1
        item = view(store)["records"][0]
        assert item["context"]["state"] == "unavailable" and item["project_hint"]["projects"] == []


def test_backfill_respects_batch_byte_budget_and_unobserved_context_barrier(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 29)
        with closing(Store(tmp_path / "old")) as old:
            path = tmp_path / "public.jsonl"
            lines(path, [record("x" * 150000, str(i), cwd=str(tmp_path)) for i in range(9)])
            scan_file(old, path, "claude")
    with closing(Store(tmp_path / "old")) as store:
        sizes = []
        saved = contexts._saved

        def observed(*args):
            sizes.append(args[2] + 1)
            return saved(*args)

        monkeypatch.setattr(contexts, "_saved", observed)
        assert contexts.backfill_file(store, 1) == 6
        assert sum(sizes) <= contexts.BATCH_BYTES
        first = view(store, limit="6")
        assert first["observations_complete"] is False
        page = view(store, **continuation(first))
        assert len(page["records"]) == 3
        assert all(r["context"]["state"] == "unobserved" for r in page["records"])
        assert all(r["project_hint"]["projects"] == [] for r in page["records"])


def test_compressed_replacement_is_read_only_up_to_expected_size(tmp_path, monkeypatch):
    root, _ = legacy_store(tmp_path, monkeypatch, version=29)
    decoder = contexts.zstandard.ZstdDecompressor
    requested = []

    class Reader:
        def __init__(self, reader):
            self.reader = reader

        def __enter__(self):
            self.reader.__enter__()
            return self

        def __exit__(self, *args):
            return self.reader.__exit__(*args)

        def read(self, size):
            requested.append(size)
            return self.reader.read(size)

    class Decoder:
        def stream_reader(self, stream):
            return Reader(decoder().stream_reader(stream))

    with closing(Store(root)) as store:
        row = store.db.execute(
            "SELECT object_sha256,byte_end-byte_start FROM raw_events"
        ).fetchone()
        store.objects.path(row[0]).write_bytes(zstandard.ZstdCompressor().compress(b"x" * 1000000))
        monkeypatch.setattr(contexts.zstandard, "ZstdDecompressor", Decoder)
        assert contexts.backfill_file(store, 1) == 1
        assert requested == [row[1] + 1]
        assert view(store)["records"][0]["context"]["state"] == "unavailable"


def test_candidate_and_registry_limits_do_not_hide_unknown_ownership():
    context = {"state": "declared", "cwd": "/public/work"}
    registry = [{"root_id": str(i), "project_id": str(i), "path": "/public"} for i in range(25)]
    hint = _hint(context, registry)
    assert hint["state"] == "ambiguous_path" and hint["candidates_partial"]
    assert len(hint["projects"]) == 20 and len(hint["matching_root_ids"]) == 20
    registry = [{"root_id": str(i), "project_id": "same", "path": "/public"} for i in range(2001)]
    hint = _hint(context, registry)
    assert hint["state"] == "unassigned" and hint["reason"] == "root_registry_limit"
    assert hint["projects"] == []


def test_restored_object_appends_recovery_and_preserves_frozen_failure(tmp_path, monkeypatch):
    root, _ = legacy_store(tmp_path, monkeypatch, version=29)
    with closing(Store(root)) as store:
        sha = store.db.execute("SELECT object_sha256 FROM raw_events").fetchone()[0]
        path = store.objects.path(sha)
        original = path.read_bytes()
        path.unlink()
        assert contexts.backfill_file(store, 1) == 1
        frozen = view(store)
        assert frozen["records"][0]["context"]["state"] == "unavailable"
        failed = tuple(store.db.execute("SELECT * FROM project_context_observations").fetchone())
        path.write_bytes(original)
        assert contexts.backfill_file(store, 1) == 0
        assert contexts.backfill_file(store, 1, retry_unavailable=True) == 1
        assert contexts.backfill_file(store, 1, retry_unavailable=True) == 0
        assert (
            store.db.execute("SELECT count(*) FROM project_context_observations").fetchone()[0] == 2
        )
        assert (
            tuple(
                store.db.execute(
                    "SELECT * FROM project_context_observations ORDER BY observation_id LIMIT 1"
                ).fetchone()
            )
            == failed
        )
        assert view(store)["records"][0]["observation_state"] == "absent"
        old = view(
            store,
            snapshot=str(frozen["snapshot"]),
            observation_snapshot=str(frozen["observation_snapshot"]),
            expected_scope_key=frozen["scope_key"],
        )
        assert old["records"][0]["context"]["state"] == "unavailable"


def test_conflicting_reinterpretation_of_valid_context_is_rejected(store, tmp_path):
    _, _, _, _, b = scene(store, tmp_path)
    before = list(store.db.iterdump())
    value = contexts.declaration("codex", codex("session_meta", {"cwd": str(b)}), "parsed")
    with pytest.raises(ValueError):
        with store.transaction() as db:
            contexts.append(db, 1, 1, value)
    assert list(store.db.iterdump()) == before


def test_read_only_legacy_store_requires_upgrade_without_writing(tmp_path, monkeypatch):
    root, _ = legacy_store(tmp_path, monkeypatch, version=29)
    before = (root / "rg.db").read_bytes()
    with pytest.raises(ValueError, match="只读入口要求当前数据库版本"):
        Store(root, readonly=True)
    assert (root / "rg.db").read_bytes() == before


def test_late_session_assignment_invalidates_fragment_page(store, tmp_path):
    scene(store, tmp_path)
    first = view(store, limit="1")
    store.db.execute("UPDATE sessions SET project_id=NULL")
    with pytest.raises(ConflictError):
        view(store, **continuation(first))
    assert view(store)["current_session_project"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"limit": "0"},
        {"limit": "51"},
        {"after_event": "1"},
        {"source": "2"},
        {"session": "2"},
        {"limit": "true"},
        {"snapshot": "1"},
        {"project": "not-this-project"},
        {"unknown": "x"},
        {"source": "9223372036854775808"},
    ],
)
def test_read_query_rejects_invalid_or_partial_scope(store, tmp_path, extra):
    scene(store, tmp_path)
    with pytest.raises(ValueError):
        view(store, **extra)


def test_cli_and_authenticated_http_share_read_only_contract(store, tmp_path):
    project, _, _, _, _ = scene(store, tmp_path)
    initial = view(store)
    root = store.root
    before = list(store.db.iterdump())
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(root),
            "project",
            "fragments",
            "--session",
            "1",
            "--source",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == initial
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>public</html>", encoding="utf-8")
    server = LocalServer(root, web, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            url = "/api/project-fragments?session=1&source=1&project=" + project
            assert client.get(url).status_code == 401
            headers = {"Authorization": "Bearer " + server.token}
            response = client.get(url, headers=headers)
            assert response.status_code == 200 and response.json() == initial
            assert client.get(url + "&limit=0", headers=headers).status_code == 400
            assert (
                client.get(url, headers={**headers, "Origin": "https://other.invalid"}).status_code
                == 403
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert list(store.db.iterdump()) == before
    output = tmp_path / "backup"
    backup(store, output)
    with closing(Store(output, readonly=True)) as copy:
        assert view(copy) == initial
