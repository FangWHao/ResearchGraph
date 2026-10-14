#!/usr/bin/env python3
"""在项目数据库的副本上试提取提示词：抽几段对话跑一次提取，把结果打印出来。

只往副本里写（重新解析的消息、模型缓存），不碰正式数据库。先用 --copy 从正式数据目录复制：

uv run python scripts/trial.py --data-dir /tmp/rg-trial --copy ~/.researchgraph --project 2 --sample 8
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rg.config import DB_NAME, DEFAULT_BASE_URL, DEFAULT_MODEL, data_dir, read_key
from rg.model import Model, cached
from rg.organize import (
    KIND_LABEL,
    OUTCOME_LABEL,
    _sizes,
    chunk_ranges,
    extract_range,
    sync_project,
)
from rg.store import Store

MARK = {"success": "✓", "failed": "✗", "open": "…"}


def copy_db(source: Path, target: Path) -> None:
    # 用 SQLite 的备份接口，正式服务开着也能拿到一致的快照
    src = sqlite3.connect(f"file:{source / DB_NAME}?mode=ro", uri=True)
    dst = sqlite3.connect(target / DB_NAME)
    src.backup(dst)
    src.close()
    dst.close()


def pick_chunks(
    store: Store, project_id: int, sessions: list[int], sample: int, seed: int
) -> list[tuple[dict[str, Any], int, int]]:
    rows = store.q("SELECT * FROM sessions WHERE project_id = ? ORDER BY started_at", (project_id,))
    if sessions:
        rows = [r for r in rows if r["id"] in sessions]
    chunks = []
    for row in rows:
        for a, b in chunk_ranges(_sizes(store, row["id"]), row["copied"]):
            chunks.append((row, a, b))
    rng = random.Random(seed)
    return sorted(
        rng.sample(chunks, min(sample, len(chunks))), key=lambda c: (c[0]["started_at"] or "", c[1])
    )


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", required=True, help="副本所在的数据目录")
    cli.add_argument("--copy", help="先从这个正式数据目录复制数据库")
    cli.add_argument("--key-dir", default="~/.researchgraph", help="从这里读 API Key")
    cli.add_argument("--project", type=int, required=True)
    cli.add_argument("--sessions", default="", help="只看这些会话 id，逗号分隔")
    cli.add_argument("--sample", type=int, default=8, help="抽几段对话")
    cli.add_argument("--seed", type=int, default=1)
    cli.add_argument("--sync", action="store_true", help="先按当前解析器重新读会话")
    cli.add_argument("--workers", type=int, default=8)
    cli.add_argument("--model", help="换一个模型试，默认用副本设置里的")
    args = cli.parse_args()

    root = data_dir(args.data_dir)
    if args.copy:
        copy_db(Path(args.copy).expanduser(), root)
    store = Store(root / DB_NAME)
    if args.sync:
        sync_project(store, args.project, list(store.setting("sources", [])))
    key = read_key(Path(args.key_dir).expanduser())
    if not key:
        raise SystemExit("没有找到 API Key")
    base_url = store.setting("base_url", DEFAULT_BASE_URL)
    model_name = args.model or store.setting("model", DEFAULT_MODEL)
    call = cached(store, Model(base_url, model_name, key), f"{base_url}|{model_name}")

    sessions = [int(s) for s in args.sessions.split(",") if s.strip()]
    chunks = pick_chunks(store, args.project, sessions, args.sample, args.seed)

    def run(chunk: tuple[dict[str, Any], int, int]) -> list[dict[str, Any]]:
        row, a, b = chunk
        messages = store.q(
            "SELECT idx, role, ts, text FROM messages WHERE session_id = ? ORDER BY idx",
            (row["id"],),
        )
        return extract_range(call, row, messages, a, b)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, chunks))

    total_msgs = 0
    total_events = 0
    for (row, a, b), events in zip(chunks, results, strict=True):
        total_msgs += b - a
        total_events += len(events)
        chars = sum(_sizes(store, row["id"])[a:b])
        print(
            f"\n=== S{row['id']}「{(row['title'] or '无标题')[:30]}」#{a}–#{b - 1}（{b - a} 条，{chars} 字）"
        )
        for e in events:
            mark = MARK.get(e["outcome"] or "", "")
            label = KIND_LABEL[e["kind"]] + mark
            print(f"  [{e['importance']}] {label} {e['title']}｜{e['summary']}")
        if not events:
            print("  （没有步骤）")
    print(f"\n共 {len(chunks)} 段、{total_msgs} 条消息 → {total_events} 个步骤")
    counts: dict[str, int] = {}
    for events in results:
        for e in events:
            key_ = f"{KIND_LABEL[e['kind']]}{OUTCOME_LABEL.get(e['outcome'] or '', '')}·{e['importance']}"
            counts[key_] = counts.get(key_, 0) + 1
    print("分布：", dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
