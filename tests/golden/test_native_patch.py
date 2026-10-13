from __future__ import annotations

import json
from copy import deepcopy

import pytest

from rg.api.views import evidence
from rg.derive.worker import derive
from rg.ingest.codex import parse_codex
from tests.golden.test_l1 import codex_item, ingest

CONTENT = "合成候选\r\n一\u2028二<script>synthetic</script>"
CHANGES = {
    "added.py": {"type": "add", "content": CONTENT},
    "removed.py": {"type": "delete", "content": "删除前\r\n没有尾换行"},
    "updated.py": {
        "type": "update",
        "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
        "move_path": "moved.py",
    },
}


def native(kind, call_id="patch", **fields):
    return {
        "type": "event_msg",
        "payload": {"type": kind, "call_id": call_id, "turn_id": "synthetic-turn", **fields},
    }


def records(changes=None, **end):
    changes = deepcopy(CHANGES if changes is None else changes)
    return [
        {"type": "session_meta", "payload": {"id": "native-patch", "cwd": "/synthetic/project"}},
        native("patch_apply_begin", changes=changes, auto_approved=True),
        native(
            "patch_apply_end",
            stdout="",
            stderr="",
            success=True,
            status="completed",
            changes=deepcopy(changes),
            **end,
        ),
    ]


def edits(store):
    return [dict(row) for row in store.db.execute("SELECT * FROM edit_records ORDER BY path")]


def text(store, identity):
    version = store.db.execute(
        "SELECT * FROM artifact_versions WHERE version_id=?", (identity,)
    ).fetchone()
    assert version["source"] == "agent_edit" and version["claim_state"] == "candidate"
    assert version["basis"] == "direct_record" and version["representation"] == "tool_reported_utf8"
    return store.objects.get(version["content_sha256"]).decode()


def test_native_begin_is_preserved_as_execution_metadata():
    parsed = parse_codex(native("patch_apply_begin", changes=CHANGES))[0]
    assert (parsed.kind, parsed.tool_name, parsed.excluded) == (
        "meta",
        "apply_patch",
        "execution_metadata",
    )


def test_native_only_changes_preserve_known_sides_without_physical_versions(store, tmp_path):
    source, _ = ingest(store, tmp_path, records(), "codex")
    original = source.read_bytes()
    rows = edits(store)
    assert len(rows) == 3
    added, moved, removed = rows
    assert text(store, added["after_version"]) == CONTENT and added["before_version"] is None
    assert text(store, removed["before_version"]) == CHANGES["removed.py"]["content"]
    assert removed["after_version"] is None
    assert moved["before_version"] is None and moved["after_version"] is None
    view = evidence(store, 3, {})["l1"]["edits"]
    assert {part["diff"]["format"] for part in view} == {
        "reported_after",
        "reported_before",
        "patch_only",
    }
    assert all(part["diff"]["complete_versions"] is False for part in view)
    assert all(part["request_event_id"] == 2 and part["result_event_id"] == 3 for part in view)
    assert source.read_bytes() == original
    before = store.db.iterdump()
    snapshot = "\n".join(before)
    derive(store)
    assert "\n".join(store.db.iterdump()) == snapshot
    source.unlink()
    assert evidence(store, 3, {})["l1"]["edits"] == view
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("status,success", [("failed", False), ("declined", False)])
def test_failed_native_patch_keeps_known_preimage_but_no_postimage(
    store, tmp_path, status, success
):
    items = records()
    items[-1]["payload"].update(status=status, success=success)
    ingest(store, tmp_path, items, "codex")
    rows = edits(store)
    assert len(rows) == 3 and all(row["after_version"] is None for row in rows)
    removed = next(row for row in rows if row["operation"] == "delete")
    assert text(store, removed["before_version"]) == CHANGES["removed.py"]["content"]
    assert all(row["gap"] == "native_patch_failed" for row in rows)
    assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["success", "status", "turn", "content", "path", "move"])
