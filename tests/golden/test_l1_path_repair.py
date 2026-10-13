from __future__ import annotations

import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from rg.derive.path_checks import check_batch, projected
from rg.derive.worker import derive
from rg.query.artifacts import version_record
from rg.query.l1 import FileRunGraph
from rg.query.reader import Reader
from rg.query.runs import native
from rg.store import migrations
from rg.store.backup import backup
from rg.store.clear import execute, preview
from rg.store.database import Store
from tests.golden.test_l1 import claude_call, claude_result, codex_item, reported_edit
from tests.golden.test_l1_paths import MAIN, OTHER, add_root, ingest, scene
from tests.golden.test_native_patch import native as patch_record

OLD = "2030-01-01T00:00:00+00:00"
REPAIR = "2030-01-01T00:00:10+00:00"
LATER = "2030-01-01T00:00:20+00:00"


def legacy(store, tmp_path, monkeypatch, *, root=MAIN, registered=True, report_path=None):
    project, identity = (
        scene(store, tmp_path, root)
        if registered
        else (store.project("合成旧库", [tmp_path / "anchor"]), None)
    )
    records = [
        claude_call(
            "edit",
            "Edit",
            {
                "file_path": "code.py",
                "old_string": "old",
                "new_string": "new",
            },
            cwd=root,
        ),
        claude_result("edit", metadata=reported_edit(filePath=report_path or "code.py")),
        claude_call("run", args={"command": "synthetic --never-execute"}, cwd=root),
        claude_result("run", metadata={"exit_code": 4, "stdout": "exit_code: 0"}),
    ]
    for record in records:
        record["sessionId"] = "legacy-" + project
        record["uuid"] += "-" + project
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: REPAIR)
    with monkeypatch.context() as old:
        old.setattr("rg.derive.path_checks.check_batch", lambda *a, **k: {})
        old.setattr("rg.derive.edits.bound_path", lambda *a, **k: (None, None))
        old.setattr("rg.derive.runtime.bound_path", lambda *a, **k: (None, None))
        old.setattr("rg.derive.edits.now", lambda: OLD)
        old.setattr("rg.derive.runtime.now", lambda: OLD)
        ingest(store, tmp_path, project, records, name="legacy-" + project)
        derive(store)
    edited = dict(
        store.db.execute("SELECT * FROM edit_records WHERE project_id=?", (project,)).fetchone()
    )
    run = dict(store.db.execute("SELECT * FROM runs WHERE project_id=?", (project,)).fetchone())
    assert edited["before_version"] is None and edited["root_id"] is None
    assert run["root_id"] is None and run["exit_code"] == 4
    return project, identity, edited, run


