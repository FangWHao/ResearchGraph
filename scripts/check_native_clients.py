"""用本机原生客户端和回环固定响应，核对生成接入包的五个只读工具。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from rg.clients.package import package
from rg.record.question import question
from rg.store.database import Store

DONE = "native-client-fixture-done"


def responses(index: int, step: tuple[str, dict] | None) -> list[tuple[str, dict]]:
    if step:
        name, arguments = step
        item = {
            "id": f"fc_fixture_{index}",
            "type": "function_call",
            "namespace": "mcp__researchgraph",
            "name": "research_" + name,
            "call_id": f"call_fixture_{index}",
            "arguments": json.dumps(arguments),
            "status": "completed",
        }
        events = [
            ("response.output_item.added", {"output_index": 0, "item": item}),
            ("response.output_item.done", {"output_index": 0, "item": item}),
        ]
    else:
        item = {
            "id": f"msg_fixture_{index}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": DONE, "annotations": []}],
        }
        events = [
            ("response.output_item.added", {"output_index": 0, "item": item}),
            ("response.output_text.delta", {"output_index": 0, "content_index": 0, "delta": DONE}),
            ("response.output_item.done", {"output_index": 0, "item": item}),
        ]
    # 用量只是协议占位值，不参与产品 token 计数或模型验收。
    response = {
        "id": f"resp_fixture_{index}",
        "object": "response",
        "status": "completed",
        "model": "researchgraph-fixture",
        "output": [item],
        "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
    }
    return [
        ("response.created", {"response": {**response, "status": "in_progress", "output": []}}),
        *events,
        ("response.completed", {"response": response}),
    ]


def messages(index: int, step: tuple[str, dict] | None) -> list[tuple[str, dict]]:
    if step:
        name, arguments = step
        block = {
            "type": "tool_use",
            "id": f"toolu_fixture_{index}",
            "name": "mcp__researchgraph__research_" + name,
            "input": {},
        }
        delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
    else:
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": DONE}
    return [
        (
            "message_start",
            {
                "message": {
                    "id": f"msg_fixture_{index}",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 1},
                }
            },
        ),
        ("content_block_start", {"index": 0, "content_block": block}),
        ("content_block_delta", {"index": 0, "delta": delta}),
        ("content_block_stop", {"index": 0}),
        (
            "message_delta",
            {
                "delta": {"stop_reason": "tool_use" if step else "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        ("message_stop", {}),
    ]


def gateway(
    client: str, steps: list[tuple[str, dict]], requests: list[dict]
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: Any) -> None:
            pass

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16 * 1024 * 1024:
                self.send_error(413)
                return
            data = json.loads(self.rfile.read(size))
            if "count_tokens" in self.path:
                body, content_type = b'{"input_tokens":100}', "application/json"
            else:
                requests.append(data)
                index = len(requests)
                step = steps[index - 1] if index <= len(steps) else None
                events = responses(index, step) if client == "codex" else messages(index, step)
                body = "".join(
                    f"event: {name}\ndata: {json.dumps({'type': name, **value})}\n\n"
                    for name, value in events
                ).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def verified_tools(client: str, stdout: bytes) -> list[str]:
    rows = [json.loads(line) for line in stdout.splitlines()]
    if client == "codex":
        items = [
            row["item"]
            for row in rows
            if row.get("type") == "item.completed"
            and row.get("item", {}).get("type") == "mcp_tool_call"
        ]
        assert len(items) == 5
        assert all(
            item["status"] == "completed"
            and item.get("error") is None
            and not item.get("result", {}).get("isError", False)
            and "<rg-context" in json.dumps(item.get("result"))
            for item in items
        ), "原生 Codex 工具返回失败或缺少标记"
        return [item["tool"].removeprefix("research.") for item in items]
    items = [
        block
        for row in rows
        if row.get("type") == "user"
        for block in row.get("message", {}).get("content", [])
        if block.get("type") == "tool_result"
    ]
    assert len(items) == 5
    assert all(
        item["tool_use_id"] == f"toolu_fixture_{index}"
        and not item.get("is_error", False)
        and "<rg-context" in json.dumps(item.get("content"))
        for index, item in enumerate(items, 1)
    ), "原生 Claude 工具返回失败或缺少标记"
    return ["context", "node", "history", "search", "evidence"]


def check_client(client: str, executable: str, base: Path, steps: list[tuple[str, dict]]) -> dict:
    profile = base / client
    profile.mkdir()
    workspace = base / "workspace"
    index_before = (workspace / ".git/index").read_bytes()
    requests: list[dict] = []
    server = gateway(client, steps, requests)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {k: v for k, v in os.environ.items() if k in {"HOME", "LANG", "LC_ALL", "TERM", "TMPDIR"}}
    env["PATH"] = os.environ["PATH"]
    try:
        if client == "codex":
            # 使用客户端自己的隔离配置目录，不改个人配置或持久信任状态。
            env.update(CODEX_HOME=str(profile), RG_NATIVE_FIXTURE_KEY="public-synthetic-fixture")
            config = f"""model = "researchgraph-fixture"
