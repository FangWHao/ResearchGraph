"""人工归属完整物理记录；科研解释与原始事件均不改写。"""

from __future__ import annotations

import json
import uuid
from contextlib import ExitStack
from itertools import groupby
from typing import Any

from rg.store.database import ConflictError, Store, dumps, now
from rg.store.locking import exclusive
from rg.store.objects import digest

FIELDS = {
    "request_id",
    "session",
    "source",
    "first_event",
    "last_event",
    "project_id",
    "actor",
    "expected_revision",
    "expected_scope_key",
    "snapshot",
    "observation_snapshot",
}


def _validate(body: dict[str, Any]) -> tuple[str, str]:
    if set(body) != FIELDS:
        raise ValueError("片段归属请求字段不完整或含未知字段")
    for key in ("request_id", "project_id"):
        value = body[key]
        if value is None and key == "project_id":
            continue
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("请求和项目编号必须为规范UUID") from None
    for key in (
        "session",
        "source",
        "first_event",
        "last_event",
        "snapshot",
        "observation_snapshot",
        "expected_revision",
    ):
        value = body[key]
        if type(value) is not int or not 0 <= value <= 9223372036854775807:
            raise ValueError("片段边界、截止与修订必须为非负整数")
        if key in {"session", "source", "first_event", "last_event"} and not value:
            raise ValueError("片段与来源编号必须为正整数")
    actor = body["actor"]
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 120:
        raise ValueError("归属操作需要有效人工身份")
    if not actor.startswith("human:") or not actor[6:].strip() or "\0" in actor:
        raise ValueError("模型不能提供人工归属身份")
    scope = body["expected_scope_key"]
    if (
        not isinstance(scope, str)
        or len(scope) != 64
        or any(c not in "0123456789abcdef" for c in scope)
    ):
        raise ValueError("归属摘要必须为规范SHA256")
    intent = {
        key: body[key]
        for key in ("session", "source", "first_event", "last_event", "project_id", "actor")
    }
    return body["request_id"], digest(dumps(intent).encode())


