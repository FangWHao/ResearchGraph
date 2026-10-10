"""用官方客户端核对两代 stdio 协议；只创建临时合成项目。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from mcp import Client, StdioServerParameters

from rg.artifacts.worker import Worker
from rg.ingest.spool import consume, register
from rg.query.tokenizer import LocalCounter
from rg.record.manifest import record
from rg.snapshot.capture import MAX_FILE_BYTES, capture
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
            work = path / "work"
            work.mkdir()
            (work / "small.bin").write_bytes(b"saved\r\nbytes")
            (work / "large.bin").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
            project = store.project("官方客户端合成项目", [work])
            claim, event = add_object(store, path, project, "sdk-only")
            entity = store.db.execute(
                "SELECT entity_id FROM claims WHERE claim_id=?", (claim,)
            ).fetchone()[0]
            source_root = store.db.execute(
                "SELECT root_id FROM source_roots WHERE project_id=?", (project,)
            ).fetchone()[0]
            assert capture(store.root, project, source_root, work)["skipped"] is None
            register(store)
            consume(store, [])
            assert Worker(store).run()["done"] == 2
            physical = [dict(row) for row in store.db.execute("SELECT * FROM artifact_versions")]
            assert len(physical) == 2 and all(r["evidence_event_id"] is None for r in physical)
            manifest_id = str(uuid4())
            record(
                store,
                project,
                {
                    "request_id": manifest_id,
                    "run_id": "synthetic-report-run",
                    "inputs": [r["version_id"] for r in physical],
                },
            )
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
                    for name, arguments in (
                        [
                            ("search", {"query": "方法甲"}),
                            ("node", {"entity_id": entity}),
                            ("history", {"entity_id": entity}),
                            ("evidence", {"event_id": event, "max_bytes": 100}),
                            ("context", {"budget": 6000}),
                        ]
                        + [("evidence", {"version_id": r["version_id"]}) for r in physical]
                        + [
                            (
                                "evidence",
                                {"run_id": "synthetic-report-run", "manifest_id": manifest_id},
                            )
                        ]
                    ):
                        result = await client.call_tool("research." + name, arguments)
                        assert not result.is_error
                        block = result.content[0]
                        assert block.type == "text"
                        text = block.text
                        assert text.startswith("<rg-context")
                        payload = json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
                        if "version_id" in arguments:
                            assert payload["version"]["claim_state"] == "candidate"
                            assert payload["observations"]["total"] == 1
                            assert (
                                payload["reported_runs"]["items"][0]["claim_state"] == "candidate"
                            )
                        if "run_id" in arguments:
                            assert payload["run"] is None
                            report = payload["manifests"]["items"][0]
                            assert report["claim_state"] == "candidate"
                            assert report["actual_io_completeness"] == "unknown"
                            assert report["io"]["total"] == 2
                        if name == "context":
                            assert {r["version_id"] for r in physical} <= {
                                r.get("version_id") for r in payload["items"]
                            }
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
                        "physical_versions": len(physical),
                        "candidate_manifests": 1,
                        "reported_io": 2,
                        "inferred_run_io": store.db.execute(
                            "SELECT count(*) FROM run_io WHERE manifest_id IS NULL"
                        ).fetchone()[0],
                    }
                )
        finally:
            store.close()
    proof = {"sdk_version": version("mcp"), "synthetic_only": True, "results": results}
    (cache / "mcp-sdk-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(check())
