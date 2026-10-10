from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import socket
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from rg.api import qa, views
from rg.store.database import ConflictError, Store
from rg.store.locking import TaskBusy

MAX_BODY_BYTES = 65536


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        root: Path,
        web_dir: Path,
        port: int = 8787,
        daily_budget: int = 500000,
        token: str | None = None,
        qa_config: qa.QAConfig | None = None,
    ):
        if not 0 <= port <= 65535 or daily_budget < 1:
            raise ValueError("端口或每日额度无效")
        if not (web_dir / "index.html").is_file():
            raise ValueError("未找到前端构建；先在 web 运行 pnpm build，或使用 --web-dir")
        self.root, self.web_dir = root.resolve(), web_dir.resolve()
        self.daily_budget = daily_budget
        self.qa_config = qa_config
        self.token = token or secrets.token_urlsafe(32)
        self.timeout = 1
        # 不接受 host 参数，避免把研究资料意外暴露到公网。
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def browser_url(self) -> str:
        return f"{self.origin}/#token={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    _unread_body = False

    def log_message(self, format: str, *args: Any) -> None:
        # 查询文字、引用、启动令牌不写 HTTP 访问日志。
        return

    def finish(self) -> None:
        if self._unread_body:
            # 先发布拒绝响应，再有限清空未消费的请求体。直接关闭含未读数据的
            # TCP 连接会发送 RST，使客户端丢失已经写出的 401/403/413。
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 2 * MAX_BODY_BYTES
                remaining = min(max(length, 0), 2 * MAX_BODY_BYTES)
                deadline = time.monotonic() + 0.2
                while remaining and (wait := deadline - time.monotonic()) > 0:
                    self.connection.settimeout(wait)
                    chunk = self.rfile.read1(min(remaining, 8192))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
        super().finish()

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'",
        )
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except ConnectionError:
            self.close_connection = True

    def _json(self, status: int, value: Any) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False).encode(),
            "application/json; charset=utf-8",
        )

    def _host_allowed(self) -> bool:
        return self.headers.get("Host") == self.server.origin.removeprefix("http://")

    def _authorized(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if (
            not self._host_allowed()
            or (origin is not None and origin != self.server.origin)
            or site in {"cross-site", "same-site"}
        ):
            self._json(HTTPStatus.FORBIDDEN, {"error": "仅允许本机同源访问"})
            return False
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "访问令牌无效，请使用服务启动时的链接"})
            return False
        return True

    def _dispatch(self, path: str, values: dict[str, str], store: Store) -> Any:
        if path == "/api/projects":
            return views.projects(store)
        if path == "/api/privacy":
            from rg.store.privacy import read

            if set(values) != {"project"}:
                raise ValueError("遮盖配置仅需明确项目")
            with store.snapshot():
                return read(store, values["project"]).metadata(store)
        if path == "/api/qa/options":
            if set(values) != {"project"}:
                raise ValueError("问答配置仅需明确项目")
            return qa.options(store, values["project"], self.server.qa_config)
        if path == "/api/health":
            return views.health(store, values, self.server.daily_budget)
        if path == "/api/parser-health":
            from rg.query.parsers import query

            return query(store, values)
        if path == "/api/hook-errors":
            from rg.query.hook_errors import query

            return query(store, values)
        if path == "/api/session-parent":
            from rg.query.session_parent import query

            return query(store, values)
        if path == "/api/versions":
            from rg.artifacts.views import versions

            return versions(store, values)
        if path == "/api/version-diff":
            from rg.api.graph import parameters
            from rg.query.version_diff import query

            return query(store, values.get("project", ""), parameters(values))
        if path == "/api/run-evidence":
            from rg.api.runs import evidence

            return evidence(store, values)
        if path == "/api/claims":
            return views.claims(store, values)
        if match := re.fullmatch(r"/api/claims/([1-9][0-9]*)", path):
            return views.claim(store, int(match[1]))
        if match := re.fullmatch(r"/api/evidence/([1-9][0-9]*)", path):
            return views.evidence(store, int(match[1]), values)
        if path == "/api/search":
            return views.search(store, values)
        if path == "/api/graph":
            return views.graph(store, values.get("project", ""))
        if path == "/api/semantic-graph":
            from rg.api.graph import semantic

            return semantic(store, values)
        if path == "/api/l1-graph":
            from rg.api.graph import l1

            return l1(store, values)
        if path == "/api/l1-evidence":
            from rg.api.graph import source

            return source(store, values)
        if path == "/api/decision-targets":
            from rg.record.targets import targets

            return targets(store, values)
        raise views.NotFound("接口不存在")

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if not self._host_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"error": "请求主机无效"})
            return
        if parsed.path.startswith("/api/"):
            if not self._authorized():
                return
            try:
                raw_values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
                if any(len(items) != 1 for items in raw_values.values()):
                    raise ValueError("查询参数不得重复")
                values = {key: items[0] for key, items in raw_values.items()}
                if parsed.path == "/api/project-clear/status":
                    from rg.store.clear import status

                    if set(values) - {"request_id"}:
                        raise ValueError("清除状态查询参数无效")
                    self._json(HTTPStatus.OK, status(self.server.root, values.get("request_id")))
                    return
                store = Store(self.server.root, readonly=True)
                try:
                    store.db.execute("BEGIN")
                    result = self._dispatch(parsed.path, values, store)
                    store.db.commit()
                finally:
                    store.close()
                self._json(HTTPStatus.OK, result)
            except views.NotFound as error:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            except (ConflictError, TaskBusy) as error:
                self._json(HTTPStatus.CONFLICT, {"error": str(error)})
            except (ValueError, UnicodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except OSError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "原文对象或本地文件不可用"})
            except Exception:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "本地读取失败，请检查数据库"}
                )
            return
        path = unquote(parsed.path)
        target = (self.server.web_dir / path.lstrip("/")).resolve()
        if not target.is_relative_to(self.server.web_dir):
            self._json(HTTPStatus.FORBIDDEN, {"error": "路径越界"})
            return
        if path == "/":
            target = self.server.web_dir / "index.html"
        try:
            if not target.is_file():
                raise FileNotFoundError
            data = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, data, content_type)
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "静态资源不存在"})

    def do_POST(self) -> None:
        self._unread_body = True
        if not self._authorized():
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "不支持流式请求体"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "请求体需为 1 到 65536 字节"}
                )
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求体需使用 application/json")
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            self._unread_body = False
            path = urlsplit(self.path).path
            if path in {"/api/exports", "/api/privacy"} or path.startswith("/api/project-clear/"):
                from rg.clients.record import unique_pairs

                body = json.loads(raw, object_pairs_hook=unique_pairs)
            else:
                body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("请求体需为 JSON 对象")
            if path.startswith("/api/project-clear/"):
                from rg.store.clear import execute, preview, resume

                required = {
                    "/api/project-clear/preview": {"project_id"},
                    "/api/project-clear/execute": {"project_id", "request_id", "preview_sha256"},
                    "/api/project-clear/resume": {"request_id"},
                }.get(path)
                if required is None:
                    raise views.NotFound("清除接口不存在")
                if set(body) != required:
                    raise ValueError("清除请求字段无效")
                if path.endswith("/preview"):
                    result = preview(self.server.root, body["project_id"])
                elif path.endswith("/execute"):
                    result = execute(
                        self.server.root,
                        body["project_id"],
                        body["request_id"],
                        body["preview_sha256"],
                    )
                else:
                    result = resume(self.server.root, body["request_id"])
                self._json(HTTPStatus.OK, result)
                return
            store = Store(
                self.server.root,
                readonly=path in {"/api/qa/retrieve", "/api/qa/preview", "/api/exports"},
            )
            try:
                if path == "/api/privacy":
                    from rg.store.privacy import update

                    result = update(store, body)
                elif path == "/api/exports":
                    from rg.export.package import archive

                    _, data = archive(store, body)
                    self._send(HTTPStatus.OK, data, "application/zip")
                    return
                elif path == "/api/qa/retrieve":
                    result = {"context_text": qa.wrap(qa.packet(store, body))}
                elif path == "/api/qa/preview":
                    result = {
                        "context_text": qa.wrap(qa.preview(store, body, self.server.qa_config))
                    }
                elif path == "/api/qa/answer":
                    result = {
                        "context_text": qa.answer(
                            store, body, self.server.qa_config, self.server.daily_budget
                        )
                    }
                elif path == "/api/qa/allow-remote":
                    result = qa.allow(store, body, self.server.qa_config)
                elif path == "/api/qa/disable-remote":
                    result = qa.disable(store, body)
                elif path == "/api/review":
                    result = views.review(store, body)
                elif path == "/api/records/question":
                    from rg.record.question import question

                    result = question(store, body)
                elif path == "/api/records/decide":
                    from rg.record.decide import decide

                    result = decide(store, body)
                elif match := re.fullmatch(r"/api/records/decisions/([1-9][0-9]*)/resolve", path):
                    from rg.record.resolve import resolve

                    result = resolve(store, int(match[1]), body)
                elif match := re.fullmatch(r"/api/claims/([1-9][0-9]*)/edit", path):
                    result = views.edit(store, int(match[1]), body)
                else:
                    raise views.NotFound("写入接口不存在")
            finally:
                store.close()
            self._json(HTTPStatus.OK, result)
        except (ConflictError, TaskBusy) as error:
            self._json(HTTPStatus.CONFLICT, {"error": str(error)})
        except PermissionError:
            if path == "/api/qa/answer":
                self._json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "本项目尚未允许远程模型；请先查看发送预览"},
                )
            else:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "本地读写权限不足"})
        except qa.ModelUnavailable as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
        except views.NotFound as error:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        except (ValueError, UnicodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except TimeoutError:
            self._json(HTTPStatus.REQUEST_TIMEOUT, {"error": "请求体读取超时"})
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "本地写入失败，没有执行会话命令"}
            )

    def do_OPTIONS(self) -> None:
        self._json(HTTPStatus.FORBIDDEN, {"error": "不开放跨源接口"})


def serve(
    root: Path,
    web_dir: Path,
    port: int,
    daily_budget: int,
    open_browser: bool = False,
    initial_view: str = "questions",
    qa_config: qa.QAConfig | None = None,
) -> None:
    if initial_view not in {"questions", "review"}:
        raise ValueError("初始页面无效")
    server = LocalServer(root, web_dir, port, daily_budget, qa_config=qa_config)
    try:
        print("ResearchGraph 本地界面（Ctrl+C 停止）", flush=True)
        url = (
            server.browser_url
            if initial_view == "questions"
            else (f"{server.origin}/?view=review#token={server.token}")
        )
        print(url, flush=True)
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
