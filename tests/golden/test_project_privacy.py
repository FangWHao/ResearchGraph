from __future__ import annotations

import json
import os
import select
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import httpx
import pytest

from rg.api.qa import preview
from rg.api.server import LocalServer
from rg.export.package import archive
from rg.extract.privacy import ProjectCounter
from rg.extract.provider import ModelResult
from rg.extract.qa import prepare_input
from rg.extract.queue import Queue
from rg.extract.redact import model_input, redact
from rg.extract.segmenter import Segment, segment
from rg.extract.validate import InvalidCitation, validate
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.query.retrieval import retrieve
from rg.slim.slimmer import slim_session
from rg.store.database import ConflictError, Store, dumps
from rg.store.privacy import gate, read, update
from tests.conftest import FakeProvider
from tests.golden.test_exports import evidence, unpack
from tests.golden.test_ingestion import lines, record
from tests.golden.test_l1 import claude_result
from tests.golden.test_read_tools import original
from tests.test_migrations import legacy_store
from tests.test_worker import PipelineProvider

RULES = [r"HOSP-\d{6}", r"CASE-\d{6}", r"SAMPLE-[A-Z]{3}\d{3}"]
TEXT = "遮盖问答：HOSP-246810，CASE-135790，SAMPLE-ABC123。"


def save(store, project, rules=RULES, **overrides):
    return update(
        store,
        {
            "project_id": project,
            "patterns": rules,
            "actor": "human:合成测试",
            "expected_revision": store.revision(),
        }
        | overrides,
    )


def scene(store):
    project = store.project("合成遮盖", [])
    entity, claim = original(store, project)
    event, span, raw = evidence(store, project, claim, TEXT)
    return project, entity, claim, event, span, raw


class Recording(FakeProvider):
    remote = True

    def __init__(self):
        super().__init__(count=10)
        self.sent = []

    def count_text(self, text):
        self.sent.append(text)
        return super().count_text(text)

    def count_request(self, request):
        self.sent.append(dumps(request))
        return super().count_request(request)

    def generate(self, request):
        self.calls += 1
        self.sent.append(dumps(request))
        return ModelResult('{"value":"ok"}', 10, 10, "stop")


