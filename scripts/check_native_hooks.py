"""公开合成项目中实测本机原生钩子；隔离配置、回环固定响应，不改个人信任。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

from check_native_clients import gateway

from rg.hooks.install import install
from rg.store.database import Store


def check_client(client: str, executable: str, base: Path) -> dict:
    own = base / client
    own.mkdir()
    workspace = own / "workspace"
    workspace.mkdir()
    (workspace / "public.txt").write_text("公开合成钩子验收\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "add", "public.txt"], check=True)
    index = (workspace / ".git/index").read_bytes()
    profile = own / "profile"
    profile.mkdir()
    requests = []
    server = gateway(client, [], requests)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR"}
    }
    env["HOME"] = str(profile)
    try:
        with closing(Store(own / "data")) as store:
            project_id = store.project("公开合成原生钩子项目", [workspace])
            installed = install(store, project_id, workspace, client)
            assert installed["configured"] and installed["activation"] == "unknown"
            before = list(store.db.iterdump())
            if client == "codex":
                env.update(
                    CODEX_HOME=str(profile), RG_NATIVE_FIXTURE_KEY="public-synthetic-fixture"
                )
                config = f"""model = "researchgraph-fixture"
model_provider = "rg_fixture"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
model_reasoning_summary = "none"
[projects.{json.dumps(str(workspace))}]
trust_level = "trusted"
[model_providers.rg_fixture]
name = "公开合成固定响应"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
env_key = "RG_NATIVE_FIXTURE_KEY"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
"""
                (profile / "config.toml").write_text(config, encoding="utf-8")
                # 仅此隔离测试的一次调用：定义由本脚本生成、核对，不写个人持久信任。
                command = [
                    executable,
                    "exec",
                    "--json",
                    "--strict-config",
                    "--dangerously-bypass-hook-trust",
                ]
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
                    "-p",
                    "--tools",
                    "",
                    "--strict-mcp-config",
                    "--mcp-config",
                    '{"mcpServers":{}}',
                    "--model",
                    "claude-sonnet-4-5",
                    "--system-prompt",
                    "公开合成测试。",
                    "--output-format",
                    "stream-json",
                    "--verbose",
                ]
            start = time.monotonic()
            result = subprocess.run(
                [*command, "公开合成钩子测试：只回复固定完成标记。"],
                cwd=workspace,
                env=env,
                capture_output=True,
                timeout=60,
            )
            (own / "stdout.jsonl").write_bytes(result.stdout)
            (own / "stderr.txt").write_bytes(result.stderr)
            assert result.returncode == 0, f"{client} 实际退出 {result.returncode}；日志在隔离目录"
            assert len(requests) == 1, "固定响应不是一次实际请求"
            expected = {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"}
            events = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                files = list((store.root / "spool").glob("*.json"))
                events = [json.loads(path.read_bytes()).get("hook_event_name") for path in files]
                if expected.issubset(events):
                    break
                time.sleep(0.05)
            proof = {
                "client": client,
                "actual_exit": result.returncode,
                "version": subprocess.check_output([executable, "--version"]).decode().strip(),
                "seconds": round(time.monotonic() - start, 3),
                "requests": len(requests),
                "events": sorted(events),
                "required_events_present": expected.issubset(events),
                "index_unchanged": index == (workspace / ".git/index").read_bytes(),
                "database_unchanged": before == list(store.db.iterdump()),
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "trust_scope": "isolated_vetted_one_invocation"
                if client == "codex"
                else "isolated_noninteractive_workspace",
                "personal_configuration_modified": False,
            }
            (own / "proof.json").write_text(json.dumps(proof, ensure_ascii=False), encoding="utf-8")
            assert proof["required_events_present"], (
                f"{client} 缺实际事件：{expected - set(events)}"
            )
            assert proof["index_unchanged"] and proof["database_unchanged"]
            return proof
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", default=shutil.which("codex"))
    parser.add_argument("--claude-bin", default=shutil.which("claude"))
    parser.add_argument("--client", choices=["codex", "claude"])
    args = parser.parse_args()
    selected = [
        (client, executable)
        for client, executable in (("codex", args.codex_bin), ("claude", args.claude_bin))
        if args.client is None or args.client == client
    ]
    if any(not executable for _, executable in selected):
        parser.error("需要本机已安装的两个客户端，本脚本不安装客户端")
    base = Path(tempfile.mkdtemp(prefix="researchgraph-native-hooks-"))
    print(f"公开合成隔离目录：{base}", flush=True)
    results = [check_client(client, executable, base) for client, executable in selected]
    proof = {
        "synthetic_only": True,
        "remote_model_calls": 0,
        "fixed_loopback_gateway": True,
        "personal_configuration_modified": False,
        "clients": results,
    }
    (base / "proof.json").write_text(json.dumps(proof, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(proof, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
