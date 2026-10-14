"""命令行入口：rg start / rg organize / rg sessions。"""

from __future__ import annotations

import argparse
import sys
import time

from rg.config import data_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rg", description="把 Claude Code / Codex 会话整理成研究时间线"
    )
    parser.add_argument("--data-dir", help="数据目录，默认 ~/.researchgraph")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="启动本机界面")
    start.add_argument("--port", type=int, default=8788)
    start.add_argument("--open", action="store_true", help="启动后打开浏览器")
    organize = commands.add_parser("organize", help="在命令行整理一个项目")
    organize.add_argument("project", type=int)
    organize.add_argument("--full", action="store_true", help="清掉自动生成的结构重新整理")
    organize.add_argument("--yes", action="store_true", help="同意把会话文字发送给模型")
    commands.add_parser("sessions", help="列出已发现的会话目录和工作目录")
    args = parser.parse_args(argv)
    root = data_dir(args.data_dir)

    if args.command == "start":
        from rg.server import serve

        serve(root, args.port, args.open)
        return 0

    from rg.server import App

    app = App(root)
    if args.command == "sessions":
        from rg.sources import default_dirs, scan, workspaces

        for found in default_dirs():
            print(f"{found['tool']:6} {found['files']:5} 个文件  {found['path']}")
        for group in workspaces(scan(app.store, app.roots()))[:30]:
            print(f"{group['sessions']:4} 个会话  {group['last'] or '':20}  {group['cwd']}")
        return 0

    app.project(args.project)
    if args.yes:
        with app.store.tx() as db:
            db.execute("UPDATE projects SET model_allowed = 1 WHERE id = ?", (args.project,))
    app.jobs.start(args.project, full=args.full)
    while (view := app.jobs.status(args.project))["running"]:
        print(f"\r{view['phase']} {view['done']}/{view['total']}", end="", flush=True)
        time.sleep(1)
    print(f"\r{view['phase']} {view['done']}/{view['total']}")
    for error in view["errors"]:
        print(error, file=sys.stderr)
    if view["phase"] == "consent":
        print(f"需要同意发送：{view['estimate']}。加 --yes 再运行。")
    return 1 if view["phase"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
