#!/usr/bin/env python3
"""只用脚本内合成候选测试链接和概览接口，不接受真实会话路径。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from rg.extract.linker import link
from rg.extract.overview import overview
from rg.extract.provider import load_key
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from rg.store.objects import atomic_write, digest
from scripts.smoke_deepseek import RecordingProvider


def seed(store: Store, root: Path, project: str) -> None:
    # 种子是直接构造的合成候选，不是人工参考，也不是模型提取成绩。
    for index in (1, 2):
        identity = f"synthetic-derived-{index}"
        text = f"采用方法甲；这是独立合成会话 {index}，条件为 data_v2。"
        source = root / (identity + ".jsonl")
        source.write_text(
            dumps(
                {
                    "type": "user",
                    "uuid": identity,
                    "sessionId": identity,
                    "message": {"content": text},
                }
            )
            + "\n"
        )
        scan_file(store, source, "claude", project)
        event = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
        raw = store.raw(event)
        start = raw.index(text.encode())
        payload = {"kind": "approach", "label": "方法甲", "content": "合成条件下的方法甲"}
        with store.transaction() as db:
            db.execute(
                "INSERT INTO entities (entity_id, project_id, kind, created_at) "
                "VALUES (?, ?, 'approach', ?)",
                (identity, project, now()),
            )
            claim = db.execute(
                "INSERT INTO claims (claim_type, entity_id, payload, scope, basis, actor, "
                "claim_state, recorded_at) VALUES ('entity_version', ?, ?, ?, "
                "'direct_record', 'synthetic:fixture', 'candidate', ?)",
                (identity, dumps(payload), dumps({"dataset_version": "data_v2"}), now()),
            ).lastrowid
            span = db.execute(
                "INSERT INTO evidence_spans (event_id,byte_start,byte_end,quote_sha256) "
                "VALUES (?, ?, ?, ?)",
                (event, start, start + len(text.encode()), digest(text.encode())),
            ).lastrowid
            db.execute("INSERT INTO claim_evidence VALUES (?, ?, 'support')", (claim, span))


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--key-file", type=Path, default=Path("api_key"))
    cli.add_argument(
        "--output", type=Path, default=Path("docs/acceptance/deepseek_derived_smoke.json")
    )
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="rg-synthetic-derived-") as folder:
        root = Path(folder)
        store = Store(root / "data")
        provider = RecordingProvider(load_key(args.key_file))
        try:
            project = store.project("合成链接概览验收", [root])
            store.db.execute(
                "UPDATE projects SET remote_model_allowed=1 WHERE project_id=?", (project,)
            )
            seed(store, root, project)
            worker = Worker(store, provider, daily_budget=50000)
            linked = link(worker, project)
            repeated = link(worker, project)
            destination = args.output.with_suffix(".md")
            summarized = overview(worker, project, destination)
            runs = [
                dict(row)
                for row in store.db.execute(
                    "SELECT stage,model,budget_tokens,measured_input_tokens,input_tokens, "
                    "output_tokens,stop_reason,status,error FROM extraction_runs "
                    "ORDER BY extraction_run_id"
                )
            ]
            result = {
                "synthetic_only": True,
                "fixture_seeded_claims": 2,
                "model": provider.model,
                "link": linked,
                "overview": summarized,
                "link_cache_ok": repeated["cached"] == linked["pairs"],
                "link_total_budget": 4000,
                "runs": runs,
                "attempts": [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT stage,status,failure_kind,sent,measured_input_tokens,input_tokens, "
                        "output_tokens,stop_reason FROM model_attempts ORDER BY attempt_id"
                    )
                ],
                "health": store.health(project, daily_budget=worker.daily_budget),
                "all_claims_candidate": all(
                    row[0] == "candidate"
                    for row in store.db.execute("SELECT claim_state FROM claims")
                ),
            }
            atomic_write(args.output, dumps(result).encode())
            atomic_write(
                args.output.with_name(args.output.stem + "_model_outputs.json"),
                dumps(provider.synthetic_outputs).encode(),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if (
                linked["manual"]
                or not linked["pairs"]
                or not result["link_cache_ok"]
                or not result["all_claims_candidate"]
                or not summarized["pages"]
            ):
                raise SystemExit(1)
        finally:
            provider.close()
            store.close()


if __name__ == "__main__":
    main()
