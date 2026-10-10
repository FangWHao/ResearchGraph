from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

from rg.derive.records import Event, block, cwd, native_patch, patch_begins
from rg.ingest.common import RG_BLOCK
from rg.store.database import Store, dumps

if TYPE_CHECKING:
    from rg.derive.edits import PatchFile


def changes(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("native_patch_changes_missing")
    result = {}
    for path, entry in raw.items():
        if not isinstance(path, str) or not path or not isinstance(entry, dict):
            raise ValueError("invalid_native_patch_changes")
        operation = entry.get("type")
        key = "unified_diff" if operation == "update" else "content"
        if (
            not isinstance(operation, str)
            or operation not in {"add", "delete", "update"}
            or not isinstance(entry.get(key), str)
        ):
            raise ValueError("invalid_native_patch_changes")
        destination = entry.get("move_path") if operation == "update" else None
        if destination is not None and (not isinstance(destination, str) or not destination):
            raise ValueError("invalid_native_patch_changes")
        result[path] = {"type": operation, key: entry[key]}
        if operation == "update":
            result[path]["move_path"] = destination
    return result


def normalized(path: str, working_dir: str | None) -> str:
    return posixpath.normpath(
        path if path.startswith("/") or not working_dir else posixpath.join(working_dir, path)
    )


def indexed(items: dict[str, dict[str, Any]], working_dir: str | None) -> dict[str, dict[str, Any]]:
    result = {}
    destinations = set()
    for path, entry in items.items():
        key = normalized(path, working_dir)
        value = dict(entry)
        destination = value.get("move_path")
        if destination:
            value["move_path"] = normalized(destination, working_dir)
        target = value.get("move_path") or key
        if key in result or target in destinations:
            raise ValueError("ambiguous_native_patch_path")
        destinations.add(target)
        result[key] = value
    return result


def status(item: dict[str, Any]) -> tuple[bool, str | None]:
    succeeded = item.get("success")
    phase = item.get("status")
    if type(succeeded) is not bool:
        return False, "native_patch_metadata_conflict"
    if phase is not None and (
        not isinstance(phase, str)
        or phase not in {"completed", "failed", "declined"}
        or succeeded != (phase == "completed")
    ):
        return False, "native_patch_metadata_conflict"
    if any(key in item and not isinstance(item[key], str) for key in ("stdout", "stderr")):
        return False, "native_patch_metadata_conflict"
    return succeeded, None if succeeded else "native_patch_failed"


def summary_matches(
    output: Any, expected: dict[str, dict[str, Any]], working_dir: str | None
) -> bool:
    prefix = "Success. Updated the following files:\n"
    if not isinstance(output, str) or not output.startswith(prefix):
        return False
    lines = output[len(prefix) :].split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    symbols = {"add": "A", "update": "M", "delete": "D"}
    actual = []
    for line in lines:
        if len(line) < 3 or line[0] not in "AMD" or line[1] != " ":
            return False
        actual.append((line[0], normalized(line[2:], working_dir)))
    wanted = {
        (symbols[entry["type"]], entry.get("move_path") or path) for path, entry in expected.items()
    }
    return len(actual) == len(wanted) and set(actual) == wanted


def native_edits(store: Store, call: Event, value: Event) -> None:
    # 只由已识别的 event_msg 调用；任意工具输出中的 success/status 不是原生回执。
    from rg.derive.edits import save

    working_dir = cwd(store, call)
    item = block(value)
    begins = patch_begins(store, value)
    begin = block(begins[0]) if len(begins) == 1 else None
    if begin and RG_BLOCK.search(dumps(begin)):
        raise ValueError("rg_context_in_tool_payload")
    primary = not native_patch(call, "patch_apply_begin")
    files = requested_files(call)
    requested = indexed(
        {
            part.path: {
                "type": part.operation,
                **({"content": part.after} if part.operation == "add" else {}),
                **({"move_path": part.destination} if part.operation == "update" else {}),
            }
            for part in files
        },
        working_dir,
    )
    report, succeeded, gap = receipt(item, begin, requested, working_dir)
    for part in files:
        entry = (report or {}).get(normalized(part.path, working_dir), {})
        known = gap != "native_patch_metadata_conflict"
        before = entry.get("content") if known and part.operation == "delete" else None
        after = part.after if succeeded and gap is None and part.operation == "add" else None
        reason = gap or (
            "native_patch_input_missing"
            if not primary
            else "postimage_unknown"
            if before is not None
            else "preimage_unknown"
        )
        save(
            store,
            call,
            value,
            part.destination or part.path,
            part.operation,
            part.text,
            before,
            after,
            reason,
        )


def request_matches(
    requested: dict[str, dict[str, Any]], report: dict[str, dict[str, Any]]
) -> bool:
    return requested.keys() == report.keys() and all(
        entry["type"] == report[path]["type"]
        and (entry["type"] != "add" or entry.get("content") == report[path].get("content"))
        and (entry["type"] != "update" or entry.get("move_path") == report[path].get("move_path"))
        for path, entry in requested.items()
    )


def requested_files(call: Event) -> list[PatchFile]:
    from rg.derive.edits import PatchFile, codex_patch

    if not native_patch(call, "patch_apply_begin"):
        raw = block(call).get("input") or block(call).get("arguments")
        if not isinstance(raw, str):
            raise ValueError("unsupported_patch_format")
        files = codex_patch(raw)
    else:
        reported = changes(block(call).get("changes"))
        files = [
            PatchFile(
                path,
                entry["type"],
                dumps({path: entry}),
                entry.get("content") if entry["type"] == "add" else None,
                entry.get("move_path"),
            )
            for path, entry in reported.items()
        ]
    return files


def receipt(
    item: dict[str, Any],
    begin: dict[str, Any] | None,
    requested: dict[str, dict[str, Any]],
    working_dir: str | None,
) -> tuple[dict[str, dict[str, Any]] | None, bool, str | None]:
    succeeded, gap = status(item)
    expected = indexed(changes(begin.get("changes")), working_dir) if begin else None
    observed = (
        indexed(changes(item["changes"]), working_dir)
        if "changes" in item and item["changes"] != {}
        else None
    )
    if expected and observed and expected != observed:
        gap = "native_patch_metadata_conflict"
    if (
        begin
        and begin.get("turn_id")
        and item.get("turn_id")
        and begin["turn_id"] != item["turn_id"]
    ):
        gap = "native_patch_metadata_conflict"
    report = observed or expected
    if report and not request_matches(requested, report):
        gap = "native_patch_metadata_conflict"
    if not report:
        if not summary_matches(item.get("stdout"), requested, working_dir):
            gap = gap or "native_patch_changes_missing"
    elif item.get("status") is None and not observed:
        if not summary_matches(item.get("stdout"), requested, working_dir):
            gap = gap or "native_patch_changes_missing"
    stdout = item.get("stdout")
    if isinstance(stdout, str) and stdout.startswith("Success. Updated the following files:\n"):
        if not summary_matches(stdout, requested, working_dir):
            gap = "native_patch_metadata_conflict"
    return report, succeeded, gap
