"""旧注入来源资格界面验收；只使用公开合成原件和生产只读接口。"""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from rg.api.server import LocalServer
from rg.store.database import Store
from tests.injected_sources import legacy_candidate


def seed_injected_sources(store: Store) -> dict:
    cases = {}
    for key, tool, name, confirmed in [
        ("codex_candidate", "codex", "0.162-native-mcp", False),
        ("claude_candidate", "claude", "2.1.280-native-mcp", False),
        ("claude_confirmed", "claude", "2.1.280-native-mcp", True),
    ]:
        project, claim, event = legacy_candidate(store, tool, name, confirmed)
        span = store.db.execute(
            "SELECT e.* FROM claim_evidence c JOIN evidence_spans e USING(span_id) "
            "WHERE c.claim_id=?",
            (claim,),
        ).fetchone()
        assert span is not None
        raw = store.raw(event)
        assert raw[span["byte_start"] : span["byte_end"]] == b"budget"
        row = store.db.execute(
            "SELECT * FROM raw_events WHERE event_id=?", (event,)
        ).fetchone()
        assert row is not None and row["exclude_reason"] is None
        cases[key] = {
            "project_id": project,
            "claim_id": claim,
            "event_id": event,
            "span_id": span["span_id"],
            "byte_start": span["byte_start"],
            "byte_end": span["byte_end"],
            "quote_sha256": span["quote_sha256"],
            "raw_sha256": sha256(raw).hexdigest(),
            "total_bytes": len(raw),
            "occurred_at": row["occurred_at"],
            "recorded_at": row["recorded_at"],
        }
    assert len({case["project_id"] for case in cases.values()}) == 3
    assert len({case["event_id"] for case in cases.values()}) == 3
    return {"cases": cases, "synthetic_only": True}


def fingerprint(store: Store) -> str:
    return sha256("\n".join(store.db.iterdump()).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9804)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="researchgraph-injected-source-browser-") as folder:
        root = Path(folder) / "store"
        with closing(Store(root)) as store:
            index = seed_injected_sources(store)
            before = fingerprint(store)
        (cache / "frontend-injected-source-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(index, ensure_ascii=False), flush=True)
        server = LocalServer(
            root, args.web_dir, args.port, token="synthetic-injected-source-token"
        )
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            with closing(Store(root)) as store:
                after = fingerprint(store)
            audit = {
                "合成库查询前摘要": before,
                "合成库查询后摘要": after,
                "合成库逐表导出未改变": before == after,
            }
            (cache / "frontend-injected-source-audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(audit, ensure_ascii=False), flush=True)
            if before != after:
                raise RuntimeError("只读来源资格验收改变了合成库")


if __name__ == "__main__":
    main()
