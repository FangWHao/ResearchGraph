#!/usr/bin/env python3
"""流式统计 Atlas 历史会话；终端只输出统计数字，不输出原话、标识或源路径。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def measure(roots: list[tuple[str, Path]], manifest: Path | None = None) -> dict[str, Any]:
    total: Counter[str] = Counter()
    selected = []
    for tool, root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            relevant = False
            counters: Counter[str] = Counter()
            with path.open("rb") as stream:
                for raw in stream:
                    counters["raw_bytes"] += len(raw)
                    counters["records"] += 1
                    try:
                        record = json.loads(raw)
                        if not isinstance(record, dict):
                            raise ValueError()
                    except (ValueError, UnicodeError):
                        counters["bad_lines"] += 1
                        continue
                    payload = record.get("payload") or {}
                    cwd = record.get("cwd") or (
                        payload.get("cwd") if isinstance(payload, dict) else ""
                    )
                    normalized = str(cwd or "").replace("\\", "/").rstrip("/").lower()
                    if any(
                        normalized == root_path or normalized.startswith(root_path + "/")
                        for root_path in ["/mnt/d/documents/atlas", "d:/documents/atlas"]
                    ):
                        relevant = True
                    if (
                        record.get("type") == "compacted"
                        or record.get("subtype") == "compact_boundary"
                    ):
                        counters["compactions"] += 1
            if relevant:
                total["sessions_files"] += 1
                total[tool + "_files"] += 1
                total.update(counters)
                selected.append(
                    {
                        "tool": tool,
                        "path": str(path),
                        "bytes": counters["raw_bytes"],
                        "records": counters["records"],
                    }
                )
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(selected, ensure_ascii=False, indent=2))
    result: dict[str, Any] = dict(total)
    result["token_counts"] = "未测量；本脚本不发送计数或模型请求"
    result["human_reference_decisions"] = "未标注"
    return result


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--claude", type=Path, default=Path.home() / ".claude/projects")
    cli.add_argument("--codex", type=Path, default=Path.home() / ".codex/sessions")
    cli.add_argument("--manifest", type=Path)
    args = cli.parse_args()
    print(
        json.dumps(
            measure([("claude", args.claude), ("codex", args.codex)], args.manifest),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
