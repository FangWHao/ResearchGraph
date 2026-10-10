"""记录显式运行清单；不运行命令、不读取文件、不确认报告。"""

from __future__ import annotations

from typing import Any

from rg.record.events import previous
from rg.record.manifest_schema import MAX_BYTES, ROLES, identity, normalize
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest


def receipt(store: Store, request_id: str, replayed: bool) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT request_id,run_id,evidence_event_id,claim_state,basis FROM run_manifests "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("运行清单回执缺失")
    return dict(row) | {"revision": store.revision(), "replayed": replayed}


def validate_links(store: Store, project: str, data: dict[str, Any]) -> None:
    if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
        raise ValueError("项目不存在")
    run = store.db.execute(
        "SELECT project_id FROM runs WHERE run_id=?", (data["run_id"],)
    ).fetchone()
    if run is not None and run[0] != project:
        raise ValueError("运行属于其它项目")
    if (
        data["attempt_id"] is not None
        and not store.db.execute(
            "SELECT 1 FROM entities WHERE entity_id=? AND project_id=? AND kind='attempt'",
            (data["attempt_id"], project),
        ).fetchone()
    ):
        raise ValueError("尝试需为当前项目内已登记的 attempt")
    if (
        data["snapshot_id"] is not None
        and not store.db.execute(
            "SELECT 1 FROM workspace_snapshots WHERE snapshot_id=? AND project_id=?",
            (data["snapshot_id"], project),
        ).fetchone()
    ):
        raise ValueError("声明快照需属于当前项目")
    for role in ROLES:
        for requested in data[role] or []:
            row = store.db.execute(
                "SELECT project_id FROM artifact_versions WHERE version_id=?", (requested,)
            ).fetchone()
            if row is not None and row[0] != project:
                raise ValueError("文件版本属于其它项目")


def original(
    store: Store,
    project: str,
    request_id: str,
    body: dict[str, Any],
    occurred: str | None,
    recorded: str,
) -> int:
    raw = dumps(
        {
            "type": "rg_run_manifest",
            "project_id": project,
            "request_id": request_id,
            "actor": "report:local",
            "input": body,
            "occurred_at": occurred,
            "recorded_at": recorded,
        }
    ).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("含来源元数据的完整运行清单超过 65536 字节")
    sha = store.objects.put(raw)
    session = store.db.execute(
        "SELECT session_pk FROM sessions WHERE tool='rg' AND native_session_id=? AND agent_id=''",
        ("manifest:" + project,),
    ).fetchone()
    session_id = (
        session[0]
        if session
        else store.db.execute(
            "INSERT INTO sessions(tool,native_session_id,project_id,project_basis,first_at) "
            "VALUES ('rg',?,?,'direct_record',?)",
            ("manifest:" + project, project, recorded),
        ).lastrowid
    )
    file_id = store.db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,prefix_length,committed_offset,"
        "parser,parser_version,status,first_seen,last_read) "
        "VALUES (?,?,?,?,?,'rg','manifest-1','virtual',?,?)",
        (
            session_id,
            "rg-manifest://" + request_id,
            digest(raw[:4096]),
            min(len(raw), 4096),
            len(raw),
            recorded,
            recorded,
        ),
    ).lastrowid
    seq = store.db.execute(
        "SELECT coalesce(max(seq),0)+1 FROM raw_events WHERE session_pk=?", (session_id,)
    ).fetchone()[0]
    event_id = store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end,"
        "object_sha256,native_id,seq,kind,role,occurred_at,recorded_at,line_sha256,exclude_reason) "
        "VALUES (?,?,0,0,?,?,?,?,'run_manifest','tool',?,?,?,'explicit_manifest')",
        (session_id, file_id, len(raw), sha, request_id, seq, occurred, recorded, sha),
    ).lastrowid
    if event_id is None:
        raise RuntimeError("运行清单原件写入失败")
    store.db.execute(
        "INSERT INTO coverage(event_id,stage,status) "
        "VALUES (?,'pass2','excluded:explicit_manifest')",
        (event_id,),
    )
    store.db.execute("UPDATE sessions SET last_at=? WHERE session_pk=?", (recorded, session_id))
    return event_id


def record(store: Store, project: str, body: dict[str, Any]) -> dict[str, Any]:
    project = identity(project, "项目")
    request_id, data, expected = normalize(body)
    intent_sha = digest(dumps({"project_id": project, "data": data}).encode())
    with store.transaction() as db:
        if previous(store, request_id, "manifest", intent_sha):
            return receipt(store, request_id, True)
        if expected is not None and expected != store.revision():
            raise ConflictError("记录版本已变化，请刷新后保存运行清单")
        validate_links(store, project, data)
        recorded = now()
        event_id = original(store, project, request_id, body, data["occurred_at"], recorded)
        db.execute(
            "INSERT INTO run_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?,'candidate','direct_record')",
            (
                request_id,
                project,
                data["run_id"],
                data["attempt_id"],
                data["snapshot_id"],
                event_id,
                intent_sha,
                dumps(data["scope"]) if data["scope"] else None,
                dumps(data),
                data["occurred_at"],
                recorded,
            ),
        )
        for role, direction in ROLES.items():
            for ordinal, requested in enumerate(data[role] or []):
                version = db.execute(
                    "SELECT version_id FROM artifact_versions WHERE version_id=? AND project_id=?",
                    (requested, project),
                ).fetchone()
                io_id = digest(dumps([request_id, role, ordinal, requested]).encode())
                db.execute(
                    "INSERT INTO run_io(run_id,version_id,direction,basis,io_id,manifest_id,"
                    "requested_version_id,role,ordinal,claim_state,evidence_event_id,occurred_at,"
                    "recorded_at) VALUES (?,?,?,'direct_record',?,?,?,?,?,'candidate',?,?,?)",
                    (
                        data["run_id"],
                        version[0] if version else None,
                        direction,
                        io_id,
                        request_id,
                        requested,
                        role,
                        ordinal,
                        event_id,
                        data["occurred_at"],
                        recorded,
                    ),
                )
        return receipt(store, request_id, False)
