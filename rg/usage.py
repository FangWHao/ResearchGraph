"""模型用量：每次调用记一笔，设置页按时间、项目和步骤汇总。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from rg.model import Meter, Usage
from rg.store import Store, now

STAGES = ("extract", "threads", "review", "brief")
DAYS = 30


def recorder(store: Store, project_id: int | None, stage: str) -> Meter:
    def record(usage: Usage | None) -> None:
        u = usage or Usage("", 0, 0)
        with store.tx() as db:
            db.execute(
                "INSERT INTO usage(at, project_id, stage, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens, cached) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now(),
                    project_id,
                    stage,
                    u.model,
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_read_tokens,
                    u.cache_write_tokens,
                    1 if usage is None else 0,
                ),
            )

    return record


def _bucket() -> dict[str, int]:
    return {"calls": 0, "cached": 0, "input": 0, "output": 0, "cache_read": 0}


def _add(bucket: dict[str, int], row: dict[str, Any]) -> None:
    if row["cached"]:
        bucket["cached"] += 1
        return
    bucket["calls"] += 1
    bucket["input"] += row["input_tokens"] + row["cache_read_tokens"] + row["cache_write_tokens"]
    bucket["output"] += row["output_tokens"]
    bucket["cache_read"] += row["cache_read_tokens"]


def local_day(at: str) -> date:
    return datetime.fromisoformat(at).astimezone().date()


def report(store: Store, today: date | None = None) -> dict[str, Any]:
    """按今天、近 7 天、近 30 天、全部，近 30 天每天，各项目，各步骤汇总。日期按本机时区。"""
    rows = store.q(
        "SELECT u.*, p.name FROM usage u LEFT JOIN projects p ON p.id = u.project_id ORDER BY u.at"
    )
    today = today or datetime.now().astimezone().date()
    periods = {"today": _bucket(), "week": _bucket(), "month": _bucket(), "all": _bucket()}
    days = {today - timedelta(days=n): _bucket() for n in range(DAYS - 1, -1, -1)}
    projects: dict[int | None, dict[str, Any]] = {}
    stages: dict[str, dict[str, int]] = defaultdict(_bucket)
    models: dict[str, dict[str, int]] = defaultdict(_bucket)
    for row in rows:
        day = local_day(row["at"])
        age = (today - day).days
        _add(periods["all"], row)
        for key, span in (("today", 1), ("week", 7), ("month", DAYS)):
            if 0 <= age < span:
                _add(periods[key], row)
        if day in days:
            _add(days[day], row)
        project = projects.setdefault(
            row["project_id"], {"id": row["project_id"], "name": row["name"], **_bucket()}
        )
        _add(project, row)
        project["last"] = row["at"]
        _add(stages[row["stage"]], row)
        if not row["cached"]:  # 命中本地缓存的没有发请求，不算到哪个模型上
            _add(models[row["model"] or "未知"], row)
    return {
        "since": rows[0]["at"] if rows else None,
        "periods": periods,
        "days": [{"day": day.isoformat(), **bucket} for day, bucket in days.items()],
        "projects": sorted(
            projects.values(), key=lambda p: (p["input"] + p["output"], p["cached"]), reverse=True
        ),
        "stages": [{"stage": s, **stages[s]} for s in STAGES if s in stages],
        "models": sorted(
            ({"model": name, **bucket} for name, bucket in models.items()),
            key=lambda m: m["input"] + m["output"],
            reverse=True,
        ),
    }
