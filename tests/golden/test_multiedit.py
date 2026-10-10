from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from rg.derive.edit_checks import recorded_edit, validate_edits
from rg.derive.edits import diff
from rg.derive.multiedit import expected_multiedit, project_edit
from rg.derive.views import evidence
from rg.derive.worker import derive
from rg.export.package import archive
from rg.ingest.scanner import scan_file
from rg.query.l1 import FileRunGraph
from rg.query.reader import Reader
from rg.store import migrations
from rg.store.backup import backup
from rg.store.clear import execute, preview
from rg.store.database import Store
from tests.golden.test_exports import unchanged, unpack
from tests.multiedit import AFTER, BEFORE, seed_case, seed_multiedit_store
from tests.test_migrations import legacy_store

VALID = [
    {"old_string": "旧", "new_string": "中", "replace_all": True},
    {"old_string": "中", "new_string": "新", "replace_all": True},
]


def saved(store, identity):
    return dict(
        store.db.execute("SELECT * FROM edit_records WHERE edit_id=?", (identity,)).fetchone()
    )


def graph_edits(store, project, **values):
    graph = FileRunGraph(Reader(store, project, values)).snapshot()
    return {
        n["record"]["edit_id"]: n["record"] for n in graph["nodes"] if n["kind"] == "edit_record"
    }, graph


def test_multiedit_cannot_skip_an_invalid_intermediate_step(store, tmp_path):
    project = store.project("合成顺序", [Path("/synthetic/multiedit")])
    seed_case(
        store,
        tmp_path,
        project,
        "invalid",
        [
            {"old_string": "旧", "new_string": "中", "replace_all": True},
            {"old_string": "不存在", "new_string": "新", "replace_all": True},
        ],
    )
    row = store.db.execute("SELECT * FROM edit_records").fetchone()
    assert row["after_version"] is None
    assert row["gap"] == "reported_edit_disagrees_with_request"
    assert row["before_version"] is not None


@pytest.mark.parametrize(
    "edits,gap",
    [
        (None, "reported_edit_request_unsupported"),
        ([], "reported_edit_request_unsupported"),
        ({"old_string": "旧", "new_string": "新"}, "reported_edit_request_unsupported"),
        ([None], "reported_edit_request_unsupported"),
        ([{"old_string": "", "new_string": "新"}], "reported_edit_request_unsupported"),
        ([{"old_string": 1, "new_string": "新"}], "reported_edit_request_unsupported"),
        ([{"old_string": "旧", "new_string": None}], "reported_edit_request_unsupported"),
        ([{"old_string": "旧"}], "reported_edit_request_unsupported"),
        (
            [{"old_string": "旧", "new_string": "新", "replace_all": 1}],
            "reported_edit_request_unsupported",
        ),
        (
            [{"old_string": "旧", "new_string": "新", "replace_all": None}],
            "reported_edit_request_unsupported",
        ),
        ([{"old_string": "旧", "new_string": "新"}], "reported_edit_disagrees_with_request"),
        (
            [{"old_string": "旧", "new_string": "新", "replace_all": False}],
            "reported_edit_disagrees_with_request",
        ),
        (list(reversed(VALID)), "reported_edit_disagrees_with_request"),
        (
            [{"old_string": "旧", "new_string": "中", "replace_all": True}],
            "reported_edit_disagrees_with_request",
        ),
    ],
)
def test_invalid_requests_keep_only_known_preimage(store, tmp_path, edits, gap):
    project = store.project("合成无效请求", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "invalid", edits)
    row = saved(store, ids["edit_id"])
    assert row["after_version"] is None and row["gap"] == gap
    projected = project_edit(store, row)
    assert projected["request_validation"]["status"] == (
        "mismatch" if gap == "reported_edit_disagrees_with_request" else "unavailable"
    )
    displayed = diff(store, row)
    assert displayed["text"] == BEFORE and displayed["format"] == "reported_before"
    assert not displayed["complete_versions"]


def test_ordered_valid_request_still_only_produces_tool_candidates(store, tmp_path):
    project = store.project("合成有效请求", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "valid", VALID)
    row = saved(store, ids["edit_id"])
    projected = project_edit(store, row)
    assert projected["request_validation"] == {
        "status": "matches_request",
        "basis": "saved_request_and_reported_versions",
    }
    assert projected["after_version"] == projected["reported_after_version"] == row["after_version"]
    assert row["gap"] is None
    versions = [dict(r) for r in store.db.execute("SELECT * FROM artifact_versions")]
    assert len(versions) == 2
    assert all(
        r["claim_state"] == "candidate"
        and r["source"] == "agent_edit"
        and r["representation"] == "tool_reported_utf8"
        for r in versions
    )
    assert (
        store.objects.get(
            next(r["content_sha256"] for r in versions if r["phase"] == "after")
        ).decode()
        == AFTER
    )
    assert diff(store, row)["complete_versions"]


