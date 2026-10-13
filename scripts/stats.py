#!/usr/bin/env python3
"""只输出统计数字：会话数量、大小、消息数和未识别记录。不打印任何会话内容。"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from rg.parse import NotASession, parse_file
from rg.sources import default_dirs, session_files


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dirs", nargs="*", help="会话目录；默认用自动发现的目录")
    cli.add_argument("--parse", type=int, default=0, help="另外完整解析最近的 N 个文件")
    args = cli.parse_args()
    roots = [Path(d) for d in args.dirs] or [Path(d["path"]) for d in default_dirs()]
    for root in roots:
        files = session_files(root)
        sizes = sorted(f.stat().st_size for f in files)
        if not sizes:
            print(f"{root}: 0 个文件")
            continue
        print(
            f"{root}: {len(files)} 个文件，合计 {sum(sizes) / 1e6:.0f} MB，"
            f"中位数 {sizes[len(sizes) // 2] / 1e6:.2f} MB，最大 {sizes[-1] / 1e6:.1f} MB"
        )
        unknown: Counter[str] = Counter()
        roles: Counter[str] = Counter()
        sub = 0
        for path in (
            sorted(files, key=lambda f: f.stat().st_mtime)[-args.parse :] if args.parse else []
        ):
            try:
                session = parse_file(path)
            except NotASession:
                unknown["not_a_session"] += 1
                continue
            sub += session.subagent
            unknown.update(session.unknown)
            roles.update(m.role for m in session.messages)
        if args.parse:
            print(
                f"  最近 {args.parse} 个：子 Agent {sub}，消息 {dict(roles)}，未识别 {dict(unknown)}"
            )


if __name__ == "__main__":
    main()
