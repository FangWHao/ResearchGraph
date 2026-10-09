from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rg.api.views import evidence
from rg.derive.edits import codex_patch, structured_patch
from rg.derive.runtime import execution_result
from rg.derive.worker import derive
from rg.ingest.scanner import scan_file
from rg.slim.slimmer import slim_session
from rg.store.backup import backup
from rg.store.database import Store
from rg.store.locking import TaskBusy, exclusive
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines


def claude_call(identity="run", name="Bash", args=None, **extra):
    return {
        "type": "assistant",
        "uuid": f"call-{identity}",
        "sessionId": "l1-synthetic",
        "cwd": "/synthetic/project",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": identity,
                    "name": name,
                    "input": args or {"command": "synthetic --never-execute"},
                }
            ]
        },
        **extra,
    }


def claude_result(identity="run", text="合成输出", metadata=None, **extra):
    return {
        "type": "user",
        "uuid": f"result-{identity}",
        "sessionId": "l1-synthetic",
        "message": {"content": [{"type": "tool_result", "tool_use_id": identity, "content": text}]},
        "toolUseResult": metadata,
        **extra,
    }


def codex_item(kind, **payload):
    return {"type": "response_item", "payload": {"type": kind, **payload}}


def ingest(store, tmp_path, records, tool="claude", project=None, filename="input.jsonl"):
    project = project or store.project("合成运行与编辑", [Path("/synthetic/project")])
    source = tmp_path / filename
    lines(source, records)
    scan_file(store, source, tool, project)
    return source, project


def runs(store):
    return [dict(row) for row in store.db.execute("SELECT * FROM runs ORDER BY request_event_id")]


@pytest.mark.parametrize(
    "output,code,state",
    [
        ("stdout exit_code: 9", None, "unknown"),
        ('{"exit_code":0}', None, "unknown"),
        ({"exit_code": 0, "output": "text"}, None, "unknown"),
        ({"wall_time_seconds": True, "exit_code": 0, "output": "text"}, None, "unknown"),
        ({"wall_time_seconds": 0.1, "exit_code": 0, "output": "exit_code: 9"}, 0, "exited"),
        ({"wall_time_seconds": 0, "session_id": 42, "output": ""}, None, "started"),
        ({"metadata": {"exit_code": 2}, "output": "exit_code: 0"}, 2, "exited"),
        ({"metadata": {"exit_code": True}, "output": ""}, None, "unknown"),
        ({"metadata": {"exit_code": 0, "exitCode": 3}, "output": ""}, None, "unknown"),
        ("Wall time: 0.1 seconds\nProcess exited with code 0\nOutput:\nexit_code: 9", 0, "exited"),
        ("prefix\nWall time: 1 seconds\nProcess exited with code 0\nOutput:\n", None, "unknown"),
    ],
)
def test_only_native_executor_metadata_is_a_runtime_fact(output, code, state):
    result = execution_result(output)
    assert (result.exit_code, result.state) == (code, state)


@pytest.mark.parametrize(
    "metadata,state,code",
    [
        (None, "unknown", None),
        ({"exit_code": 0}, "exited", 0),
        ({"exit_code": 2}, "exited", 2),
        ({"exit_code": True}, "unknown", None),
    ],
)
def test_claude_missing_exit_and_is_error_never_mean_success(
    store, tmp_path, metadata, state, code
):
    result = claude_result(text="exit_code: 0", metadata=metadata)
    result["message"]["content"][0]["is_error"] = False
    ingest(store, tmp_path, [claude_call(), result])
    assert (runs(store)[0]["state"], runs(store)[0]["exit_code"]) == (state, code)
    assert runs(store)[0]["started_at"] is None


def test_request_without_result_is_requested_not_failed(store, tmp_path):
    ingest(store, tmp_path, [claude_call()])
    assert runs(store)[0]["state"] == "requested"
    assert runs(store)[0]["exit_code"] is None
    assert store.db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 0


def test_identical_commands_with_different_calls_are_distinct(store, tmp_path):
    ingest(store, tmp_path, [claude_call("one"), claude_call("two")])
    assert len(runs(store)) == 2


