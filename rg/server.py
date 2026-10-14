"""本机 HTTP 服务：JSON 接口与静态页面。只监听 127.0.0.1。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from rg.config import DB_NAME, DEFAULT_BASE_URL, DEFAULT_MODEL, clean_key, read_key, save_key
from rg.model import Call, Model, ModelError, cached
from rg.organize import (
    KINDS,
    Jobs,
    add_sessions,
    cleanup,
    estimate,
    fork_point,
    pending,
    search,
)
from rg.parse import NotASession
from rg.sources import default_dirs, read_info, scan, under, workspaces
from rg.store import Store, now
from rg.usage import report

MAX_UPLOAD = 1024 * 1024 * 1024
# 自动更新：打开的项目在会话安静 quiet 分钟后整理新内容；新内容超过 limit 字先问（None 表示不限）；
# 进入项目时发现离开期间的新内容，on_open = ask 先问，auto 直接更新。
AUTO_DEFAULTS: dict[str, Any] = {"enabled": True, "quiet": 3, "limit": 200_000, "on_open": "ask"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def web_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "_web", here.parent / "web" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


class App:
    def __init__(self, root: Path):
        self.root = root
        self.store = Store(root / DB_NAME)
        self.jobs = Jobs(self.store, self.roots, self.model)
        self._model: tuple[tuple[str, str, str], Call] | None = None
        self.transport: httpx.BaseTransport | None = None  # 测试时替换成假接口
        self._model_lock = threading.Lock()

    # ----- 设置 -----

    def roots(self) -> list[str]:
        return list(self.store.setting("sources", []))

    def auto_settings(self) -> dict[str, Any]:
        saved = self.store.setting("auto", {})
        return {**AUTO_DEFAULTS, **(saved if isinstance(saved, dict) else {})}

    def model_settings(self) -> dict[str, str]:
        return {
            "base_url": self.store.setting("base_url", DEFAULT_BASE_URL),
            "model": self.store.setting("model", DEFAULT_MODEL),
        }

    def model(self) -> Call | None:
        key = read_key(self.root)
        if not key:
            return None
        settings = self.model_settings()
        identity = (settings["base_url"], settings["model"], key)
        with self._model_lock:
            if self._model is None or self._model[0] != identity:
                client = Model(settings["base_url"], settings["model"], key, self.transport)
                tag = f"{settings['base_url']}|{settings['model']}"
                self._model = (identity, cached(self.store, client, tag))
            return self._model[1]

    # ----- 查询 -----

    def project(self, project_id: int) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if row is None:
            raise ApiError(404, "项目不存在")
        return row

    def event(self, event_id: int) -> dict[str, Any]:
        row = self.store.one("SELECT * FROM events WHERE id = ? AND hidden = 0", (event_id,))
        if row is None:
            raise ApiError(404, "事件不存在")
        return row

    def branch_in_project(self, branch_id: Any, project_id: int) -> int:
        row = self.store.one(
            "SELECT b.id FROM branches b JOIN threads t ON t.id = b.thread_id "
            "WHERE b.id = ? AND t.project_id = ?",
            (branch_id, project_id),
        )
        if row is None:
            raise ApiError(400, "分支不属于这个项目")
        return row["id"]

    def event_in_project(self, event_id: Any, project_id: int) -> int | None:
        if event_id is None:
            return None
        row = self.store.one(
            "SELECT id FROM events WHERE id = ? AND project_id = ? AND hidden = 0",
            (event_id, project_id),
        )
        if row is None:
            raise ApiError(400, "事件不属于这个项目")
        return row["id"]


# ----- 接口 -----

Handler = Callable[[App, re.Match[str], dict[str, Any], dict[str, list[str]]], Any]
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        ROUTES.append((method, re.compile(f"^{pattern}$"), func))
        return func

    return register


def _text(body: dict[str, Any], name: str, limit: int, required: bool = False) -> str | None:
    value = body.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ApiError(400, f"{name} 不能为空")
    return value.strip()[:limit]


@route("GET", "/api/state")
def get_state(app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]) -> Any:
    projects = app.store.q(
        "SELECT p.*, (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS sessions, "
        "(SELECT COUNT(*) FROM events e WHERE e.project_id = p.id AND e.hidden = 0) AS events "
        "FROM projects p ORDER BY p.id"
    )
    return {
        "sources": app.roots(),
        "detected": default_dirs(),
        "model": {**app.model_settings(), "has_key": read_key(app.root) is not None},
        "auto": app.auto_settings(),
        "projects": projects,
    }


def _auto(value: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiError(400, "auto 必须是对象")
    merged = dict(current)
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ApiError(400, "enabled 必须是 true 或 false")
        merged["enabled"] = value["enabled"]
    if "quiet" in value:
        quiet = value["quiet"]
        if not isinstance(quiet, int) or isinstance(quiet, bool) or not 1 <= quiet <= 120:
            raise ApiError(400, "quiet 必须是 1 到 120 分钟")
        merged["quiet"] = quiet
    if "limit" in value:
        limit = value["limit"]
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1000
        ):
            raise ApiError(400, "limit 必须是不少于 1000 的字数，或 null 表示不限")
        merged["limit"] = limit
    if "on_open" in value:
        if value["on_open"] not in ("ask", "auto"):
            raise ApiError(400, "on_open 只能是 ask 或 auto")
        merged["on_open"] = value["on_open"]
    return merged


@route("POST", "/api/settings")
def post_settings(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    auto = _auto(body["auto"], app.auto_settings()) if "auto" in body else None
    if "sources" in body:
        sources = body["sources"]
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise ApiError(400, "sources 必须是路径列表")
        cleaned = []
        for source in sources:
            path = Path(source.strip()).expanduser()
            if not path.is_dir():
                raise ApiError(400, f"目录不存在：{source}")
            if str(path) not in cleaned:
                cleaned.append(str(path))
        app.store.set_setting("sources", cleaned)
    for name in ("base_url", "model"):
        value = _text(body, name, 200)
        if value:
            app.store.set_setting(name, value.rstrip("/") if name == "base_url" else value)
    key = _text(body, "api_key", 500)
    if key:
        try:
            save_key(app.root, key)
        except ValueError as error:
            raise ApiError(400, str(error)) from None
    if auto is not None:
        app.store.set_setting("auto", auto)
    return get_state(app, m, {}, {})


@route("POST", "/api/settings/check")
def check_model(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """用填写中（或已保存）的 Key、接口和模型发一条极短的消息，不保存任何设置。"""
    settings = app.model_settings()
    base_url = _text(body, "base_url", 200) or settings["base_url"]
    model = _text(body, "model", 200) or settings["model"]
    typed = _text(body, "api_key", 500)
    key = clean_key(typed) if typed else read_key(app.root)
    if not key:
        return {"ok": False, "message": "API Key 格式不对" if typed else "还没有填写 API Key"}
    try:
        client = Model(base_url.rstrip("/"), model, key, app.transport, retries=0)
    except ValueError as error:
        return {"ok": False, "message": str(error)}
    try:
        name = client.check()
    except ModelError as error:
        return {"ok": False, "message": str(error)}
    finally:
        client.close()
    return {"ok": True, "message": f"可以使用（{name}）"}


@route("GET", "/api/sessions")
def get_sessions(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    infos = scan(app.store, app.roots())
    return {"sessions": [asdict(i) for i in infos], "workspaces": workspaces(infos)}


@route("POST", "/api/projects")
def create_project(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    name = _text(body, "name", 80, required=True)
    workspace = _text(body, "workspace", 1000) or None
    paths = body.get("sessions") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ApiError(400, "sessions 必须是路径列表")
    infos = []
    if workspace:
        infos = [i for i in scan(app.store, app.roots()) if under(i.cwd, workspace)]
    known = {i.path for i in infos}
    for path in paths:
        if path in known:
            continue
        try:
            infos.append(read_info(Path(path)))
        except (NotASession, OSError, UnicodeError):
            raise ApiError(400, f"不是可识别的会话文件：{path}") from None
    with app.store.tx() as db:
        cursor = db.execute(
            "INSERT INTO projects(name, workspace, follow, created_at) VALUES (?, ?, ?, ?)",
            (name, workspace, 1 if workspace and body.get("follow", True) else 0, now()),
        )
        project_id = int(cursor.lastrowid or 0)
    add_sessions(app.store, project_id, infos)
    if body.get("start", True):
        app.jobs.start(project_id)
    return {"id": project_id}


@route("POST", r"/api/projects/(\d+)/upload")
def upload(app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]) -> Any:
    project_id = int(m[1])
    app.project(project_id)
    data: bytes = body["_raw"]
    raw_name = (query.get("name") or ["session.jsonl"])[0].replace("\\", "/")
    name = re.sub(r"[^\w.\-]+", "_", Path(raw_name).name).lstrip(".")[-80:] or "session.jsonl"
    folder = app.root / "uploads" / str(project_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / f"{hashlib.sha256(data).hexdigest()[:16]}-{name}"
    target.write_bytes(data)
    try:
        info = read_info(target)
    except (NotASession, UnicodeError):
        target.unlink(missing_ok=True)
        raise ApiError(400, f"{name} 不是 Claude Code 或 Codex 会话") from None
    added = add_sessions(app.store, project_id, [info])
    return {"added": added, "title": info.title}


@route("PATCH", r"/api/projects/(\d+)")
def patch_project(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project = app.project(int(m[1]))
    name = _text(body, "name", 80, required="name" in body)
    follow = project["follow"]
    if "follow" in body:
        if not isinstance(body["follow"], bool):
            raise ApiError(400, "follow 必须是 true 或 false")
        if body["follow"] and not project["workspace"]:
            raise ApiError(400, "这个项目没有工作目录，没法跟随")
        follow = 1 if body["follow"] else 0
    with app.store.tx() as db:
        db.execute(
            "UPDATE projects SET name = COALESCE(?, name), follow = ? WHERE id = ?",
            (name, follow, project["id"]),
        )
    return app.project(project["id"])


@route("POST", r"/api/projects/(\d+)/sessions")
def add_project_sessions(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """把已发现的会话加进已有项目。手动加回来的会话不再算“移出过”。"""
    project_id = app.project(int(m[1]))["id"]
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise ApiError(400, "paths 必须是非空的路径列表")
    infos = []
    for path in paths:
        try:
            infos.append(read_info(Path(path)))
        except (NotASession, OSError, UnicodeError):
            raise ApiError(400, f"不是可识别的会话文件：{path}") from None
    with app.store.tx() as db:
        db.executemany(
            "DELETE FROM excluded WHERE project_id = ? AND path = ?",
            [(project_id, i.path) for i in infos],
        )
    return {"added": add_sessions(app.store, project_id, infos)}


@route("DELETE", r"/api/projects/(\d+)/sessions/(\d+)")
def remove_project_session(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """把会话移出项目：它的消息和事件一起删掉，会话文件不动；跟随目录时也不会自动加回来。"""
    project_id = app.project(int(m[1]))["id"]
    session = app.store.one(
        "SELECT id, path FROM sessions WHERE id = ? AND project_id = ?", (int(m[2]), project_id)
    )
    if session is None:
        raise ApiError(404, "会话不在这个项目里")
    if app.jobs.status(project_id)["running"]:
        raise ApiError(409, "正在整理，稍后再移出")
    with app.store.tx() as db:
        db.execute(
            "INSERT OR IGNORE INTO excluded(project_id, path) VALUES (?, ?)",
            (project_id, session["path"]),
        )
        # 从它 fork 出来的会话失去了原会话，不再当作 fork
        db.execute(
            "UPDATE sessions SET fork_of = NULL, fork_branch = NULL WHERE fork_of = ?",
            (session["id"],),
        )
        db.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
        cleanup(db, project_id)
    return {"ok": True}


@route("DELETE", r"/api/projects/(\d+)")
def delete_project(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project_id = int(m[1])
    app.project(project_id)
    if app.jobs.status(project_id)["running"]:
        raise ApiError(409, "正在整理，稍后再删除")
    with app.store.tx() as db:
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return {"ok": True}


def status(app: App, project_id: int) -> dict[str, Any]:
    view = app.jobs.status(project_id)
    if view["estimate"] is None:
        view["estimate"] = estimate(app.store, project_id)
    return view


@route("GET", r"/api/projects/(\d+)")
def get_project(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project_id = int(m[1])
    project = app.project(project_id)
    events = app.store.q(
        "SELECT id, session_id, branch_id, kind, title, summary, ts, anchor, refs, edited "
        "FROM events WHERE project_id = ? AND hidden = 0",
        (project_id,),
    )
    for event in events:
        event["refs"] = json.loads(event["refs"])
    sessions = app.store.q(
        "SELECT id, tool, path, title, cwd, started_at, ended_at, message_count, extracted_upto, "
        "missing, fork_of, copied FROM sessions WHERE project_id = ? ORDER BY started_at",
        (project_id,),
    )
    for session in sessions:
        point = fork_point(app.store, session) if session["fork_of"] else None
        session["fork_event"] = point["id"] if point else None
    return {
        "project": project,
        "sessions": sessions,
        "threads": app.store.q("SELECT * FROM threads WHERE project_id = ?", (project_id,)),
        "branches": app.store.q(
            "SELECT b.* FROM branches b JOIN threads t ON t.id = b.thread_id WHERE t.project_id = ?",
            (project_id,),
        ),
        "events": events,
        "status": status(app, project_id),
    }


@route("GET", r"/api/projects/(\d+)/status")
def get_status(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    app.project(int(m[1]))
    return status(app, int(m[1]))


@route("GET", r"/api/projects/(\d+)/pending")
def get_pending(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    app.project(int(m[1]))
    return pending(app.store, int(m[1]), app.roots())


@route("GET", r"/api/projects/(\d+)/search")
def get_search(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    app.project(int(m[1]))
    return search(app.store, int(m[1]), (query.get("q") or [""])[0][:200])


@route("POST", r"/api/projects/(\d+)/organize")
def post_organize(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project_id = int(m[1])
    project = app.project(project_id)
    if body.get("auto") is True:
        # 自动更新只用于已经同意过发送的项目，并受单次上限约束；不能顺带同意或重新整理。
        if not project["model_allowed"]:
            raise ApiError(409, "这个项目还没有同意发送给模型")
        if not app.jobs.start(project_id, auto=True, limit=app.auto_settings()["limit"]):
            raise ApiError(409, "这个项目正在整理")
        return status(app, project_id)
    if body.get("consent") is True:
        with app.store.tx() as db:
            db.execute("UPDATE projects SET model_allowed = 1 WHERE id = ?", (project_id,))
    if not app.jobs.start(project_id, full=body.get("full") is True):
        raise ApiError(409, "这个项目正在整理")
    return status(app, project_id)


@route("GET", "/api/usage")
def get_usage(app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]) -> Any:
    return report(app.store)


@route("PATCH", r"/api/events/(\d+)")
def patch_event(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    event = app.event(int(m[1]))
    changes: dict[str, Any] = {}
    if "title" in body:
        changes["title"] = _text(body, "title", 40, required=True)
    if "summary" in body:
        changes["summary"] = _text(body, "summary", 1000) or ""
    if "kind" in body:
        if body["kind"] not in KINDS:
            raise ApiError(400, "未知的事件种类")
        changes["kind"] = body["kind"]
    if "branch_id" in body:
        changes["branch_id"] = app.branch_in_project(body["branch_id"], event["project_id"])
    if body.get("hidden") is True:
        changes["hidden"] = 1
    if not changes:
        return {"ok": True}
    sets = ", ".join(f"{name} = ?" for name in changes)
    with app.store.tx() as db:
        db.execute(
            f"UPDATE events SET {sets}, edited = 1 WHERE id = ?", (*changes.values(), event["id"])
        )
        if "branch_id" in changes:
            db.execute("UPDATE branches SET edited = 1 WHERE id = ?", (changes["branch_id"],))
        cleanup(db, event["project_id"])
    return {"ok": True}


@route("GET", r"/api/projects/(\d+)/hidden")
def hidden_events(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project_id = app.project(int(m[1]))["id"]
    return {
        "events": app.store.q(
            "SELECT e.id, e.kind, e.title, e.summary, e.ts, s.title AS session_title "
            "FROM events e JOIN sessions s ON s.id = e.session_id "
            "WHERE e.project_id = ? AND e.hidden = 1 ORDER BY e.ts DESC, e.id DESC",
            (project_id,),
        )
    }


@route("POST", r"/api/events/(\d+)/restore")
def restore_event(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """恢复删掉的事件。原来的分支还在就回原处，否则跟着同一会话里最近的事件走，都没有就等下次归类。"""
    event = app.store.one("SELECT * FROM events WHERE id = ? AND hidden = 1", (int(m[1]),))
    if event is None:
        raise ApiError(404, "没有这个已删除的事件")
    branch = event["branch_id"]
    if branch is None:
        near = app.store.one(
            "SELECT branch_id FROM events WHERE session_id = ? AND hidden = 0 "
            "AND branch_id IS NOT NULL ORDER BY ABS(anchor - ?), id LIMIT 1",
            (event["session_id"], event["anchor"]),
        )
        branch = near["branch_id"] if near else None
    with app.store.tx() as db:
        db.execute(
            "UPDATE events SET hidden = 0, branch_id = ? WHERE id = ?", (branch, event["id"])
        )
    return {"ok": True, "branch_id": branch}


@route("GET", r"/api/events/(\d+)/messages")
def event_messages(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    event = app.event(int(m[1]))
    try:
        context = max(0, min(20, int((query.get("context") or ["0"])[0])))
        focus = int(query["focus"][0]) if query.get("focus") else None
    except ValueError:
        context, focus = 0, None
    refs = sorted(json.loads(event["refs"]))
    wanted: set[int] = set()
    for ref in refs:
        wanted.update(range(ref - context, ref + context + 1))
    if focus is not None:  # 从搜索结果点进来：把命中的那条和它前后各一条也带上
        wanted.update(range(focus - 1, focus + 2))
    rows = app.store.q(
        "SELECT idx, role, ts, text FROM messages WHERE session_id = ? ORDER BY idx",
        (event["session_id"],),
    )
    session = app.store.one(
        "SELECT id, tool, title, path, missing FROM sessions WHERE id = ?", (event["session_id"],)
    )
    return {
        "session": session,
        "messages": [
            {**r, "ref": r["idx"] in refs, "focus": r["idx"] == focus}
            for r in rows
            if r["idx"] in wanted
        ],
    }


@route("PATCH", r"/api/threads/(\d+)")
def patch_thread(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    title = _text(body, "title", 30, required="title" in body)
    summary = _text(body, "summary", 300)
    with app.store.tx() as db:
        db.execute(
            "UPDATE threads SET title = COALESCE(?, title), summary = COALESCE(?, summary), "
            "edited = 1 WHERE id = ?",
            (title, summary, int(m[1])),
        )
    return {"ok": True}


@route("POST", r"/api/threads/(\d+)/merge")
def merge_thread(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """把一条研究线并入另一条：它的各条分支搬过去，原来的主线成为一条以原名命名的分支。"""
    source = app.store.one("SELECT * FROM threads WHERE id = ?", (int(m[1]),))
    target = app.store.one("SELECT * FROM threads WHERE id = ?", (body.get("into"),))
    if source is None or target is None:
        raise ApiError(404, "研究线不存在")
    if source["project_id"] != target["project_id"] or source["id"] == target["id"]:
        raise ApiError(400, "只能并入同一个项目里的另一条研究线")
    with app.store.tx() as db:
        start = db.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?",
            (target["id"],),
        ).fetchone()[0]
        moved = db.execute(
            "SELECT id, title FROM branches WHERE thread_id = ? ORDER BY position, id",
            (source["id"],),
        ).fetchall()
        for offset, branch in enumerate(moved):
            title = branch["title"] or (source["title"] if offset == 0 else "")
            db.execute(
                "UPDATE branches SET thread_id = ?, position = ?, title = ?, edited = 1 WHERE id = ?",
                (target["id"], start + offset, title[:30], branch["id"]),
            )
        db.execute("DELETE FROM threads WHERE id = ?", (source["id"],))
        db.execute("UPDATE threads SET edited = 1 WHERE id = ?", (target["id"],))
    return {"ok": True}


@route("POST", r"/api/projects/(\d+)/threads")
def create_thread(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    project_id = int(m[1])
    app.project(project_id)
    title = _text(body, "title", 30, required=True)
    with app.store.tx() as db:
        tid = db.execute(
            "INSERT INTO threads(project_id, title, edited) VALUES (?, ?, 1)", (project_id, title)
        ).lastrowid
        bid = db.execute(
            "INSERT INTO branches(thread_id, title, position, edited) VALUES (?, '', 0, 1)", (tid,)
        ).lastrowid
    return {"thread_id": tid, "branch_id": bid}


@route("POST", r"/api/threads/(\d+)/branches")
def create_branch(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    thread = app.store.one("SELECT * FROM threads WHERE id = ?", (int(m[1]),))
    if thread is None:
        raise ApiError(404, "研究线不存在")
    title = _text(body, "title", 30, required=True)
    source = app.event_in_project(body.get("from_event"), thread["project_id"])
    with app.store.tx() as db:
        position = db.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM branches WHERE thread_id = ?",
            (thread["id"],),
        ).fetchone()[0]
        bid = db.execute(
            "INSERT INTO branches(thread_id, title, position, from_event, edited) "
            "VALUES (?, ?, ?, ?, 1)",
            (thread["id"], title, position, source),
        ).lastrowid
    return {"branch_id": bid}


@route("PATCH", r"/api/branches/(\d+)")
def patch_branch(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    row = app.store.one(
        "SELECT b.*, t.project_id FROM branches b JOIN threads t ON t.id = b.thread_id "
        "WHERE b.id = ?",
        (int(m[1]),),
    )
    if row is None:
        raise ApiError(404, "分支不存在")
    title = _text(body, "title", 30)
    source = row["from_event"]
    target = row["into_event"]
    if "from_event" in body:
        source = app.event_in_project(body["from_event"], row["project_id"])
    if "into_event" in body:
        target = app.event_in_project(body["into_event"], row["project_id"])
    with app.store.tx() as db:
        db.execute(
            "UPDATE branches SET title = COALESCE(?, title), from_event = ?, into_event = ?, "
            "edited = 1 WHERE id = ?",
            (title, source, target, row["id"]),
        )
    return {"ok": True}


@route("DELETE", r"/api/branches/(\d+)")
def delete_branch(
    app: App, m: re.Match[str], body: dict[str, Any], query: dict[str, list[str]]
) -> Any:
    """删掉一条分支，它的事件并入同一研究线里排在最前的另一条分支。"""
    row = app.store.one(
        "SELECT b.*, t.project_id FROM branches b JOIN threads t ON t.id = b.thread_id "
        "WHERE b.id = ?",
        (int(m[1]),),
    )
    if row is None:
        raise ApiError(404, "分支不存在")
    heir = app.store.one(
        "SELECT id FROM branches WHERE thread_id = ? AND id != ? ORDER BY position, id LIMIT 1",
        (row["thread_id"], row["id"]),
    )
    if heir is None:
        raise ApiError(409, "这是研究线里唯一的一条，不能删；可以把整条研究线并入别的研究线")
    with app.store.tx() as db:
        db.execute("UPDATE events SET branch_id = ? WHERE branch_id = ?", (heir["id"], row["id"]))
        db.execute("UPDATE branches SET edited = 1 WHERE id = ?", (heir["id"],))
        db.execute("DELETE FROM branches WHERE id = ?", (row["id"],))
        cleanup(db, row["project_id"])
    return {"ok": True}


# ----- HTTP -----


def find_route(method: str, path: str) -> tuple[Handler, re.Match[str]] | None:
    for allowed, pattern, func in ROUTES:
        match = pattern.match(path)
        if match and allowed == method:
            return func, match
    return None


def make_handler(app: App, port: int, static: Path | None) -> type[BaseHTTPRequestHandler]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "ResearchGraph"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send(
            self, status: int, data: bytes, content_type: str, cache: str = "no-store"
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def json(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send(status, data, "application/json; charset=utf-8")

        def allowed(self) -> bool:
            # Host 校验防 DNS 重绑定；修改类请求要求自定义头，跨站页面无法直接发出。
            if self.headers.get("Host") not in hosts:
                self.json(403, {"error": "只接受本机地址访问"})
                return False
            if self.command != "GET" and self.headers.get("X-RG") != "1":
                self.json(403, {"error": "缺少 X-RG 请求头"})
                return False
            return True

        def handle_any(self) -> None:
            if not self.allowed():
                return
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                if self.command == "GET":
                    self.static(url.path)
                else:
                    self.json(405, {"error": "不支持的请求"})
                return
            found = find_route(self.command, url.path)
            if found is None:
                self.json(404, {"error": "接口不存在"})
                return
            func, match = found
            try:
                body = self.body(url.path.endswith("/upload"))
                self.json(200, func(app, match, body, parse_qs(url.query)))
            except ApiError as error:
                self.json(error.status, {"error": str(error)})
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self.json(500, {"error": "服务内部错误"})

        def body(self, raw: bool) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > (MAX_UPLOAD if raw else 10 * 1024 * 1024):
                raise ApiError(413, "请求太大")
            data = self.rfile.read(length) if length else b""
            if raw:
                return {"_raw": data}
            if not data:
                return {}
            try:
                value = json.loads(data)
            except ValueError:
                raise ApiError(400, "请求不是合法 JSON") from None
            if not isinstance(value, dict):
                raise ApiError(400, "请求必须是 JSON 对象")
            return value

        def static(self, path: str) -> None:
            if static is None:
                self.send(
                    503,
                    "界面还没有构建：在 web 目录运行 pnpm build".encode(),
                    "text/plain; charset=utf-8",
                )
                return
            target = (static / path.lstrip("/")).resolve()
            if not target.is_relative_to(static) or not target.is_file():
                target = static / "index.html"
            cache = "no-cache" if target.name == "index.html" else "max-age=31536000, immutable"
            content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            self.send(200, target.read_bytes(), content_type, cache)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_any

    return RequestHandler


def serve(root: Path, port: int, open_browser: bool = False) -> None:
    app = App(root)
    static = web_dir()
    static = static.resolve() if static else None
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port, static))
    url = f"http://127.0.0.1:{port}/"
    print(f"ResearchGraph 已启动：{url}（数据目录 {root}）", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.store.close()
