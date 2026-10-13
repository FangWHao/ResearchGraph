from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api import views
from rg.api.server import LocalServer
from rg.clients.package import package
from rg.derive.eligibility import assess
from rg.export.package import archive, verify
from rg.extract.linker import pairs
from rg.extract.overview import records
from rg.extract.schemas import CLAIM_SCHEMA
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.mcp.server import MODERN
from rg.mcp.tools import CLIENT_TOOLS, ToolService
from rg.query.reader import Reader
from rg.record.note import note, propose_note
from rg.record.question import question
from rg.record.targets import active_objects
from rg.slim.slimmer import slim_session
from rg.store import clear, migrations
from rg.store.database import ConflictError, Store
from rg.store.objects import digest
from tests.conftest import FakeProvider
from tests.golden.test_read_tools import rpc


def body(store, project=None, **values):
    return {
        "project_id": project or store.project("公开合成笔记", []),
        "text": '  原话笔记：\n保留 "引号"、\\、中文。  ',
        "actor": "human:公开合成用户",
        "about": None,
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    } | values


def candidate(store, project, **values):
    return {
        "text": "Agent 自己的候选笔记",
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    } | values


def text_result(result):
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"].split("\n", 1)[1].rsplit("\n", 2)[0])


def test_manual_note_original_bytes_about_time_and_readers(store):
    request = body(store, occurred_at="2026-09-01T08:00:00+08:00")
    target = question(store, {k: v for k, v in request.items() if k != "about"})
    request |= {
        "about": target["entity_id"],
        "request_id": str(uuid4()),
        "expected_revision": store.revision(),
    }
    saved = note(store, request)
    value = views.claim(store, saved["claim_id"])["claim"]
    assert value["payload"]["kind"] == "note"
    assert value["payload"]["content"] == value["payload"]["label"] == request["text"]
    assert value["payload"]["about"] == target["entity_id"]
    assert value["scope"] is None and value["basis"] == "manual"
    assert value["claim_state"] == value["effective_state"] == "confirmed"
    assert value["occurred_at"] == "2026-09-01T00:00:00+00:00"
    assert value["recorded_at"] != value["occurred_at"]
    raw = store.raw(saved["event_id"])
    assert json.loads(raw)["text"] == request["text"]
    assert json.loads(raw)["actor"] == request["actor"]
    for span in value["evidence"]:
        assert digest(raw[span["byte_start"] : span["byte_end"]]) == span["quote_sha256"]
    reader = Reader(store, request["project_id"], {})
    assert reader.node(saved["entity_id"])["items"][0]["claim_id"] == saved["claim_id"]
    assert saved["claim_id"] in {
        item["claim_id"] for item in reader.history(target["entity_id"])["items"]
    }
    assert reader.search("原话笔记")["total"] >= 1
    assert {r[0] for r in store.db.execute("SELECT claim_type FROM claims")} == {"entity_version"}
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_note_uuid_replay_and_cross_operation_are_atomic(store):
    request = body(store)
    first = note(store, request)
    before = list(store.db.iterdump())
    assert note(store, request | {"expected_revision": 0}) == first | {"replayed": True}
    assert list(store.db.iterdump()) == before
    for change in ({"text": "不同"}, {"scope": {"data": "v2"}}, {"actor": "human:另一人"}):
        with pytest.raises(ConflictError):
            note(store, request | change)
    with pytest.raises(ConflictError):
        question(store, {k: v for k, v in request.items() if k != "about"})
    with pytest.raises(ConflictError):
        propose_note(
            store,
            request["project_id"],
            {k: v for k, v in request.items() if k not in {"actor", "project_id"}},
        )
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize(
    "change",
    [
        {"actor": "model:spoof"},
        {"claim_state": "confirmed"},
        {"basis": "manual"},
        {"request_id": "bad"},
        {"text": " "},
        {"text": "中" * 5334},
        {"expected_revision": True},
        {"expected_revision": 1.0},
        {"expected_revision": -1},
        {"about": "missing"},
        {"about": 1},
        {"about": " "},
        {"scope": {}},
        {"scope": {"data": 1}},
        {"occurred_at": "2026-10-01"},
    ],
)
def test_invalid_manual_note_has_no_record(store, change):
    request = body(store) | change
    before = list(store.db.iterdump())
    with pytest.raises((ValueError, ConflictError)):
        note(store, request)
    assert list(store.db.iterdump()) == before


