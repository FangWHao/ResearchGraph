from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from rg.snapshot.capture import identifier
from rg.snapshot.git import Git
from rg.store.database import Store, dumps, now
from rg.store.locking import exclusive


def consume_record(store: Store, payload: dict[str, Any], tool: str, sha: str) -> None:
    if not isinstance(payload.get("project_id"), str):
        raise ValueError("快照缺少项目")
    project = identifier(payload["project_id"])
    with exclusive(store.root / "snapshots" / f"{project}.lock", "影子快照忙"):
        _consume_record(store, payload, tool, sha)


def _consume_record(store: Store, payload: dict[str, Any], tool: str, sha: str) -> None:
    key = payload.get("snapshot_key")
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("快照标识非法")
    for field in ["project_id", "root_id"]:
        if not isinstance(payload.get(field), str):
            raise ValueError("快照缺少项目或根目录")
    project, root = identifier(payload["project_id"]), identifier(payload["root_id"])
    registered = store.db.execute(
        "SELECT path FROM source_roots WHERE root_id=? AND project_id=? AND host_id='local'",
        (root, project),
    ).fetchone()
    if not registered or str(Path(registered[0])) != payload.get("worktree"):
        raise ValueError("快照根目录未明确登记")
    previous = store.db.execute(
        "SELECT record_sha256 FROM workspace_snapshots WHERE snapshot_key=?", (key,)
    ).fetchone()
    if previous:
        if previous[0] != sha:
            raise ValueError("不可变快照标识内容冲突")
        return
    commit = payload.get("shadow_commit")
    skipped = payload.get("skipped")
    if (commit is None) == (skipped is None):
        raise ValueError("快照须为有效提交或明确缺口")
    if skipped is not None and (not isinstance(skipped, str) or not skipped):
        raise ValueError("快照缺口原因非法")
    for field in ["head_commit", "branch", "prompt_id", "native_session_id", "source_sha256"]:
        if payload.get(field) is not None and not isinstance(payload[field], str):
            raise ValueError("快照元数据字段非法")
    if payload.get("source_sha256") is not None and not re.fullmatch(
        r"[a-f0-9]{64}", payload["source_sha256"]
    ):
        raise ValueError("快照原始提示摘要非法")
    if type(payload.get("async_race")) is not bool:
        raise ValueError("快照竞态标记非法")
    if type(payload.get("duration_ms")) is not int or payload["duration_ms"] < 0:
        raise ValueError("快照耗时非法")
    if payload.get("dirty") is not None and type(payload["dirty"]) is not bool:
        raise ValueError("快照 dirty 标记非法")
    if not isinstance(payload.get("omitted_files"), list):
        raise ValueError("快照未捕获文件清单非法")
    for field in ["taken_at", "requested_at", "trigger"]:
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise ValueError("快照时间或触发来源非法")
    if commit is not None:
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("影子提交格式非法")
        if payload["duration_ms"] > 2000:
            raise ValueError("超时结果不能成为有效快照")
        repository = store.root.resolve() / "snapshots" / f"{project}.git"
        git = Git(store.root.resolve(), time.monotonic() + 2, repository)
        if git.run("cat-file", "-t", commit).strip() != b"commit":
            raise ValueError("影子提交缺失或类型不符")
        # 不只验证提交外壳；缺失的树或 blob 不能作为已捕获的证据。
        git.run("rev-list", "--objects", "--missing=error", commit)
        git.run("update-ref", f"refs/rg/{key}", commit)
    native = payload.get("native_session_id")
    sessions = (
        store.db.execute(
            "SELECT session_pk FROM sessions WHERE tool=? AND native_session_id=? AND project_id=?",
            (tool, native, project),
        ).fetchall()
        if isinstance(native, str)
        else []
    )
    session_pk = sessions[0][0] if len(sessions) == 1 else None
    store.db.execute(
        "INSERT INTO workspace_snapshots "
        "(project_id,root_id,session_pk,trigger,prompt_id,head_commit,branch,dirty,shadow_commit,"
        "skipped,duration_ms,taken_at,snapshot_key,record_sha256,async_race,metadata,recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project,
            root,
            session_pk,
            payload["trigger"],
            payload.get("prompt_id"),
            payload.get("head_commit"),
            payload.get("branch"),
            payload.get("dirty"),
            commit,
            skipped,
            payload["duration_ms"],
            payload["taken_at"],
            key,
            sha,
            payload["async_race"],
            dumps(payload),
            now(),
        ),
    )