def test_codex_call_id_is_scoped_to_actual_session(store, tmp_path):
    project = store.project("合成", [Path("/synthetic/project")])
    for session in ("one", "two"):
        ingest(
            store,
            tmp_path,
            [
                {"type": "session_meta", "payload": {"id": session, "cwd": "/synthetic/project"}},
                codex_item(
                    "function_call",
                    name="exec_command",
                    call_id="same",
                    arguments=json.dumps({"cmd": "synthetic"}),
                ),
                codex_item(
                    "function_call_output",
                    call_id="same",
                    output={"wall_time_seconds": 0, "exit_code": 0, "output": ""},
                ),
            ],
            "codex",
            project,
            f"{session}.jsonl",
        )
    assert len(runs(store)) == 2
    assert {row["state"] for row in runs(store)} == {"exited"}
    assert (
        store.db.execute("SELECT count(*) FROM raw_events WHERE alias_of IS NOT NULL").fetchone()[0]
        == 0
    )


def test_late_result_uses_call_id_and_never_nearest_command(store, tmp_path):
    ingest(store, tmp_path, [claude_result(metadata={"exit_code": 7}), claude_call("other")])
    assert runs(store)[0]["state"] == "requested"
    path = tmp_path / "input.jsonl"
    with path.open("a") as stream:
        stream.write(json.dumps(claude_call()) + "\n")
    scan_file(store, path, "claude")
    by_call = {row["call_id"]: row for row in runs(store)}
    assert by_call["run"]["exit_code"] == 7
    assert by_call["other"]["state"] == "requested"


def test_conflicting_reused_call_id_invalidates_projection_and_keeps_facts(store, tmp_path):
    source, _ = ingest(store, tmp_path, [claude_call(), claude_result(metadata={"exit_code": 0})])
    changed = claude_call(args={"command": "different"}, uuid="different-call")
    with source.open("a") as stream:
        stream.write(json.dumps(changed) + "\n")
    scan_file(store, source, "claude")
    assert (runs(store)[0]["state"], runs(store)[0]["exit_code"], runs(store)[0]["gap"]) == (
        "unknown",
        None,
        "ambiguous_call_id",
    )
    assert store.db.execute("SELECT count(*) FROM run_observations").fetchone()[0] == 3


def test_same_uuid_result_metadata_changes_are_not_deduplicated(store, tmp_path):
    source, _ = ingest(store, tmp_path, [claude_call(), claude_result(metadata={"exit_code": 0})])
    with source.open("a") as stream:
        stream.write(json.dumps(claude_result(metadata={"exit_code": 5})) + "\n")
    scan_file(store, source, "claude")
    assert runs(store)[0]["gap"] == "conflicting_execution_facts"
    assert (
        store.db.execute("SELECT count(*) FROM raw_events WHERE alias_of IS NOT NULL").fetchone()[0]
        == 0
    )


def test_async_poll_binds_unique_handle_in_same_session(store, tmp_path):
    records = [
        {"type": "session_meta", "payload": {"id": "async", "cwd": "/synthetic/project"}},
        codex_item(
            "function_call",
            name="exec_command",
            call_id="run",
            arguments=json.dumps({"cmd": "synthetic"}),
        ),
        codex_item(
            "function_call_output",
            call_id="run",
            output={"wall_time_seconds": 0, "session_id": 42, "output": ""},
        ),
        codex_item(
            "function_call",
            name="write_stdin",
            call_id="poll",
            arguments=json.dumps({"session_id": 42}),
        ),
        codex_item(
            "function_call_output",
            call_id="poll",
            output={"wall_time_seconds": 1, "exit_code": 7, "output": ""},
        ),
    ]
    ingest(store, tmp_path, records, "codex")
    assert (runs(store)[0]["state"], runs(store)[0]["exit_code"]) == ("exited", 7)
    observations = store.db.execute("SELECT state FROM run_observations").fetchall()
    assert {row[0] for row in observations} == {"requested", "started", "exited"}


