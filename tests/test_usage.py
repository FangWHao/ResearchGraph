from __future__ import annotations

from datetime import datetime, timedelta

from rg.store import Store
from rg.usage import report


def add(store: Store, days_ago: int, project_id: int | None, stage: str, cached: int = 0) -> None:
    at = (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with store.tx() as db:
        db.execute(
            "INSERT INTO usage(at, project_id, stage, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cached) VALUES (?, ?, ?, 'm', ?, ?, ?, ?, ?)",
            (
                at,
                project_id,
                stage,
                0 if cached else 100,
                0 if cached else 10,
                0 if cached else 50,
                0,
                cached,
            ),
        )


def test_report_groups_by_period_day_project_and_stage(store):
    with store.tx() as db:
        a = db.execute("INSERT INTO projects(name, created_at) VALUES ('甲', 'now')").lastrowid
        b = db.execute("INSERT INTO projects(name, created_at) VALUES ('乙', 'now')").lastrowid
    add(store, 0, a, "extract")
    add(store, 0, a, "extract", cached=1)
    add(store, 3, a, "threads")
    add(store, 20, b, "extract")
    add(store, 45, b, "extract")
    with store.tx() as db:
        db.execute("DELETE FROM projects WHERE id = ?", (b,))  # 删掉项目，用量仍然算在总数里

    data = report(store, datetime.now().astimezone().date())
    periods = data["periods"]
    # 输入 = 未命中 + 命中缓存 + 写入缓存
    assert periods["today"] == {
        "calls": 1,
        "cached": 1,
        "input": 150,
        "output": 10,
        "cache_read": 50,
    }
    assert [periods[k]["calls"] for k in ("today", "week", "month", "all")] == [1, 2, 3, 4]
    assert len(data["days"]) == 30 and data["days"][-1]["calls"] == 1
    assert sum(d["calls"] for d in data["days"]) == 3
    by_name = {p["name"]: p for p in data["projects"]}
    assert by_name["甲"]["calls"] == 2 and by_name["甲"]["cached"] == 1
    assert by_name[None]["calls"] == 2  # 已删除的项目
    assert {s["stage"]: s["calls"] for s in data["stages"]} == {"extract": 3, "threads": 1}
    assert data["since"] is not None


def test_empty_report(store):
    data = report(store)
    assert data["since"] is None and data["projects"] == [] and data["periods"]["all"]["calls"] == 0
