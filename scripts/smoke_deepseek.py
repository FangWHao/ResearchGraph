#!/usr/bin/env python3
"""只使用仓库的合成 fixture 进行真实计数和提取测试，不接受真实会话路径。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from rg.extract.provider import Provider, load_key
from rg.extract.report import report
from rg.extract.worker import Worker
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from rg.store.objects import atomic_write


class RecordingProvider(Provider):
    def __init__(self, key: str):
        super().__init__("https://api.deepseek.com", "deepseek-flash", key)
        self.synthetic_outputs = []

    def generate(self, request):
        result = super().generate(request)
        try:
            self.synthetic_outputs.append(json.loads(result.text))
        except ValueError:
            self.synthetic_outputs.append({"invalid_json": True})
        return result


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--key-file", type=Path, default=Path("api_key"))
    cli.add_argument("--output", type=Path, default=Path("docs/acceptance/deepseek_smoke.json"))
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="rg-synthetic-") as folder:
        root = Path(folder)
        store = Store(root / "data")
        provider = RecordingProvider(load_key(args.key_file))
        try:
            project = store.project("合成接口验收项目", [root])
            # 用户已明确授权接口测试；仅本脚本内的合成项目允许远程调用。
            store.db.execute(
                "UPDATE projects SET remote_model_allowed = 1 WHERE project_id = ?", (project,)
            )
            sample = root / "synthetic.jsonl"
            records = [
                {
                    "type": "user",
                    "uuid": "synthetic-u1",
                    "sessionId": "synthetic-live",
                    "message": {"content": "采用方法甲，仅用于 data_v2 的 cnv 步骤。"},
                },
                {
                    "type": "user",
                    "uuid": "synthetic-u2",
                    "sessionId": "synthetic-live",
                    "message": {"content": "暂缓方法甲，原有阴性结果保留；方法乙暂不采用。"},
                },
            ]
            sample.write_text("".join(dumps(x) + "\n" for x in records))
            scan_file(store, sample, "claude", project)
            scope = {"dataset_version": "data_v2", "analysis_step": "cnv"}
            result = Worker(store, provider, daily_budget=50000).process(1, scope)
            runs = [
                dict(row)
                for row in store.db.execute(
                    "SELECT stage, model, measured_input_tokens, input_tokens, output_tokens, "
                    "stop_reason, status, error "
                    "FROM extraction_runs ORDER BY extraction_run_id"
                )
            ]
            result.update(
                {
                    "model": provider.model,
                    "synthetic_only": True,
                    "input_budget": 128000,
                    "model_context_window": provider.context_window,
                    "scope_consistent": all(
                        json.loads(row[0]) == scope
                        for row in store.db.execute("SELECT scope FROM claims")
                    ),
                    "pending": store.health()["pending"],
                    "runs": runs,
                    "all_claims_candidate": all(
                        row[0] == "candidate"
                        for row in store.db.execute("SELECT claim_state FROM claims")
                    ),
                }
            )
            if result["manual"] == 0 and result["claims"] > 0 and result["pending"] == 0:
                before = len(runs)
                repeated = Worker(store, provider, daily_budget=50000).process(1, scope)
                after = store.db.execute("SELECT count(*) FROM extraction_runs").fetchone()[0]
                result["cache_ok"] = repeated["claims"] == 0 and before == after
            else:
                result["cache_ok"] = False
            atomic_write(args.output, dumps(result).encode())
            atomic_write(
                args.output.with_name(args.output.stem + "_model_outputs.json"),
                dumps(provider.synthetic_outputs).encode(),
            )
            report(store, 1, args.output.with_suffix(".md"))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if (
                result["manual"]
                or not result["claims"]
                or result["pending"]
                or not result["cache_ok"]
                or not result["scope_consistent"]
            ):
                raise SystemExit(1)
        finally:
            provider.close()
            store.close()


if __name__ == "__main__":
    main()