def test_native_execution_events_preserve_literal_command_without_execution(store, tmp_path):
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "native", "cwd": "/synthetic/project"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_begin",
                    "call_id": "native",
                    "command": ["synthetic", "--never-run"],
                    "cwd": "/synthetic/project",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "native",
                    "exit_code": 3,
                    "aggregated_output": "exit_code: 0",
                },
            },
        ],
        "codex",
    )
    assert runs(store)[0]["exit_code"] == 3
    assert json.loads(runs(store)[0]["command"]) == ["synthetic", "--never-run"]


def test_codex_cwd_uses_preceding_turn_not_last_session_path(store, tmp_path):
    project = store.project("双根合成", [Path("/synthetic/one"), Path("/synthetic/two")])
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "roots", "cwd": "/synthetic/one"}},
            codex_item(
                "function_call", name="exec_command", call_id="one", arguments='{"cmd":"synthetic"}'
            ),
            {"type": "turn_context", "payload": {"cwd": "/synthetic/two"}},
            codex_item(
                "function_call", name="exec_command", call_id="two", arguments='{"cmd":"synthetic"}'
            ),
        ],
        "codex",
        project,
    )
    assert [row["cwd"] for row in runs(store)] == ["/synthetic/one", "/synthetic/two"]
    assert len({row["root_id"] for row in runs(store)}) == 2


def test_l1_never_derives_rg_context_or_research_tool(store, tmp_path):
    ingest(
        store,
        tmp_path,
        [
            claude_call("research", "research.ask"),
            claude_result("research"),
            claude_call("context", args={"command": "<rg-context>exit</rg-context>"}),
            claude_result("context", metadata={"exit_code": 0}),
            claude_call("metadata"),
            claude_result(
                "metadata", metadata={"exit_code": 0, "stdout": "<rg-context>x</rg-context>"}
            ),
        ],
    )
    assert len(runs(store)) == 1
    assert runs(store)[0]["state"] == "requested"
    assert store.db.execute("SELECT count(*) FROM run_observations").fetchone()[0] == 1


def reported_edit(original="old\n", new="new", **extra):
    return {
        "filePath": "/synthetic/project/code.py",
        "originalFile": original,
        "structuredPatch": [
            {
                "oldStart": 1,
                "oldLines": 1,
                "newStart": 1,
                "newLines": 1,
                "lines": ["-old", "+" + new],
            }
        ],
        **extra,
    }


def test_reported_full_versions_are_candidates_and_survive_source_backup(store, tmp_path):
    source, _ = ingest(
        store,
        tmp_path,
        [
            claude_call(
                "edit",
                "Edit",
                {
                    "file_path": "/synthetic/project/code.py",
                    "old_string": "old",
                    "new_string": "新",
                },
            ),
            claude_result("edit", metadata=reported_edit(new="新")),
        ],
    )
    result_id = 2
    payload = evidence(store, result_id, {})
    assert len(payload["artifact_versions"]) == 2
    assert {row["claim_state"] for row in payload["artifact_versions"]} == {"candidate"}
    assert {row["algo"] for row in payload["artifact_versions"]} == {"sha256:tool-utf8"}
    diff = payload["l1"]["edits"][0]["diff"]
    assert diff["format"] == "reported_versions" and diff["complete_versions"] is True
    assert "-old" in diff["text"] and "+新" in diff["text"]
    source.unlink()
    destination = tmp_path / "backup"
    counts = backup(store, destination)
    assert counts["objects"] == 5
    restored = Store(destination)
    assert evidence(restored, result_id, {})["l1"]["edits"][0]["diff"] == diff
    restored.close()


