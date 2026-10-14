from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from rg.server import App, make_handler
from tests.conftest import FIXTURES, FakeModel
from tests.test_organize import batch_story


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr("rg.sources._homes", lambda: [])
    for name in ("RG_API_KEY", "RG_API_KEY_FILE", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    app = App(data)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    httpd.RequestHandlerClass = make_handler(app, httpd.server_port, None)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{httpd.server_port}", headers={"X-RG": "1"}, trust_env=False
    )
    yield app, client
    client.close()
    httpd.shutdown()
    httpd.server_close()
    app.store.close()


def sessions_dir(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    shutil.copy(FIXTURES / "claude" / "research-synthetic.jsonl", root / "claude.jsonl")
    shutil.copy(FIXTURES / "codex" / "research-synthetic.jsonl", root / "codex.jsonl")
    return root


def wait(app: App, project_id: int) -> dict:
    for _ in range(200):
        status = app.jobs.status(project_id)
        if not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("整理没有结束")


def test_rejects_foreign_host_and_requests_without_header(server):
    app, client = server
    assert client.get("/api/state", headers={"Host": "evil.example:80"}).status_code == 403
    response = httpx.post(
        str(client.base_url) + "/api/settings", json={"model": "x"}, trust_env=False
    )
    assert response.status_code == 403
    assert app.store.setting("model") is None


def test_first_open_to_timeline(server, tmp_path):
    app, client = server
    root = sessions_dir(tmp_path)
    state = client.post("/api/settings", json={"sources": [str(root)]}).json()
    assert state["sources"] == [str(root)] and state["model"]["has_key"] is False

    listing = client.get("/api/sessions").json()
    assert {w["cwd"] for w in listing["workspaces"]} == {
        "/workspace/demo",
        "/workspace/demo/analysis",
    }

    project_id = client.post(
        "/api/projects", json={"name": "demo", "workspace": "/workspace/demo"}
    ).json()["id"]
    status = wait(app, project_id)
    assert status["phase"] == "consent"  # 没有同意前不调用模型
    assert status["estimate"]["sessions"] == 2

    model = FakeModel(batch_story)
    app.jobs.model = lambda: model
    client.post(f"/api/projects/{project_id}/organize", json={"consent": True})
    assert wait(app, project_id)["phase"] == "done"
    data = client.get(f"/api/projects/{project_id}").json()
    assert len(data["sessions"]) == 2
    assert len(data["events"]) == 6
    assert all(e["branch_id"] for e in data["events"])
    assert {t["title"] for t in data["threads"]} >= {"去批次"}
    assert data["project"]["model_allowed"] == 1


def test_manual_edits(server, tmp_path):
    app, client = server
    root = sessions_dir(tmp_path)
    client.post("/api/settings", json={"sources": [str(root)]})
    project_id = client.post(
        "/api/projects", json={"name": "demo", "sessions": [str(root / "claude.jsonl")]}
    ).json()["id"]
    wait(app, project_id)
    app.jobs.model = lambda: FakeModel(batch_story)
    client.post(f"/api/projects/{project_id}/organize", json={"consent": True})
    wait(app, project_id)
    events = {e["title"]: e for e in client.get(f"/api/projects/{project_id}").json()["events"]}
    first = next(e for t, e in events.items() if t.startswith("先试"))

    assert client.patch(f"/api/events/{first['id']}", json={"title": "  "}).status_code == 400
    assert (
        client.patch(
            f"/api/events/{first['id']}", json={"kind": "decision", "title": "选 Harmony"}
        ).status_code
        == 200
    )
    created = client.post(f"/api/projects/{project_id}/threads", json={"title": "新方向"}).json()
    client.patch(f"/api/events/{first['id']}", json={"branch_id": created["branch_id"]})
    row = app.store.one("SELECT * FROM events WHERE id = ?", (first["id"],))
    assert (row["title"], row["kind"], row["branch_id"], row["edited"]) == (
        "选 Harmony",
        "decision",
        created["branch_id"],
        1,
    )

    messages = client.get(f"/api/events/{first['id']}/messages", params={"context": 1}).json()
    assert [m["ref"] for m in messages["messages"]] == [False, True, False]

    other = next(e for t, e in events.items() if t.startswith("也试试"))
    client.patch(
        f"/api/branches/{created['branch_id']}", json={"into_event": other["id"], "title": "x"}
    )
    assert (
        app.store.one("SELECT into_event FROM branches WHERE id = ?", (created["branch_id"],))[
            "into_event"
        ]
        == other["id"]
    )
    client.patch(f"/api/events/{other['id']}", json={"hidden": True})
    titles = [e["title"] for e in client.get(f"/api/projects/{project_id}").json()["events"]]
    assert not any(t.startswith("也试试") for t in titles)

    foreign = client.post("/api/projects", json={"name": "other", "start": False}).json()["id"]
    branch = client.post(f"/api/projects/{foreign}/threads", json={"title": "别处"}).json()[
        "branch_id"
    ]
    assert client.patch(f"/api/events/{first['id']}", json={"branch_id": branch}).status_code == 400


def test_upload_session_file(server):
    app, client = server
    project_id = client.post("/api/projects", json={"name": "upload", "start": False}).json()["id"]
    data = (FIXTURES / "codex" / "research-synthetic.jsonl").read_bytes()
    response = client.post(
        f"/api/projects/{project_id}/upload", params={"name": "../a b.jsonl"}, content=data
    )
    assert response.json() == {"added": 1, "title": "用 Harmony 的结果做细胞注释。"}
    saved = app.store.one("SELECT path FROM sessions")["path"]
    assert saved.startswith(str(app.root / "uploads")) and ".." not in os.path.basename(saved)
    bad = client.post(
        f"/api/projects/{project_id}/upload", params={"name": "x.jsonl"}, content=b'{"a": 1}\n' * 30
    )
    assert bad.status_code == 400
    assert len(list((app.root / "uploads" / str(project_id)).iterdir())) == 1


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX 权限位")
def test_api_key_is_saved_privately_and_never_returned(server):
    app, client = server
    response = client.post("/api/settings", json={"api_key": "sk-test123", "model": "m1"})
    assert "sk-test123" not in response.text
    model = response.json()["model"]
    assert (model["base_url"], model["model"], model["has_key"]) == (
        "https://api.deepseek.com/anthropic",
        "m1",
        True,
    )
    # 旧版本只存一个模型：三个阶段都用它
    assert {s["model"] for s in model["models"].values()} == {"m1"}
    mode = stat.S_IMODE((app.root / "api_key").stat().st_mode)
    assert mode == 0o600


def test_missing_web_build_and_unknown_routes(server):
    app, client = server
    assert client.get("/").status_code == 503
    assert client.get("/api/nope").status_code == 404
    assert client.post("/api/projects", content=b"[]").status_code == 400
    assert json.loads(client.get("/api/projects/99").text) == {"error": "项目不存在"}


def test_check_key_without_saving(server):
    app, client = server
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-api-key"])
        if request.headers["x-api-key"] == "sk-good":
            return httpx.Response(200, json={"model": "deepseek-flash"})
        return httpx.Response(401, json={"error": {"message": "bad key sk-bad"}})

    app.transport = httpx.MockTransport(handler)
    assert client.post("/api/settings/check", json={}).json() == {
        "ok": False,
        "message": "还没有填写 API Key",
    }
    good = client.post("/api/settings/check", json={"api_key": "sk-good"}).json()
    # 新装时三个阶段用推荐设置：提取 flash，归类和复审 pro；同一个模型只试一次
    assert (good["ok"], good["message"]) == (True, "可以使用（deepseek-flash、deepseek-v4-pro）")
    bad = client.post("/api/settings/check", json={"api_key": "sk-bad"}).json()
    assert bad["ok"] is False and "401" in bad["message"] and "sk-bad" not in bad["message"]
    assert not (app.root / "api_key").exists()  # 检测不保存
    assert (
        client.post(
            "/api/settings/check", json={"api_key": "sk-x", "base_url": "http://evil"}
        ).json()["message"]
        == "模型接口必须使用 HTTPS"
    )
    assert seen == ["sk-good", "sk-good", "sk-bad", "sk-bad"]


def test_auto_update_settings_are_validated_before_anything_is_saved(server, tmp_path):
    app, client = server
    defaults = client.get("/api/state").json()["auto"]
    assert defaults == {"enabled": True, "quiet": 3, "limit": 200_000, "on_open": "ask"}
    root = sessions_dir(tmp_path)
    bad = client.post("/api/settings", json={"sources": [str(root)], "auto": {"quiet": 0}})
    assert bad.status_code == 400 and app.roots() == []
    for wrong in ({"enabled": "yes"}, {"limit": 10}, {"on_open": "later"}):
        assert client.post("/api/settings", json={"auto": wrong}).status_code == 400
    saved = client.post(
        "/api/settings", json={"auto": {"enabled": False, "limit": None, "on_open": "auto"}}
    ).json()["auto"]
    assert saved == {"enabled": False, "quiet": 3, "limit": None, "on_open": "auto"}


def test_auto_update_needs_consent_and_respects_the_limit(server, tmp_path):
    app, client = server
    root = sessions_dir(tmp_path)
    client.post("/api/settings", json={"sources": [str(root)]})
    app.store.set_setting("auto", {"limit": 100})  # 合成会话很短，界面上最小只能选 1000
    project_id = client.post(
        "/api/projects", json={"name": "demo", "workspace": "/workspace/demo"}
    ).json()["id"]
    wait(app, project_id)
    model = FakeModel(batch_story)
    app.jobs.model = lambda: model

    # 没同意过的项目，自动更新不能顺带同意。
    refused = client.post(
        f"/api/projects/{project_id}/organize", json={"auto": True, "consent": True}
    )
    assert refused.status_code == 409
    assert client.get(f"/api/projects/{project_id}").json()["project"]["model_allowed"] == 0

    with app.store.tx() as db:
        db.execute("UPDATE projects SET model_allowed = 1")
    client.post(f"/api/projects/{project_id}/organize", json={"auto": True})
    status = wait(app, project_id)
    assert status["phase"] == "confirm" and status["auto"] is True and not model.calls

    client.post(f"/api/projects/{project_id}/organize", json={})  # 用户点“更新”，不受上限约束
    assert wait(app, project_id)["phase"] == "done"
    assert client.get(f"/api/projects/{project_id}/pending").json() == {
        "changed": 0,
        "new": 0,
        "chars": 0,
        "unplaced": 0,
        "quiet": None,
    }
    usage = client.get("/api/usage").json()
    assert usage["projects"][0]["name"] == "demo" and usage["periods"]["today"]["calls"] > 0


def organized_demo(app: App, client: httpx.Client, tmp_path) -> tuple[int, dict]:
    root = sessions_dir(tmp_path)
    client.post("/api/settings", json={"sources": [str(root)]})
    project_id = client.post(
        "/api/projects", json={"name": "demo", "sessions": [str(root / "claude.jsonl")]}
    ).json()["id"]
    wait(app, project_id)
    app.jobs.model = lambda: FakeModel(batch_story)
    client.post(f"/api/projects/{project_id}/organize", json={"consent": True})
    wait(app, project_id)
    return project_id, client.get(f"/api/projects/{project_id}").json()


def test_merge_research_lines_and_delete_branches_keep_every_event(server, tmp_path):
    app, client = server
    project_id, data = organized_demo(app, client, tmp_path)
    before = {e["id"] for e in data["events"]}
    main_thread = data["threads"][0]["id"]
    scvi = next(e for e in data["events"] if e["title"].startswith("也试试"))
    created = client.post(f"/api/projects/{project_id}/threads", json={"title": "新方向"}).json()
    client.patch(f"/api/events/{scvi['id']}", json={"branch_id": created["branch_id"]})

    merged = client.post(f"/api/threads/{created['thread_id']}/merge", json={"into": main_thread})
    assert merged.status_code == 200
    data = client.get(f"/api/projects/{project_id}").json()
    assert [t["id"] for t in data["threads"]] == [main_thread]
    moved = next(b for b in data["branches"] if b["id"] == created["branch_id"])
    assert (moved["thread_id"], moved["title"]) == (main_thread, "新方向")
    assert {e["id"] for e in data["events"]} == before

    # 删除分支：事件并入排在最前的分支，一个都不丢
    assert client.delete(f"/api/branches/{created['branch_id']}").status_code == 200
    data = client.get(f"/api/projects/{project_id}").json()
    assert {e["id"] for e in data["events"]} == before
    first = min(data["branches"], key=lambda b: b["position"])
    assert next(e for e in data["events"] if e["id"] == scvi["id"])["branch_id"] == first["id"]
    for branch in data["branches"][1:]:
        client.delete(f"/api/branches/{branch['id']}")
    assert client.delete(f"/api/branches/{first['id']}").status_code == 409  # 唯一的一条

    foreign = client.post("/api/projects", json={"name": "other", "start": False}).json()["id"]
    other = client.post(f"/api/projects/{foreign}/threads", json={"title": "别处"}).json()
    response = client.post(f"/api/threads/{other['thread_id']}/merge", json={"into": main_thread})
    assert response.status_code == 400


def test_hiding_a_fork_point_clears_the_link(server, tmp_path):
    app, client = server
    project_id, data = organized_demo(app, client, tmp_path)
    branch = next(b for b in data["branches"] if b["from_event"])
    client.patch(f"/api/events/{branch['from_event']}", json={"hidden": True})
    row = app.store.one("SELECT from_event FROM branches WHERE id = ?", (branch["id"],))
    assert row["from_event"] is None


def test_search_finds_events_and_the_quotes_behind_them(server, tmp_path):
    app, client = server
    project_id, data = organized_demo(app, client, tmp_path)
    by_title = {e["title"]: e["id"] for e in data["events"]}
    text = {e["id"]: (e["title"] + e["summary"]).lower() for e in data["events"]}

    def find(q: str) -> dict:
        return client.get(f"/api/projects/{project_id}/search", params={"q": q}).json()

    found = find("harmony")  # 英文不区分大小写
    assert set(found["events"]) == {i for i, t in text.items() if "harmony" in t}
    quote = next(h for h in found["messages"] if h["role"] == "user" and "先试" in h["snippet"])
    assert quote["event"] == next(i for t, i in by_title.items() if t.startswith("先试"))

    both = find("Harmony 采用")  # 几个词都要出现
    assert both["events"] == [i for i, t in text.items() if "harmony" in t and "采用" in t]
    assert find("%")["messages"] == [] and find("%")["events"] == []  # 通配符按字面匹配
    assert find("  ") == {"terms": [], "events": [], "messages": [], "more": False}

    shown = client.get(
        f"/api/events/{quote['event']}/messages", params={"focus": quote["idx"] + 1}
    ).json()["messages"]
    assert [m["idx"] for m in shown if m["focus"]] == [quote["idx"] + 1]


def test_project_sessions_can_be_added_removed_and_stay_removed(server, tmp_path):
    app, client = server
    root = sessions_dir(tmp_path)
    client.post("/api/settings", json={"sources": [str(root)]})
    project_id = client.post(
        "/api/projects", json={"name": "demo", "workspace": "/workspace/demo"}
    ).json()["id"]
    wait(app, project_id)
    app.jobs.model = lambda: FakeModel(batch_story)
    client.post(f"/api/projects/{project_id}/organize", json={"consent": True})
    wait(app, project_id)
    data = client.get(f"/api/projects/{project_id}").json()
    codex = next(s for s in data["sessions"] if s["tool"] == "codex")

    assert client.patch(f"/api/projects/{project_id}", json={"name": "改名了"}).json()["name"] == (
        "改名了"
    )
    removed = client.delete(f"/api/projects/{project_id}/sessions/{codex['id']}")
    assert removed.status_code == 200
    data = client.get(f"/api/projects/{project_id}").json()
    assert [s["tool"] for s in data["sessions"]] == ["claude"]
    assert all(e["session_id"] != codex["id"] for e in data["events"])
    # 跟随工作目录也不会自动加回来，也不算“有新内容”
    assert client.get(f"/api/projects/{project_id}/pending").json()["new"] == 0
    client.post(f"/api/projects/{project_id}/organize", json={})
    wait(app, project_id)
    assert len(client.get(f"/api/projects/{project_id}").json()["sessions"]) == 1

    added = client.post(f"/api/projects/{project_id}/sessions", json={"paths": [codex["path"]]})
    assert added.json() == {"added": 1}
    assert (
        client.post(
            f"/api/projects/{project_id}/sessions", json={"paths": [str(tmp_path / "nope.jsonl")]}
        ).status_code
        == 400
    )


def test_deleted_events_can_be_restored(server, tmp_path):
    app, client = server
    project_id, data = organized_demo(app, client, tmp_path)
    victim = data["events"][0]
    client.patch(f"/api/events/{victim['id']}", json={"hidden": True})
    hidden = client.get(f"/api/projects/{project_id}/hidden").json()["events"]
    assert [e["id"] for e in hidden] == [victim["id"]]
    assert client.post(f"/api/events/{victim['id']}/restore").json()["branch_id"] is not None
    events = client.get(f"/api/projects/{project_id}").json()["events"]
    assert victim["id"] in {e["id"] for e in events}
    assert client.post(f"/api/events/{victim['id']}/restore").status_code == 404


def test_event_outcome_and_importance_follow_the_kind(server, tmp_path):
    app, client = server
    project_id, data = organized_demo(app, client, tmp_path)
    attempt = next(e for e in data["events"] if e["kind"] == "attempt")
    url = f"/api/events/{attempt['id']}"
    assert client.patch(url, json={"outcome": "failed", "importance": 3}).status_code == 200
    assert client.patch(url, json={"outcome": "maybe"}).status_code == 400
    assert client.patch(url, json={"importance": 5}).status_code == 400
    client.patch(url, json={"kind": "decision"})
    row = app.store.one(
        "SELECT kind, outcome, importance FROM events WHERE id = ?", (attempt["id"],)
    )
    assert row == {"kind": "decision", "outcome": None, "importance": 3}
    client.patch(url, json={"kind": "attempt"})
    assert app.store.one("SELECT outcome FROM events WHERE id = ?", (attempt["id"],)) == {
        "outcome": "open"
    }


def test_each_stage_can_use_its_own_model_and_thinking(server, tmp_path):
    app, client = server
    bad = client.post(
        "/api/settings", json={"models": {"extract": {"model": "x", "thinking": "huge"}}}
    )
    assert bad.status_code == 400
    saved = client.post(
        "/api/settings",
        json={
            "models": {
                "extract": {"model": "deepseek-flash", "thinking": "off"},
                "threads": {"model": "deepseek-v4-pro", "thinking": "high"},
            }
        },
    ).json()["model"]["models"]
    assert saved["threads"] == {"model": "deepseek-v4-pro", "thinking": "high"}
    assert saved["review"] == {"model": "deepseek-v4-pro", "thinking": "off"}  # 没填的阶段保持原样
    (app.root / "api_key").write_text("sk-test", encoding="utf-8")
    calls = app.model()
    assert calls is not None and calls["extract"] is not calls["threads"]


def test_model_list_and_per_model_check(server):
    app, client = server
    pinged: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/anthropic/v1/models":
            return httpx.Response(404)
        if request.url.path == "/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "deepseek-flash",
                            "name": "DeepSeek-V4.1-Flash",
                            "context_window": 1048576,
                            "effort": {
                                "supported_levels": ["low", "high", "max"],
                                "default_level": "high",
                            },
                        },
                        {"id": "plain"},
                    ]
                },
            )
        body = json.loads(request.content)
        pinged.append(body["model"])
        if body["model"] == "missing":
            return httpx.Response(400, json={"error": {"message": "Model Not Exist"}})
        return httpx.Response(200, json={"model": body["model"]})

    app.transport = httpx.MockTransport(handler)
    listed = client.post("/api/settings/models", json={"api_key": "sk-x"}).json()
    assert listed["error"] is None
    assert listed["models"][0] == {
        "id": "deepseek-flash",
        "name": "DeepSeek-V4.1-Flash",
        "context_window": 1048576,
        "max_output_tokens": None,
        "thinking": ["low", "high", "max"],
        "default_thinking": "high",
    }
    assert listed["models"][1]["thinking"] == []
    checked = client.post(
        "/api/settings/check",
        json={
            "api_key": "sk-x",
            "models": {
                "extract": {"model": "deepseek-flash"},
                "threads": {"model": "missing"},
                "review": {"model": "deepseek-flash"},
            },
        },
    ).json()
    assert checked["ok"] is False and "missing" in checked["message"]
    assert pinged == ["deepseek-flash", "missing"]  # 同一个模型只试一次
