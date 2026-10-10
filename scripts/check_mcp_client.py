"""用官方客户端核对两代 stdio 协议；只创建临时合成项目。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib.metadata import version
from pathlib import Path

from mcp import Client, StdioServerParameters

from rg.query.tokenizer import LocalCounter
from rg.store.database import Store
from tests.golden.test_links_overview import add_object


async def check() -> None:
    root = Path(__file__).resolve().parents[1]
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=cache, prefix="mcp-sdk-") as temporary:
        path = Path(temporary)
        store = Store(path / "data")
        try:
            project = store.project("官方客户端合成项目", [])
            claim, event = add_object(store, path, project, "sdk-only")
            entity = store.db.execute(
                "SELECT entity_id FROM claims WHERE claim_id=?", (claim,)
            ).fetchone()[0]
            before = list(store.db.iterdump())
            parameters = StdioServerParameters(
                command=str(root / ".venv/bin/rg"),
                args=["--data-dir", str(store.root), "mcp", "--project", project],
            )
            counter = LocalCounter()
            for mode in ("auto", "legacy"):
                calls = []
                async with Client(parameters, mode=mode) as client:
                    listed = await client.list_tools()
                    assert len(listed.tools) == 5
                    for name, arguments in [
                        ("search", {"query": "方法甲"}),
                        ("node", {"entity_id": entity}),
                        ("history", {"entity_id": entity}),
                        ("evidence", {"event_id": event, "max_bytes": 100}),
                        ("context", {"budget": 2000}),
                    ]:
                        result = await client.call_tool("research." + name, arguments)
                        assert not result.is_error
                        block = result.content[0]
                        assert block.type == "text"
                        text = block.text
                        assert text.startswith("<rg-context")
                        payload = json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
                        calls.append(
                            {
                                "tool": name,
                                "text_tokens": counter.count(text),
                                "has_content": bool(payload),
                            }
                        )
                    protocol = client.protocol_version
                assert list(store.db.iterdump()) == before
                results.append(
                    {
                        "mode": mode,
                        "protocol_version": protocol,
                        "tools": 5,
                        "queries": calls,
                        "logical_database_unchanged": True,
                    }
                )
        finally:
            store.close()
    proof = {"sdk_version": version("mcp"), "synthetic_only": True, "results": results}
    (cache / "mcp-sdk-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(check())
