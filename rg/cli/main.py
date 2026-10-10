from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
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
    hook_config = commands.add_parser("hook-config", help="生成钩子示例与根目录清单，不安装钩子")
    hook_config.add_argument("--output", type=Path, required=True)
    snapshot = commands.add_parser("snapshot", help="手工拍影子快照，或实测 p95 决定钩子模式")
    snapshot.add_argument("--project", required=True)
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--benchmark", type=int, help="拍 5–100 次；p95 超过 300 ms 改为异步")
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
    scanned = commands.add_parser("scan", help="轮询显式来源与已导入路径，消费 spool；不调模型")
    scanned.add_argument(
        "--path", type=Path, action="append", default=[], help="登记持久扫描文件或目录"
    )
    scanned.add_argument("--tool", choices=["claude", "codex"])
    scanned.add_argument("--project", help="登记来源的显式项目归属")
    scanned.add_argument("--watch", action="store_true", help="持续扫描；Ctrl+C 停止")
    scanned.add_argument("--interval", type=float, default=2, help="轮询间隔秒数，默认 2")
    scanned.add_argument("--retry-failed", action="store_true", help="显式重试失败的 spool 提示")
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
    selection = extract.add_mutually_exclusive_group()
    selection.add_argument("--session", type=int, help="显式处理一个会话；省略时使用持久队列")
    selection.add_argument("--project", help="只自动处理这个项目；省略则处理所有已授权项目")
    mode = extract.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="仅实测计数和切片，不调用生成")
    mode.add_argument("--watch", action="store_true", help="持续扫描、派生和提取；Ctrl+C 停止")
    extract.add_argument("--interval", type=float, default=2, help="持续模式轮询间隔秒数")
    extract.add_argument("--limit", type=int, default=20, help="单轮自动处理会话数，1–200")
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
    asked = commands.add_parser("ask", help="先检索有限原文，再生成带引用的模型解释")
    asked.add_argument("question")
    asked.add_argument("--project", required=True)
    asked.add_argument("--k", type=int, default=12, help="最多读取证据条数，默认 12，1–100")
    asked.add_argument("--max-bytes", type=int, default=4000, help="每条证据的 UTF8 字节窗口")
    asked.add_argument("--occurred-until", help="发生截止，需明确时区")
    asked.add_argument("--known-until", help="已知截止，需明确时区")
    asked.add_argument("--expected-revision", type=int)
    asked.add_argument(
        "--retrieve-only", action="store_true", help="只读本地检索，不联网、不调用模型"
    )
    model_arguments(asked)
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
    derived = commands.add_parser("derive", help="从已复制的 L0 派生运行与编辑，不读当前文件")
    derived.add_argument("--session", type=int)
    derived.add_argument("--limit", type=int, default=100)
    derived.add_argument("--retry-failed", action="store_true")
    question = commands.add_parser("question", help="人工记录研究问题，保留原文；不调用模型")
    question.add_argument("text")
    question.add_argument("--project", required=True, help="明确项目 ID")
    question.add_argument("--actor", default="human:本机用户", help="human:人工身份")
    question.add_argument("--scope", nargs="+", action="extend", default=[], metavar="字段=值")
    question.add_argument("--request-id", help="重试同一意图的 UUID；默认新建")
    question.add_argument("--occurred-at", help="回填发生时间，需带明确时区")
    question.add_argument("--expected-revision", type=int, help="读取版本后提交，冲突则不写入")
    decided = commands.add_parser("decide", help="人工记录采用、暂缓、拒绝或撤回；歧义进复核")
    decided.add_argument("action", choices=["accept", "defer", "reject", "withdraw"])
    decided.add_argument("selector", help="现有对象的完整名称或 ID")
    decided.add_argument("--why", required=True)
    decided.add_argument("--project", required=True)
    decided.add_argument("--actor", default="human:本机用户")
    decided.add_argument("--scope", nargs="+", action="extend", default=[], metavar="字段=值")
    decided.add_argument("--request-id")
    decided.add_argument("--occurred-at")
    decided.add_argument("--expected-revision", type=int)
    choices = commands.add_parser("decision-targets", help="按字面量搜索当前项目可选对象，支持分页")
    choices.add_argument("--project", required=True)
    choices.add_argument("--query", default="")
    choices.add_argument("--scope", nargs="+", action="extend", default=[], metavar="字段=值")
    choices.add_argument("--limit", type=int, default=200)
    choices.add_argument("--offset", type=int, default=0)
    resolved = commands.add_parser(
        "resolve-decision", help="人工为待复核决定选择对象，保留原动作及范围"
    )
    resolved.add_argument("claim", type=int)
    resolved.add_argument("--target", required=True)
    resolved.add_argument("--actor", default="human:本机用户")
    resolved.add_argument("--request-id")
    resolved.add_argument("--expected-revision", type=int, required=True)
    contextual = commands.add_parser("context", help="读取项目状态卡；不调用模型、不修改记录")
    contextual.add_argument("--project", required=True)
    contextual.add_argument("--scope", nargs="+", action="extend", default=[], metavar="字段=值")
    contextual.add_argument("--budget", type=int, default=2000)
    contextual.add_argument("--limit", type=int, default=20)
    contextual.add_argument("--offset", type=int, default=0)
    contextual.add_argument("--occurred-until")
    contextual.add_argument("--known-until")
    mcp = commands.add_parser("mcp", help="启动指定项目的只读 stdio MCP，不打开网络端口")
    mcp.add_argument("--project", required=True)
    for command in (contextual, mcp):
        command.add_argument(
            "--encoding", choices=["cl100k_base", "o200k_base"], default="cl100k_base"
        )
        command.add_argument("--tokenizer-dir", type=Path)
    return cli