def enabled(store, project):
    store.db.execute("UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (project,))


def cli(root, *args):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("bad", [None, "HOSP", 1, [""], [1], ["["], ["x" * 1001], ["x"] * 33])
def test_bad_rules_do_not_write_or_enable_remote(store, bad):
    project = store.project("合成", [])
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        save(store, project, bad)
    assert list(store.db.iterdump()) == before


def test_project_policy_is_append_only_scoped_and_does_not_rewrite_raw(store):
    project, _, _, event, _, raw = scene(store)
    other = store.project("合成二", [])
    first = read(store, project)
    rev = store.revision()
    saved = save(store, project)
    assert saved["revision"] == rev + 1 and saved["raw_unchanged"]
    assert read(store, other).patterns == ()
    assert (
        store.raw(event) == raw
        and not store.db.execute(
            "SELECT remote_model_allowed FROM projects WHERE project_id=?", (project,)
        ).fetchone()[0]
    )
    with pytest.raises(ConflictError):
        first.check(store)
    for sql in ("UPDATE project_privacy SET patterns='[]'", "DELETE FROM project_privacy"):
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute(sql)
    with pytest.raises(ConflictError):
        save(store, project, expected_revision=rev)
    last = save(store, project, [])
    assert last["rule_id"] > saved["rule_id"] and read(store, project).patterns == ()
    assert len(store.db.execute("SELECT * FROM project_privacy").fetchall()) == 2
    assert store.raw(event) == raw


@pytest.mark.parametrize("stage", ["pass1", "pass2", "link", "overview", "qa"])
def test_all_model_stages_mask_before_counting_and_generation(store, stage):
    project = store.project("合成", [])
    save(store, project)
    enabled(store, project)
    provider = Recording()
    worker = Worker(store, provider)
    content = dumps({"text": TEXT, "escaped": "HOSP-246810", "slim": dumps([{"text": TEXT}])})
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    first, _ = worker.invoke(stage, Segment("synthetic", []), project, content, schema, [])
    assert provider.calls == 1 and provider.sent
    assert all(
        value not in sent
        for sent in provider.sent
        for value in ("HOSP-246810", "CASE-135790", "SAMPLE-ABC123")
    )
    cached, _ = worker.invoke(stage, Segment("synthetic", []), project, content, schema, [])
    assert cached == first and provider.calls == 1
    save(store, project, RULES + [r"NEVERMATCH-[A-Z]+"])
    fresh, _ = worker.invoke(stage, Segment("synthetic", []), project, content, schema, [])
    assert fresh != first and provider.calls == 2


def test_no_permission_means_no_count_request(store):
    project = store.project("合成", [])
    save(store, project)
    provider = Recording()
    with pytest.raises(PermissionError):
        ProjectCounter(store, project, provider).count_text(TEXT)
    assert not provider.sent


def test_counter_masks_full_text_before_prefix_and_invalidates_old_instance(store):
    project = store.project("合成", [])
    save(store, project)
    enabled(store, project)
    provider = Recording()
    counter = ProjectCounter(store, project, provider)
    source = "a" * 1995 + "HOSP-246810" + "b" * 2100
    pieces = segment(
        [{"event_id": 1, "kind": "user_msg", "text": source, "raw_text": source, "raw_start": 0}],
        counter,
    )
    assert sum(len(p.events) for p in pieces) >= 2
    assert all("HOSP" not in sent and "246810" not in sent for sent in provider.sent)
    assert "HOSP-246810" in "".join(e["raw_text"] for p in pieces for e in p.events)
    before = len(provider.sent)
    counter.count_text(TEXT)
    counter.count_text(TEXT)
    assert len(provider.sent) == before + 1
    save(store, project, [])
    with pytest.raises(ConflictError):
        counter.count_text(TEXT)


def test_rules_mask_json_escapes_keep_byte_mapping_and_reject_field_collision():
    raw = json.dumps({"text": "合成编号 HOSP-246810 中文"}, ensure_ascii=True).encode()
    safe = redact(raw, tuple(RULES))
    assert len(raw) == len(safe.data) and b"HOSP" not in safe.data
    a = raw.index(b"HOSP")
    with pytest.raises(ValueError):
        safe.original_span(a, a + 3)
    safe.original_span(0, 1)
    with pytest.raises(ValueError, match="碰撞"):
        model_input('{"CASE-135790":1,"HOSP-246810":2}', "qa", (r"(?:CASE|HOSP)-\d{6}",))


def test_qa_masks_entire_raw_before_short_window_and_actual_preview_matches_sent(store):
    project, _, _, _, _, _ = scene(store)
    save(store, project)
    enabled(store, project)
    packet = retrieve(store, project, "合成方案", max_bytes=19)
    assert packet["sources"] and "HOSP" in packet["sources"][0]["text"]
    sent = prepare_input(packet, custom=tuple(RULES), store=store)
    assert all("HOSP" not in source["text"] for source in sent["sources"])
    assert sent["sources"][0]["text_redacted"]
    core = {"project_id": project, "question": "合成方案", "max_bytes": 19}
    data = preview(store, core, None)
    assert data["input"]["sources"][0]["text"] == sent["sources"][0]["text"]


def test_custom_sensitive_quote_cannot_become_model_evidence(store, tmp_path):
    project = store.project("合成", [])
    path = tmp_path / "synthetic.jsonl"
    lines(path, [record(TEXT)])
    scan_file(store, path, "claude", project)
    save(store, project)
    event = store.db.execute("SELECT event_id FROM raw_events").fetchone()[0]
    raw = store.raw(event)
    start = raw.index(b"HOSP-246810")
    output = {
        "segment_id": "synthetic",
        "claims": [
            {
                "claim_type": "entity_version",
                "temp_id": "new:1",
                "kind": "finding",
                "label": "合成",
                "content": "合成",
                "scope": {"data": "synthetic"},
                "evidence": [
                    {
                        "event_id": event,
                        "byte_start": start,
                        "byte_end": start + 11,
                        "quote": "HOSP-246810",
                    }
                ],
            }
        ],
        "lookup_terms": [],
        "unresolved": [],
    }
    with pytest.raises(InvalidCitation, match="遮盖"):
        validate(store, output, project, set(), {event}, "synthetic")


def test_export_always_combines_current_project_and_per_export_rules(store):
    project, _, claim, _, _, _ = scene(store)
    save(store, project, [RULES[0]])
    before = list(store.db.iterdump())
    manifest, data = archive(
        store, {"project_id": project, "include_evidence": True, "redact_patterns": [RULES[1]]}
    )
    values = unpack(data)
    payload = dumps(values)
    assert "HOSP-246810" not in payload and "CASE-135790" not in payload
    assert "SAMPLE-ABC123" in payload
    assert values["claims.json"][0]["claim_id"] == claim
    assert manifest["privacy"]["project_policy_id"] == read(store, project).identity
    assert manifest["privacy"]["custom_patterns_count"] == 2
    assert list(store.db.iterdump()) == before


def test_cli_read_write_preview_receipts_and_old_ack_refusal(store, tmp_path):
    project = store.project("合成", [])
    path = tmp_path / "synthetic.jsonl"
    lines(path, [record(TEXT)])
    scan_file(store, path, "claude", project)
    shown = cli(store.root, "project", "privacy", project)
    assert shown.returncode == 0, shown.stderr
    config = json.loads(shown.stdout)
    output = tmp_path / "preview.json"
    old = cli(store.root, "preview", "1", "--output", str(output))
    assert old.returncode == 0, old.stderr
    old_sha = json.loads(old.stdout)["preview_sha256"]
    rules = tmp_path / "rules.json"
    rules.write_text(dumps(RULES))
    saved = cli(
        store.root,
        "project",
        "privacy",
        project,
        "--patterns-file",
        str(rules),
        "--actor",
        "human:合成",
        "--expected-revision",
        str(config["revision"]),
    )
    assert saved.returncode == 0, saved.stderr
    rejected = cli(store.root, "project", "allow-remote", project, "--ack-preview", old_sha)
    assert rejected.returncode == 1
    fresh = cli(store.root, "preview", "1", "--output", str(output))
    assert fresh.returncode == 0, fresh.stderr
    assert "HOSP-246810" not in output.read_text()
    receipt = json.loads(fresh.stdout)["preview_sha256"]
    granted = cli(store.root, "project", "allow-remote", project, "--ack-preview", receipt)
    assert granted.returncode == 0, granted.stderr
    save(store, project, [])
    assert store.db.execute("SELECT remote_model_allowed FROM projects").fetchone()[0] == 1


def test_v15_upgrade_failure_is_atomic_and_original_survives(tmp_path, monkeypatch):
    from rg.store import migrations

    root, raw = legacy_store(tmp_path, monkeypatch, version=15)
    with monkeypatch.context() as change:
        change.setitem(migrations.MIGRATIONS, 16, (*migrations.MIGRATIONS[16], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 15
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='project_privacy'"
        ).fetchall()
    with closing(Store(root)) as recovered:
        assert recovered.raw(1) == raw
        assert recovered.db.execute("PRAGMA user_version").fetchone()[0] == 16


def test_real_http_scope_auth_revision_duplicates_and_busy_gate(store, tmp_path):
    project, _, _, event, _, raw = scene(store)
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "index.html").write_text("合成")
    server = LocalServer(store.root, directory, port=0, token="synthetic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=server.origin,
            trust_env=False,
            headers={"Authorization": "Bearer synthetic-token"},
        ) as client:
            before = list(store.db.iterdump())
            response = client.get("/api/privacy", params={"project": project})
            assert response.status_code == 200 and response.json()["patterns"] == []
            assert list(store.db.iterdump()) == before
            body = {
                "project_id": project,
                "patterns": RULES,
                "actor": "human:合成",
                "expected_revision": response.json()["revision"],
            }
            assert (
                client.post("/api/privacy", json=body, headers={"Authorization": ""}).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/privacy", json=body, headers={"Origin": "https://foreign.invalid"}
                ).status_code
                == 403
            )
            assert client.post("/api/privacy", json=body | {"patterns": ["["]}).status_code == 400
            duplicate = dumps(body)[:-1] + ',"patterns":[]}'
            assert (
                client.post(
                    "/api/privacy", content=duplicate, headers={"Content-Type": "application/json"}
                ).status_code
                == 400
            )
            with gate(store, project):
                assert client.post("/api/privacy", json=body).status_code == 409
            assert client.post("/api/privacy", json=body).status_code == 200
            assert client.post("/api/privacy", json=body).status_code == 409
            assert store.raw(event) == raw
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_tool_preview_masks_before_head_tail_truncation_and_keeps_local_slim(store, tmp_path):
    project = store.project("合成工具", [])
    save(store, project)
    enabled(store, project)
    source = tmp_path / "synthetic-tool.jsonl"
    text = "a" * 197 + "HOSP-246810" + "b" * 210
    lines(source, [claude_result(text=text)])
    scan_file(store, source, "claude", project)
    provider = Recording()
    events = slim_session(store, 1, provider)
    assert provider.sent and all(
        "HOS" not in value and "246810" not in value for value in provider.sent
    )
    assert all("HOS" not in e["text"] for e in events)
    assert "HOS" in store.db.execute("SELECT text FROM slim_events").fetchone()[0]


