"""公开合成 MultiEdit；旧分支仅用于重现已存双侧候选，不生成核验结论。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from rg.derive.edits import save, structured_patch
from rg.derive.records import Event, arguments
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from tests.golden.test_ingestion import lines

BEFORE = "旧 旧\n<script>字面</script>\n"
AFTER = "新 新\n<script>字面</script>\n"


def transcript(identity: str, edits: Any, *, before: str = BEFORE, after: str = AFTER):
    old = before.removesuffix("\n").split("\n")
    new = after.removesuffix("\n").split("\n")
    return [
        {
            "type": "assistant",
            "uuid": f"request-{identity}",
            "sessionId": f"multiedit-{identity}",
            "timestamp": "2026-10-10T08:00:00Z",
            "cwd": "/synthetic/multiedit",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": identity,
                        "name": "MultiEdit",
                        "input": {
                            "file_path": "/synthetic/multiedit/example.py",
                            "edits": edits,
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "uuid": f"result-{identity}",
            "sessionId": f"multiedit-{identity}",
            "timestamp": "2026-10-10T08:01:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": identity,
                        "content": "合成工具报告，不执行正文",
                    }
                ]
            },
            "toolUseResult": {
                "filePath": "/synthetic/multiedit/example.py",
                "originalFile": before,
                "structuredPatch": [
                    {
                        "oldStart": 1,
                        "oldLines": len(old),
                        "newStart": 1,
                        "newLines": len(new),
                        "lines": [*["-" + text for text in old], *["+" + text for text in new]],
                    }
                ],
            },
        },
    ]


def legacy_edit(store: Store, call: Event, value: Event) -> None:
    """重现修复前分支：严格重建补丁，但未核对 MultiEdit 请求。"""
    reported = value["record"]["toolUseResult"]
    before = reported["originalFile"]
    save(
        store,
        call,
        value,
        arguments(call)["file_path"],
        "multiedit",
        dumps(reported["structuredPatch"]),
        before,
        structured_patch(before, reported["structuredPatch"]),
        None,
    )


def seed_case(
    store: Store,
    directory: Path,
    project: str,
    identity: str,
    edits: Any,
    *,
    legacy: bool = False,
    before: str = BEFORE,
    after: str = AFTER,
) -> dict[str, int | str]:
    source = directory / f"{identity}.jsonl"
    lines(source, transcript(identity, edits, before=before, after=after))
    if legacy:
        with patch("rg.derive.worker.edit", side_effect=legacy_edit):
            scan_file(store, source, "claude", project)
    else:
        scan_file(store, source, "claude", project)
    row = store.db.execute(
        "SELECT * FROM edit_records WHERE project_id=? AND call_id=?", (project, identity)
    ).fetchone()
    assert row is not None
    ids = {key: row[key] for key in ("request_event_id", "result_event_id", "edit_id")}
    if legacy:
        assert row["after_version"] is not None and row["gap"] is None
        ids["saved_after_version"] = row["after_version"]
    return ids


def seed_multiedit_store(store: Store, directory: Path):
    project = store.project("合成 MultiEdit 顺序核验", [Path("/synthetic/multiedit")])
    valid = [
        {"old_string": "旧", "new_string": "中", "replace_all": True},
        {"old_string": "中", "new_string": "新", "replace_all": True},
    ]
    invalid = [valid[0], {"old_string": "不存在", "new_string": "新", "replace_all": True}]
    cases = {
        "valid": seed_case(store, directory, project, "valid", valid),
        "mismatch": seed_case(store, directory, project, "mismatch", invalid),
        "legacy_mismatch": seed_case(
            store, directory, project, "legacy_mismatch", invalid, legacy=True
        ),
        "legacy_unavailable": seed_case(
            store, directory, project, "legacy_unavailable", None, legacy=True
        ),
    }
    return project, cases