def test_conflicting_native_metadata_does_not_prove_versions(store, tmp_path, mutation):
    items = records()
    result = items[-1]["payload"]
    if mutation == "success":
        result["success"] = False
    elif mutation == "status":
        result["status"] = "unknown"
    elif mutation == "turn":
        result["turn_id"] = "other-turn"
    elif mutation == "content":
        result["changes"]["added.py"]["content"] = "different"
    elif mutation == "path":
        result["changes"]["other.py"] = result["changes"].pop("added.py")
    else:
        result["changes"]["updated.py"]["move_path"] = "other.py"
    ingest(store, tmp_path, items, "codex")
    rows = edits(store)
    assert len(rows) == 3
    assert all(row["before_version"] is None and row["after_version"] is None for row in rows)
    assert all(row["gap"] == "native_patch_metadata_conflict" for row in rows)


def test_missing_begin_waits_then_late_same_call_retries_without_reading_workspace(store, tmp_path):
    items = records()
    source, project = ingest(store, tmp_path, [items[0], items[2]], "codex")
    assert not edits(store)
    assert evidence(store, 2, {})["l1"]["derivation"]["state"] == "waiting"
    with source.open("a") as file:
        file.write(json.dumps(items[1], ensure_ascii=False) + "\n")
    from rg.ingest.scanner import scan_file

    scan_file(store, source, "codex", project)
    assert len(edits(store)) == 3
    assert evidence(store, 2, {})["l1"]["derivation"]["state"] == "done"
    assert all(row["request_event_id"] == 3 and row["result_event_id"] == 2 for row in edits(store))


def test_duplicate_begin_is_ambiguous_without_closest_record_guess(store, tmp_path):
    items = records()
    duplicate = deepcopy(items[1])
    duplicate["payload"]["changes"]["added.py"]["content"] = "second"
    ingest(store, tmp_path, items[:2] + [duplicate, items[2]], "codex")
    assert not edits(store)
    derivation = evidence(store, 4, {})["l1"]["derivation"]
    assert (derivation["state"], derivation["error"]) == ("failed", "ambiguous_call_id")


@pytest.mark.parametrize("wrapper", ["exec_command", "functions.exec"])
def test_native_patch_uses_begin_instead_of_wrapper_runtime(store, tmp_path, wrapper):
    items = records()
    call = codex_item(
        "function_call",
        name=wrapper,
        call_id="patch",
        arguments='{"cmd":"synthetic-never-execute"}',
    )
    ingest(store, tmp_path, [items[0], call, *items[1:]], "codex")
    assert len(edits(store)) == 3
    assert all(row["request_event_id"] == 3 and row["result_event_id"] == 4 for row in edits(store))
    observations = store.db.execute(
        "SELECT event_id,state,exit_code FROM run_observations"
    ).fetchall()
    assert all(
        row["event_id"] == 2 and row["state"] == "requested" and row["exit_code"] is None
        for row in observations
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"x": {"type": "unknown"}},
        {"x": {"type": "add", "content": 9}},
        {"x": {"type": "update", "unified_diff": 7}},
        {"x": {"type": "update", "unified_diff": "diff", "move_path": True}},
        {"": {"type": "delete", "content": "old"}},
    ],
)
def test_invalid_native_changes_are_retained_but_fail_derivation(store, tmp_path, bad):
    ingest(store, tmp_path, records(bad), "codex")
    assert not edits(store)
    assert evidence(store, 3, {})["l1"]["derivation"]["state"] == "failed"
    assert json.loads(store.raw(3))["payload"]["changes"] == bad


@pytest.mark.parametrize(
    "content,available",
    [("", True), ("x" * 64_001, False), ("x\n" * 2001, False)],
    ids=["empty", "bytes", "lines"],
)
def test_single_sided_content_is_exact_or_unavailable(store, tmp_path, content, available):
    ingest(store, tmp_path, records({"x": {"type": "delete", "content": content}}), "codex")
    row = edits(store)[0]
    assert text(store, row["before_version"]) == content
    diff = evidence(store, 3, {})["l1"]["edits"][0]["diff"]
    assert diff["available"] is available
    if available:
        assert diff["text"] == content and diff["format"] == "reported_before"
        assert diff["complete_versions"] is False
    else:
        assert "text" not in diff


