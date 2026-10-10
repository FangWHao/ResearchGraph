"""只核对已保存请求与候选正文；不修改编辑史，不确认物理文件。"""

from __future__ import annotations

from typing import Any

from zstandard import ZstdError

from rg.derive.records import arguments, block, event
from rg.store.database import Store, dumps
from rg.store.objects import digest

MAX_CONTENT_BYTES = 5_000_000


def expected_multiedit(original: str, edits: Any, *, limit: int = MAX_CONTENT_BYTES) -> str:
    if not isinstance(edits, list) or not edits:
        raise ValueError("reported_edit_request_unsupported")
    current = original
    size = len(current.encode("utf-8"))
    if size > limit:
        raise ValueError("reported_content_over_limit")
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("reported_edit_request_unsupported")
        old, new = edit.get("old_string"), edit.get("new_string")
        all_occurrences = edit.get("replace_all", False)
        if (
            not isinstance(old, str)
            or not old
            or not isinstance(new, str)
            or type(all_occurrences) is not bool
        ):
            raise ValueError("reported_edit_request_unsupported")
        count = current.count(old)
        if count == 0 or (count > 1 and not all_occurrences):
            raise ValueError("reported_edit_disagrees_with_request")
        # 在分配下一步正文之前检查精确 UTF-8 长度，限制中间结果而非仅最终结果。
        size += (len(new.encode("utf-8")) - len(old.encode("utf-8"))) * (
            count if all_occurrences else 1
        )
        if size > limit:
            raise ValueError("reported_content_over_limit")
        current = current.replace(old, new, -1 if all_occurrences else 1)
    return current


def _request(store: Store, row: dict[str, Any]) -> dict[str, Any]:
    try:
        request = event(store, row["request_event_id"])
        item = block(request)
        if (
            request["tool"] != "claude"
            or request["kind"] != "file_edit"
            or request["tool_name"] != "MultiEdit"
            or request["project_id"] != row["project_id"]
            or request["session_pk"] != row["session_pk"]
            or request["call_id"] != row["call_id"]
            or request["alias_of"] is not None
            or request["exclude_reason"] is not None
            or item.get("type") != "tool_use"
            or item.get("name") != "MultiEdit"
            or item.get("id") != row["call_id"]
        ):
            raise ValueError("invalid_request")
        return arguments(request)
    except (OSError, ValueError, UnicodeError, ZstdError) as error:
        raise ValueError("multiedit_request_unavailable") from error


def _content(store: Store, row: dict[str, Any], phase: str) -> str:
    try:
        saved = store.db.execute(
            "SELECT * FROM artifact_versions WHERE version_id=?", (row[f"{phase}_version"],)
        ).fetchone()
        identity = digest(
            dumps(
                [
                    row["project_id"],
                    row["root_id"],
                    row["path"],
                    row["result_event_id"],
                    phase,
                    saved["content_sha256"] if saved else None,
                ]
            ).encode()
        )
        if saved is None or any(
            saved[key] != value
            for key, value in {
                "version_id": identity,
                "project_id": row["project_id"],
                "root_id": row["root_id"],
                "path": row["path"],
                "evidence_event_id": row["result_event_id"],
                "phase": phase,
                "source": "agent_edit",
                "claim_state": "candidate",
                "basis": "direct_record",
                "representation": "tool_reported_utf8",
                "algo": "sha256:tool-utf8",
            }.items()
        ):
            raise ValueError("invalid_version")
        if saved["digest"] != saved["content_sha256"] or saved["size"] > MAX_CONTENT_BYTES:
            raise ValueError("invalid_version")
        raw = store.objects.get(saved["content_sha256"])
        if len(raw) != saved["size"] or len(raw) > MAX_CONTENT_BYTES:
            raise ValueError("invalid_version")
        return raw.decode("utf-8")
    except (OSError, ValueError, UnicodeError, ZstdError) as error:
        raise ValueError("multiedit_version_unavailable") from error


def project_edit(store: Store, record: dict[str, Any]) -> dict[str, Any]:
    if record["operation"] != "multiedit":
        return record
    row = dict(record)
    row["reported_after_version"] = row["after_version"]
    status = "unavailable"
    if row["after_version"] is not None:
        try:
            args = _request(store, row)
            before = _content(store, row, "before")
            after = _content(store, row, "after")
            if expected_multiedit(before, args.get("edits")) != after:
                raise ValueError("reported_edit_disagrees_with_request")
            status = "matches_request"
        except UnicodeError:
            row["gap"] = "reported_content_invalid_utf8"
            row["after_version"] = None
        except ValueError as error:
            row["gap"] = str(error)
            row["after_version"] = None
    if row["gap"] == "reported_edit_disagrees_with_request":
        status = "mismatch"
    row["request_validation"] = {"status": status, "basis": "saved_request_and_reported_versions"}
    return row
