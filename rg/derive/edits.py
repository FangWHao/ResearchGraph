from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

from rg.derive.records import Event, arguments, block, bound_path, cwd, native_patch
from rg.store.database import Store, dumps, now
from rg.store.objects import digest

MAX_CONTENT_BYTES = 5_000_000


@dataclass(frozen=True)
class PatchFile:
    path: str
    operation: str
    text: str
    after: str | None = None
    destination: str | None = None


def codex_patch(text: str) -> list[PatchFile]:
    # Rust str::lines 只按 LF/CRLF 分行；U+2028 等字符属于文件正文。
    lines = [line.removesuffix("\r") for line in text.strip("\r\n").split("\n")]
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("unsupported_patch_format")
    files: list[PatchFile] = []
    index = 1
    while index < len(lines) - 1:
        first = index
        marker = lines[index]
        operation = next(
            (op for op in ("Add", "Update", "Delete") if marker.startswith(f"*** {op} File: ")),
            None,
        )
        if operation is None:
            raise ValueError("unsupported_patch_format")
        path = marker[len(f"*** {operation} File: ") :]
        if not path or any(item.path == path for item in files):
            raise ValueError("ambiguous_patch_path")
        index += 1
        destination = None
        if operation == "Update" and lines[index].startswith("*** Move to: "):
            destination = lines[index][len("*** Move to: ") :]
            index += 1
        start = index
        while index < len(lines) - 1 and not lines[index].startswith(
            ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
        ):
            index += 1
        body = lines[start:index]
        after = None
        if operation == "Add":
            if any(not line.startswith("+") for line in body):
                raise ValueError("unsupported_patch_format")
            after = "".join(line[1:] + "\n" for line in body)
        elif operation == "Delete":
            if body:
                raise ValueError("unsupported_patch_format")
        elif not body or any(
            not (line.startswith(("@@", " ", "+", "-")) or line == "*** End of File")
            for line in body
        ):
            raise ValueError("unsupported_patch_format")
        files.append(
            PatchFile(
                path, operation.lower(), "\n".join(lines[first:index]) + "\n", after, destination
            )
        )
    return files


def structured_patch(original: str, hunks: Any) -> str:
    if not isinstance(hunks, list) or not hunks:
        raise ValueError("structured_patch_missing")
    # 未提供字节尾界时不猜无末尾换行或混合换行的结果。
    if original and not original.endswith("\n"):
        raise ValueError("newline_boundary_unknown")
    newline = "\r\n" if "\r\n" in original else "\n"
    old = original.split(newline)
    if old[-1] == "":
        old.pop()
    if any("\n" in line or "\r" in line for line in old):
        raise ValueError("mixed_newlines_unknown")
    result: list[str] = []
    cursor = 0
    for hunk in hunks:
        if not isinstance(hunk, dict) or any(
            type(hunk.get(key)) is not int or hunk[key] < 0
            for key in ("oldStart", "oldLines", "newStart", "newLines")
        ):
            raise ValueError("invalid_structured_patch")
        start = max(0, hunk["oldStart"] - 1)
        if not cursor <= start <= len(old):
            raise ValueError("overlapping_structured_patch")
        result.extend(old[cursor:start])
        if max(0, hunk["newStart"] - 1) != len(result):
            raise ValueError("structured_patch_position_mismatch")
        lines = hunk.get("lines")
        if not isinstance(lines, list) or any(
            not isinstance(line, str) or not line or line[0] not in " +-" or "\n" in line
            for line in lines
        ):
            raise ValueError("unsupported_structured_patch")
        before = [line[1:] for line in lines if line[0] in " -"]
        after = [line[1:] for line in lines if line[0] in " +"]
        if (
            len(before) != hunk["oldLines"]
            or len(after) != hunk["newLines"]
            or old[start : start + len(before)] != before
        ):
            raise ValueError("structured_patch_preimage_mismatch")
        result.extend(after)
        cursor = start + len(before)
    result.extend(old[cursor:])
    return newline.join(result) + (newline if result else "")


def version(
    store: Store, call: Event, value: Event, root: str, path: str, phase: str, content: str
) -> str:
    raw = content.encode("utf-8")
    if len(raw) > MAX_CONTENT_BYTES:
        raise ValueError("reported_content_over_limit")
    sha = store.objects.put(raw)
    identity = digest(
        dumps([call["project_id"], root, path, value["event_id"], phase, sha]).encode()
    )
    store.db.execute(
        "INSERT OR IGNORE INTO artifact_versions "
        "(version_id,project_id,path,algo,digest,size,source,evidence_event_id,observed_at,"
        "content_sha256,phase,root_id,basis,claim_state,representation) "
        "VALUES (?,?,?,'sha256:tool-utf8',?,?,'agent_edit',?,?,?,?,?,"
        "'direct_record','candidate','tool_reported_utf8')",
        (
            identity,
            call["project_id"],
            path,
            sha,
            len(raw),
            value["event_id"],
            value["occurred_at"],
            sha,
            phase,
            root,
        ),
    )
    return identity


def save(
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
    root, absolute = bound_path(store, call["project_id"], path, cwd(store, call))
    patch_sha = store.objects.put(patch.encode())
    before_version = after_version = None
    if root and absolute:
        try:
            if before is not None:
                before_version = version(store, call, value, root, absolute, "before", before)
            if after is not None:
                after_version = version(store, call, value, root, absolute, "after", after)
        except UnicodeError:
            gap = "reported_content_invalid_utf8"
        except ValueError as error:
            gap = str(error)
    else:
        gap = "artifact_root_unknown"
    identity = digest(
        dumps([call["event_id"], value["event_id"], path, operation, patch_sha]).encode()
    )
    store.db.execute(
        "INSERT OR IGNORE INTO edit_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            call["project_id"],
            call["session_pk"],
            call["call_id"],
            call["event_id"],
            value["event_id"],
            absolute,
            root,
            operation,
            patch_sha,
            before_version,
            after_version,
            gap,
            int(user_modified) if user_modified is not None else None,
            value["occurred_at"],
            now(),
        ),
    )