def _replay(store: Store, request_id: str, signature: str) -> dict[str, Any] | None:
    row = store.db.execute(
        "SELECT intent_sha256,response_json FROM fragment_assignments WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row:
        if row[0] != signature:
            raise ConflictError("请求编号已用于不同归属意图")
        return json.loads(row[1]) | {"replayed": True}
    from rg.record.events import previous

    previous(store, request_id, "fragment", signature)
    return None


def assign(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    request_id, signature = _validate(body)
    replay = _replay(store, request_id, signature)
    if replay:
        return replay
    session = body["session"]
    # 锁定所有现有处理边界；事务内复核修订，拒绝并发新增边界。
    sessions = [
        session,
        *[
            r[0]
            for r in store.db.execute(
                "SELECT session_pk FROM sessions WHERE physical_session_pk=? ORDER BY session_pk",
                (session,),
            )
        ],
    ]
    paths = [
        r[0]
        for r in store.db.execute(
            "SELECT path FROM source_files WHERE session_pk=? ORDER BY file_instance_id", (session,)
        )
    ]
    with ExitStack() as locks:
        for path in sorted(set(paths)):
            locks.enter_context(
                exclusive(
                    store.root / "locks" / "sources" / (digest(path.encode()) + ".lock"),
                    "来源正在扫描，请刷新后再归属",
                )
            )
        locks.enter_context(exclusive(store.root / "locks" / "derive.lock", "L1正在处理该来源"))
        for value in sorted(set(sessions)):
            locks.enter_context(
                exclusive(
                    store.root / "locks" / "sessions" / f"{value}.lock",
                    "该片段正在提取",
                )
            )
        with store.transaction():
            replay = _replay(store, request_id, signature)
            if replay:
                return replay
            return _assign(store, body, request_id, signature)


def _assign(store: Store, body: dict[str, Any], request_id: str, signature: str) -> dict[str, Any]:
    from rg.query.project_fragments import query

    db = store.db
    if body["expected_revision"] != store.revision():
        raise ConflictError("图修订已变化，请重新读取片段后归属")
    origin = db.execute("SELECT * FROM sessions WHERE session_pk=?", (body["session"],)).fetchone()
    if not origin or origin["fragment_id"] or origin["tool"] not in {"claude", "codex"}:
        raise ValueError("归属入口需要原生物理会话编号")
    if db.execute(
        "SELECT 1 FROM raw_events r WHERE r.session_pk=? AND NOT EXISTS ("
        "SELECT 1 FROM raw_events first WHERE first.file_instance_id=r.file_instance_id "
        "AND first.byte_start=r.byte_start AND first.record_index=0) LIMIT 1",
        (body["session"],),
    ).fetchone():
        raise ConflictError("旧记录缺少完整物理首块，保留原归属；需先核对来源完整性")
    if (
        body["project_id"] is not None
        and not db.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (body["project_id"],)
        ).fetchone()
    ):
        raise ValueError("目标项目不存在")
    query(
        store,
        {
            key: str(body[key])
            for key in (
                "session",
                "source",
                "snapshot",
                "observation_snapshot",
                "expected_scope_key",
            )
        },
    )
    bounds = [
        db.execute(
            "SELECT byte_start FROM raw_events WHERE event_id=? AND file_instance_id=? "
            "AND session_pk=? AND record_index=0 AND event_id<=?",
            (body[key], body["source"], body["session"], body["snapshot"]),
        ).fetchone()
        for key in ("first_event", "last_event")
    ]
    if any(row is None for row in bounds) or bounds[0][0] > bounds[1][0]:
        raise ValueError("首末边界必须是同来源的完整物理记录首事件")
    chosen = [
        dict(r)
        for r in db.execute(
            "SELECT event_id,byte_start,byte_end FROM raw_events WHERE "
            "file_instance_id=? AND record_index=0 "
            "AND byte_start BETWEEN ? AND ? AND event_id<=? ORDER BY byte_start",
            (body["source"], bounds[0][0], bounds[1][0], body["snapshot"]),
        )
    ]
    if any(
        left["byte_end"] != right["byte_start"]
        for left, right in zip(chosen, chosen[1:], strict=False)
    ):
        raise ValueError("归属范围跨过已清除或未保存的物理记录缺口")
    records = [
        dict(r)
        for r in db.execute(
            "SELECT r.event_id,r.file_instance_id,r.byte_start,r.byte_end,s.project_id,"
            "e.session_pk AS logical_session_pk,p.partition_id,p.basis "
            "FROM raw_events r JOIN effective_raw_events e USING(event_id) "
            "JOIN sessions s ON s.session_pk=e.session_pk "
            "LEFT JOIN fragment_record_maps m ON m.map_id=(SELECT max(x.map_id) "
            "FROM fragment_record_maps x WHERE x.first_event_id=r.event_id) "
            "LEFT JOIN fragment_partitions p USING(partition_id) "
            "WHERE r.session_pk=? AND r.record_index=0 ORDER BY r.file_instance_id,r.byte_start",
            (body["session"],),
        )
    ]
    _protect_science(store, body["session"])
    selected = {r["event_id"] for r in chosen}
    touched = {r["partition_id"] for r in records if r["event_id"] in selected}
    for record in records:
        if record["partition_id"] in touched:
            record["partition_id"] = None
        if record["event_id"] in selected:
            record.update(project_id=body["project_id"], basis="manual", partition_id=None)
        elif record["basis"] is None:
            record["basis"] = "inherited_manual" if record["project_id"] else "unassigned"
    # 来源、归属与已有分段均是边界；绝不把A/B/A的两个A片段合并。
    created, recorded = 0, now()
    for _, members in groupby(
        records, lambda r: (r["file_instance_id"], r["project_id"], r["basis"], r["partition_id"])
    ):
        group = list(members)
        segments: list[list[dict[str, Any]]] = []
        for record in group:
            if not segments or segments[-1][-1]["byte_end"] != record["byte_start"]:
                segments.append([])
            segments[-1].append(record)
        for segment in segments:
            # 未受本次操作影响的完整现有边界直接保留。
            if segment[0]["partition_id"] is not None:
                continue
            _partition(store, origin, segment, request_id, recorded)
            created += 1
    db.execute(
        "UPDATE sessions SET project_id=NULL,project_basis='fragment_container' WHERE session_pk=?",
        (body["session"],),
    )
    db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
    result = {
        "request_id": request_id,
        "revision": store.revision(),
        "replayed": False,
        "assignments_applied": True,
        "session_pk": body["session"],
        "file_instance_id": body["source"],
        "first_event_id": body["first_event"],
        "last_event_id": body["last_event"],
        "project_id": body["project_id"],
        "records_assigned": len(chosen),
        "partitions_created": created,
    }
    db.execute(
        "INSERT INTO fragment_assignments(request_id,intent_sha256,physical_session_pk,"
        "file_instance_id,first_event_id,last_event_id,project_id,actor,response_json,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            signature,
            body["session"],
            body["source"],
            body["first_event"],
            body["last_event"],
            body["project_id"],
            body["actor"],
            dumps(result),
            recorded,
        ),
    )
    return result


