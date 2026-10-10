from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.ingest.spool import enqueue, register
from rg.snapshot.config import export_registry
from rg.store import clear
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.locking import TaskBusy
from rg.store.objects import digest
from tests.l1_graph_browser import seed_l1_graph

MARKER = "SYNTHETIC_ERASE_MARKER_97531"


def scene(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        project = seed_l1_graph(store)
        work = tmp_path / "workspace"
        work.mkdir()
        (work / "protected.txt").write_text(MARKER)
        store.db.execute("UPDATE source_roots SET path=? WHERE project_id=?", (str(work), project))
        other = store.project("保留项目", [])
        shared = store.objects.put(b"synthetic shared bytes")
        for p in (project, other):
            store.db.execute(
                "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
                "content_sha256) VALUES (?,?,?,'sha256',?,'current_file',?)",
                (str(uuid4()), p, "synthetic-shared", shared, shared),
            )
        source = tmp_path / "synthetic.jsonl"
        source.write_text(
            dumps(
                {
                    "type": "user",
                    "sessionId": "synthetic-private",
                    "cwd": str(work),
                    "message": {"role": "user", "content": MARKER},
                }
            )
            + "\n"
        )
        scan_file(store, source, "claude", project)
        event = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
        store.db.execute("INSERT INTO slim_events VALUES (?,?,1,'synthetic',0)", (event, MARKER))
        run = store.db.execute(
            "INSERT INTO extraction_runs(job_key,stage,input_event_ids,working_set_ids,status,"
            "output_json,created_at) VALUES (?,'overview',?,'[]','failed',?,?)",
            (str(uuid4()), dumps([event]), MARKER, now()),
        ).lastrowid
        store.db.execute(
            "INSERT INTO model_attempts(extraction_run_id,project_id,stage,provider,model,"
            "segment_id,input_budget,output_budget,status,created_at) "
            "VALUES (?,?,'overview','synthetic','synthetic','synthetic',1,1,'failed',?)",
            (run, project, now()),
        )
        store.db.execute(
            "INSERT INTO jobs(kind,payload,state,updated_at) VALUES ('budget_pause',?,'queued',?)",
            (dumps({"project_id": project, "session_pk": 1, "text": MARKER}), now()),
        )
        hint = enqueue(
            root,
            "claude",
            dumps({"session_id": "synthetic-private", "cwd": str(work), "text": MARKER}).encode(),
        )
        register(store)
        export_registry(store)
        snapshot = root / "snapshots" / (project + ".git")
        snapshot.mkdir()
        (snapshot / "synthetic.txt").write_text(MARKER)
        overview = root / "overviews" / digest(project.encode())
        overview.mkdir(parents=True)
        (overview / "project.md").write_text(MARKER)
        before_other = [
            tuple(r)
            for r in store.db.execute(
                "SELECT * FROM artifact_versions WHERE project_id=?", (other,)
            )
        ]
    return root, project, other, shared, source, work, hint, before_other


def perform(root, project):
    info = clear.preview(root, project)
    assert info["blockers"] == []
    request = str(uuid4())
    return info, request, clear.execute(root, project, request, info["preview_sha256"])


def test_complete_managed_project_clear_scrubs_sqlite_fts_files_and_preserves_shared(tmp_path):
    root, project, other, shared, source, work, hint, before_other = scene(tmp_path)
    before = {
        p.relative_to(root): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.name not in {"rg.db-wal", "rg.db-shm"}
    }
    info = clear.preview(root, project)
    assert info["blockers"] == [] and info["shared_objects_retained"] == 1
    assert {
        p.relative_to(root): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.name not in {"rg.db-wal", "rg.db-shm"}
    } == before
    request = str(uuid4())
    result = clear.execute(root, project, request, info["preview_sha256"])
    assert result["state"] == "complete" and result["rows"]["model_attempts"] == 1
    assert result == clear.execute(root, project, request, info["preview_sha256"])
    assert result == clear.resume(root, request) == clear.status(root, request)
    with closing(Store(root)) as store:
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.db.execute("SELECT project_id FROM projects").fetchone()[0] == other
        assert [
            tuple(r)
            for r in store.db.execute(
                "SELECT * FROM artifact_versions WHERE project_id=?", (other,)
            )
        ] == before_other
        assert store.objects.get(shared) == b"synthetic shared bytes"
        for index in ("slim_fts", "event_fts"):
            assert not store.db.execute(
                f'SELECT rowid FROM "{index}" WHERE "{index}" MATCH ?', ('"' + MARKER + '"',)
            ).fetchall()
        assert scan_file(store, source, "claude", project) == {"privacy_blocked": 1}
        assert scan_file(store, source, "claude") == {"privacy_blocked": 1}
        assert store.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert source.read_text().find(MARKER) >= 0
    assert (work / "protected.txt").read_text() == MARKER
    assert not hint.exists() and not (root / "snapshots" / (project + ".git")).exists()
    for path in root.rglob("*"):
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes(), path
    assert {p.stem for p in (root / "objects").glob("*/*.zst")} == {shared}
    record = (root / "clear-records" / (request + ".json")).read_text()
    assert str(source) not in record and str(work) not in record
    assert "synthetic-private" not in record and "验收L1" not in record
    assert not (root / clear.ACTIVE).exists()


@pytest.mark.parametrize("stage", ["_delete", "_finish"])
def test_actual_killed_process_is_resumable_and_blocks_stores_hooks(tmp_path, stage):
    root, project, *_ = scene(tmp_path)
    info, request, ready = clear.preview(root, project), str(uuid4()), tmp_path / "ready"
    script = """
import sys,time
from pathlib import Path
from rg.store import clear
root,project,request,proof,stage,ready=sys.argv[1:]
original=getattr(clear,stage)
def pause(*args):
 Path(ready).write_text('ready')
 while True: time.sleep(.1)
setattr(clear,stage,pause)
clear.execute(Path(root),project,request,proof)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            project,
            request,
            info["preview_sha256"],
            stage,
            str(ready),
        ],
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1]
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert clear.status(root)["state"] == "pending"
        for readonly in (True, False):
            with pytest.raises(TaskBusy):
                Store(root, readonly=readonly)
        with pytest.raises(TaskBusy):
            enqueue(root, "claude", b'{"text":"synthetic"}')
    finally:
        process.kill()
        process.communicate(timeout=10)
    result = clear.resume(root, request)
    assert result["state"] == "complete"
    assert clear.status(root, request)["state"] == "complete"
    with closing(Store(root)) as store:
        assert not store.db.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project,)
        ).fetchone()


@pytest.mark.parametrize("change", ["row", "file"])
def test_stale_preview_refuses_before_any_deletion(tmp_path, change):
    root, project, *_ = scene(tmp_path)
    info = clear.preview(root, project)
    if change == "row":
        with closing(Store(root)) as store:
            store.db.execute("UPDATE jobs SET error='synthetic changed'")
    else:
        (root / "snapshots" / (project + ".git") / "synthetic.txt").write_text("changed")
    with pytest.raises(ConflictError, match="已变化"):
        clear.execute(root, project, str(uuid4()), info["preview_sha256"])
    assert not (root / clear.ACTIVE).exists()
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()


@pytest.mark.parametrize("problem", ["orphan", "unknown_spool", "cross_alias", "symlink"])
def test_unsafe_ownership_never_deletes_or_modifies_other_project(tmp_path, problem):
    root, project, other, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        if problem == "orphan":
            store.objects.put(b"unassigned synthetic original")
        elif problem == "unknown_spool":
            enqueue(root, "codex", b'{"unknown":"synthetic original"}')
        elif problem == "cross_alias":
            event = store.db.execute("SELECT min(event_id) FROM raw_events").fetchone()[0]
            # 保留项目的 L0 别名指向待清除的原记录。
            session = store.db.execute(
                "INSERT INTO sessions(tool,native_session_id,project_id) "
                "VALUES ('rg','retained',?)",
                (other,),
            ).lastrowid
            file = store.db.execute(
                "INSERT INTO source_files(session_pk,path,prefix_sha256,"
                "parser,parser_version,first_seen) VALUES (?,'virtual','x',"
                "'synthetic','1',?)",
                (session, now()),
            ).lastrowid
            sha = store.db.execute(
                "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
            ).fetchone()[0]
            store.db.execute(
                "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,"
                "byte_end,object_sha256,seq,kind,recorded_at,line_sha256,alias_of) "
                "VALUES (?,?,0,1,?,1,'unknown',?,?,?)",
                (session, file, sha, now(), sha, event),
            )
        else:
            (root / "snapshots" / (project + ".git") / "outside").symlink_to(tmp_path)
    if problem == "symlink":
        with pytest.raises(ValueError, match="符号链接"):
            clear.preview(root, project)
    else:
        info = clear.preview(root, project)
        assert info["blockers"]
        with pytest.raises(ConflictError, match="归属阻碍"):
            clear.execute(root, project, str(uuid4()), info["preview_sha256"])
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 2
    assert not (root / clear.ACTIVE).exists()


def test_real_http_auth_stale_conflict_execute_idempotency_and_status(tmp_path):
    root, project, *_ = scene(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("synthetic")
    server = LocalServer(root, web, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            path = "/api/project-clear/preview"
            assert client.post(path, json={"project_id": project}).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            info = client.post(path, json={"project_id": project})
            assert info.status_code == 200
            body = {
                "project_id": project,
                "request_id": str(uuid4()),
                "preview_sha256": info.json()["preview_sha256"],
            }
            result = client.post("/api/project-clear/execute", json=body)
            assert result.status_code == 200, result.text
            assert client.post("/api/project-clear/execute", json=body).json() == result.json()
            assert (
                client.get(
                    "/api/project-clear/status", params={"request_id": body["request_id"]}
                ).json()
                == result.json()
            )
            assert (
                client.post(
                    path,
                    content='{"project_id":"x","project_id":"y"}',
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 400
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_immutable_protection_restored_and_denied_hook_does_not_publish(tmp_path):
    root, project, *_ = scene(tmp_path)
    perform(root, project)
    with closing(Store(root)) as store:
        other = seed_l1_graph(store)
        assert other != project
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute("DELETE FROM raw_events")
    before = list((root / "spool").glob("*"))
    result = subprocess.run(
        [sys.executable, "-m", "rg.hooks.entry", "claude", "SessionStart", "--data-dir", str(root)],
        input=b'{"session_id":"synthetic-private","hook_event_name":"SessionStart"}',
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr
    assert list((root / "spool").glob("*")) == before


def test_unattempted_model_working_set_and_manual_event_job_are_cleared(tmp_path):
    root, project, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        entity = store.db.execute(
            "SELECT entity_id FROM entities WHERE project_id=?", (project,)
        ).fetchone()[0]
        event = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
        store.db.execute(
            "INSERT INTO extraction_runs(job_key,stage,input_event_ids,working_set_ids,"
            "status,output_json,created_at) VALUES (?,'link','[]',?,'failed',?,?)",
            (str(uuid4()), dumps([entity]), MARKER, now()),
        )
        store.db.execute(
            "INSERT INTO jobs(kind,payload,state,updated_at) VALUES ('manual_review',?,'failed',?)",
            (dumps({"event_ids": [event], "text": MARKER}), now()),
        )
    _, _, result = perform(root, project)
    assert result["rows"]["extraction_runs"] == 2 and result["rows"]["jobs"] == 3


@pytest.mark.parametrize("kind", ["model", "job"])
def test_mixed_project_json_inputs_block_whole_clear(tmp_path, kind):
    root, project, other, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        entity = str(uuid4())
        store.db.execute(
            "INSERT INTO entities VALUES (?,?,'finding',NULL,?)", (entity, other, now())
        )
        if kind == "model":
            store.db.execute("UPDATE extraction_runs SET working_set_ids=?", (dumps([entity]),))
        else:
            store.db.execute(
                "UPDATE jobs SET payload=? WHERE kind='budget_pause'",
                (dumps({"project_id": project, "entity_ids": [entity]}),),
            )
    info = clear.preview(root, project)
    assert any("其他项目记录" in issue for issue in info["blockers"])
    with pytest.raises(ConflictError):
        clear.execute(root, project, str(uuid4()), info["preview_sha256"])
    assert not (root / clear.ACTIVE).exists()


@pytest.mark.parametrize("kind", ["model", "job"])
def test_unowned_outputs_fail_closed_instead_of_being_silently_left(tmp_path, kind):
    root, project, *_ = scene(tmp_path)
    with closing(Store(root)) as store:
        if kind == "model":
            store.db.execute(
                "INSERT INTO extraction_runs(job_key,stage,input_event_ids,status,"
                "created_at,output_json) VALUES (?,'unknown','[]','failed',?,?)",
                (str(uuid4()), now(), MARKER),
            )
        else:
            store.db.execute(
                "INSERT INTO jobs(kind,payload,state,updated_at) "
                "VALUES ('manual_review',?,'failed',?)",
                (dumps({"text": MARKER}), now()),
            )
    info = clear.preview(root, project)
    assert any("无法确定" in issue for issue in info["blockers"])
    with pytest.raises(ConflictError):
        clear.execute(root, project, str(uuid4()), info["preview_sha256"])


def test_partial_file_failure_retains_journal_and_resume_is_idempotent(tmp_path, monkeypatch):
    root, project, *_ = scene(tmp_path)
    info, request = clear.preview(root, project), str(uuid4())
    original = clear._managed_path
    calls = []

    def fail_second(*args):
        calls.append(args[1])
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return original(*args)

    monkeypatch.setattr(clear, "_managed_path", fail_second)
    with pytest.raises(OSError):
        clear.execute(root, project, request, info["preview_sha256"])
    assert len(calls) == 2 and not (root / calls[0]).exists()
    assert clear.status(root)["state"] == "pending"
    monkeypatch.setattr(clear, "_managed_path", original)
    assert clear.resume(root, request)["state"] == "complete"
    assert not (root / clear.ACTIVE).exists()


def test_backup_carries_denials_and_does_not_reimport_cleared_provider_log(tmp_path):
    root, project, _, _, source, *_ = scene(tmp_path)
    perform(root, project)
    destination = tmp_path / "backup"
    with closing(Store(root)) as store:
        backup(store, destination)
    with closing(Store(destination)) as restored:
        assert scan_file(restored, source, "claude") == {"privacy_blocked": 1}
    assert list((destination / "clear-records").glob("*.json"))


@pytest.mark.parametrize("pending", [False, True])
def test_real_cli_server_allows_clear_and_restart_resume(tmp_path, pending, monkeypatch):
    root, project, *_ = scene(tmp_path)
    info, request = clear.preview(root, project), str(uuid4())
    if pending:

        def stop(*args):
            raise OSError("synthetic interrupted finish")

        with monkeypatch.context() as patch:
            patch.setattr(clear, "_finish", stop)
            with pytest.raises(OSError):
                clear.execute(root, project, request, info["preview_sha256"])
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("synthetic")
    process = subprocess.Popen(
        [
            str(Path(sys.executable).with_name("rg")),
            "--data-dir",
            str(root),
            "serve",
            "--web-dir",
            str(web),
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert "本地界面" in process.stdout.readline()
        url = process.stdout.readline().strip()
        origin, token = url.split("/#token=")
        with httpx.Client(
            base_url=origin, trust_env=False, headers={"Authorization": "Bearer " + token}
        ) as client:
            if pending:
                assert client.get("/api/projects").status_code == 409
                response = client.post("/api/project-clear/resume", json={"request_id": request})
            else:
                response = client.post(
                    "/api/project-clear/execute",
                    json={
                        "project_id": project,
                        "request_id": request,
                        "preview_sha256": info["preview_sha256"],
                    },
                )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "complete"
    finally:
        process.kill()
        process.communicate(timeout=10)