def run(args: argparse.Namespace, store: Store) -> object:
    if args.command == "context":
        from rg.mcp.tools import ToolService

        values = {"budget": args.budget, "limit": args.limit, "offset": args.offset}
        scope = parse_scope(args.scope)
        if scope is not None:
            values["scope"] = scope
        for key in ("occurred_until", "known_until"):
            if getattr(args, key) is not None:
                values[key] = getattr(args, key)
        service = ToolService(store, args.project, args.encoding, args.tokenizer_dir)
        return service.call("research.context", values)["content"][0]["text"]
    if args.command in {"decide", "resolve-decision"}:
        import uuid

        from rg.record.decide import decide
        from rg.record.resolve import resolve

        body = {
            "actor": args.actor,
            "request_id": args.request_id or str(uuid.uuid4()),
            "expected_revision": args.expected_revision
            if args.expected_revision is not None
            else store.revision(),
        }
        if args.command == "resolve-decision":
            return resolve(store, args.claim, body | {"target_id": args.target})
        return decide(
            store,
            body
            | {
                "project_id": args.project,
                "selector": args.selector,
                "action": args.action,
                "why": args.why,
                "scope": parse_scope(args.scope),
                "occurred_at": args.occurred_at,
            },
        )
    if args.command == "decision-targets":
        from rg.record.targets import targets
        from rg.store.database import dumps

        values = {
            "project": args.project,
            "q": args.query,
            "limit": str(args.limit),
            "offset": str(args.offset),
        }
        scope = parse_scope(args.scope)
        if scope is not None:
            values["scope"] = dumps(scope)
        return targets(store, values)
    if args.command == "question":
        import uuid

        from rg.record.question import question

        return question(
            store,
            {
                "project_id": args.project,
                "text": args.text,
                "actor": args.actor,
                "scope": parse_scope(args.scope),
                "request_id": args.request_id or str(uuid.uuid4()),
                "occurred_at": args.occurred_at,
                "expected_revision": (
                    args.expected_revision
                    if args.expected_revision is not None
                    else store.revision()
                ),
            },
        )
    if args.command == "derive":
        from rg.derive.worker import derive

        return derive(store, args.limit, args.session, args.retry_failed)
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
        from rg.snapshot.config import examples, export_registry

        export_registry(store)
        output = store.root / "hook-examples"
        if not output.exists():
            examples(store, output)
        return {
            "data_dir": str(store.root),
            "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
            "hook_examples": str(output),
            "hooks_installed": False,
        }
    if args.command == "hook-config":
        from rg.snapshot.config import examples

        return examples(store, args.output)
    if args.command == "snapshot":
        from rg.snapshot.cli import snapshot

        return snapshot(store, args.project, args.root, args.benchmark)
    if args.command == "project":
        if args.project_command == "add":
            from rg.snapshot.config import export_registry

            project_id = store.project(args.name, [args.root, *args.alias])
            export_registry(store)
            return {"project_id": project_id}
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
    if args.command == "scan":
        from rg.ingest.sources import register
        from rg.ingest.watch import cycle, watch
        from rg.store.database import dumps

        if args.path and not args.tool:
            raise ValueError("登记扫描来源必须指定 --tool")
        if not args.path and (args.tool or args.project):
            raise ValueError("--tool/--project 必须与 --path 一起指定")
        for path in args.path:
            register(store, path, args.tool, args.project)
        if args.watch:
            try:
                for result in watch(store, args.interval, args.retry_failed):
                    print(dumps(result), flush=True)
            except KeyboardInterrupt:
                return {"watch": "stopped"}
        return cycle(store, args.retry_failed)
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
    if args.command == "ask" and args.retrieve_only:
        from rg.query.context import wrap
        from rg.query.retrieval import retrieve

        return wrap(
            retrieve(store, args.project, args.question, args.k, _ask_values(args), args.max_bytes)
        )
    if args.command in {"extract", "link", "overview", "ask"}:
        scope = parse_scope(args.scope)
        if args.command == "ask" and args.daily_budget < 1:
            raise ValueError("每日额度须为正整数")
        if args.command == "extract":
            from rg.extract.queue import Queue

            Queue.validate_limit(args.limit)
            if args.watch and args.session is not None:
                raise ValueError("持续提取请按项目或全库运行，不能指定单个会话")
            if args.report and args.session is None:
                raise ValueError("报告必须指定单个会话")
            if args.daily_budget < 1:
                raise ValueError("每日额度须为正整数")
            if args.watch:
                import math

                if not math.isfinite(args.interval) or args.interval <= 0:
                    raise ValueError("轮询间隔必须为有限正数")
        if not args.base_url or not args.model:
            raise ValueError("必须提供 base URL 和模型名称")
        provider = Provider(args.base_url, args.model, load_key(args.key_file))
        try:
            worker = Worker(
                store, provider, input_budget=args.input_budget, daily_budget=args.daily_budget
            )
            if args.command == "ask":
                from rg.extract.qa import ask

                return ask(
                    worker, args.project, args.question, args.k, _ask_values(args), args.max_bytes
                )
            if args.command == "link":
                from rg.extract.linker import link

                return link(worker, args.project, args.limit, scope, args.retry_failed)
            if args.command == "overview":
                from rg.extract.overview import overview

                return overview(worker, args.project, args.output, args.session, scope)
            if args.estimate:
                from rg.extract.estimate import estimate_batch, estimate_session

                if args.session is not None:
                    return estimate_session(worker, args.session)
                return estimate_batch(worker, args.project, args.limit)
            if args.session is None:
                queue = Queue(worker, args.project, scope)
                if args.watch:
                    from rg.store.database import dumps

                    try:
                        for result in queue.watch(args.interval, args.limit, args.retry_failed):
                            print(dumps(result), flush=True)
                    except KeyboardInterrupt:
                        return {"watch": "stopped"}
                return queue.run(args.limit, args.retry_failed)
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


def _ask_values(args: argparse.Namespace) -> dict:
    values = {
        key: getattr(args, key) for key in ("occurred_until", "known_until", "expected_revision")
    }
    scope = parse_scope(args.scope)
    return values | ({"scope": scope} if scope is not None else {})


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    store = None
    try:
        store = Store(
            args.data_dir,
            readonly=args.command in {"mcp", "context"}
            or (args.command == "ask" and args.retrieve_only),
        )
        if args.command == "mcp":
            from rg.mcp.server import serve
            from rg.mcp.tools import ToolService

            # 项目校验在读取协议前完成；启动错误只写 stderr。
            from rg.query.reader import Reader

            Reader(store, args.project, {})
            serve(
                ToolService(store, args.project, args.encoding, args.tokenizer_dir),
                sys.stdin.buffer,
                sys.stdout.buffer,
            )
            return
        result = run(args, store)
        print(
            result
            if args.command in {"context", "ask"}
            else json.dumps(result, ensure_ascii=False, indent=2)
        )
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        cli.exit(1, f"错误：{error}\n")
    finally:
        if store is not None:
            store.close()
