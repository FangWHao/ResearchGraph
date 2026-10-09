from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from rg.extract.segmenter import Segment, segment
from rg.slim.slimmer import slim_session
from rg.slim.tokens import TokenCounter
from rg.store.database import Store, dumps, now
from rg.store.objects import digest


def register(store: Store, config: str, session: int, items: list[Segment]) -> None:
    """调用者持有事务；保存不可变的所有权窗口与原始重叠，不续接模型摘要。"""
    for item in items:
        store.db.execute(
            "INSERT OR IGNORE INTO extraction_plans "
            "(config_key,segment_id,session_pk,event_ids,segment_json,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'pending',?,?)",
            (config, item.segment_id, session, dumps(item.ids), dumps(asdict(item)), now(), now()),
        )


def complete(store: Store, config: str | None, segment_id: str, outcome: str) -> None:
    """与候选/无候选结果同事务完成；故障不能留下结果已提交、计划未完成的窗口。"""
    if config is None:
        return
    updated = store.db.execute(
        "UPDATE extraction_plans SET state='done',outcome=?,updated_at=? "
        "WHERE config_key=? AND segment_id=?",
        (outcome, now(), config, segment_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError("当前片段缺少持久计划，不能提交完成状态")
    for row in store.db.execute(
        "SELECT job_id,payload FROM jobs WHERE kind='manual_review' "
        "AND state IN ('failed','queued')"
    ).fetchall():
        data = json.loads(row["payload"])
        if data.get("config_key") == config and data.get("segment_id") == segment_id:
            store.db.execute(
                "UPDATE jobs SET state='done',error=NULL,updated_at=? WHERE job_id=?",
                (now(), row["job_id"]),
            )


def inventory(store: Store, config: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.db.execute(
            "SELECT * FROM extraction_plans WHERE config_key=? ORDER BY rowid", (config,)
        )
    ]


def cache_key(
    session: int,
    project: str,
    definition: list[Any],
    rows: list[dict[str, Any]],
    *,
    legacy: bool = False,
) -> str:
    identity = [(r["event_id"], r["object_sha256"], r["exclude_reason"]) for r in rows]
    data = [session, identity, *definition]
    return digest(dumps(data if legacy else [project, "progress-v1", *data]).encode())


def adopt_legacy(
    store: Store,
    config: str,
    session: int,
    project: str,
    definition: list[Any],
    snapshot: list[dict[str, Any]],
) -> None:
    # v4 没有片段元数据：只有精确匹配配置、项目和完整覆盖的旧缓存前缀能继承。
    closed = {
        r[0]
        for r in store.db.execute(
            "SELECT c.event_id FROM coverage c JOIN raw_events r USING(event_id) "
            "WHERE r.session_pk=? AND c.stage IN ('slim','pass1','pass2') "
            "GROUP BY c.event_id HAVING count(*)=3 AND sum(c.status='pending')=0",
            (session,),
        )
    }
    prefix = []
    for raw in snapshot:
        if raw["event_id"] not in closed:
            break
        prefix.append(raw)
    key = cache_key(session, project, definition, prefix, legacy=True)
    previous = store.db.execute(
        "SELECT 1 FROM session_results WHERE cache_key=? AND session_pk=?", (key, session)
    ).fetchone()
    if prefix and previous and legacy_project(store, session, project):
        with store.transaction():
            adopt(store, config, session, key, [r["event_id"] for r in prefix])


def restore(
    store: Store, plans: list[dict[str, Any]], coverage: Callable[[Segment, str, str], None]
) -> None:
    for plan in plans:
        if plan["state"] != "done":
            continue
        if plan["outcome"] == "legacy":
            with store.transaction() as db:
                for value in json.loads(plan["segment_json"]):
                    db.execute(
                        "UPDATE coverage SET status=?,segment_id=? WHERE event_id=? AND stage=?",
                        (value["status"], value["segment_id"], value["event_id"], value["stage"]),
                    )
        else:
            item = Segment(**json.loads(plan["segment_json"]))
            coverage(item, "pass1", "covered")
            coverage(item, "pass2", plan["outcome"])


def legacy_project(store: Store, session: int, project: str) -> bool:
    rows = store.db.execute(
        "SELECT DISTINCT m.project_id FROM model_attempts m JOIN extraction_runs e "
        "USING(extraction_run_id) WHERE m.stage IN ('pass1','pass2') AND EXISTS "
        "(SELECT 1 FROM json_each(e.input_event_ids) x JOIN raw_events r "
        "ON r.event_id=x.value WHERE r.session_pk=?)",
        (session,),
    ).fetchall()
    return bool(rows) and all(row[0] == project for row in rows)


def manual(store: Store, config: str | None, item: Segment, failures: int, error: str) -> None:
    payload = dumps(
        {
            "segment_id": item.segment_id,
            "event_ids": item.ids,
            "context_event_ids": [event["event_id"] for event in item.context_events],
            "context_gaps": item.context_gaps,
            "config_key": config,
        }
    )
    with store.transaction() as db:
        existing = db.execute(
            "SELECT job_id FROM jobs WHERE kind='manual_review' AND payload=?", (payload,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE jobs SET state='failed',attempts=max(attempts,?),error=?,"
                "updated_at=? WHERE job_id=?",
                (failures + 1, error, now(), existing[0]),
            )
        else:
            db.execute(
                "INSERT INTO jobs (kind,payload,state,attempts,error,updated_at) "
                "VALUES ('manual_review',?,'failed',?,?,?)",
                (payload, failures + 1, error, now()),
            )
        if config is not None:
            db.execute(
                "UPDATE extraction_plans SET state='manual',updated_at=? "
                "WHERE config_key=? AND segment_id=?",
                (now(), config, item.segment_id),
            )


def adopt(store: Store, config: str, session: int, cache: str, ids: list[int]) -> None:
    """只有旧缓存键与完整身份精确匹配后才能调用；保留既有阶段完成/排除原因。"""
    wanted = set(ids)
    statuses = [
        dict(row)
        for row in store.db.execute(
            "SELECT c.* FROM coverage c JOIN raw_events r USING(event_id) "
            "WHERE r.session_pk=? ORDER BY r.seq,c.stage",
            (session,),
        )
        if row["event_id"] in wanted
    ]
    if any(row["status"] == "pending" for row in statuses):
        raise RuntimeError("旧缓存前缀仍有覆盖缺口，不能继承完成状态")
    store.db.execute(
        "INSERT OR IGNORE INTO extraction_plans "
        "(config_key,segment_id,session_pk,event_ids,segment_json,state,outcome,"
        "created_at,updated_at) "
        "VALUES (?,?,?,?,?,'done','legacy',?,?)",
        (config, "legacy:" + cache, session, dumps(ids), dumps(statuses), now(), now()),
    )


def prepare(
    store: Store,
    session: int,
    snapshot: list[dict[str, Any]],
    reserved: set[int],
    counter: TokenCounter,
    budget: int,
) -> list[Segment]:
    excluded = {
        row["event_id"] for row in snapshot if row["exclude_reason"] or row.get("mirror_id")
    }
    # 排除事件没有所有权片段；不能把它当成永远未处理的新事件或回合边界。
    if excluded:
        slim_session(store, session, counter, excluded)
    new = {row["event_id"] for row in snapshot} - reserved - excluded
    if not new:
        return []
    first = next(index for index, row in enumerate(snapshot) if row["event_id"] in new)
    # 只加载紧邻的新/未完成内容及上一回合，不重新加载和计数整份历史。
    start = max(
        (
            i
            for i in range(first)
            if snapshot[i]["kind"] == "user_msg" and snapshot[i]["event_id"] not in excluded
        ),
        default=first,
    )
    prefix = {row["event_id"] for row in snapshot[start:first]}
    results = {row["call_id"] for row in snapshot[first:] if row["kind"] == "tool_result"}
    prefix.update(
        row["event_id"]
        for row in snapshot[:first]
        if row["call_id"] and row["call_id"] in results and row["kind"] != "tool_result"
    )
    events = slim_session(store, session, counter, new | prefix)
    result = []
    for item in segment(events, counter, budget):
        owned = [event for event in item.events if event["event_id"] in new]
        if not owned:
            continue
        old = [event for event in item.events if event["event_id"] not in new]
        if old:
            item.context_events.extend(old)
            item.events = owned
            item.segment_id = digest(dumps([item.content(), item.context_gaps]).encode())[:24]
        result.append(item)
    return result