@pytest.mark.parametrize(
    "before,edits,after",
    [
        (
            "旧\r\n一\u2028二\r\n",
            [{"old_string": "旧", "new_string": "新"}],
            "新\r\n一\u2028二\r\n",
        ),
        (
            "abc\n",
            [{"old_string": "ab", "new_string": "x"}, {"old_string": "xc", "new_string": "整行"}],
            "整行\n",
        ),
        ("一次\n", [{"old_string": "一次", "new_string": ""}], "\n"),
    ],
)
def test_literal_sequential_substitution_preserves_unicode_newlines(before, edits, after):
    assert expected_multiedit(before, edits) == after


def test_utf8_limit_applies_to_intermediate_all_occurrence_expansion():
    edits = [
        {"old_string": "a", "new_string": "界界", "replace_all": True},
        {"old_string": "界界", "new_string": "a", "replace_all": True},
    ]
    with pytest.raises(ValueError, match="reported_content_over_limit"):
        expected_multiedit("aa", edits, limit=10)
    assert expected_multiedit("aa", edits, limit=12) == "aa"


def test_legacy_diagnostics_are_readonly_keep_reported_identity_and_use_same_graph_export(
    store,
    tmp_path,
):
    project, cases = seed_multiedit_store(store, tmp_path)
    before = unchanged(store)
    revision = store.revision()
    originals = {key: saved(store, ids["edit_id"]) for key, ids in cases.items()}
    for path in tmp_path.glob("*.jsonl"):
        path.unlink()
    edits, graph = graph_edits(store, project)
    for key, status in (
        ("valid", "matches_request"),
        ("mismatch", "mismatch"),
        ("legacy_mismatch", "mismatch"),
        ("legacy_unavailable", "unavailable"),
    ):
        ids = cases[key]
        actual = edits[ids["edit_id"]]
        assert actual["request_validation"]["status"] == status
        assert actual["reported_after_version"] == originals[key]["after_version"]
        native = evidence(store, ids["result_event_id"])["edits"][0]
        assert native["request_validation"] == actual["request_validation"]
        assert native["after_version"] == actual["after_version"]
        assert diff(store, originals[key]) == native["diff"]
        output = next(
            e
            for e in graph["edges"]
            if e["role"] == "after_version" and e["source"]["record_id"] == ids["edit_id"]
        )
        if key == "valid":
            assert output["resolved"] and native["diff"]["complete_versions"]
        else:
            assert actual["after_version"] is None and not output["resolved"]
            assert output["target"]["record_id"] is None
            assert native["diff"]["text"] == BEFORE and not native["diff"]["complete_versions"]
        if key.startswith("legacy"):
            assert originals[key]["after_version"] == ids["saved_after_version"]
            assert originals[key]["gap"] is None
            assert saved(store, ids["edit_id"]) == originals[key]
    _, data = archive(store, {"project_id": project})
    exported = unpack(data)["graph.json"]["l1"]
    assert {
        n["record"]["edit_id"]: n["record"] for n in exported["nodes"] if n["kind"] == "edit_record"
    } == edits
    assert unchanged(store) == before and store.revision() == revision
    root = store.root
    store.close()
    with closing(Store(root, readonly=True)) as readonly:
        assert graph_edits(readonly, project)[0] == edits
        assert unchanged(readonly) == before


@pytest.mark.parametrize(
    "phase,damage",
    [("before", "missing"), ("after", "missing"), ("after", "corrupt"), ("request", "missing")],
)
def test_unavailable_saved_objects_cannot_complete_legacy_edit(store, tmp_path, phase, damage):
    project = store.project("合成损坏引用", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "old", VALID, legacy=True)
    row = saved(store, ids["edit_id"])
    if phase == "request":
        sha = store.db.execute(
            "SELECT object_sha256 FROM raw_events WHERE event_id=?", (ids["request_event_id"],)
        ).fetchone()[0]
    else:
        sha = store.db.execute(
            "SELECT content_sha256 FROM artifact_versions WHERE version_id=?",
            (row[f"{phase}_version"],),
        ).fetchone()[0]
    path = store.objects.path(sha)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"not a zstandard object")
    original = unchanged(store)
    projected = project_edit(store, row)
    assert projected["after_version"] is None
    assert projected["reported_after_version"] == row["after_version"]
    assert projected["request_validation"]["status"] == "unavailable"
    assert projected["gap"] == (
        "multiedit_request_unavailable" if phase == "request" else "multiedit_version_unavailable"
    )
    assert not diff(store, row).get("complete_versions", False)
    if phase == "before":
        assert not diff(store, row)["available"]
    else:
        assert diff(store, row)["format"] == "reported_before"
    assert unchanged(store) == original