def test_changed_policy_after_count_rejects_generation_and_records_unsent_invalid_attempt(store):
    project = store.project("合成", [])
    enabled(store, project)

    class Changed(Recording):
        def count_request(self, request):
            count = super().count_request(request)
            # 模拟接口之外的直接 SQL 写入；正常设置入口会被发送锁拒绝。
            store.db.execute(
                "INSERT INTO project_privacy(project_id,patterns,actor,recorded_at) "
                "VALUES (?,'[]','human:合成','2026-01-01')",
                (project,),
            )
            return count

    provider = Changed()
    with pytest.raises(ConflictError):
        Worker(store, provider).invoke(
            "qa", Segment("synthetic", []), project, '{"text":"合成"}', {"type": "object"}, []
        )
    assert provider.calls == 0
    assert tuple(store.db.execute("SELECT status,sent FROM model_attempts").fetchone()) == (
        "invalid",
        0,
    )


def test_queue_cancels_old_policy_and_fresh_config_processes_without_new_events(store, tmp_path):
    project = store.project("合成", [])
    source = tmp_path / "synthetic-queue.jsonl"
    lines(source, [record("采用方法甲。" + TEXT)])
    scan_file(store, source, "claude", project)
    worker = Worker(store, PipelineProvider())
    queue = Queue(worker, project)
    assert queue._discover(False)["enqueued"] == 1
    old = store.db.execute("SELECT queue_id FROM extraction_queue").fetchone()[0]
    save(store, project)
    assert queue._claim(old) is None
    assert (
        store.db.execute("SELECT state FROM extraction_queue WHERE queue_id=?", (old,)).fetchone()[
            0
        ]
        == "cancelled"
    )
    assert queue.run(5)["done"] == 1
    before = worker.provider.calls
    assert queue.run(5).get("done", 0) == 0
    save(store, project, RULES + ["NEVERMATCH"])
    assert queue.run(5)["done"] == 1 and worker.provider.calls > before


