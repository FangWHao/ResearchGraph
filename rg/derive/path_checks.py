"""按已存原件复核旧L1路径；只追加证明和缺失候选，不改旧事实。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

from zstandard import ZstdError

from rg.derive.edits import edit, version
from rg.derive.multiedit import MAX_CONTENT_BYTES
from rg.derive.records import Event, bound_path, calls, cwd, event, native_patch
from rg.derive.runtime import EXEC_TOOLS
from rg.ingest.project_context import saved_context
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

ALGORITHM = "l1-native-path-replay-v1"
EDIT_FIELDS = ("path", "root_id", "before_version", "after_version", "gap")
RUN_FIELDS = ("cwd", "root_id", "gap")


def _time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if value else None
        return parsed if parsed and parsed.tzinfo else None
    except ValueError:
        return None


def proof(
    store: Store, record: dict[str, Any], *, known_at: str | None = None
) -> dict[str, Any] | None:
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 36:
        return None
    key = "edit_id" if "edit_id" in record else "run_id"
    cutoff = _time(known_at)
    for row in store.db.execute(
        f"SELECT * FROM l1_path_checks WHERE {key}=? ORDER BY check_id DESC", (record[key],)
    ):
        recorded = _time(row["recorded_at"])
        if recorded is not None and (known_at is None or cutoff and recorded <= cutoff):
            return dict(row)
    return None


def projected(
    store: Store, record: dict[str, Any], *, known_at: str | None = None
) -> dict[str, Any]:
    checked = proof(store, record, known_at=known_at)
    if checked is None:
        return record
    fields = EDIT_FIELDS if "edit_id" in record else RUN_FIELDS
    value = json.loads(checked["payload"])
    result = record | {key: value[key] for key in fields}
    result["path_validation"] = {
        "check_id": checked["check_id"],
        "state": checked["state"],
        "algorithm": value["algorithm"],
        "input_signature": checked["input_signature"],
        "recorded_at": checked["recorded_at"],
        "basis": "saved_request_and_report",
        "original": {key: record[key] for key in fields},
        "reason": value.get("reason"),
        "context_event_id": checked["context_event_id"],
        "root_declaration": value.get("root_declaration"),
    }
    if "recorded_at" in record:
        original, updated = _time(record["recorded_at"]), _time(checked["recorded_at"])
        if original and updated and updated > original:
            result["original_recorded_at"] = record["recorded_at"]
            result["recorded_at"] = checked["recorded_at"]
    return result


def registration(store: Store, identity: str) -> str | None:
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 36:
        return None
    row = store.db.execute(
        "SELECT recorded_at FROM l1_repair_versions WHERE version_id=?", (identity,)
    ).fetchone()
    return row[0] if row else None


def fingerprint(store: Store, row: dict[str, Any], roots: list[dict[str, Any]]) -> str:
    request = store.db.execute(
        "SELECT r.*,s.project_id,s.tool FROM effective_raw_events r "
        "JOIN sessions s USING(session_pk) "
        "WHERE r.event_id=?",
        (row["request_event_id"],),
    ).fetchone()
    related = []
    if request:
        related = [
            dict(r)
            for r in store.db.execute(
                "SELECT r.* FROM effective_raw_events r WHERE r.session_pk=? AND r.call_id IS ? "
                "ORDER BY r.event_id",
                (request["session_pk"], request["call_id"]),
            )
        ]
    return digest(
        dumps(
            [
                ALGORITHM,
                row,
                dict(request) if request else None,
                related,
                saved_context(store, row["request_event_id"]),
                roots,
            ]
        ).encode()
    )


def _request(store: Store, row: dict[str, Any]) -> Event:
    from rg.derive.worker import contaminated

    call = event(store, row["request_event_id"])
    if (
        call["project_id"] != row["project_id"]
        or call["session_pk"] != row["session_pk"]
        or call["call_id"] != row["call_id"]
        or call["alias_of"] is not None
        or contaminated(call)
        or not (
            call["kind"] in {"tool_call", "file_edit"}
            and call["exclude_reason"] is None
            or call["kind"] == "meta"
            and call["exclude_reason"] == "execution_metadata"
        )
    ):
        raise ValueError("l1_path_request_unavailable")
    return call


def _edited(store: Store, row: dict[str, Any], recorded_at: str) -> dict[str, Any]:
    from rg.derive.worker import contaminated

    call = _request(store, row)
    result = event(store, row["result_event_id"])
    matched = calls(store, result)
    if (
        not (call["kind"] == "file_edit" or native_patch(call, "patch_apply_begin"))
        or result["kind"] != "tool_result"
        or result["call_id"] != call["call_id"]
        or len(matched) != 1
        or matched[0]["event_id"] != call["event_id"]
        or result["project_id"] != call["project_id"]
        or result["session_pk"] != call["session_pk"]
        or result["alias_of"] is not None
        or result["exclude_reason"] not in {None, "execution_metadata"}
        or contaminated(result)
    ):
        raise ValueError("l1_path_association_unavailable")
    captured: list[dict[str, Any]] = []

    def collect(
        store: Store,
        call: Event,
        value: Event,
        path: Any,
        operation: str,
        patch: str,
        before: str | None,
        after: str | None,
        gap: str | None,
        user_modified: bool | None = None,
    ) -> None:
        identity = digest(
            dumps(
                [call["event_id"], value["event_id"], path, operation, digest(patch.encode())]
            ).encode()
        )
        if identity != row["edit_id"]:
            return
        if digest(patch.encode()) != row["patch_sha256"] or operation != row["operation"]:
            raise ValueError("l1_path_edit_identity_conflict")
        root, resolved = bound_path(store, call["project_id"], path, cwd(store, call))
        value_out: dict[str, Any] = {
            "path": resolved,
            "root_id": root,
            "before_version": None,
            "after_version": None,
            "gap": gap if root and resolved else "artifact_root_unknown",
        }
        if root and resolved:
            for phase, content in (("before", before), ("after", after)):
                if content is None:
                    continue
                try:
                    raw = content.encode("utf-8")
                    if len(raw) > MAX_CONTENT_BYTES:
                        raise ValueError("reported_content_over_limit")
                    identity = digest(
                        dumps(
                            [
                                call["project_id"],
                                root,
                                resolved,
                                value["event_id"],
                                phase,
                                digest(raw),
                            ]
                        ).encode()
                    )
                    exists = store.db.execute(
                        "SELECT 1 FROM artifact_versions WHERE version_id=?", (identity,)
                    ).fetchone()
                    published = version(store, call, value, root, resolved, phase, content)
                    assert published == identity
                    if not exists:
                        store.db.execute(
                            "INSERT INTO l1_repair_versions(version_id,recorded_at) VALUES (?,?)",
                            (identity, recorded_at),
                        )
                    value_out[f"{phase}_version"] = identity
                except UnicodeError:
                    value_out["gap"] = "reported_content_invalid_utf8"
                    break
                except ValueError as error:
                    value_out["gap"] = str(error)
                    break
        captured.append(value_out)

    edit(store, call, result, collect)
    if len(captured) != 1:
        raise ValueError("l1_path_edit_identity_unavailable")
    return captured[0]


def _run(store: Store, row: dict[str, Any]) -> dict[str, Any]:
    call = _request(store, row)
    if call["tool_name"] not in EXEC_TOOLS:
        raise ValueError("l1_path_request_unavailable")
    # 请求唯一性独立于退出事实；不依据命令文字或运行时间找另一请求。
    candidates = store.db.execute(
        "SELECT * FROM effective_raw_events WHERE session_pk=? AND call_id IS ? AND "
        "alias_of IS NULL AND ((kind IN ('tool_call','file_edit') AND exclude_reason IS NULL) "
        "OR (kind='meta' AND tool_name='exec_command' AND exclude_reason='execution_metadata'))",
        (call["session_pk"], call["call_id"]),
    ).fetchall()
    ordinary = [r for r in candidates if r["kind"] != "meta"]
    selected = ordinary or candidates
    if len(selected) != 1 or selected[0]["event_id"] != call["event_id"]:
        raise ValueError("l1_path_association_unavailable")
    working_dir = cwd(store, call)
    root, _ = bound_path(store, call["project_id"], working_dir, None)
    return {
        "cwd": working_dir,
        "root_id": root,
        "gap": (None if row["gap"] == "cwd_root_unknown" else row["gap"])
        if root
        else row["gap"] or "cwd_root_unknown",
    }


def check_batch(
    store: Store,
    limit: int = 100,
    *,
    session: int | None = None,
    retry_unavailable: bool = False,
    fault: Callable[[], None] | None = None,
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise ValueError("L1路径复核单次上限为1到1000")
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 36:
        return {}
    counts: Counter[str] = Counter()
    targets = (("edit_records", "edit_id", EDIT_FIELDS), ("runs", "run_id", RUN_FIELDS))
    for table, key, fields in targets:
        for saved in store.db.execute(
            f"SELECT * FROM effective_{table} WHERE (? IS NULL OR session_pk=?) ORDER BY {key}",
            (session, session),
        ):
            with store.transaction() as db:
                current = db.execute(
                    f"SELECT * FROM effective_{table} WHERE {key}=?", (saved[key],)
                ).fetchone()
                if current is None:
                    continue
                row = dict(current)
                roots = [
                    dict(r)
                    for r in db.execute(
                        "SELECT root_id,project_id,host_id,path FROM effective_source_roots "
                        "WHERE host_id='local' ORDER BY root_id,project_id"
                    )
                ]
                signature = fingerprint(store, row, roots)
                previous = proof(store, row)
                if (
                    previous
                    and previous["input_signature"] == signature
                    and not (retry_unavailable and previous["state"] == "unavailable")
                ):
                    continue
                recorded_at = now()
                try:
                    # 不可用处理也必须回滚中途候选，不能留下没有回执的半次修复。
                    db.execute("SAVEPOINT l1_path_candidates")
                    try:
                        payload = (
                            _edited(store, row, recorded_at)
                            if key == "edit_id"
                            else _run(store, row)
                        )
                    except BaseException:
                        db.execute("ROLLBACK TO l1_path_candidates")
                        db.execute("RELEASE l1_path_candidates")
                        raise
                    db.execute("RELEASE l1_path_candidates")
                    state = "matches" if all(row[k] == payload[k] for k in fields) else "revised"
                except (OSError, ValueError, UnicodeError, ZstdError) as error:
                    state = "unavailable"
                    payload = {k: None for k in fields}  # type: dict[str, Any]
                    payload["gap"] = (
                        str(error)
                        if isinstance(error, ValueError)
                        else "l1_path_source_unavailable"
                    )
                    payload["reason"] = type(error).__name__
                payload["algorithm"] = ALGORITHM
                payload["root_declaration"] = next(
                    (
                        root
                        for root in roots
                        if root["root_id"] == payload["root_id"]
                        and root["project_id"] == row["project_id"]
                    ),
                    None,
                )
                context = saved_context(store, row["request_event_id"])
                db.execute(
                    "INSERT INTO l1_path_checks(edit_id,run_id,input_signature,state,payload,"
                    "before_version,after_version,context_event_id,recorded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        row[key] if key == "edit_id" else None,
                        row[key] if key == "run_id" else None,
                        signature,
                        state,
                        dumps(payload),
                        payload.get("before_version"),
                        payload.get("after_version"),
                        context["event_id"] if context else None,
                        recorded_at,
                    ),
                )
                if fault:
                    fault()
            counts["l1_path_checked"] += 1
            counts[f"l1_path_{state}"] += 1
            if counts["l1_path_checked"] >= limit:
                return dict(counts)
    return dict(counts)
