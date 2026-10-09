from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rg.extract.provider import Provider, load_key
from rg.extract.redact import redact
from rg.extract.report import report
from rg.extract.worker import Worker
from rg.ingest.scanner import mark_deleted, scan_file
from rg.store.backup import backup
from rg.store.database import Store


def model_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--base-url", default=os.environ.get("RG_BASE_URL"))
    command.add_argument("--model", default=os.environ.get("RG_MODEL"))
    command.add_argument("--key-file", type=Path)
    command.add_argument("--daily-budget", type=int, default=500000)
    command.add_argument(
        "--input-budget", type=int, default=128000, help="完整输入上限，默认 128000 token"
    )
    command.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="字段=值",
        help="完整范围，可重复，不指定则不猜测范围",
    )


def parse_scope(fields: list[str]) -> dict[str, str] | None:
    scope: dict[str, str] = {}
    for field in fields:
        key, separator, value = field.partition("=")
        if not separator or not key or not value or key in scope:
            raise ValueError("范围需为不重复的非空 字段=值")
        scope[key] = value
    return scope or None


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="rg", description="可查证的研究决定史（后端试验版）")
    cli.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("RG_DATA_DIR", "~/.researchgraph")).expanduser(),
    )
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="建立数据库；不修改用户工具配置")
    project = commands.add_parser("project", help="登记项目与根目录")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    add = project_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--root", type=Path, required=True)
    add.add_argument("--alias", type=Path, action="append", default=[])
    allow = project_sub.add_parser("allow-remote", help="预览后明确允许该项目的远程计数与提取")
    allow.add_argument("project_id")
    allow.add_argument("--ack-preview", required=True, help="预览文件的 sha256")
    imported = commands.add_parser("import", help="按行导入指定文件或目录")
    imported.add_argument("path", type=Path)
    imported.add_argument("--tool", choices=["claude", "codex"], required=True)
    imported.add_argument("--project")
    health = commands.add_parser("health", help="本地查看覆盖缺口、模型用量与提取告警")
    health.add_argument("--project", help="仅筛选提取指标和覆盖缺口，日额度仍为全库")
    health.add_argument("--day", help="模型用量日期 YYYY-MM-DD，默认当天 UTC")
    health.add_argument("--daily-budget", type=int, default=500000, help="与 worker 使用同一日额度")
    health.add_argument("--limit", type=int, default=50, help="覆盖缺口页长")
    health.add_argument("--offset", type=int, default=0, help="覆盖缺口偏移")
    search = commands.add_parser("search", help="原文检索，用户文本按字面量处理")
    search.add_argument("text")
    search.add_argument("--limit", type=int, default=20)
    evidence = commands.add_parser("evidence", help="读取已复制进对象库的原文")
    evidence.add_argument("event_id", type=int)
    preview = commands.add_parser("preview", help="本地导出遮盖后的输入样例与字段，供外发授权")
    preview.add_argument("session", type=int)
    preview.add_argument("--output", type=Path, required=True)
    extract = commands.add_parser("extract", help="实测计数并有界提取；需项目允许外发")
    extract.add_argument("--session", type=int, required=True)
    extract.add_argument("--estimate", action="store_true", help="仅实测计数和切片，不调用生成")
    extract.add_argument(
        "--retry-failed", action="store_true", help="显式重试人工失败片段，保留历史尝试"
    )
    extract.add_argument("--report", type=Path)
    model_arguments(extract)
    linked = commands.add_parser("link", help="逐对提出跨会话关系，保持候选")
    linked.add_argument("--project", required=True)
    linked.add_argument(
        "--limit", type=int, default=50, help="本次新任务/重试上限，已完成缓存不占用"
    )
    linked.add_argument("--estimate", action="store_true", help="本地列候选对，不联网")
    linked.add_argument(
        "--retry-failed", action="store_true", help="本次重试已进入人工队列的失败对"
    )
    model_arguments(linked)
    summarized = commands.add_parser("overview", help="从 claims 生成带标记的模型摘要")
    summarized.add_argument("--project", required=True)
    summarized.add_argument("--session", type=int)
    summarized.add_argument("--output", type=Path, required=True)
    model_arguments(summarized)
    review = commands.add_parser("review", help="查看候选或人工确认/驳回（带乐观并发保护）")
    review.add_argument("--claim", type=int)
    review.add_argument("--action", choices=["confirm", "dismiss"])
    review.add_argument("--actor")
    review.add_argument("--expected-revision", type=int)
    review.add_argument("--open", action="store_true", help="启动并打开本地复核界面")
    review.add_argument("--port", type=int, default=8787)
    review.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    served = commands.add_parser("serve", help="启动仅监听 127.0.0.1 的研究记录界面")
    served.add_argument("--port", type=int, default=8787)
    served.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    served.add_argument("--daily-budget", type=int, default=500000)
    served.add_argument("--open", action="store_true", help="自动打开浏览器")
    backed = commands.add_parser("backup", help="使用 SQLite backup API 备份证据")
    backed.add_argument("destination", type=Path)
    return cli