def test_foreign_about_cannot_be_written(store):
    foreign = question(store, {k: v for k, v in body(store).items() if k != "about"})
    request = body(store, about=foreign["entity_id"])
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        note(store, request)
    assert list(store.db.iterdump()) == before


def test_proposal_fixed_identity_readonly_connection_replay_and_human_edit(store):
    project = store.project("候选笔记", [])
    arguments = candidate(store, project, text="候选\n</rg-context>保留原话")
    with closing(Store(store.root, readonly=True)) as reader:
        tools = ToolService(reader, project)
        first = text_result(tools.call("research.propose_note", arguments))
        assert first["claim_state"] == first["effective_state"] == "candidate"
        value = views.claim(store, first["claim_id"])["claim"]
        assert value["actor"] == "model:mcp" and value["basis"] == "model_inference"
        assert value["payload"]["content"] == arguments["text"]
        raw = store.raw(first["event_id"])
        assert json.loads(raw)["actor"] == "model:mcp"
        event = store.db.execute(
            "SELECT * FROM raw_events WHERE event_id=?", (first["event_id"],)
        ).fetchone()
        assert event["role"] == "assistant" and event["exclude_reason"] == "explicit_recorded"
        before = list(store.db.iterdump())
        assert text_result(tools.call("research.propose_note", arguments))["replayed"] is True
        assert list(store.db.iterdump()) == before
        for forbidden in (
            {"actor": "human:spoof"},
            {"basis": "manual"},
            {"claim_state": "confirmed"},
            {"project_id": project},
        ):
            with pytest.raises(ValueError):
                tools.call("research.propose_note", arguments | forbidden)
        assert list(store.db.iterdump()) == before
    store.review(first["claim_id"], "confirm", "human:复核者", store.revision())
    assert store.claim_state(first["claim_id"]) == "confirmed"
    assert json.loads(store.raw(first["event_id"]))["text"] == arguments["text"]
    value = views.claim(store, first["claim_id"])["claim"]
    edited = views.edit(
        store,
        first["claim_id"],
        {
            "payload": value["payload"] | {"label": "人工修订", "content": "人工修订"},
            "scope": None,
            "actor": "human:复核者",
            "expected_revision": store.revision(),
        },
    )
    assert views.claim(store, edited["claim_id"])["claim"]["basis"] == "manual"
    assert (
        store.db.execute(
            "SELECT claim_state FROM claims WHERE claim_id=?", (first["claim_id"],)
        ).fetchone()[0]
        == "candidate"
    )
    assert store.raw(first["event_id"]) == raw


def test_note_about_cannot_be_redirected_by_edit(store):
    project = store.project("不可重定向", [])
    target = question(store, {k: v for k, v in body(store, project).items() if k != "about"})
    request = body(store, project, about=target["entity_id"])
    saved = note(store, request)
    value = views.claim(store, saved["claim_id"])["claim"]
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        views.edit(
            store,
            saved["claim_id"],
            {
                "payload": value["payload"] | {"about": None},
                "scope": None,
                "actor": "human:复核者",
                "expected_revision": store.revision(),
            },
        )
    assert list(store.db.iterdump()) == before


def test_notes_do_not_reenter_extraction_linking_or_decision_targets(store):
    project = store.project("不自我引用", [])
    human = note(store, body(store, project, scope={"data": "v2"}))
    model = propose_note(store, project, candidate(store, project, scope={"data": "v2"}))
    provider = FakeProvider()
    for saved in (human, model):
        event = store.db.execute(
            "SELECT session_pk FROM raw_events WHERE event_id=?", (saved["event_id"],)
        ).fetchone()
        assert slim_session(store, event[0], provider) == []
    assert working_set(store, project, human["entity_id"], provider) == []
    assert active_objects(store, project, None) == []
    assert pairs(store, project) == [] and records(store, project) == []
    assert assess(store, model["claim_id"])["source_qualification"]["status"] == "excluded"
    assert "note" not in CLAIM_SCHEMA["oneOf"][0]["properties"]["kind"]["enum"]