@pytest.mark.parametrize("status_value", [[], {}, 1, True], ids=["list", "dict", "int", "bool"])
def test_malformed_status_does_not_crash_worker_or_prove_versions(store, tmp_path, status_value):
    items = records()
    items[-1]["payload"]["status"] = status_value
    ingest(store, tmp_path, items, "codex")
    assert len(edits(store)) == 3
    assert all(row["gap"] == "native_patch_metadata_conflict" for row in edits(store))
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0


@pytest.mark.parametrize("include_begin", [False, True])
def test_primary_patch_and_legacy_native_receipt_keep_real_request_source(
    store, tmp_path, include_begin
):
    content = "new\n"
    data = {"code.py": {"type": "add", "content": content}}
    items = records(data)
    primary = codex_item(
        "custom_tool_call",
        name="apply_patch",
        call_id="patch",
        input="*** Begin Patch\n*** Add File: code.py\n+new\n*** End Patch\n",
    )
    end = native(
        "patch_apply_end",
        success=True,
        stdout="Success. Updated the following files:\nA code.py\n",
        stderr="",
    )
    sequence = [items[0], primary] + ([items[1]] if include_begin else []) + [end]
    ingest(store, tmp_path, sequence, "codex")
    row = edits(store)[0]
    assert row["request_event_id"] == 2 and row["gap"] == "preimage_unknown"
    assert text(store, row["after_version"]) == content


@pytest.mark.parametrize(
    "field,value",
    [
        ("success", False),
        ("stdout", "no success proof"),
        ("stdout", "Success. Updated the following files:\nA wrong.py\n"),
    ],
)
def test_legacy_native_receipt_requires_both_success_and_matching_paths(
    store, tmp_path, field, value
):
    items = records({"code.py": {"type": "add", "content": "new\n"}})
    result = native(
        "patch_apply_end",
        success=True,
        stdout="Success. Updated the following files:\nA code.py\n",
        stderr="",
    )
    result["payload"][field] = value
    ingest(store, tmp_path, [items[0], items[1], result], "codex")
    assert len(edits(store)) == 1 and edits(store)[0]["after_version"] is None


def test_native_call_is_not_borrowed_from_another_actual_session(store, tmp_path):
    items = records()
    _, project = ingest(store, tmp_path, items[:2], "codex", filename="begin.jsonl")
    second = records()
    second[0]["payload"]["id"] = "other-native-session"
    ingest(store, tmp_path, [second[0], second[2]], "codex", project, "other.jsonl")
    assert not edits(store)
    assert evidence(store, 4, {})["l1"]["derivation"]["error"] == "call_not_recorded"


def test_combined_single_sided_bodies_are_bounded_without_partial_text(store, tmp_path):
    data = {f"{number}.py": {"type": "delete", "content": "x" * 40_000} for number in range(2)}
    ingest(store, tmp_path, records(data), "codex")
    diffs = [row["diff"] for row in evidence(store, 3, {})["l1"]["edits"]]
    assert sorted(diff["available"] for diff in diffs) == [False, True]
    assert sum(len(diff.get("text", "").encode()) for diff in diffs) == 40_000
    assert all(diff.get("text") is None or len(diff["text"]) == 40_000 for diff in diffs)


def test_generic_function_output_cannot_impersonate_native_patch(store, tmp_path):
    items = records({"code.py": {"type": "add", "content": "new\n"}})
    primary = codex_item(
        "custom_tool_call",
        name="apply_patch",
        call_id="patch",
        input="*** Begin Patch\n*** Add File: code.py\n+new\n*** End Patch\n",
    )
    spoof = codex_item(
        "custom_tool_call_output", call_id="patch", output=json.dumps(items[-1]["payload"])
    )
    ingest(store, tmp_path, [items[0], primary, spoof], "codex")
    assert edits(store)[0]["after_version"] is None
    assert edits(store)[0]["gap"] == "patch_success_not_proven"


