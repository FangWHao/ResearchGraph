from __future__ import annotations

import json
import ntpath
from pathlib import Path
from uuid import uuid4

import pytest

from rg.api.views import evidence
from rg.derive.native_patch import indexed
from rg.derive.records import bound_path
from rg.derive.worker import derive
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from rg.store.lexical_paths import resolve
from tests.golden.test_ingestion import lines
from tests.golden.test_l1 import claude_call, claude_result, codex_item, reported_edit
from tests.golden.test_native_patch import native

MAIN = r"C:\synthetic\合成 项目"
OTHER = r"C:\synthetic\另一工作树"


def scene(store: Store, tmp_path: Path, root: str = MAIN):
    project = store.project("原生路径合成", [tmp_path / "anchor"])
    identity = add_root(store, project, root)
    return project, identity


def add_root(store: Store, project: str, path: str) -> str:
    identity = str(uuid4())
    store.db.execute(
        "INSERT INTO source_roots(root_id,project_id,host_id,path,kind,git_metadata) "
        "VALUES (?,?,'local',?,'repo','{}')",
        (identity, project, path),
    )
    return identity


def ingest(store, tmp_path, project, records, tool="claude", name="input"):
    source = tmp_path / (name + ".jsonl")
    lines(source, records)
    scan_file(store, source, tool, project)
    return source


@pytest.mark.parametrize(
    "path,cwd,expected",
    [
        (MAIN + r"\code.py", None, MAIN + r"\code.py"),
        ("C:/synthetic/合成 项目/code.py", None, MAIN + r"\code.py"),
        (r"analysis\..\code.py", MAIN, MAIN + r"\code.py"),
        ("../code.py", MAIN + r"\nested", MAIN + r"\code.py"),
        (r"\\server\share\合成\code.py", None, r"\\server\share\合成\code.py"),
        ("code.py", r"\\server\share\合成", r"\\server\share\合成\code.py"),
        ("../code.py", "/synthetic/nested", "/synthetic/code.py"),
    ],
)
def test_lexical_paths_preserve_native_flavor_without_filesystem_access(path, cwd, expected):
    assert resolve(path, cwd) == expected


@pytest.mark.parametrize(
    "path",
    [
        "C:code.py",
        r"\code.py",
        "/code.py",
        "code.py:stream",
        MAIN + r"\code.py:stream",
        r"\\?\C:\synthetic\code.py",
        "//?/C:/synthetic/code.py",
        r"\\.\C:\synthetic\code.py",
        "//server",  # 缺少共享名。
        "code\0.py",
        "code\n.py",
        "http://example.invalid/code.py",
    ],
)
def test_incomplete_device_or_stream_paths_do_not_borrow_a_drive(path):
    assert resolve(path, MAIN) is None


def test_binding_keeps_physical_roots_and_rejects_foreign_nested_root(store, tmp_path):
    project, root = scene(store, tmp_path)
    another = add_root(store, project, OTHER)
    assert bound_path(store, project, "code.py", MAIN) == (root, MAIN + r"\code.py")
    assert bound_path(store, project, r"..\code.py", OTHER + r"\nested") == (
        another,
        OTHER + r"\code.py",
    )
    assert bound_path(store, project, r"..\outside.py", MAIN) == (None, None)
    nested = MAIN + r"\foreign"
    foreign = store.project("另一个项目", [tmp_path / "foreign-anchor"])
    add_root(store, foreign, nested)
    assert bound_path(store, project, "code.py", nested) == (None, None)
    assert bound_path(store, project, MAIN + "-copy/code.py", None) == (None, None)


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_windows_runtime_uses_native_directory_then_explicit_workdir_and_keeps_command_literal(
    store, tmp_path, tool
):
    project, main = scene(store, tmp_path)
    other = add_root(store, project, OTHER)
    command = "synthetic --never-execute && echo exit_code: 0"
    if tool == "claude":
        records = [
            claude_call("one", cwd=MAIN, args={"command": command}),
            claude_result("one", metadata={"exit_code": 3}),
            claude_call("two", cwd=MAIN, args={"command": command, "cwd": OTHER}),
            claude_call("bad", cwd=MAIN, args={"command": command, "workdir": ""}),
        ]
    else:
        records = [
            {"type": "session_meta", "payload": {"id": "win-runs", "cwd": MAIN}},
            codex_item(
                "function_call",
                name="exec_command",
                call_id="one",
                arguments=dumps({"cmd": command}),
            ),
            codex_item(
                "function_call_output",
                call_id="one",
                output={"wall_time_seconds": 0, "exit_code": 3, "output": "exit_code: 0"},
            ),
            codex_item(
                "function_call",
                name="exec_command",
                call_id="two",
                arguments=dumps({"cmd": command, "workdir": OTHER}),
            ),
            codex_item(
                "function_call",
                name="exec_command",
                call_id="bad",
                arguments=dumps({"cmd": command, "workdir": ""}),
            ),
        ]
    ingest(store, tmp_path, project, records, tool)
    rows = {r["call_id"]: r for r in store.db.execute("SELECT * FROM runs")}
    assert (rows["one"]["cwd"], rows["one"]["root_id"], rows["one"]["exit_code"]) == (MAIN, main, 3)
    assert rows["one"]["command"] == command
    assert (rows["two"]["cwd"], rows["two"]["root_id"], rows["two"]["state"]) == (
        OTHER,
        other,
        "requested",
    )
    assert rows["bad"]["cwd"] is None and rows["bad"]["gap"] == "cwd_root_unknown"