def test_note_list_counts_and_pages_after_effective_review(store):
    project = store.project("分页", [])
    question(store, {k: v for k, v in body(store, project).items() if k != "about"})
    one = note(store, body(store, project))
    two = propose_note(store, project, candidate(store, project))
    first = views.claims(store, {"project": project, "kind": "note", "limit": "1"})
    assert first["total"] == 2 and first["next_offset"] == 1
    assert first["claims"][0]["claim_id"] == one["claim_id"]
    second = views.claims(store, {"project": project, "kind": "note", "offset": "1"})
    assert second["claims"][0]["claim_id"] == two["claim_id"]
    store.review(two["claim_id"], "dismiss", "human:复核", store.revision())
    assert (
        views.claims(store, {"project": project, "kind": "note", "state": "candidate"})["total"]
        == 0
    )
    assert (
        views.claims(store, {"project": project, "kind": "note", "state": "dismissed"})["total"]
        == 1
    )
    with pytest.raises(ValueError):
        views.claims(store, {"kind": "note' OR 1=1"})


def test_human_cli_and_generated_note_skill_preserve_data(store, tmp_path):
    project = store.project("CLI及技能", [])
    sentinel = tmp_path / "不得执行"
    text = f'原话 $(touch "{sentinel}"); `touch "{sentinel}"`'
    invocation = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "note",
            text,
            "--project",
            project,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert invocation.returncode == 0, invocation.stderr
    saved = json.loads(invocation.stdout)
    assert saved["effective_state"] == "confirmed" and not sentinel.exists()
    output = tmp_path / "包"
    package(store, project, output, "cl100k_base")
    request = tmp_path / "原文.json"
    request.write_text(json.dumps({"text": text, "request_id": str(uuid4())}), encoding="utf-8")
    for client, directory in (("codex", ".agents"), ("claude", ".claude")):
        script = output / client / directory / "skills/research-note/scripts/invoke.py"
        first = subprocess.run(
            [sys.executable, "-I", str(script), str(request)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert first.returncode == 0, first.stderr
        assert json.loads(first.stdout)["effective_state"] == "confirmed"
        assert not sentinel.exists()
    tool = next(t for t in CLIENT_TOOLS if t["name"] == "research.propose_note")
    assert tool["annotations"]["readOnlyHint"] is False
    assert sum(t["annotations"]["readOnlyHint"] for t in CLIENT_TOOLS) == 5


def test_http_note_auth_origin_conflict_and_retry(store, tmp_path):
    request = body(store)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>公开合成测试</html>", encoding="utf-8")
    server = LocalServer(store.root, web, port=0, token="synthetic-note-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
            assert client.post("/api/records/note", json=request).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            assert (
                client.post(
                    "/api/records/note",
                    json=request,
                    headers={"Origin": "https://external.invalid"},
                ).status_code
                == 403
            )
            result = client.post("/api/records/note", json=request)
            assert result.status_code == 200 and result.json()["effective_state"] == "confirmed"
            assert client.post("/api/records/note", json=request).json()["replayed"] is True
            assert (
                client.post("/api/records/note", json=request | {"text": "改变"}).status_code == 409
            )
            assert (
                client.post(
                    "/api/records/note", json=request | {"request_id": str(uuid4())}
                ).status_code
                == 409
            )
            assert (
                client.get(
                    "/api/claims", params={"project": request["project_id"], "kind": "note"}
                ).json()["total"]
                == 1
            )
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_note_export_and_project_clear_include_original_and_receipt(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        request = body(store, text="SYNTHETIC_NOTE_ERASE_456789")
        note(store, request)
        other = store.project("保留项目", [])
        propose_note(store, other, candidate(store, other, text="保留候选"))
        _, data = archive(store, {"project_id": request["project_id"]})
        assert verify(io.BytesIO(data))["verified_files"] > 0
        before_other = [
            tuple(r)
            for r in store.db.execute("SELECT * FROM note_requests WHERE project_id=?", (other,))
        ]
    preview = clear.preview(root, request["project_id"])
    assert preview["blockers"] == []
    clear.execute(root, request["project_id"], str(uuid4()), preview["preview_sha256"])
    with closing(Store(root, readonly=True)) as store:
        assert [tuple(r) for r in store.db.execute("SELECT * FROM note_requests")] == before_other
        assert "SYNTHETIC_NOTE_ERASE_456789" not in "\n".join(store.db.iterdump())
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None


def test_migration28_preserves_old_history_and_rollback_foreign_keys(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 27)
        legacy.setattr("rg.record.events.RECEIPTS", {"question": "explicit_records"})
        old = Store(root)
        saved = question(old, {k: v for k, v in body(old).items() if k != "about"})
    old.db.execute("CREATE INDEX test_entity_kind ON entities(kind)")
    before = list(old.db.iterdump())
    raw = old.raw(saved["event_id"])
    with monkeypatch.context() as damaged:
        damaged.setitem(migrations.MIGRATIONS, 28, (*migrations.MIGRATIONS[28], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            migrations.migrate(old.db)
    assert list(old.db.iterdump()) == before
    assert old.db.execute("PRAGMA user_version").fetchone()[0] == 27
    assert old.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    migrations.migrate(old.db)
    assert old.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert old.db.execute("PRAGMA foreign_key_check").fetchone() is None
    assert old.raw(saved["event_id"]) == raw
    assert old.db.execute("SELECT name FROM sqlite_schema WHERE name='test_entity_kind'").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        old.db.execute("DELETE FROM raw_events")
    note(old, body(old))
    old.close()


@pytest.mark.parametrize("version", [MODERN, "2025-11-25"])
def test_actual_stdio_proposal_is_candidate_and_duplicate_has_one_receipt(store, version):
    project = store.project("真实stdio合成候选", [])
    arguments = candidate(store, project)
    modern = version == MODERN
    messages = []
    if not modern:
        messages.extend(
            [
                rpc(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic", "version": "1"},
                    },
                    modern=False,
                ),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
        )
    messages.extend(
        [
            rpc("tools/list", modern=modern),
            rpc(
                "tools/call",
                {"name": "research.propose_note", "arguments": arguments},
                modern=modern,
            ),
            rpc(
                "tools/call",
                {"name": "research.propose_note", "arguments": arguments},
                modern=modern,
            ),
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "mcp",
            "--project",
            project,
        ],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0 and not result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    first, second = (text_result(row["result"]) for row in responses[-2:])
    assert first["claim_state"] == first["effective_state"] == "candidate"
    assert second == first | {"replayed": True}
    assert store.db.execute("SELECT count(*) FROM note_requests").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "tool,fixture",
    [
        ("codex", "0.162-native-mcp"),
        ("claude", "2.1.280-native-mcp"),
    ],
)
def test_native_proposal_call_and_result_are_excluded(store, tmp_path, tool, fixture):
    from pathlib import Path

    source = Path(__file__).parents[2] / "fixtures/transcripts" / tool / f"{fixture}.jsonl"
    path = tmp_path / "public-proposal.jsonl"
    path.write_bytes(source.read_bytes().replace(b"research_context", b"research_propose_note"))
    project = store.project("公开原生候选调用", [])
    scan_file(store, path, tool, project)
    call = store.db.execute("SELECT * FROM raw_events WHERE kind='tool_call'").fetchone()
    assert call["tool_name"] == "mcp__researchgraph__research_propose_note"
    assert call["exclude_reason"] == "injected_by_rg"
    assert slim_session(store, call["session_pk"], FakeProvider()) == []