def test_primary_add_disagreement_with_native_report_preserves_patch_only(store, tmp_path):
    items = records({"code.py": {"type": "add", "content": "different\n"}})
    primary = codex_item(
        "custom_tool_call",
        name="apply_patch",
        call_id="patch",
        input="*** Begin Patch\n*** Add File: code.py\n+new\n*** End Patch\n",
    )
    ingest(store, tmp_path, [items[0], primary, *items[1:]], "codex")
    assert edits(store)[0]["after_version"] is None
    assert edits(store)[0]["gap"] == "native_patch_metadata_conflict"


def test_native_patch_transaction_failure_rolls_back_all_file_versions(
    store, tmp_path, monkeypatch
):
    with monkeypatch.context() as change:
        change.setattr("rg.derive.worker.derive", lambda *args, **kwargs: {})
        ingest(store, tmp_path, records(), "codex")
    raw = [store.raw(number) for number in (1, 2, 3)]
    offset = store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0]
    # 先完成开始记录，再仅在原生结束记录的事实已写入时模拟崩溃。
    derive(store, limit=1)

    def fail():
        assert store.db.execute("SELECT count(*) FROM edit_records").fetchone()[0] == 3
        raise RuntimeError("合成提交前崩溃")

    with pytest.raises(RuntimeError, match="崩溃"):
        derive(store, fault=fail)
    assert not edits(store)
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0
    assert [store.raw(number) for number in (1, 2, 3)] == raw
    assert store.db.execute("SELECT committed_offset FROM source_files").fetchone()[0] == offset
    assert derive(store)["l1_done"] == 1
    assert len(edits(store)) == 3


def test_native_saved_reports_survive_independent_backup_and_missing_source(store, tmp_path):
    from rg.store.backup import backup
    from rg.store.database import Store

    source, _ = ingest(store, tmp_path, records(), "codex")
    original = evidence(store, 3, {})
    destination = tmp_path / "backup"
    backup(store, destination)
    source.unlink()
    restored = Store(destination)
    try:
        assert evidence(restored, 3, {})["l1"] == original["l1"]
        assert restored.raw(3) == store.raw(3)
    finally:
        restored.close()


def test_native_changes_outside_registered_root_do_not_create_versions(store, tmp_path):
    ingest(
        store,
        tmp_path,
        records({"/synthetic/other/code.py": {"type": "add", "content": "new"}}),
        "codex",
    )
    assert len(edits(store)) == 1 and edits(store)[0]["gap"] == "artifact_root_unknown"
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0


def test_native_rg_injected_report_is_not_its_own_evidence(store, tmp_path):
    data = {"code.py": {"type": "add", "content": '<rg-context v="1">合成注入</rg-context>'}}
    ingest(store, tmp_path, records(data), "codex")
    assert not edits(store)
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 0


@pytest.mark.parametrize("primary", [False, True])
def test_late_duplicate_begin_marks_current_association_without_rewriting_earlier_view(
    store, tmp_path, primary
):
    from rg.ingest.scanner import scan_file
    from rg.query.l1 import FileRunGraph
    from rg.query.reader import Reader

    items = records({"code.py": {"type": "add", "content": "new\n"}})
    if primary:
        items.insert(
            1,
            codex_item(
                "custom_tool_call",
                name="apply_patch",
                call_id="patch",
                input="*** Begin Patch\n*** Add File: code.py\n+new\n*** End Patch\n",
            ),
        )
    source, project = ingest(store, tmp_path, items, "codex")
    original = edits(store)
    cutoff = max(row["recorded_at"] for row in original)
    duplicate = deepcopy(items[-2])
    duplicate["payload"]["changes"]["code.py"]["content"] = "later conflicting begin"
    with source.open("a") as file:
        file.write(json.dumps(duplicate, ensure_ascii=False) + "\n")
    scan_file(store, source, "codex", project)
    assert edits(store) == original
    assert all(
        row["association_gap"] == "ambiguous_call_id"
        for row in evidence(store, len(items), {})["l1"]["edits"]
    )
    for values, expected in [({}, "ambiguous_call_id"), ({"known_until": cutoff}, None)]:
        graph = FileRunGraph(Reader(store, project, values)).snapshot()
        records_now = [node["record"] for node in graph["nodes"] if node["kind"] == "edit_record"]
        assert len(records_now) == 1 and all(
            row["association_gap"] == expected for row in records_now
        )