def _partition(
    store: Store, origin: Any, records: list[dict[str, Any]], request: str, at: str
) -> None:
    partition = str(uuid.uuid4())
    first, last = records[0], records[-1]
    session = store.db.execute(
        "INSERT INTO sessions(tool,native_session_id,agent_id,project_id,project_basis,"
        "physical_session_pk,fragment_id,git_branch,tool_version) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            origin["tool"],
            origin["native_session_id"],
            origin["agent_id"],
            first["project_id"],
            "fragment_manual",
            origin["session_pk"],
            partition,
            origin["git_branch"],
            origin["tool_version"],
        ),
    ).lastrowid
    store.db.execute(
        "INSERT INTO fragment_partitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            partition,
            origin["session_pk"],
            session,
            first["file_instance_id"],
            first["project_id"],
            first["basis"],
            first["event_id"],
            last["event_id"],
            first["byte_start"],
            last["byte_end"],
            request,
            at,
        ),
    )
    store.db.executemany(
        "INSERT INTO fragment_record_maps(first_event_id,partition_id,request_id,recorded_at) "
        "VALUES (?,?,?,?)",
        [(r["event_id"], partition, request, at) for r in records],
    )


def _protect_science(store: Store, session: int) -> None:
    """已有科研依赖需单独迁移方案；普通归属操作不能改写既有解释。"""
    checks = (
        "SELECT 1 FROM claim_evidence c JOIN evidence_spans e USING(span_id) "
        "JOIN raw_events r USING(event_id) WHERE r.session_pk=? LIMIT 1",
        "SELECT 1 FROM extraction_runs x,json_each(x.input_event_ids) j "
        "JOIN raw_events r ON r.event_id=j.value WHERE r.session_pk=? LIMIT 1",
        "SELECT 1 FROM extraction_plans p JOIN sessions s USING(session_pk) "
        "WHERE s.session_pk=? OR s.physical_session_pk=? LIMIT 1",
        "SELECT 1 FROM run_observations o JOIN raw_events r USING(event_id) "
        "WHERE r.session_pk=? LIMIT 1",
        "SELECT 1 FROM edit_records e JOIN raw_events r ON "
        "r.event_id=e.request_event_id OR r.event_id=e.result_event_id "
        "WHERE r.session_pk=? LIMIT 1",
    )
    if any(
        store.db.execute(sql, (session, session) if sql.count("?") == 2 else (session,)).fetchone()
        for sql in checks
    ):
        raise ConflictError("该来源已有科研依赖，需先核对依赖迁移；不能直接重归属")