@pytest.mark.parametrize(
    "metadata,gap",
    [
        (reported_edit(original="different\n"), "structured_patch_preimage_mismatch"),
        (reported_edit(original="old"), "newline_boundary_unknown"),
        (reported_edit(userModified=True), "user_modified"),
    ],
)
def test_edit_gap_never_uses_present_file_to_repair_history(store, tmp_path, metadata, gap):
    (tmp_path / "code.py").write_text("current unrelated file\n")
    ingest(
        store,
        tmp_path,
        [
            claude_call("edit", "Edit", {"file_path": "/synthetic/project/code.py"}),
            claude_result("edit", metadata=metadata),
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["gap"] == gap
    if gap != "user_modified":
        assert row["after_version"] is None
    assert (tmp_path / "code.py").read_text() == "current unrelated file\n"


@pytest.mark.parametrize(
    "operation,body,summary,after",
    [
        ("Add", "+one\u2028two\n", "A code.py", "one\u2028two\n"),
        ("Update", "@@\n-old\n+new\n", "M code.py", None),
        ("Delete", "", "D code.py", None),
    ],
)
def test_codex_patch_does_not_invent_preimage(store, tmp_path, operation, body, summary, after):
    patch = f"*** Begin Patch\n*** {operation} File: code.py\n{body}*** End Patch\n"
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "patch", "cwd": "/synthetic/project"}},
            codex_item("custom_tool_call", name="apply_patch", call_id="patch", input=patch),
            codex_item(
                "custom_tool_call_output",
                call_id="patch",
                output=json.dumps(
                    {"body": "Success. Updated the following files:\n" + summary + "\n"}
                ),
            ),
        ],
        "codex",
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["before_version"] is None and row["gap"] == "preimage_unknown"
    assert evidence(store, 3, {})["l1"]["edits"][0]["diff"]["format"] == "patch_only"
    if after is not None:
        sha = store.db.execute(
            "SELECT content_sha256 FROM artifact_versions WHERE version_id=?",
            (row["after_version"],),
        ).fetchone()[0]
        assert store.objects.get(sha).decode() == after
    else:
        assert row["after_version"] is None


def test_failed_patch_summary_cannot_prove_any_postimage(store, tmp_path):
    patch = "*** Begin Patch\n*** Add File: code.py\n+new\n*** End Patch\n"
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "patch", "cwd": "/synthetic/project"}},
            codex_item("custom_tool_call", name="apply_patch", call_id="patch", input=patch),
            codex_item("custom_tool_call_output", call_id="patch", output="tool failed"),
        ],
        "codex",
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["after_version"] is None and row["gap"] == "patch_success_not_proven"


def test_fact_edit_append_only_and_derive_idempotent(store, tmp_path):
    ingest(
        store,
        tmp_path,
        [
            claude_call(),
            claude_result(metadata={"exit_code": 0}),
            claude_call("edit", "Edit", {"file_path": "/synthetic/project/code.py"}),
            claude_result("edit", metadata=reported_edit()),
        ],
    )
    assert derive(store) == {"l1_remaining": 0}
    for table in ("run_observations", "edit_records"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE run_observations SET state='exited'")


def test_l1_crash_rolls_back_facts_without_touching_l0_cursor(store, tmp_path, monkeypatch):
    with monkeypatch.context() as change:
        change.setattr("rg.derive.worker.derive", lambda *args, **kwargs: {})
        ingest(store, tmp_path, [claude_call(), claude_result(metadata={"exit_code": 0})])
    raw = store.raw(1)
    offset = store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0]

    def fail():
        raise RuntimeError("模拟派生提交前崩溃")

    with pytest.raises(RuntimeError):
        derive(store, fault=fail)
    assert runs(store) == []
    assert store.db.execute("SELECT count(*) FROM run_observations").fetchone()[0] == 0
    assert store.raw(1) == raw
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == offset
    assert derive(store)["l1_done"] == 2
    assert runs(store)[0]["exit_code"] == 0


def test_slim_cache_upgrade_removes_false_stdout_exit_code(store, tmp_path):
    ingest(store, tmp_path, [claude_call(), claude_result(text="exit_code: 9")])
    counter = FakeProvider()
    slim_session(store, 1, counter)
    store.db.execute(
        "UPDATE slim_events SET text='false cached exit',slim_version=? WHERE event_id=2",
        (counter.provider + ":" + counter.model + ":1",),
    )
    slim = slim_session(store, 1, counter)
    assert json.loads(slim[1]["text"])["exit_code"] == "unknown"
    assert "exit_code: 9" in store.raw(2).decode()


