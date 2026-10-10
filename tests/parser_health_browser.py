"""解析器健康界面的独立合成服务；只扫描临时构造的日志。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.store import migrations
from rg.store.database import Store


def write(path: Path, items: list[object], *, bad_json: bool = False) -> None:
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    if bad_json:
        text += "{合成坏JSON，不是合法记录}\n"
    path.write_text(text, encoding="utf-8")


def seed(root: Path, directory: Path) -> tuple[str, str]:
    latest = migrations.LATEST_VERSION
    try:
        migrations.LATEST_VERSION = 18
        legacy = Store(root)
        try:
            first = legacy.project("验收解析器账本项目", [Path("/synthetic/parser-health")])
            source = directory / "legacy.jsonl"
            write(
                source,
                [
                    {
                        "type": "user",
                        "sessionId": "synthetic-parser-legacy",
                        "message": {"content": "旧版本导入，过去没有类型账本"},
                    }
                ],
            )
            scan_file(legacy, source, "claude", first)
        finally:
            legacy.close()
    finally:
        migrations.LATEST_VERSION = latest

    store = Store(root)
    try:
        second = store.project("验收解析器账本项目二", [Path("/synthetic/parser-health-two")])
        source = directory / "claude-multi.jsonl"
        items: list[object] = [
            {
                "type": "assistant",
                "sessionId": "synthetic-parser-claude",
                "version": "2.1-synthetic",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "合成正文<script>window.parserInjected=true</script>",
                        },
                        {"type": "future_multi", "value": "同记录第一次"},
                        {"type": "future_multi", "value": "同记录第二次"},
                        {"type": "future_other", "value": "同记录另一类型"},
                    ]
                },
            }
        ]
        items += [
            {"type": f"future_record_{index:02}", "sessionId": "synthetic-parser-claude"}
            for index in range(26)
        ]
        items += [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "synthetic-unused-call",
                            "content": [
                                {"type": "text", "text": "合成工具回执"},
                                {"type": "future_result", "value": "未知结果内容"},
                            ],
                        },
                    ]
                },
            },
            [],
            {"type": "assistant", "message": ["合成结构异常"]},
        ]
        write(source, items, bad_json=True)
        scan_file(store, source, "claude", first)

        for tool in ("claude", "codex"):
            for basis in ("unknown", "invalid", "known"):
                source = directory / f"{tool}-{basis}.jsonl"
                value: object = ["非法版本"] if basis == "invalid" else "2.2.0-synthetic"
                if tool == "claude":
                    record = {
                        "type": "user",
                        "sessionId": f"synthetic-{tool}-{basis}",
                        "message": {"content": "合成工具版本记录"},
                    }
                    if basis != "unknown":
                        record["version"] = value
                    write(source, [record])
                else:
                    payload: dict[str, object] = {"id": f"synthetic-{tool}-{basis}"}
                    if basis != "unknown":
                        payload["cli_version"] = value
                    write(
                        source,
                        [
                            {"type": "session_meta", "payload": payload},
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "user",
                                    "version": "8.8.8-business",
                                    "content": [
                                        {"type": "input_text", "text": "合成Codex正文"},
                                        {"type": "future_codex", "value": "未知消息块"},
                                    ],
                                },
                            },
                            {
                                "type": "response_item",
                                "payload": {
                                    "type": "function_call_output",
                                    "call_id": "synthetic-output",
                                    "output": [
                                        {"type": "text", "text": "合成回执"},
                                        {"type": "future_output", "value": "未知回执块"},
                                    ],
                                },
                            },
                        ],
                    )
                scan_file(store, source, tool, first)

        source = directory / "secondary.jsonl"
        write(
            source,
            [
                {
                    "type": "user",
                    "sessionId": "synthetic-parser-second",
                    "message": {"content": "第二项目仅有一条合成原文"},
                }
            ],
        )
        scan_file(store, source, "claude", second)
        return first, second
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9796)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-parser-health-") as temp:
        directory = Path(temp)
        root = directory / "store"
        first, second = seed(root, directory)
        print(json.dumps({"project": first, "second": second}, ensure_ascii=False), flush=True)
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-parser-health-token")
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
