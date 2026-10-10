"""从生成的两种客户端配置启动官方 MCP 客户端，只使用临时合成项目。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import tomllib
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from mcp import Client, StdioServerParameters

from rg.clients.package import package
from rg.record.question import question
from rg.store.database import Store


async def check() -> None:
    cache = Path(__file__).resolve().parents[1] / ".cache"
    cache.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=cache, prefix="client-package-") as temporary:
        root = Path(temporary)
        store = Store(root / "合成数据 空格")
        try:
            project = store.project("接入包官方客户端合成项目", [])
            question(
                store,
                {
                    "project_id": project,
                    "text": "如何核对接入包？",
                    "actor": "human:合成测试",
                    "request_id": str(uuid4()),
                    "expected_revision": store.revision(),
                },
            )
            before = list(store.db.iterdump())
            output = root / "包"
            package(store, project, output, "cl100k_base")
            codex = tomllib.loads((output / "codex/.codex/config.toml").read_text())
            claude = json.loads((output / "claude/.mcp.json").read_text())
            for name, config in (
                ("codex", codex["mcp_servers"]["researchgraph"]),
                ("claude", claude["mcpServers"]["researchgraph"]),
            ):
                parameters = StdioServerParameters(command=config["command"], args=config["args"])
                async with Client(parameters) as client:
                    tools = await client.list_tools()
                    assert len(tools.tools) == 5
                    result = await client.call_tool("research.context", {"budget": 2000})
                    assert not result.is_error and result.content[0].type == "text"
                    text = result.content[0].text
                    assert text.startswith("<rg-context") and project in text
                    protocol = client.protocol_version
                assert list(store.db.iterdump()) == before
                results.append(
                    {
                        "client_config": name,
                        "protocol_version": protocol,
                        "tools": 5,
                        "context_ok": True,
                        "database_unchanged": True,
                    }
                )
        finally:
            store.close()
    proof = {"sdk_version": version("mcp"), "synthetic_only": True, "results": results}
    (cache / "client-package-sdk-proof.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2)
    )
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(check())