def originals(store):
    return {
        table: [tuple(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("raw_events", "runs", "run_observations", "edit_records", "l1_derivations")
    }


def reader(store, project, at=LATER, **values):
    return Reader(
        store,
        project,
        {
            "occurred_until": "2099-01-01T00:00:00+00:00",
            "known_until": at,
            **values,
        },
    )


def test_background_repairs_old_windows_edits_and_runs_without_rewriting_facts(
    store,
    tmp_path,
    monkeypatch,
):
    project, root, edit, run = legacy(store, tmp_path, monkeypatch)
    saved = originals(store)
    result = derive(store)
    assert result["l1_path_checked"] == result["l1_path_revised"] == 2
    assert originals(store) == saved
    actual = projected(store, edit)
    assert actual["path"] == MAIN + r"\code.py" and actual["root_id"] == root
    assert actual["before_version"] and actual["after_version"] and actual["gap"] is None
    assert actual["path_validation"]["original"]["before_version"] is None
    assert actual["recorded_at"] == REPAIR and actual["original_recorded_at"] == OLD
    versions = [dict(r) for r in store.db.execute("SELECT * FROM artifact_versions")]
    assert {store.objects.get(r["content_sha256"]) for r in versions} == {b"old\n", b"new\n"}
    assert all(
        r["claim_state"] == "candidate" and r["algo"] == "sha256:tool-utf8" for r in versions
    )
    shown = native(reader(store, project), run["run_id"])
    assert shown and shown["root_id"] == root and shown["state"] == "exited"
    assert shown["exit_code"] == 4 and shown["command"] == "synthetic --never-execute"
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0
    frozen = [tuple(r) for r in store.db.execute("SELECT * FROM l1_path_checks")]
    revision = store.revision()
    assert check_batch(store) == {}
    assert [tuple(r) for r in store.db.execute("SELECT * FROM l1_path_checks")] == frozen
    assert store.revision() == revision and originals(store) == saved


def test_repair_and_repaired_versions_do_not_leak_into_earlier_views(store, tmp_path, monkeypatch):
    project, root, edit, run = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    before = reader(store, project, "2030-01-01T00:00:09.999999+00:00")
    after = reader(store, project)
    assert projected(store, edit, known_at=before.known_until) == edit
    assert native(before, run["run_id"])["root_id"] is None
    assert native(after, run["run_id"])["root_id"] == root
    for row in store.db.execute("SELECT * FROM artifact_versions"):
        assert version_record(before, dict(row)) is None
        assert version_record(after, dict(row))[0]["recorded_at"] == REPAIR
    before.values |= {"event_id": edit["result_event_id"]}
    after.values |= {"event_id": edit["result_event_id"]}
    assert before.evidence()["artifact_versions"]["items"] == []
    assert len(after.evidence()["artifact_versions"]["items"]) == 2
    early = FileRunGraph(before).snapshot()
    late = FileRunGraph(after).snapshot()
    old_edit = next(n["record"] for n in early["nodes"] if n["kind"] == "edit_record")
    new_edit = next(n["record"] for n in late["nodes"] if n["kind"] == "edit_record")
    assert old_edit["before_version"] is None and old_edit["root_id"] is None
    assert new_edit["before_version"] and new_edit["root_id"] == root


def test_registration_change_rechecks_unknown_root_and_replay_is_bounded(
    store, tmp_path, monkeypatch
):
    project, _, edit, run = legacy(store, tmp_path, monkeypatch, registered=False)
    assert check_batch(store, 1)["l1_path_checked"] == 1
    assert projected(store, edit)["root_id"] is None
    assert check_batch(store, 1)["l1_path_checked"] == 1
    assert check_batch(store, 1) == {}
    root = add_root(store, project, MAIN)
    result = check_batch(store, 1)
    assert result["l1_path_revised"] == 1
    assert projected(store, edit)["root_id"] == root
    assert projected(store, run)["root_id"] is None
    assert check_batch(store, 1)["l1_path_revised"] == 1
    assert projected(store, run)["root_id"] == root
    assert store.db.execute("SELECT count(*) FROM l1_path_checks").fetchone()[0] == 4


def test_path_mismatch_keeps_only_reported_preimage_and_preserves_original_gap(
    store,
    tmp_path,
    monkeypatch,
):
    _, _, edit, _ = legacy(store, tmp_path, monkeypatch, report_path="another.py")
    check_batch(store)
    actual = projected(store, edit)
    assert actual["path"] == MAIN + r"\another.py"
    assert actual["before_version"] and actual["after_version"] is None
    assert actual["gap"] == "reported_path_disagrees_with_request"
    assert actual["path_validation"]["original"]["gap"] == "artifact_root_unknown"


def test_missing_saved_source_is_recorded_unavailable_and_only_explicit_retry_restores(
    store,
    tmp_path,
    monkeypatch,
):
    _, root, edit, _ = legacy(store, tmp_path, monkeypatch)
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (edit["request_event_id"],)
    ).fetchone()[0]
    location = store.objects.path(sha)
    original = location.read_bytes()
    location.unlink()
    check_batch(store)
    assert projected(store, edit)["path_validation"]["state"] == "unavailable"
    assert not store.db.execute("SELECT 1 FROM artifact_versions").fetchone()
    location.write_bytes(original)
    assert check_batch(store) == {}
    check_batch(store, retry_unavailable=True)
    assert projected(store, edit)["root_id"] == root
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 2


def test_new_ambiguous_call_revokes_projection_without_deleting_saved_candidates(
    store,
    tmp_path,
    monkeypatch,
):
    project, _, edit, _ = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    versions = [tuple(r) for r in store.db.execute("SELECT * FROM artifact_versions")]
    with monkeypatch.context() as stop:
        stop.setattr("rg.derive.path_checks.check_batch", lambda *a, **k: {})
        ingest(
            store,
            tmp_path,
            project,
            [
                claude_call(
                    "edit",
                    "Edit",
                    {
                        "file_path": "code.py",
                        "old_string": "different",
                        "new_string": "other",
                    },
                    cwd=MAIN,
                    uuid="different-request",
                    sessionId="legacy-" + project,
                )
            ],
            name="late",
        )
    check_batch(store)
    assert projected(store, edit)["path_validation"]["state"] == "unavailable"
    assert projected(store, edit)["after_version"] is None
    assert [tuple(r) for r in store.db.execute("SELECT * FROM artifact_versions")] == versions


def test_candidate_writes_and_receipt_roll_back_together_on_interruption(
    store, tmp_path, monkeypatch
):
    _, _, _, _ = legacy(store, tmp_path, monkeypatch)
    saved, revision = originals(store), store.revision()

    def fault():
        raise RuntimeError("合成退出")

    with pytest.raises(RuntimeError, match="合成退出"):
        check_batch(store, 1, fault=fault)
    assert not store.db.execute("SELECT 1 FROM artifact_versions").fetchone()
    assert not store.db.execute("SELECT 1 FROM l1_repair_versions").fetchone()
    assert not store.db.execute("SELECT 1 FROM l1_path_checks").fetchone()
    assert originals(store) == saved and store.revision() == revision
    assert check_batch(store, 1)["l1_path_revised"] == 1


@pytest.mark.parametrize("table", ["l1_path_checks", "l1_repair_versions"])
def test_repair_history_is_append_only(store, tmp_path, monkeypatch, table):
    legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(f"UPDATE {table} SET recorded_at=?", (LATER,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(f"DELETE FROM {table}")


def test_readonly_graph_projects_saved_proof_without_reading_objects_or_writing(
    store,
    tmp_path,
    monkeypatch,
):
    project, _, _, _ = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    saved, revision = originals(store), store.revision()
    monkeypatch.setattr(store.objects, "get", lambda *a: pytest.fail("图不能读取原件正文"))
    graph = FileRunGraph(reader(store, project)).snapshot()
    assert any(n["kind"] == "edit_record" and n["record"]["after_version"] for n in graph["nodes"])
    assert originals(store) == saved and store.revision() == revision


def test_repaired_history_survives_backup_and_clears_as_whole_project(store, tmp_path, monkeypatch):
    project, _, edit, _ = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    target = tmp_path / "backup"
    backup(store, target)
    with closing(Store(target)) as recovered:
        assert projected(recovered, edit)["after_version"]
        assert recovered.db.execute("PRAGMA foreign_key_check").fetchone() is None
    root = store.root
    store.close()
    plan = preview(root, project)
    assert plan["blockers"] == []
    assert plan["rows"]["l1_path_checks"] == 2
    assert plan["rows"]["l1_repair_versions"] == 2
    execute(root, project, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root)) as cleaned:
        assert not cleaned.db.execute("SELECT 1 FROM l1_path_checks").fetchone()
        assert not cleaned.db.execute("SELECT 1 FROM l1_repair_versions").fetchone()
        assert not cleaned.db.execute("SELECT 1 FROM artifact_versions").fetchone()
        assert cleaned.db.execute("PRAGMA foreign_key_check").fetchone() is None


def test_v35_upgrade_preserves_records_and_appends_only_when_background_runs(tmp_path, monkeypatch):
    root = tmp_path / "data"
    with monkeypatch.context() as previous:
        previous.setattr(migrations, "LATEST_VERSION", 35)
        with closing(Store(root)) as old:
            project, _, edit, _ = legacy(old, tmp_path, monkeypatch)
            saved = originals(old)
    with closing(Store(root)) as current:
        assert current.db.execute("PRAGMA user_version").fetchone()[0] == 36
        assert originals(current) == saved
        assert not current.db.execute("SELECT 1 FROM l1_path_checks").fetchone()
        assert not current.db.execute("SELECT 1 FROM l1_repair_versions").fetchone()
        derive(current)
        assert projected(current, edit)["after_version"]
        assert originals(current) == saved
        assert reader(current, project).event_versions(edit["result_event_id"])


def test_codex_legacy_add_gets_reported_candidate_only_after_matching_receipt(
    store,
    tmp_path,
    monkeypatch,
):
    project, root = scene(store, tmp_path)
    patch = "*** Begin Patch\n*** Add File: code.py\n+新\n*** End Patch"
    records = [
        {"type": "session_meta", "payload": {"id": "legacy-codex", "cwd": MAIN}},
        codex_item("custom_tool_call", name="apply_patch", call_id="add", input=patch),
        codex_item(
            "custom_tool_call_output",
            call_id="add",
            output=("Success. Updated the following files:\nA C:/synthetic/合成 项目/code.py\n"),
        ),
    ]
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: REPAIR)
    with monkeypatch.context() as old:
        old.setattr("rg.derive.path_checks.check_batch", lambda *a, **k: {})
        old.setattr("rg.derive.edits.bound_path", lambda *a, **k: (None, None))
        ingest(store, tmp_path, project, records, tool="codex")
    original = dict(store.db.execute("SELECT * FROM edit_records").fetchone())
    assert original["after_version"] is None
    check_batch(store)
    actual = projected(store, original)
    assert actual["root_id"] == root and actual["before_version"] is None
    assert actual["after_version"] and actual["gap"] == "preimage_unknown"
    assert (
        store.objects.get(
            store.db.execute(
                "SELECT content_sha256 FROM artifact_versions WHERE version_id=?",
                (actual["after_version"],),
            ).fetchone()[0]
        )
        == "新\n".encode()
    )


def test_candidate_savepoint_rolls_back_partial_versions_but_keeps_unavailable_proof(
    store,
    tmp_path,
    monkeypatch,
):
    _, _, edit, _ = legacy(store, tmp_path, monkeypatch)
    from rg.derive.path_checks import version

    def partial(store, call, value, root, path, phase, content):
        if phase == "after":
            raise OSError("合成对象故障")
        return version(store, call, value, root, path, phase, content)

    monkeypatch.setattr("rg.derive.path_checks.version", partial)
    check_batch(store, 1)
    assert not store.db.execute("SELECT 1 FROM artifact_versions").fetchone()
    assert not store.db.execute("SELECT 1 FROM l1_repair_versions").fetchone()
    assert projected(store, edit)["path_validation"]["state"] == "unavailable"
    assert projected(store, edit)["before_version"] is None


def test_session_filter_leaves_other_project_repair_for_its_next_batch(
    store, tmp_path, monkeypatch
):
    _, root, first, _ = legacy(store, tmp_path, monkeypatch)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    _, _, second, _ = legacy(store, second_dir, monkeypatch, root=OTHER)
    result = derive(store, session=first["session_pk"])
    assert result["l1_path_checked"] == 2
    assert projected(store, first)["root_id"] == root
    assert projected(store, second) == second
    assert check_batch(store, session=second["session_pk"])["l1_path_checked"] == 2


def test_foreign_root_change_revokes_association_but_keeps_previous_proof_and_candidates(
    store,
    tmp_path,
    monkeypatch,
):
    _, root, edit, _ = legacy(store, tmp_path, monkeypatch)
    check_batch(store)
    saved = [tuple(r) for r in store.db.execute("SELECT * FROM artifact_versions")]
    foreign = store.project("合成其它项目", [])
    add_root(store, foreign, MAIN + r"\code.py")
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: LATER)
    check_batch(store)
    assert projected(store, edit)["root_id"] is None
    assert projected(store, edit, known_at=REPAIR)["root_id"] == root
    assert [tuple(r) for r in store.db.execute("SELECT * FROM artifact_versions")] == saved


