from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import httpx
import pytest

from rg.api.projects import create
from rg.api.server import LocalServer
from rg.store.roots import register


def body(tmp_path, **updates):
    work = tmp_path / "公开研究工作区"
    work.mkdir(exist_ok=True)
    return {"name": " 合成研究项目 ", "root": str(work), "collect_local": False} | updates


def test_create_and_replay_keep_one_project_without_model_hooks_or_log_access(store, tmp_path):
    request = body(tmp_path)
    before = store.revision()
    created = create(store, request)
    assert created["created"] and created["sources"] == []
    assert created["project"]["name"] == "合成研究项目"
    assert created["project"]["remote_model_allowed"] == 0
    assert created["project"]["sessions"] == created["project"]["claims"] == 0
    assert created["revision"] == before + 1
    assert not (Path(request["root"]) / ".codex").exists()
    assert store.db.execute("SELECT count(*) FROM ingest_sources").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0
    register(store, created["project"]["project_id"], Path(request["root"]) / "subset", "data")
    replay = create(store, request)
    assert not replay["created"] and replay["project"] == created["project"]
    assert store.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_explicit_local_sources_remain_shared_and_only_register_existing_directories(
    store, tmp_path, monkeypatch
):
    home = tmp_path / "公开用户目录"
    logs = home / ".codex/sessions"
    logs.mkdir(parents=True)
    transcript = logs / "public.jsonl"
    transcript.write_bytes(b"synthetic not read during registration\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    result = create(store, body(tmp_path, collect_local=True))
    assert [(r["tool"], r["state"]) for r in result["sources"]] == [
        ("claude", "missing"),
        ("codex", "registered"),
    ]
    source = store.db.execute("SELECT * FROM ingest_sources").fetchone()
    assert source["project_id"] is None and source["tool"] == "codex"
    assert transcript.read_bytes() == b"synthetic not read during registration\n"
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 0


def test_unavailable_source_does_not_lose_created_project_or_overwrite_existing_parser(
    store, tmp_path, monkeypatch
):
    from rg.ingest.sources import register as source_register

    home = tmp_path / "公开用户目录"
    logs = home / ".codex/sessions"
    logs.mkdir(parents=True)
    source_register(store, logs, "claude")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    result = create(store, body(tmp_path, collect_local=True))
    assert result["created"] and result["sources"][1]["state"] == "unavailable"
    assert store.db.execute("SELECT tool FROM ingest_sources").fetchone()[0] == "claude"
    assert not create(store, body(tmp_path, collect_local=True))["created"]


@pytest.mark.parametrize(
    "updates",
    [{"name": "\n"}, {"collect_local": 1}, {"root": "relative"}, {"root": "\0"}],
)
def test_invalid_requests_cannot_publish_partial_project(store, tmp_path, updates):
    with pytest.raises(ValueError):
        create(store, body(tmp_path, **updates))
    assert store.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM source_roots").fetchone()[0] == 0


def test_conflicting_name_and_overlapping_project_do_not_change_ownership(store, tmp_path):
    request = body(tmp_path)
    created = create(store, request)
    child = Path(request["root"]) / "subset"
    child.mkdir()
    before = list(store.db.iterdump())
    for changed in ({"name": "其它名字"}, {"root": str(child)}, {"root": str(tmp_path)}):
        with pytest.raises(ValueError):
            create(store, request | changed)
        assert list(store.db.iterdump()) == before
    assert created["project"]["project_id"]


def test_registration_failure_rolls_back_project_and_root(store, tmp_path, monkeypatch):
    import rg.api.projects as module

    def failed(*args):
        raise sqlite3.IntegrityError("synthetic conflict")

    monkeypatch.setattr(module, "insert", failed)
    with pytest.raises(ValueError, match="冲突"):
        create(store, body(tmp_path))
    assert store.db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM source_roots").fetchone()[0] == 0


def test_real_http_creation_requires_token_origin_and_rejects_duplicate_json(store, tmp_path):
    web = tmp_path / "public-web"
    web.mkdir()
    (web / "index.html").write_text("<title>公开合成界面</title>", encoding="utf-8")
    server = LocalServer(store.root, web, 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    headers = {"Authorization": f"Bearer {server.token}", "Origin": address}
    try:
        with httpx.Client(base_url=address, trust_env=False) as client:
            request = body(tmp_path)
            assert client.post("/api/projects", json=request).status_code == 401
            assert (
                client.post(
                    "/api/projects",
                    json=request,
                    headers=headers | {"Origin": "http://evil.invalid"},
                ).status_code
                == 403
            )
            malformed = client.post(
                "/api/projects",
                content='{"name":"one","name":"two"}',
                headers=headers | {"Content-Type": "application/json"},
            )
            assert malformed.status_code == 400
            result = client.post("/api/projects", json=request, headers=headers)
            assert result.status_code == 200 and result.json()["created"]
            assert not client.post("/api/projects", json=request, headers=headers).json()["created"]
            listed = client.get("/api/projects", headers=headers).json()
            assert listed["projects"][0]["project_id"] == result.json()["project"]["project_id"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