def test_full_payload_is_masked_before_record_preview_limit(store):
    project, entity, _, _, _, _ = scene(store)
    from tests.golden.test_read_tools import append

    payload = {"label": "合成方案", "content": "a" * 3957 + "HOSP-246810" + "b" * 100}
    claim = append(store, entity, "entity_version", payload)
    evidence(store, project, claim, TEXT)
    save(store, project)
    packet = retrieve(store, project, "合成方案")
    prepared = prepare_input(packet, custom=tuple(RULES), store=store)
    records = [r for source in prepared["sources"] for r in source["records"]]
    assert any(r["payload_preview_truncated"] for r in records)
    assert all("HOSP" not in r["payload_preview"] for r in records)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 共享发送锁；原生 Windows 保守串行分支")
def test_independent_senders_share_lock_but_configuration_is_exclusive(store):
    project = store.project("合成共享发送", [])
    save(store, project)
    source = (
        "import sys\nfrom pathlib import Path\nfrom contextlib import closing\n"
        "from rg.store.database import Store\nfrom rg.store.privacy import read\n"
        "with closing(Store(Path(sys.argv[1]),readonly=True)) as s:\n"
        " with read(s,sys.argv[2]).sending(s):\n"
        "  print('ready',flush=True)\n  sys.stdin.read(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", source, str(store.root), project],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == "ready"
        with read(store, project).sending(store):
            with pytest.raises(ConflictError):
                save(store, project, [])
        child.stdin.write("x")
        child.stdin.flush()
        child.communicate(timeout=10)
        assert child.returncode == 0
        assert save(store, project, ["AFTER-SEND"])["patterns"] == ["AFTER-SEND"]
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
