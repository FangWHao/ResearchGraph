"""解析账本不能漏原文、跨文件借版本、补造历史或拼接不同归属。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.query.parsers import query
from rg.store import clear, migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store
from rg.store.objects import digest
from tests.golden.test_ingestion import lines, record


def observations(store):
    return [
        dict(row) for row in store.db.execute("SELECT * FROM parser_records ORDER BY record_id")
    ]


def continuation(first, offset):
    return {
        "snapshot": str(first["snapshot_id"]),
        "expected_scope_key": first["scope_key"],
        "offset": str(offset),
    }


def test_multiblock_is_one_record_and_unknown_type_retains_exact_event_pointer(store, tmp_path):
    path = tmp_path / "native.jsonl"
    lines(
        path,
        [
            record(
                version="2.1.201",
                message={
                    "content": [
                        {"type": "text", "text": "合成正文"},
                        {"type": "future_image", "bytes": "synthetic"},
                        {"type": "future_image", "bytes": "synthetic-other"},
                    ]
                },
            )
        ],
    )
    result = scan_file(store, path, "claude")
    assert result["events"] == 3 and result["unknown"] == 2
    observed = observations(store)
    assert len(observed) == 1 and observed[0]["unknown_events"] == 2
    view = query(store, {})
    assert view["records_total"] == view["records_observed"] == 1
    assert view["records_with_unknown_types"] == 1
    unknown = next(row for row in view["types"] if row["type_name"] == "future_image")
    assert unknown["occurrences"] == 2 and unknown["records"] == 1
    assert unknown["first_event_id"] == 2 and not unknown["recognized"]
    assert unknown["last_event_id"] == 3
    assert store.raw(unknown["first_event_id"]) == path.read_bytes()
    assert {alert["code"] for alert in view["alerts"]} == {"unknown_types"}
    assert scan_file(store, path, "claude") == {} and observations(store) == observed


def test_versions_are_explicit_then_same_file_context_across_restart(tmp_path):
    path, root = tmp_path / "native.jsonl", tmp_path / "data"
    lines(path, [record(uuid="a", version="2.1.201")])
    with closing(Store(root)) as store:
        scan_file(store, path, "claude")
    with path.open("a") as stream:
        stream.write(json.dumps(record(uuid="b")) + "\n")
    with closing(Store(root)) as store:
        scan_file(store, path, "claude")
        rows = observations(store)
        assert [(r["tool_version"], r["version_basis"]) for r in rows] == [
            ("2.1.201", "direct_record"),
            ("2.1.201", "file_context"),
        ]
        assert query(store, {})["known_version_count"] == 1


def test_invalid_version_resets_context_and_later_valid_version_is_not_overwritten(store, tmp_path):
    path = tmp_path / "versions.jsonl"
    lines(
        path,
        [
            record(uuid="a", version="2.1.201"),
            record(uuid="b", version={"token": "synthetic"}),
            record(uuid="c"),
            record(uuid="d", version="2.2.0-preview+test"),
            record(uuid="e"),
        ],
    )
    scan_file(store, path, "claude")
    assert [(r["tool_version"], r["version_basis"]) for r in observations(store)] == [
        ("2.1.201", "direct_record"),
        (None, "invalid"),
        (None, "unknown"),
        ("2.2.0-preview+test", "direct_record"),
        ("2.2.0-preview+test", "file_context"),
    ]
    view = query(store, {})
    assert view["known_version_count"] == 2
    assert view["records_invalid_version"] == 1 and view["records_unknown_version"] == 2
    assert "synthetic" not in "\n".join(r["types"] for r in observations(store))


@pytest.mark.parametrize(
    "value", [None, "2026-synthetic", "2", " 2.1.0", "2.1.0\n", 201, "2.1." + "0" * 100]
)
def test_invalid_declared_versions_are_not_guessed(store, tmp_path, value):
    path = tmp_path / "invalid.jsonl"
    lines(path, [record(version=value)])
    scan_file(store, path, "claude")
    row = observations(store)[0]
    assert row["tool_version"] is None and row["version_basis"] == "invalid"


def test_codex_version_comes_only_from_session_metadata_and_unknown_payload_is_visible(
    store, tmp_path
):
    path = tmp_path / "codex.jsonl"
    lines(
        path,
        [
            {"type": "session_meta", "payload": {"id": "synthetic", "cli_version": "0.156.1"}},
            {"type": "event_msg", "version": "99.0.0", "payload": {"type": "future_event"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "cli_version": "88.0.0",
                    "content": [{"type": "output_text", "text": "合成"}],
                },
            },
            {
                "type": "compacted",
                "payload": {},
                "replacement_history": [
                    {"type": "future_replay", "text": "不能重复观测"},
                ],
            },
        ],
    )
    scan_file(store, path, "codex")
    assert {r["tool_version"] for r in observations(store)} == {"0.156.1"}
    view = query(store, {})
    assert view["records_total"] == 4 and view["records_with_unknown_types"] == 1
    assert "future_replay" not in {r["type_name"] for r in view["types"]}
    future = next(r for r in view["types"] if r["type_name"] == "future_event")
    assert future["category"] == "payload" and not future["recognized"]
    assert all(r["recognized"] for r in view["types"] if r["type_name"] == "event_msg")


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_nested_tool_result_types_do_not_disappear(store, tmp_path, tool):
    content = [{"type": "text", "text": "合成输出"}, {"type": "future_binary", "data": "private"}]
    item = record(message={"content": [{"type": "tool_result", "content": content}]})
    if tool == "codex":
        item = {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": content},
        }
    path = tmp_path / "result.jsonl"
    lines(path, [item])
    scan_file(store, path, tool)
    view = query(store, {})
    assert view["records_with_unknown_types"] == 1
    row = next(r for r in view["types"] if r["type_name"] == "future_binary")
    assert row["category"] == ("tool_result_content" if tool == "claude" else "output_content")
    assert not row["recognized"] and store.raw(row["first_event_id"]) == path.read_bytes()
    assert "private" not in observations(store)[0]["types"]


def test_copies_and_rotations_never_borrow_version_from_session(store, tmp_path):
    original, copied = tmp_path / "first.jsonl", tmp_path / "copy.jsonl"
    lines(original, [record(uuid="a", version="2.1.201")])
    scan_file(store, original, "claude")
    lines(copied, [record(uuid="b")])
    scan_file(store, copied, "claude")
    replacement = tmp_path / "replacement.jsonl"
    lines(replacement, [record(uuid="c")])
    replacement.replace(original)
    scan_file(store, original, "claude")
    rows = observations(store)
    assert [r["version_basis"] for r in rows] == ["direct_record", "unknown", "unknown"]
    assert len({r["file_instance_id"] for r in rows}) == 3


def test_duplicate_physical_copies_still_have_separate_observations(store, tmp_path):
    original, copied = tmp_path / "first.jsonl", tmp_path / "copy.jsonl"
    lines(original, [record(version="2.1.201")])
    shutil.copyfile(original, copied)
    scan_file(store, original, "claude")
    assert scan_file(store, copied, "claude")["aliases"] == 1
    view = query(store, {})
    assert view["records_total"] == 2
    assert all(r["records"] == r["source_files"] == 2 for r in view["types"])


def test_bad_json_nonobject_and_parser_error_are_distinct_without_losing_bytes(store, tmp_path):
    path = tmp_path / "broken.jsonl"
    raws = [b"{broken\n", b"[1,2]\n", b'{"type":"assistant","message":true}\n']
    path.write_bytes(b"".join(raws) + b"{unfinished")
    scan_file(store, path, "claude")
    rows = observations(store)
    assert [r["status"] for r in rows] == ["bad_json", "invalid_record", "parser_error"]
    assert [store.raw(r["event_id"]) for r in rows] == raws
    view = query(store, {})
    assert view["records_total"] == 3
    assert (
        view["bad_json_records"]
        == view["invalid_record_records"]
        == view["parser_error_records"]
        == 1
    )
    assert (
        store.db.execute("SELECT tail_fragment FROM source_files").fetchone()[0] == b"{unfinished"
    )


def test_raw_event_observation_and_cursor_rollback_together(store, tmp_path):
    path = tmp_path / "atomic.jsonl"
    lines(path, [record(version="2.1.201")])

    def crash():
        assert store.db.execute("SELECT count(*) FROM parser_records").fetchone()[0] == 1
        raise RuntimeError("合成崩溃")

    with pytest.raises(RuntimeError, match="合成崩溃"):
        scan_file(store, path, "claude", fault=crash)
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    assert observations(store) == []
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == 0
    assert scan_file(store, path, "claude")["events"] == 1
    assert len(observations(store)) == 1


@pytest.mark.parametrize(
    "sql", ["UPDATE parser_records SET tool_version='9.9'", "DELETE FROM parser_records"]
)
def test_observations_are_append_only(store, tmp_path, sql):
    path = tmp_path / "immutable.jsonl"
    lines(path, [record(version="2.1.201")])
    scan_file(store, path, "claude")
    before = observations(store)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(sql)
    assert observations(store) == before


def test_legacy_upgrade_is_atomic_and_does_not_backfill_versions(tmp_path, monkeypatch):
    root, path = tmp_path / "data", tmp_path / "old.jsonl"
    lines(path, [record(uuid="a", version="2.1.201")])
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 18)
        with closing(Store(root)) as store:
            scan_file(store, path, "claude")
            old = store.raw(1)
            assert query(store, {}) == {"available": False, "reason": "legacy_schema"}
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 19, (*migrations.MIGRATIONS[19], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 18
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE name='parser_records'").fetchone()[
                0
            ]
            == 0
        )
    with closing(Store(root)) as store:
        assert store.raw(1) == old and observations(store) == []
        assert query(store, {})["records_unobserved"] == 1
        with path.open("a") as stream:
            stream.write(json.dumps(record(uuid="b")) + "\n")
        scan_file(store, path, "claude")
        view = query(store, {})
        assert view["records_total"] == 2 and view["records_observed"] == 1
        assert view["records_unobserved"] == 1 and view["known_version_count"] == 0
        assert observations(store)[0]["version_basis"] == "unknown"


def test_long_control_surrogate_and_invalid_types_keep_raw_without_copying_bodies(store, tmp_path):
    path = tmp_path / "labels.jsonl"
    names = ["x" * 129, "bad\nname", "\ud800", ["unhashable"]]
    items = [
        record(uuid=str(i), message={"content": [{"type": name}]}) for i, name in enumerate(names)
    ]
    items.append(record(uuid="body", message={"content": ["private body"]}))
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    scan_file(store, path, "claude")
    rows = observations(store)
    labels = [json.loads(r["types"])[1]["type_name"] for r in rows]
    assert labels[:3] == ["sha256:" + digest(n.encode(errors="surrogatepass")) for n in names[:3]]
    assert labels[3:] == ["<invalid:list>", "<invalid:str>"]
    assert "private body" not in "\n".join(r["types"] for r in rows)
    for row, raw in zip(rows, path.read_bytes().splitlines(keepends=True), strict=True):
        assert store.raw(row["event_id"]) == raw


def test_frozen_pagination_excludes_later_appends_and_is_read_only(store, tmp_path):
    project = store.project("合成分页", [])
    path = tmp_path / "pages.jsonl"
    lines(
        path,
        [
            record(uuid=str(i), version="2.1.201", message={"content": [{"type": f"future-{i}"}]})
            for i in range(25)
        ],
    )
    scan_file(store, path, "claude", project)
    first = query(store, {"project": project, "limit": "20"})
    assert first["type_groups_total"] == 26 and first["next_offset"] == 20
    with path.open("a") as stream:
        stream.write(json.dumps(record(uuid="new", version="2.2.0")) + "\n")
    scan_file(store, path, "claude", project)
    before, revision = list(store.db.iterdump()), store.revision()
    second = query(store, {"project": project} | continuation(first, 20))
    assert second["records_total"] == 25 and second["type_groups_total"] == 26
    assert second["next_offset"] is None and len(second["types"]) == 6
    identities = {(r["category"], r["type_name"]) for r in first["types"] + second["types"]}
    assert len(identities) == 26
    assert query(store, {"project": project})["records_total"] == 26
    assert list(store.db.iterdump()) == before and store.revision() == revision
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_late_explicit_assignment_invalidates_scope_and_does_not_leak_projects(store, tmp_path):
    project, other = store.project("甲", []), store.project("乙", [])
    assigned, pending = tmp_path / "assigned.jsonl", tmp_path / "pending.jsonl"
    lines(assigned, [record(sessionId="a", uuid="a", version="2.1.201")])
    lines(pending, [record(sessionId="p", uuid="p", version="2.1.201")])
    scan_file(store, assigned, "claude", project)
    scan_file(store, pending, "claude")
    global_first = query(store, {})
    first = query(store, {"project": other})
    assert first["records_total"] == 0
    assert scan_file(store, pending, "claude", other) == {}
    with pytest.raises(ConflictError):
        query(store, continuation(global_first, 1))
    with pytest.raises(ConflictError):
        query(store, {"project": other} | continuation(first, 0))
    assert query(store, {"project": project})["records_total"] == 1
    assert query(store, {"project": other})["records_total"] == 1
    with pytest.raises(ValueError):
        query(store, {"project": "' OR 1=1 --"})


@pytest.mark.parametrize(
    "values",
    [
        {"limit": "0"},
        {"limit": "201"},
        {"offset": "-1"},
        {"offset": "1"},
        {"snapshot": "0"},
        {"expected_scope_key": "0" * 64},
        {"unexpected": "1"},
        {"snapshot": "-1", "expected_scope_key": "0" * 64},
        {"snapshot": "1", "expected_scope_key": "0" * 64},
        {"snapshot": "0", "expected_scope_key": "INVALID"},
    ],
)
def test_invalid_queries_do_not_write(store, values):
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        query(store, values)
    assert list(store.db.iterdump()) == before


def test_backup_keeps_observations_and_evidence_after_source_is_gone(store, tmp_path):
    path, destination = tmp_path / "source.jsonl", tmp_path / "backup"
    lines(path, [record(version="2.1.201")])
    scan_file(store, path, "claude")
    expected, raw = query(store, {}), store.raw(1)
    backup(store, destination)
    path.unlink()
    with closing(Store(destination, readonly=True)) as restored:
        assert query(restored, {}) == expected and restored.raw(1) == raw


def test_actual_cli_and_authenticated_http_share_read_only_page_and_evidence(store, tmp_path):
    path = tmp_path / "http.jsonl"
    project = store.project("合成HTTP", [])
    lines(path, [record(version="2.1.201")])
    scan_file(store, path, "claude", project)
    expected, before = query(store, {"project": project}), list(store.db.iterdump())
    output = subprocess.run(
        [
            str(Path(sys.executable).with_name("rg")),
            "--data-dir",
            str(store.root),
            "parser-health",
            "--project",
            project,
        ],
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0 and json.loads(output.stdout) == expected
    (tmp_path / "index.html").write_text("合成界面")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-parser-http")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            params = {"project": project}
            assert client.get("/api/parser-health", params=params).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.get("/api/parser-health", params=params)
            assert response.status_code == 200 and response.json() == expected
            assert response.headers["Cache-Control"] == "no-store"
            stale = params | continuation(expected, 1) | {"expected_scope_key": "0" * 64}
            assert client.get("/api/parser-health", params=stale).status_code == 409
            assert client.get("/api/parser-health", params={"limit": "201"}).status_code == 400
            assert client.get("/api/parser-health", params={"project": "absent"}).status_code == 404
            evidence = client.get(
                "/api/evidence/1",
                params={"start": "0", "end": str(len(path.read_bytes()))},
            )
            assert evidence.status_code == 200
            assert evidence.json()["event"]["quote_sha256"] == digest(path.read_bytes())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive() and list(store.db.iterdump()) == before


def test_whole_project_clear_includes_ledger_and_preserves_other_project(tmp_path):
    root = tmp_path / "data"
    path, other_path = tmp_path / "target.jsonl", tmp_path / "other.jsonl"
    with closing(Store(root)) as store:
        project, other = store.project("清除甲", []), store.project("保留乙", [])
        lines(path, [record(sessionId="a", uuid="a", version="2.1.201")])
        lines(other_path, [record(sessionId="b", uuid="b", version="2.2.0")])
        scan_file(store, path, "claude", project)
        scan_file(store, other_path, "claude", other)
        retained = query(store, {"project": other})
    preview = clear.preview(root, project)
    assert preview["blockers"] == []
    clear.execute(root, project, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert len(observations(store)) == 1 and query(store, {"project": other}) == retained
        assert scan_file(store, path, "claude", project) == {"privacy_blocked": 1}
        assert path.is_file() and other_path.is_file()
