from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from rg.store.database import Store, now


def set_status(
    store: Store,
    run: int,
    state: str,
    error: str | None = None,
    failure_kind: str | None = None,
    attempt_id: int | None = None,
) -> None:
    """原子更新运行与当前未结束的尝试；已结束的旧尝试不被重试覆盖。"""

    def write(db: sqlite3.Connection) -> None:
        db.execute(
            "UPDATE extraction_runs SET status=?, error=?, "
            "output_json=CASE WHEN ? IN ('ok','validated','lookup') THEN output_json END "
            "WHERE extraction_run_id=?",
            (state, error, state, run),
        )
        db.execute(
            "UPDATE model_attempts SET status=?, failure_kind=?, finished_at=? "
            "WHERE attempt_id=? AND extraction_run_id=? AND status IN ('pending','validated')",
            (
                state,
                failure_kind,
                None if state in {"pending", "validated"} else now(),
                attempt_id,
                run,
            ),
        )

    if store.db.in_transaction:
        write(store.db)
    else:
        with store.transaction() as db:
            write(db)


def _metrics(rows: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    alerts: list[dict[str, Any]] = []
    stages: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        stages[row["stage"]].append(row)
    metrics = []
    for stage, attempts in sorted(stages.items()):
        ratios = []
        high = 0
        segments: dict[tuple[str, str], bool] = {}
        for item in attempts:
            tokens = item["input_tokens"]
            if tokens is None:
                tokens = item["measured_input_tokens"]
            if tokens is not None:
                ratios.append(tokens / item["input_budget"])
                high += tokens * 5 > item["input_budget"] * 4
                key = (item["project_id"], item["segment_id"])
                segments[key] = segments.get(key, False) or tokens * 5 > item["input_budget"] * 4
        validated = [item for item in attempts if item["status"] in {"ok", "invalid", "lookup"}]
        rejected = sum(item["status"] == "invalid" for item in validated)
        citations = sum(item["failure_kind"] == "citation" for item in validated)
        metrics.append(
            {
                "stage": stage,
                "attempts": len(attempts),
                "sent": sum(item["sent"] for item in attempts),
                "statuses": dict(Counter(item["status"] for item in attempts)),
                "utilization_samples": len(ratios),
                "mean_utilization": sum(ratios) / len(ratios) if ratios else None,
                "max_utilization": max(ratios) if ratios else None,
                "over_80_percent": high,
                "over_80_percent_ratio": high / len(ratios) if ratios else None,
                "measured_segments": len(segments),
                "over_80_percent_segments": sum(segments.values()),
                "over_80_percent_segment_ratio": sum(segments.values()) / len(segments)
                if segments
                else None,
                "validation_samples": len(validated),
                "validation_rejections": rejected,
                "validation_rejection_rate": rejected / len(validated) if validated else None,
                "citation_failures": citations,
                "citation_failure_rate": citations / len(validated) if validated else None,
            }
        )
        if stage in {"pass1", "pass2"} and segments and sum(segments.values()) * 10 > len(segments):
            alerts.append(
                {
                    "code": "input_utilization",
                    "stage": stage,
                    "message": "超过 80% 输入预算的片段占比大于 10%，请调整切片参数",
                }
            )
        if validated and rejected * 20 > len(validated):
            alerts.append(
                {
                    "code": "validation_rejections",
                    "stage": stage,
                    "message": "校验拒绝率大于 5%，请检查提示词和切片；引用失败另列",
                }
            )
    return metrics, alerts


def _coverage(
    store: Store, project: str | None, limit: int, offset: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    alerts: list[dict[str, Any]] = []
    query = (
        "FROM coverage c JOIN raw_events r USING(event_id) JOIN sessions s USING(session_pk) "
        "WHERE c.status='pending' AND (? IS NULL OR s.project_id=?)"
    )
    pending = store.db.execute("SELECT count(*) " + query, (project, project)).fetchone()[0]
    gaps = store.db.execute(
        "SELECT c.event_id,r.session_pk,c.stage,c.segment_id "
        + query
        + " ORDER BY c.event_id,c.stage LIMIT ? OFFSET ?",
        (project, project, limit + 1, offset),
    ).fetchall()
    if pending:
        alerts.append(
            {
                "code": "coverage_gaps",
                "count": pending,
                "message": "仍有待覆盖的事件阶段，会话不能标为已处理",
            }
        )
    return {
        "pending_event_stages": pending,
        "gaps": [dict(row) for row in gaps[:limit]],
        "offset": offset,
        "next_offset": offset + limit if len(gaps) > limit else None,
    }, alerts


def _daily(
    store: Store, day: str, daily_budget: int
) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    alerts: list[dict[str, Any]] = []
    usage = store.db.execute(
        "SELECT reserved_tokens FROM daily_usage WHERE day=?", (day,)
    ).fetchone()
    consumed = usage[0] if usage else 0
    all_rows = store.db.execute(
        "SELECT sent,input_tokens,output_tokens FROM model_attempts "
        "WHERE COALESCE(usage_day,substr(created_at,1,10))=?",
        (day,),
    ).fetchall()
    known_input = sum(row["input_tokens"] or 0 for row in all_rows)
    known_output = sum(row["output_tokens"] or 0 for row in all_rows)
    unknown = sum(
        row["sent"] and (row["input_tokens"] is None or row["output_tokens"] is None)
        for row in all_rows
    )
    legacy = store.db.execute(
        "SELECT count(*) FROM extraction_runs r WHERE NOT EXISTS "
        "(SELECT 1 FROM model_attempts a WHERE a.extraction_run_id=r.extraction_run_id)"
    ).fetchone()[0]
    if consumed >= daily_budget:
        alerts.append({"code": "daily_limit", "message": "当天全库额度已达到本次配置的上限"})
    if unknown:
        alerts.append(
            {
                "code": "usage_unknown",
                "count": unknown,
                "message": "已尝试发送但实际用量不完整，保守预留仍可能占用额度",
            }
        )
    if legacy:
        alerts.append(
            {
                "code": "history_missing",
                "count": legacy,
                "message": "旧运行缺少尝试账本，失败率和实测用量只代表可观测的新尝试",
            }
        )
    return (
        {
            "scope": "all_projects",
            "budget_tokens": daily_budget,
            "reserved_or_settled_tokens": consumed,
            "remaining_tokens": max(0, daily_budget - consumed),
            "known_input_tokens": known_input,
            "known_output_tokens": known_output,
            "unsettled_sent_attempts": unknown,
        },
        legacy,
        alerts,
    )


def monitor(
    store: Store,
    project: str | None = None,
    day: str | None = None,
    daily_budget: int = 500000,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    day = now()[:10] if day is None else day
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("日期需为 YYYY-MM-DD，按 UTC 统计")
    if daily_budget < 1 or not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("每日额度必须为正数，页长 1 到 1000，偏移不能为负")
    if (
        project is not None
        and not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()
    ):
        raise ValueError("项目不存在")
    rows = store.db.execute(
        "SELECT * FROM model_attempts WHERE COALESCE(usage_day,substr(created_at,1,10))=? "
        "AND (? IS NULL OR project_id=?) ORDER BY attempt_id",
        (day, project, project),
    ).fetchall()
    metrics, alerts = _metrics(rows)
    states = Counter(row["status"] for row in rows)
    for state, message in {
        "truncated": "存在截断结果，结果已作废，请检查重试和覆盖缺口",
        "over_budget": "存在输入或输出预算违规，结果未采用",
        "contaminated": "存在执行器污染，结果已作废",
        "budget_paused": "存在每日额度暂停记录，请在额度恢复后继续任务",
        "failed": "存在计数或提供方失败，请检查服务与重试",
    }.items():
        if states[state]:
            alerts.append({"code": state, "count": states[state], "message": message})
    unfinished = sum(row["status"] in {"pending", "validated"} for row in rows)
    if unfinished:
        alerts.append(
            {
                "code": "unfinished_attempts",
                "count": unfinished,
                "message": "尝试缺少最终验收记录；此状态不能证明进程仍在运行",
            }
        )
    gaps, coverage_alerts = _coverage(store, project, limit, offset)
    daily_usage, legacy, usage_alerts = _daily(store, day, daily_budget)
    return {
        "day_utc": day,
        "project_id": project,
        "stages": metrics,
        "alerts": alerts + coverage_alerts + usage_alerts,
        "coverage": gaps,
        "daily_usage": daily_usage,
        "legacy_runs_without_attempt_history": legacy,
    }