def test_old_request_cannot_borrow_different_project_candidate_or_call(store, tmp_path):
    project = store.project("合成引用边界", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "old", VALID, legacy=True)
    row = saved(store, ids["edit_id"])
    for changed in (
        row | {"project_id": "another-project"},
        row | {"call_id": "another-call"},
        row | {"before_version": row["after_version"]},
    ):
        actual = project_edit(store, changed)
        assert actual["after_version"] is None
        assert actual["request_validation"]["status"] == "unavailable"


def test_query_time_diagnostic_requires_visible_original_edit_and_does_not_rewrite_it(
    store, tmp_path
):
    project = store.project("合成双时间", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "old", None, legacy=True)
    row = saved(store, ids["edit_id"])
    assert graph_edits(store, project, known_until="2026-10-10T07:59:00Z")[0] == {}
    assert graph_edits(store, project, occurred_until="2026-10-10T08:00:00Z")[0] == {}
    shown = graph_edits(
        store, project, known_until=row["recorded_at"], occurred_until=row["occurred_at"]
    )[0]
    assert shown[ids["edit_id"]]["request_validation"]["status"] == "unavailable"
    assert saved(store, ids["edit_id"]) == row


def test_legacy_report_survives_reimport_derivation_and_independent_backup(store, tmp_path):
    project = store.project("合成备份", [Path("/synthetic/multiedit")])
    ids = seed_case(store, tmp_path, project, "old", None, legacy=True)
    row = saved(store, ids["edit_id"])
    initial = unchanged(store)
    assert scan_file(store, tmp_path / "old.jsonl", "claude", project) == {}
    assert derive(store)["l1_remaining"] == 0
    assert unchanged(store) == initial
    expected = graph_edits(store, project)[0]
    backup(store, tmp_path / "restored")
    (tmp_path / "old.jsonl").unlink()
    with closing(Store(tmp_path / "restored", readonly=True)) as restored:
        assert graph_edits(restored, project)[0] == expected
        assert saved(restored, ids["edit_id"]) == row