def test_function_call_and_native_execution_metadata_keep_unique_original_request(
    store,
    tmp_path,
    monkeypatch,
):
    project, root = scene(store, tmp_path)
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: REPAIR)
    ingest(
        store,
        tmp_path,
        project,
        [
            {"type": "session_meta", "payload": {"id": "mixed-native", "cwd": MAIN}},
            codex_item(
                "function_call",
                name="exec_command",
                call_id="mixed",
                arguments=('{"cmd":"synthetic --never-execute"}'),
            ),
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_begin",
                    "call_id": "mixed",
                    "cwd": MAIN,
                    "command": ["synthetic", "--never-execute"],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "mixed",
                    "exit_code": 4,
                },
            },
        ],
        tool="codex",
    )
    run = dict(store.db.execute("SELECT * FROM runs").fetchone())
    assert projected(store, run)["root_id"] == root
    assert projected(store, run)["path_validation"]["state"] == "matches"
    assert native(reader(store, project), run["run_id"])["exit_code"] == 4


def test_previous_wrong_version_binding_remains_history_with_later_review_warning(
    store,
    tmp_path,
    monkeypatch,
):
    project, main = scene(store, tmp_path)
    other = add_root(store, project, OTHER)
    records = [
        claude_call(
            "wrong",
            "Edit",
            {
                "file_path": "code.py",
                "old_string": "old",
                "new_string": "new",
            },
            cwd=MAIN,
        ),
        claude_result("wrong", metadata=reported_edit(filePath="code.py")),
    ]
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: REPAIR)
    with monkeypatch.context() as previous:
        previous.setattr("rg.derive.path_checks.check_batch", lambda *a, **k: {})
        previous.setattr("rg.derive.edits.now", lambda: OLD)
        previous.setattr("rg.derive.edits.bound_path", lambda *a, **k: (other, OTHER + r"\code.py"))
        ingest(store, tmp_path, project, records)
    original = dict(store.db.execute("SELECT * FROM edit_records").fetchone())
    old_versions = [dict(r) for r in store.db.execute("SELECT * FROM artifact_versions")]
    check_batch(store)
    actual = projected(store, original)
    assert actual["root_id"] == main and actual["after_version"] != original["after_version"]
    assert store.db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 4
    for row in old_versions:
        assert (
            dict(
                store.db.execute(
                    "SELECT * FROM artifact_versions WHERE version_id=?", (row["version_id"],)
                ).fetchone()
            )
            == row
        )
        assert "edit_path_reviews" not in version_record(reader(store, project, OLD), row)[0]
        card = version_record(reader(store, project), row)[0]
        assert card["edit_path_reviews"][0]["state"] == "revised" and card["provenance_warnings"]


def test_codex_native_begin_end_replay_keeps_reported_add_and_missing_original_input(
    store,
    tmp_path,
    monkeypatch,
):
    project, root = scene(store, tmp_path)
    changes = {"code.py": {"type": "add", "content": "新\n"}}
    records = [
        {"type": "session_meta", "payload": {"id": "native-repair", "cwd": MAIN}},
        patch_record("patch_apply_begin", changes=changes),
        patch_record("patch_apply_end", success=True, status="completed", changes=changes),
    ]
    monkeypatch.setattr("rg.derive.path_checks.now", lambda: REPAIR)
    with monkeypatch.context() as old:
        old.setattr("rg.derive.path_checks.check_batch", lambda *a, **k: {})
        old.setattr("rg.derive.edits.bound_path", lambda *a, **k: (None, None))
        ingest(store, tmp_path, project, records, tool="codex")
    original = dict(store.db.execute("SELECT * FROM edit_records").fetchone())
    assert original["after_version"] is None
    check_batch(store)
    actual = projected(store, original)
    assert actual["root_id"] == root and actual["before_version"] is None
    assert actual["after_version"] and actual["gap"] == "native_patch_input_missing"