def test_claude_edit_report_produces_distinct_candidate_versions_with_native_paths(store, tmp_path):
    project, main = scene(store, tmp_path)
    other = add_root(store, project, OTHER)
    for name, directory in (("main", MAIN), ("other", OTHER)):
        args = {"file_path": "code.py", "old_string": "old", "new_string": "新"}
        ingest(
            store,
            tmp_path,
            project,
            [
                claude_call(name, "Edit", args, cwd=directory),
                claude_result(
                    name, metadata=reported_edit(filePath=directory + r"\code.py", new="新")
                ),
            ],
            name=name,
        )
    edits = list(store.db.execute("SELECT * FROM edit_records ORDER BY request_event_id"))
    assert [(r["root_id"], r["path"]) for r in edits] == [
        (main, MAIN + r"\code.py"),
        (other, OTHER + r"\code.py"),
    ]
    versions = list(store.db.execute("SELECT * FROM artifact_versions"))
    assert len(versions) == 4 and len({r["version_id"] for r in versions}) == 4
    assert {r["algo"] for r in versions} == {"sha256:tool-utf8"}
    assert {r["claim_state"] for r in versions} == {"candidate"}
    shown = evidence(store, edits[0]["result_event_id"], {})["l1"]["edits"][0]["diff"]
    assert shown["complete_versions"] and "-old" in shown["text"] and "+新" in shown["text"]
    before = list(store.db.iterdump())
    derive(store)
    assert list(store.db.iterdump()) == before


def test_native_patch_relative_request_matches_absolute_windows_receipt_and_move(store, tmp_path):
    project, root = scene(store, tmp_path)
    request = {
        "added.py": {"type": "add", "content": "新增\n"},
        "changed.py": {
            "type": "update",
            "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
            "move_path": "moved.py",
        },
    }
    report = {
        ntpath.join(MAIN, key): value
        | ({"move_path": MAIN + r"\moved.py"} if key == "changed.py" else {})
        for key, value in request.items()
    }
    ingest(
        store,
        tmp_path,
        project,
        [
            {"type": "session_meta", "payload": {"id": "win-patch", "cwd": MAIN}},
            native("patch_apply_begin", changes=request),
            native(
                "patch_apply_end",
                changes=report,
                success=True,
                status="completed",
                stdout="",
                stderr="",
            ),
        ],
        "codex",
    )
    rows = list(store.db.execute("SELECT * FROM edit_records ORDER BY path"))
    assert len(rows) == 2 and {r["root_id"] for r in rows} == {root}
    added = next(r for r in rows if r["operation"] == "add")
    moved = next(r for r in rows if r["operation"] == "update")
    assert added["path"] == MAIN + r"\added.py" and added["after_version"]
    assert moved["path"] == MAIN + r"\moved.py" and moved["gap"] == "native_patch_input_missing"
    assert moved["before_version"] is None and moved["after_version"] is None


def test_duplicate_windows_spelling_in_receipt_is_ambiguous_instead_of_overwriting():
    with pytest.raises(ValueError, match="ambiguous_native_patch_path"):
        indexed(
            {
                "code.py": {"type": "add", "content": "a"},
                MAIN + r"\code.py": {"type": "add", "content": "b"},
            },
            MAIN,
        )