model_provider = "rg_fixture"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
model_reasoning_summary = "none"
[model_providers.rg_fixture]
name = "公开合成固定响应"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
env_key = "RG_NATIVE_FIXTURE_KEY"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
"""
            config += (base / "package/codex/.codex/config.toml").read_text()
            (profile / "config.toml").write_text(config)
            command = [executable, "exec", "--json", "--strict-config"]
        else:
            env.update(
                CLAUDE_CONFIG_DIR=str(profile),
                ANTHROPIC_API_KEY="public-synthetic-fixture",
                ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                DISABLE_AUTOUPDATER="1",
                DISABLE_TELEMETRY="1",
                DISABLE_ERROR_REPORTING="1",
            )
            command = [
                executable,
                "--restricted",
                "-p",
                "--tools",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                str(base / "package/claude/.mcp.json"),
                "--model",
                "claude-sonnet-4-5",
                "--system-prompt",
                "公开合成接入测试。",
                "--allowedTools",
                "mcp__researchgraph__*",
                "--output-format",
                "stream-json",
                "--verbose",
            ]
        result = subprocess.run(
            [*command, "公开合成测试：只回复固定完成标记。"],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=60,
        )
        (base / f"{client}-stdout.jsonl").write_bytes(result.stdout)
        (base / f"{client}-stderr.txt").write_bytes(result.stderr)
        assert result.returncode == 0, f"{client} 实际退出 {result.returncode}，参见隔离目录日志"
        assert len(requests) == 6, f"{client} 实际请求数不是六次"
        tools = verified_tools(client, result.stdout)
        assert tools == [name for name, _ in steps]
        assert index_before == (workspace / ".git/index").read_bytes()
        return {
            "client": client,
            "version": subprocess.check_output([executable, "--version"]).decode().strip(),
            "tools": tools,
            "requests": len(requests),
            "actual_exit": result.returncode,
            "index_unchanged": True,
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", default=shutil.which("codex"))
    parser.add_argument("--claude-bin", default=shutil.which("claude"))
    args = parser.parse_args()
    if not args.codex_bin or not args.claude_bin:
        parser.error("需要已安装的 Codex 和 Claude Code；本脚本不安装客户端")
    base = Path(tempfile.mkdtemp(prefix="researchgraph-native-clients-"))
    print(f"公开合成隔离目录：{base}", flush=True)
    workspace = base / "workspace"
    workspace.mkdir()
    (workspace / "fixture.txt").write_text("公开合成客户端接入资料\n")
    subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "add", "fixture.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Codex",
            "-c",
            "user.email=codex@users.noreply.github.com",
            "commit",
            "-m",
            "合成输入",
        ],
        check=True,
        capture_output=True,
    )
    store = Store(base / "data")
    try:
        project = store.project("公开合成原生客户端", [])
        receipt = question(
            store,
            {
                "project_id": project,
                "text": "如何核对公开合成客户端？",
                "actor": "human:合成测试",
                "request_id": str(uuid4()),
                "expected_revision": store.revision(),
            },
        )
        before = list(store.db.iterdump())
        package(store, project, base / "package", "cl100k_base")
        steps = [
            ("context", {"budget": 2000}),
            ("node", {"entity_id": receipt["entity_id"]}),
            ("history", {"entity_id": receipt["entity_id"]}),
            ("search", {"query": "公开合成"}),
            ("evidence", {"claim_id": receipt["claim_id"]}),
        ]
        results = []
        for client, executable in [("codex", args.codex_bin), ("claude", args.claude_bin)]:
            result = check_client(client, executable, base, steps)
            assert list(store.db.iterdump()) == before, "原生只读查询改变了数据库"
            result["database_unchanged"] = True
            results.append(result)
        proof = {"synthetic_only": True, "fixed_gateway": True, "results": results}
        (base / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        print(json.dumps(proof, ensure_ascii=False), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