def test_structured_patch_crlf_and_no_unicode_line_split():
    patch = [
        {
            "oldStart": 1,
            "oldLines": 1,
            "newStart": 1,
            "newLines": 1,
            "lines": ["-old", "+one\u2028two"],
        }
    ]
    assert structured_patch("old\r\n", patch) == "one\u2028two\r\n"
    assert (
        codex_patch("*** Begin Patch\n*** Add File: a\n+x\u2028y\n*** End Patch\n")[0].after
        == "x\u2028y\n"
    )


def test_ambiguous_claude_metadata_is_not_applied_to_multiple_results(store, tmp_path):
    result = claude_result("one", metadata={"exit_code": 0})
    result["message"]["content"].append(
        {"type": "tool_result", "tool_use_id": "two", "content": "another result"}
    )
    ingest(store, tmp_path, [claude_call("one"), claude_call("two"), result])
    assert {row["state"] for row in runs(store)} == {"unknown"}
    assert {row["exit_code"] for row in runs(store)} == {None}
    assert {row["gap"] for row in runs(store)} == {"ambiguous_tool_metadata"}


def test_same_uuid_identical_result_replay_keeps_single_runtime_fact(store, tmp_path):
    source, _ = ingest(store, tmp_path, [claude_call(), claude_result(metadata={"exit_code": 0})])
    with source.open("a") as stream:
        stream.write(json.dumps(claude_result(metadata={"exit_code": 0})) + "\n")
    assert scan_file(store, source, "claude")["aliases"] == 1
    assert store.db.execute("SELECT count(*) FROM run_observations").fetchone()[0] == 2


def test_reused_executor_handle_never_attaches_poll_to_a_guessed_run(store, tmp_path):
    records = [{"type": "session_meta", "payload": {"id": "handles", "cwd": "/synthetic/project"}}]
    for identity in ("one", "two"):
        records.extend(
            [
                codex_item(
                    "function_call",
                    name="exec_command",
                    call_id=identity,
                    arguments='{"cmd":"synthetic"}',
                ),
                codex_item(
                    "function_call_output",
                    call_id=identity,
                    output={"wall_time_seconds": 0, "session_id": 42, "output": ""},
                ),
            ]
        )
    records.extend(
        [
            codex_item(
                "function_call", name="write_stdin", call_id="poll", arguments='{"session_id":42}'
            ),
            codex_item(
                "function_call_output",
                call_id="poll",
                output={"wall_time_seconds": 1, "exit_code": 0, "output": ""},
            ),
        ]
    )
    ingest(store, tmp_path, records, "codex")
    assert {row["state"] for row in runs(store)} == {"started"}
    assert (
        evidence(store, 7, {})["l1"]["derivation"]["error"]
        == "executor_session_unknown_or_ambiguous"
    )
    assert evidence(store, 7, {})["l1"]["runs"] == []


def test_poll_handle_is_never_joined_across_sessions(store, tmp_path):
    project = store.project("合成", [Path("/synthetic/project")])
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "first", "cwd": "/synthetic/project"}},
            codex_item(
                "function_call", name="exec_command", call_id="run", arguments='{"cmd":"synthetic"}'
            ),
            codex_item(
                "function_call_output",
                call_id="run",
                output={"wall_time_seconds": 0, "session_id": 42, "output": ""},
            ),
        ],
        "codex",
        project,
        "first.jsonl",
    )
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "second", "cwd": "/synthetic/project"}},
            codex_item(
                "function_call", name="write_stdin", call_id="poll", arguments='{"session_id":42}'
            ),
            codex_item(
                "function_call_output",
                call_id="poll",
                output={"wall_time_seconds": 1, "exit_code": 0, "output": ""},
            ),
        ],
        "codex",
        project,
        "second.jsonl",
    )
    assert runs(store)[0]["state"] == "started"
    assert evidence(store, 6, {})["l1"]["derivation"]["state"] == "waiting"