@pytest.mark.parametrize("has_multiedit", [True, False])
def test_schema25_only_invalidates_old_multiedit_projection_atomically(
    tmp_path,
    monkeypatch,
    has_multiedit,
):
    root, raw = legacy_store(tmp_path, monkeypatch, version=24)
    with monkeypatch.context() as old:
        old.setattr(migrations, "LATEST_VERSION", 24)
        with closing(Store(root)) as store:
            if has_multiedit:
                seed_multiedit_store(store, tmp_path)
            revision = store.revision()
            initial = "\n".join(
                line for line in unchanged(store).splitlines() if line.startswith("INSERT INTO")
            )
    with monkeypatch.context() as failure:
        failure.setitem(migrations.MIGRATIONS, 25, (*migrations.MIGRATIONS[25], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 24
        assert db.execute("SELECT revision FROM graph_clock WHERE id=1").fetchone()[0] == revision
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='edit_request_checks'"
        ).fetchone()
    with monkeypatch.context() as no_raw:
        no_raw.setattr(Store, "raw", lambda *args: pytest.fail("升级不能读取原文"))
        with closing(Store(root)) as upgraded:
            assert upgraded.revision() == revision + int(has_multiedit)
            expected = initial.replace(
                f'INSERT INTO "graph_clock" VALUES(1,{revision});',
                f'INSERT INTO "graph_clock" VALUES(1,{revision + int(has_multiedit)});',
            )
            actual = "\n".join(
                line for line in unchanged(upgraded).splitlines() if line.startswith("INSERT INTO")
            )
            assert actual == expected
            upgraded_revision = upgraded.revision()
    with closing(Store(root)) as reopened:
        assert reopened.revision() == upgraded_revision
        assert reopened.raw(1) == raw


def test_graph_never_reads_body_pending_becomes_checked_only_through_background_derive(
    store,
    tmp_path,
    monkeypatch,
):
    project = store.project("合成后台核验", [Path("/synthetic/multiedit")])
    with patch("rg.derive.edit_checks.validate_edits", return_value=0):
        ids = seed_case(store, tmp_path, project, "old", None, legacy=True)
    row = saved(store, ids["edit_id"])
    before = unchanged(store)
    with monkeypatch.context() as no_body:
        no_body.setattr(store.objects, "get", lambda *args: pytest.fail("图不能读取正文对象"))
        shown = graph_edits(store, project)[0][ids["edit_id"]]
        assert (
            shown["after_version"] is None
            and shown["reported_after_version"] == row["after_version"]
        )
        assert shown["gap"] == "multiedit_validation_pending"
        assert shown["request_validation"]["status"] == "unavailable"
    assert unchanged(store) == before
    assert derive(store)["edit_checks"] == 1
    checked = graph_edits(store, project)[0][ids["edit_id"]]
    assert checked["gap"] == "reported_edit_request_unsupported"
    after = unchanged(store)
    revision = store.revision()
    with monkeypatch.context() as no_body:
        no_body.setattr(store.objects, "get", lambda *args: pytest.fail("已核验图不能读取正文对象"))
        assert graph_edits(store, project)[0][ids["edit_id"]] == checked
        _, package = archive(store, {"project_id": project})
        exported = unpack(package)["graph.json"]["l1"]
        assert next(n["record"] for n in exported["nodes"] if n["kind"] == "edit_record") == checked
    assert unchanged(store) == after and store.revision() == revision
    assert derive(store).get("edit_checks", 0) == 0
    assert saved(store, ids["edit_id"]) == row
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE edit_request_checks SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM edit_request_checks")


def test_whole_project_clear_removes_only_owned_diagnostics(store, tmp_path):
    project = store.project("合成清除核验", [Path("/synthetic/multiedit")])
    other = store.project("合成保留核验", [Path("/synthetic/multiedit-other")])
    removed = seed_case(store, tmp_path, project, "removed", None, legacy=True)
    kept = seed_case(store, tmp_path, other, "kept", VALID)
    kept_record = recorded_edit(store, saved(store, kept["edit_id"]))
    kept_checks = [
        tuple(r)
        for r in store.db.execute("SELECT * FROM edit_request_checks WHERE project_id=?", (other,))
    ]
    root = store.root
    store.close()
    plan = preview(root, project)
    execute(root, project, str(uuid4()), plan["preview_sha256"])
    with closing(Store(root, readonly=True)) as cleared:
        assert not cleared.db.execute(
            "SELECT 1 FROM edit_request_checks WHERE edit_id=?", (removed["edit_id"],)
        ).fetchone()
        assert [
            tuple(r)
            for r in cleared.db.execute(
                "SELECT * FROM edit_request_checks WHERE project_id=?", (other,)
            )
        ] == kept_checks
        assert recorded_edit(cleared, saved(cleared, kept["edit_id"])) == kept_record


def test_background_validation_failure_rolls_back_diagnostic_and_revision(
    store, tmp_path, monkeypatch
):
    project = store.project("合成核验中断", [Path("/synthetic/multiedit")])
    with patch("rg.derive.edit_checks.validate_edits", return_value=0):
        ids = seed_case(store, tmp_path, project, "old", VALID, legacy=True)
    before = unchanged(store)
    transaction = store.transaction

    @contextmanager
    def interrupted():
        with transaction() as db:
            yield db
            raise RuntimeError("合成提交前中断")

    with monkeypatch.context() as fault:
        fault.setattr(store, "transaction", interrupted)
        with pytest.raises(RuntimeError, match="中断"):
            validate_edits(store)
    assert unchanged(store) == before
    assert derive(store)["edit_checks"] == 1
    assert (
        recorded_edit(store, saved(store, ids["edit_id"]))["request_validation"]["status"]
        == "matches_request"
    )


def test_unavailable_cache_retries_append_history_and_cannot_starve_unchecked_rows(store, tmp_path):
    project = store.project("合成核验重试", [Path("/synthetic/multiedit")])
    with patch("rg.derive.edit_checks.validate_edits", return_value=0):
        ids = seed_case(store, tmp_path, project, "old", VALID, legacy=True)
    row = saved(store, ids["edit_id"])
    sha = store.db.execute(
        "SELECT content_sha256 FROM artifact_versions WHERE version_id=?", (row["after_version"],)
    ).fetchone()[0]
    raw = store.objects.get(sha)
    store.objects.path(sha).unlink()
    assert validate_edits(store, 1) == 1
    unavailable = recorded_edit(store, row)
    assert unavailable["request_validation"]["status"] == "unavailable"
    store.objects.put(raw)
    assert validate_edits(store) == 0
    assert recorded_edit(store, row) == unavailable
    with patch("rg.derive.edit_checks.validate_edits", return_value=0):
        new = seed_case(store, tmp_path, project, "new", VALID)
    assert validate_edits(store, 1, retry_unavailable=True) == 1
    assert (
        recorded_edit(store, saved(store, new["edit_id"]))["request_validation"]["status"]
        == "matches_request"
    )
    assert recorded_edit(store, row) == unavailable
    assert validate_edits(store, 1, retry_unavailable=True) == 1
    assert recorded_edit(store, row)["request_validation"]["status"] == "matches_request"
    assert (
        store.db.execute(
            "SELECT count(*) FROM edit_request_checks WHERE edit_id=?", (ids["edit_id"],)
        ).fetchone()[0]
        == 2
    )
    assert saved(store, ids["edit_id"]) == row