def claude_edit(store: Store, call: Event, value: Event) -> None:
    content = value["record"].get("message", {}).get("content", [])
    tool_count = (
        sum(isinstance(part, dict) and part.get("type") == "tool_result" for part in content)
        if isinstance(content, list)
        else 0
    )
    reported = value["record"].get("toolUseResult") if tool_count == 1 else None
    item = reported if isinstance(reported, dict) else {}
    args = arguments(call)
    path = item.get("filePath") or args.get("file_path")
    original = item.get("originalFile")
    before = original if isinstance(original, str) else None
    after = None
    gap = None
    patch = dumps(item.get("structuredPatch"))
    if block(value).get("is_error") is True:
        gap = "tool_error_after_unknown"
    elif before is None:
        gap = "reported_preimage_missing"
    else:
        try:
            after = structured_patch(before, item.get("structuredPatch"))
            # 输入和工具报告不一致时不能以请求正文补造文件版本。
            expected = args.get("content") if call["tool_name"] == "Write" else None
            if call["tool_name"] == "Edit" and isinstance(args.get("old_string"), str):
                old_string, new_string = args["old_string"], args.get("new_string")
                if not old_string or not isinstance(new_string, str):
                    raise ValueError("reported_edit_request_unsupported")
                count = before.count(old_string)
                if count < 1 or (count > 1 and args.get("replace_all") is not True):
                    raise ValueError("reported_edit_disagrees_with_request")
                expected = before.replace(
                    old_string, new_string, -1 if args.get("replace_all") is True else 1
                )
            if isinstance(expected, str) and expected != after:
                raise ValueError("reported_edit_disagrees_with_request")
        except ValueError as error:
            after = None
            gap = str(error)
    modified = item.get("userModified")
    if modified is True:
        gap = gap or "user_modified"
    save(
        store,
        call,
        value,
        path,
        call["tool_name"].lower(),
        patch,
        before,
        after,
        gap,
        modified if type(modified) is bool else None,
    )


def codex_edit(store: Store, call: Event, value: Event) -> None:
    if native_patch(value, "patch_apply_end"):
        from rg.derive.native_patch import native_edits

        native_edits(store, call, value)
        return
    item = block(call)
    patch = item.get("input") or item.get("arguments")
    if not isinstance(patch, str):
        raise ValueError("unsupported_patch_format")
    files = codex_patch(patch)
    output = block(value).get("output")
    if isinstance(output, str):
        try:
            decoded = json.loads(output)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            output = decoded
    if isinstance(output, dict):
        output = output.get("body", output.get("output"))
    success = isinstance(output, str) and output.startswith(
        "Success. Updated the following files:\n"
    )
    symbols = {"add": "A", "update": "M", "delete": "D"}
    expected = {f"{symbols[part.operation]} {part.destination or part.path}" for part in files}
    if success:
        assert isinstance(output, str)
        success = set(output.splitlines()[1:]) == expected
    for part in files:
        gap = "preimage_unknown" if success else "patch_success_not_proven"
        save(
            store,
            call,
            value,
            part.destination or part.path,
            part.operation,
            part.text,
            None,
            part.after if success else None,
            gap,
        )


def edit(store: Store, call: Event, value: Event) -> None:
    if call["tool"] == "claude":
        claude_edit(store, call, value)
    else:
        codex_edit(store, call, value)


def diff(store: Store, row: dict[str, Any]) -> dict[str, Any]:
    limit = 64_000
    before, after = row["before_version"], row["after_version"]
    if before and after:
        contents = []
        for identity in (before, after):
            sha = store.db.execute(
                "SELECT content_sha256 FROM artifact_versions WHERE version_id=?", (identity,)
            ).fetchone()[0]
            raw = store.objects.get(sha)
            if len(raw) > limit or raw.count(b"\n") > 2000:
                return {"available": False, "reason": "版本正文超过差异展示上限", "gap": row["gap"]}
            contents.append(raw.decode())
        text = "".join(
            difflib.unified_diff(
                contents[0].splitlines(keepends=True),
                contents[1].splitlines(keepends=True),
                fromfile="编辑前",
                tofile="编辑后",
            )
        )
        kind = "reported_versions"
    elif before or after:
        sha = store.db.execute(
            "SELECT content_sha256 FROM artifact_versions WHERE version_id=?", (before or after,)
        ).fetchone()[0]
        raw = store.objects.get(sha)
        if len(raw) > limit or raw.count(b"\n") > 2000:
            return {"available": False, "reason": "版本正文超过差异展示上限", "gap": row["gap"]}
        text = raw.decode()
        kind = "reported_before" if before else "reported_after"
    else:
        raw = store.objects.get(row["patch_sha256"])
        if len(raw) > limit:
            return {"available": False, "reason": "补丁超过差异展示上限", "gap": row["gap"]}
        text = raw.decode()
        kind = "patch_only"
    if len(text.encode()) > limit or text.count("\n") > 2000:
        return {"available": False, "reason": "差异超过展示上限", "gap": row["gap"]}
    return {
        "available": True,
        "format": kind,
        "text": text,
        "complete_versions": bool(before and after),
        "gap": row["gap"],
        "reason": "工具报告的候选文本版本"
        if before and after
        else "工具报告的编辑前候选全文；编辑后版本未知"
        if before
        else "工具报告的编辑后候选全文；编辑前版本未知"
        if after
        else "仅有补丁；编辑前后完整版本未知",
    }