def test_foreign_artifact_root_has_no_guessed_file_version(store, tmp_path):
    metadata = reported_edit(filePath="/foreign/project/code.py")
    ingest(
        store,
        tmp_path,
        [
            claude_call("edit", "Edit", {"file_path": "/foreign/project/code.py"}),
            claude_result("edit", metadata=metadata),
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["gap"] == "artifact_root_unknown"
    assert row["before_version"] is None and row["after_version"] is None
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0


def test_reported_patch_disagreeing_with_edit_request_keeps_only_preimage(store, tmp_path):
    ingest(
        store,
        tmp_path,
        [
            claude_call(
                "edit",
                "Edit",
                {
                    "file_path": "/synthetic/project/code.py",
                    "old_string": "old",
                    "new_string": "different requested text",
                },
            ),
            claude_result("edit", metadata=reported_edit()),
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["gap"] == "reported_edit_disagrees_with_request"
    assert row["before_version"] is not None and row["after_version"] is None


def test_diff_over_line_limit_is_explicitly_unavailable_without_silent_truncation(store, tmp_path):
    patch = "*** Begin Patch\n*** Add File: code.py\n" + "+x\n" * 2001 + "*** End Patch\n"
    ingest(
        store,
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": "large", "cwd": "/synthetic/project"}},
            codex_item("custom_tool_call", name="apply_patch", call_id="patch", input=patch),
            codex_item(
                "custom_tool_call_output",
                call_id="patch",
                output="Success. Updated the following files:\nA code.py\n",
            ),
        ],
        "codex",
    )
    diff = evidence(store, 3, {})["l1"]["edits"][0]["diff"]
    assert diff["available"] is False and "text" not in diff
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 1


def test_large_reported_content_is_not_stored_as_a_small_complete_version(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.derive.edits.MAX_CONTENT_BYTES", 3)
    ingest(
        store,
        tmp_path,
        [
            claude_call("edit", "Edit", {"file_path": "/synthetic/project/code.py"}),
            claude_result("edit", metadata=reported_edit()),
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["gap"] == "reported_content_over_limit"
    assert row["before_version"] is None and row["after_version"] is None
    assert evidence(store, 2, {})["l1"]["edits"][0]["diff"]["complete_versions"] is False


def test_l1_single_writer_and_queue_failure_never_changes_original_record(store, tmp_path):
    source, _ = ingest(store, tmp_path, [claude_call(args={"unknown_command_key": "text"})])
    raw = store.raw(1)
    assert evidence(store, 1, {})["l1"]["derivation"]["state"] == "failed"
    source.unlink()
    with exclusive(store.root / "locks" / "derive.lock"):
        with pytest.raises(TaskBusy):
            derive(store)
    assert derive(store) == {"l1_remaining": 0}
    assert derive(store, retry_failed=True)["l1_failed"] == 1
    assert store.raw(1) == raw


def test_unassigned_project_work_is_recoverable_and_bounded(store, tmp_path):
    source = tmp_path / "unassigned.jsonl"
    lines(source, [claude_call("one"), claude_call("two")])
    scan_file(store, source, "claude")
    assert derive(store, limit=1)["l1_waiting"] == 1
    assert runs(store) == []
    project = store.project("明确项目", [Path("/synthetic/project")])
    scan_file(store, source, "claude", project)
    source.unlink()
    assert derive(store, limit=1)["l1_done"] == 1
    assert derive(store, limit=1)["l1_done"] == 1
    assert len(runs(store)) == 2


def test_long_command_preview_has_byte_boundary_and_original_remains_exact(store, tmp_path):
    command = "synthetic " + "中文" * 4000
    ingest(store, tmp_path, [claude_call(args={"command": command})])
    run = evidence(store, 1, {})["l1"]["runs"][0]
    assert run["command_truncated"] is True
    assert run["command_total_bytes"] == len(command.encode())
    assert len(run["command"].encode()) <= 8000 and command.startswith(run["command"])
    assert run["observations"][0]["details"]["command_sha256"]
    assert "command" not in run["observations"][0]["details"]
    assert command in json.loads(store.raw(1))["message"]["content"][0]["input"]["command"]
    assert runs(store)[0]["command"] == command


def test_invalid_historical_cwd_does_not_abort_l0_ingestion(store, tmp_path):
    ingest(store, tmp_path, [claude_call(cwd={"malformed": "path"})])
    assert runs(store)[0]["cwd"] is None
    assert runs(store)[0]["gap"] == "cwd_root_unknown"
    assert store.raw(1)