def run(args: argparse.Namespace, store: Store) -> object:
    if args.command == "serve" or (args.command == "review" and args.open):
        from rg.api.server import serve

        serve(
            store.root,
            args.web_dir,
            args.port,
            getattr(args, "daily_budget", 500000),
            args.open,
            initial_view="review" if args.command == "review" else "questions",
        )
        return {"server": "stopped"}
    if args.command == "init":
        return {
            "data_dir": str(store.root),
            "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
        }
    if args.command == "project":
        if args.project_command == "add":
            return {"project_id": store.project(args.name, [args.root, *args.alias])}
        preview = store.db.execute(
            "SELECT 1 FROM remote_previews WHERE project_id = ? AND preview_sha256 = ?",
            (args.project_id, args.ack_preview),
        ).fetchone()
        if not preview:
            raise ValueError("必须先运行 preview，再用该预览的摘要明确授权")
        store.db.execute(
            "UPDATE projects SET remote_model_allowed = 1 WHERE project_id = ?", (args.project_id,)
        )
        return {"project_id": args.project_id, "remote_model_allowed": True}
    if args.command == "import":
        paths = sorted(args.path.rglob("*.jsonl")) if args.path.is_dir() else [args.path]
        results = [scan_file(store, path, args.tool, args.project) for path in paths]
        return {"files": len(paths), "events": sum(x.get("events", 0) for x in results)}
    if args.command == "health":
        mark_deleted(store)
        return store.health(args.project, args.day, args.daily_budget, args.limit, args.offset)
    if args.command == "search":
        return store.search(args.text, args.limit)
    if args.command == "evidence":
        return {"event_id": args.event_id, "raw": store.raw(args.event_id).decode("utf-8")}
    if args.command == "preview":
        from rg.store.database import dumps, now
        from rg.store.objects import atomic_write, digest

        session = store.db.execute(
            "SELECT project_id FROM sessions WHERE session_pk = ?", (args.session,)
        ).fetchone()
        if not session or not session[0]:
            raise ValueError("会话没有明确项目归属")
        rows = store.db.execute(
            "SELECT event_id, kind FROM raw_events WHERE session_pk = ? "
            "AND exclude_reason IS NULL ORDER BY seq LIMIT 3",
            (args.session,),
        ).fetchall()
        data = dumps(
            {
                "project_id": session[0],
                "fields": [
                    "event_id",
                    "kind",
                    "raw_windows",
                    "byte_start",
                    "byte_end",
                    "slim",
                    "working_set",
                    "scope",
                ],
                "samples": [
                    {
                        "event_id": r[0],
                        "kind": r[1],
                        "redacted_raw": redact(store.raw(r[0])).data.decode()[:1500],
                    }
                    for r in rows
                ],
            }
        ).encode()
        atomic_write(args.output, data)
        sha = digest(data)
        store.db.execute(
            "INSERT OR IGNORE INTO remote_previews VALUES (?, ?, ?)", (session[0], sha, now())
        )
        return {"preview": str(args.output), "preview_sha256": sha}
    if args.command == "link" and args.estimate:
        from rg.extract.linker import pairs

        candidates = pairs(store, args.project, args.limit, parse_scope(args.scope))
        return {
            "pairs": [
                {"pair_id": p.pair_id, "cards": p.cards, "basis": p.basis} for p in candidates
            ],
            "generation_calls": 0,
        }
    if args.command in {"extract", "link", "overview"}:
        scope = parse_scope(args.scope)
        if not args.base_url or not args.model:
            raise ValueError("必须提供 base URL 和模型名称")
        provider = Provider(args.base_url, args.model, load_key(args.key_file))
        try:
            worker = Worker(
                store, provider, input_budget=args.input_budget, daily_budget=args.daily_budget
            )
            if args.command == "link":
                from rg.extract.linker import link

                return link(worker, args.project, args.limit, scope, args.retry_failed)
            if args.command == "overview":
                from rg.extract.overview import overview

                return overview(worker, args.project, args.output, args.session, scope)
            if args.estimate:
                from rg.extract.segmenter import segment
                from rg.slim.slimmer import slim_session

                events = slim_session(store, args.session, provider)
                provider.validate_budget(worker.input_budget, worker.output_budget)
                items = segment(events, provider, worker.budgets.content_tokens)
                return {
                    "measured_slim_tokens": sum(x["tokens"] for x in events),
                    "segments": len(items),
                    "model": provider.model,
                    "generation_calls": 0,
                    "input_budget": worker.input_budget,
                    "content_budget": worker.budgets.content_tokens,
                }
            result = worker.process(args.session, scope, args.retry_failed)
            if args.report:
                report(store, args.session, args.report)
            return result
        finally:
            provider.close()
    if args.command == "review":
        if args.action:
            if args.claim is None or args.expected_revision is None or not args.actor:
                raise ValueError("复核必须提供 claim、actor 和 expected-revision")
            return {
                "revision": store.review(
                    args.claim, args.action, args.actor, args.expected_revision
                )
            }
        return {
            "revision": store.revision(),
            "claims": [
                {**dict(r), "effective_state": store.claim_state(r["claim_id"])}
                for r in store.db.execute("SELECT * FROM claims ORDER BY claim_id LIMIT 100")
            ],
        }
    if args.command == "backup":
        return backup(store, args.destination)
    raise ValueError("未知命令")


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    store = Store(args.data_dir)
    try:
        print(json.dumps(run(args, store), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, PermissionError) as error:
        cli.exit(1, f"错误：{error}\n")
    finally:
        store.close()