def test_claude_report_for_another_file_keeps_known_preimage_and_path_conflict(store, tmp_path):
    project, _ = scene(store, tmp_path)
    ingest(
        store,
        tmp_path,
        project,
        [
            claude_call(
                "edit",
                "Edit",
                {"file_path": "requested.py", "old_string": "old", "new_string": "新"},
                cwd=MAIN,
            ),
            claude_result(
                "edit", metadata=reported_edit(filePath=MAIN + r"\different.py", new="新")
            ),
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["gap"] == "reported_path_disagrees_with_request"
    assert row["before_version"] and row["after_version"] is None
    assert row["path"] == MAIN + r"\different.py"


@pytest.mark.parametrize("summary", [MAIN + r"\added.py", MAIN + r"\wrong.py"])
def test_legacy_codex_patch_receipt_uses_equal_windows_path_and_never_guesses_success(
    store, tmp_path, summary
):
    project, _ = scene(store, tmp_path)
    patch = "*** Begin Patch\n*** Add File: added.py\n+新增\n*** End Patch"
    ingest(
        store,
        tmp_path,
        project,
        [
            {"type": "session_meta", "payload": {"id": "legacy-win", "cwd": MAIN}},
            codex_item("custom_tool_call", name="apply_patch", call_id="patch", input=patch),
            codex_item(
                "custom_tool_call_output",
                call_id="patch",
                output="Success. Updated the following files:\nA " + summary + "\n",
            ),
        ],
        "codex",
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["path"] == MAIN + r"\added.py"
    assert bool(row["after_version"]) == (summary == MAIN + r"\added.py")
    assert row["before_version"] is None


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_invalid_later_native_context_prevents_fallback_to_last_session_directory(
    store, tmp_path, tool
):
    project, _ = scene(store, tmp_path)
    if tool == "claude":
        records = [
            claude_call("first", cwd=MAIN),
            claude_call("bad", cwd="C:relative"),
            claude_call("after"),
        ]
        records[-1].pop("cwd")
    else:
        records = [
            {"type": "session_meta", "payload": {"id": "invalid", "cwd": MAIN}},
            {"type": "turn_context", "payload": {"cwd": "C:relative"}},
            codex_item(
                "function_call",
                name="exec_command",
                call_id="after",
                arguments=dumps({"cmd": "synthetic"}),
            ),
        ]
    ingest(store, tmp_path, project, records, tool)
    row = store.db.execute("SELECT * FROM runs WHERE call_id='after'").fetchone()
    assert row["cwd"] is None and row["root_id"] is None
    assert row["gap"] == "cwd_root_unknown"


def test_native_execution_begin_windows_cwd_is_fixed_metadata_not_stdout(store, tmp_path):
    project, root = scene(store, tmp_path)
    ingest(
        store,
        tmp_path,
        project,
        [
            {"type": "session_meta", "payload": {"id": "native-run", "cwd": OTHER}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_begin",
                    "call_id": "run",
                    "command": ["synthetic", "--never-run"],
                    "cwd": MAIN,
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "run",
                    "exit_code": 4,
                    "aggregated_output": "exit_code: 0",
                },
            },
        ],
        "codex",
    )
    row = store.db.execute("SELECT * FROM runs").fetchone()
    assert row["cwd"] == MAIN and row["root_id"] == root and row["exit_code"] == 4
    assert json.loads(row["command"]) == ["synthetic", "--never-run"]


def test_real_local_directory_reports_versions_without_reading_or_changing_current_file(
    store, tmp_path
):
    workspace = tmp_path / "公开 本机工作区"
    workspace.mkdir()
    current = workspace / "code.py"
    current.write_bytes(b"present file is different\n")
    project = store.project("本机目录公开验收", [workspace])
    source = ingest(
        store,
        tmp_path,
        project,
        [
            claude_call(
                "edit",
                "Edit",
                {"file_path": "code.py", "old_string": "old", "new_string": "新"},
                cwd=str(workspace),
            ),
            claude_result("edit", metadata=reported_edit(filePath=str(current), new="新")),
        ],
    )
    edit = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert edit["path"] == str(current) and edit["root_id"]
    versions = {
        row["phase"]: store.objects.get(row["content_sha256"]).decode()
        for row in store.db.execute("SELECT * FROM artifact_versions")
    }
    assert versions == {"before": "old\n", "after": "新\n"}
    assert current.read_bytes() == b"present file is different\n"
    assert json.loads(store.raw(edit["request_event_id"]))["cwd"] == str(workspace)
    assert source.is_file()
