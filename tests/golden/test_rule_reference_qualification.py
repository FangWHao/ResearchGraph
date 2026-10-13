from __future__ import annotations

import sqlite3
from contextlib import closing
from copy import deepcopy
from unittest.mock import patch

import pytest

from rg.derive import rule_references
from rg.query.reader import Reader, event_state
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from tests.golden.test_rule_confirmation import decision, save
from tests.golden.test_rule_scope_versions import action_source, another_scope, source_object


def legacy_action(store, tmp_path, *, scoped=False, text="采用方法甲。", mutate=None):
    project, entity, output, definition = source_object(store, tmp_path)
    if scoped:
        another_scope(store, entity, output["claims"][0], {"dataset_version": "data_v3"})
    _, span = action_source(store, tmp_path, project, text)
    with patch("rg.extract.validate.confirm_explicit", return_value=0):
        claim = save(store, project, [decision(entity, span)], {entity})[0]
    store.db.execute(
        "UPDATE extraction_runs SET working_set_ids=? WHERE extraction_run_id="
        "(SELECT extraction_run_id FROM claims WHERE claim_id=?)",
        (dumps([entity]), claim),
    )
    proof = {
        "rule_version": "explicit-user-v1",
        "basis": "direct_record",
        "scope": {"dataset_version": "data_v2"},
        "target_ids": [entity],
        "reason_source": "verified-action-description",
        **{k: span[k] for k in ("event_id", "byte_start", "byte_end")},
        "quote_sha256": digest(span["quote"].encode()),
    }
    if mutate:
        proof.update(mutate)
    review = store.db.execute(
        "INSERT INTO review_actions(claim_id,action,reason,actor,expected_revision,recorded_at) "
        "VALUES(?,'confirm',?,'rule:explicit-user-v1',?,?)",
        (claim, dumps(proof), store.revision(), now()),
    ).lastrowid
    return project, entity, claim, review, definition, output


@pytest.mark.parametrize("scoped", [False, True])
def test_old_rule_review_keeps_badge_but_unchecked_proof_cannot_set_adoption(
    store: Store, tmp_path, scoped
):
    project, entity, claim, _, _, _ = legacy_action(store, tmp_path, scoped=scoped)
    reader = Reader(store, project, {})
    row = next(r for r in reader.claims() if r["claim_id"] == claim)
    assert row["effective_state"] == "confirmed"
    assert row["source_qualification"]["input_eligible"] is True
    assert row["input_eligible"] is False
    assert reader.states(entity, {"dataset_version": "data_v2"})["adoption"]["state"] == "unknown"


@pytest.mark.parametrize("text", ["采用方法甲。", "采用[aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee]。"])
@pytest.mark.parametrize("scoped", [False, True])
def test_legacy_proof_uses_known_versions_without_inventing_scope_filter(
    store, tmp_path, text, scoped
):
    project, entity, claim, _, definition, output = legacy_action(
        store, tmp_path, scoped=scoped, text=text
    )
    originals = {
        name: [tuple(r) for r in store.db.execute("SELECT * FROM " + name)]
        for name in ("claims", "review_actions", "raw_events")
    }
    result = rule_references.assess(store, claim)
    assert result["status"] == ("unverifiable" if scoped else "verified")
    if scoped:
        assert result["reasons"] == ["scope_filter_not_recorded"]
    assert store.claim_state(claim) == "confirmed"
    for name, rows in originals.items():
        assert [tuple(r) for r in store.db.execute("SELECT * FROM " + name)] == rows
    checks = store.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0]
    later = deepcopy(output["claims"][0])
    later["label"] = "后来的方法名"
    store.review(definition, "dismiss", "human:公开后续更正", store.revision())
    another_scope(store, entity, later, {"dataset_version": "data_v4"}, replaces=definition)
    assert rule_references.recorded(store, claim)["status"] == result["status"]
    assert rule_references.assess(store, claim, refresh=True)["status"] == result["status"]
    assert store.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0] == checks
    row = next(r for r in Reader(store, project, {}).claims() if r["claim_id"] == claim)
    assert row["rule_qualification"]["status"] == result["status"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"target_ids": ["public-wrong-object"]},
        {"reason_source": "unverified-model-reason"},
        {"scope": {"dataset_version": "wrong"}},
        {"quote_sha256": "0" * 64},
        {"byte_start": 0},
        {"scope_filter": {"dataset_version": "wrong"}},
        {"basis": "model_inference"},
    ],
)
def test_source_eligibility_cannot_validate_forged_proof(store, tmp_path, mutation):
    from rg.derive.claim_sources import recorded

    project, _, claim, _, _, _ = legacy_action(store, tmp_path, mutate=mutation)
    assert recorded(store, claim)["input_eligible"] is True
    assert rule_references.assess(store, claim)["status"] == "invalid"
    row = next(r for r in Reader(store, project, {}).claims() if r["claim_id"] == claim)
    assert row["effective_state"] == "confirmed" and not row["input_eligible"]


@pytest.mark.parametrize(
    "text", ["如果通过就采用方法甲。", "采用方法甲吗？", "采用方法甲。算了，撤回。"]
)
def test_model_flags_cannot_replace_complete_statement(store, tmp_path, text):
    _, _, claim, _, _, _ = legacy_action(store, tmp_path, text=text)
    assert rule_references.assess(store, claim)["status"] == "invalid"


def test_missing_working_set_is_unverifiable(store, tmp_path):
    _, _, claim, _, _, _ = legacy_action(store, tmp_path)
    store.db.execute(
        "UPDATE extraction_runs SET working_set_ids=NULL WHERE extraction_run_id="
        "(SELECT extraction_run_id FROM claims WHERE claim_id=?)",
        (claim,),
    )
    assert rule_references.assess(store, claim)["reasons"] == ["working_set_not_recorded"]


def test_metadata_reads_do_not_open_source_or_persist_checks(store, tmp_path):
    from rg.api.views import _claim
    from rg.extract.overview import records
    from rg.query.context import context

    project, entity, claim, _, _, _ = legacy_action(store, tmp_path, scoped=True)
    rule_references.assess(store, claim)
    before, revision = list(store.db.iterdump()), store.revision()
    with patch.object(store, "raw", side_effect=AssertionError("只读资格不能打开正文")):
        reader = Reader(store, project, {})
        assert (
            reader.states(entity, {"dataset_version": "data_v2"})["adoption"]["state"] == "unknown"
        )
        value = context(reader)
        assert value["rule_coverage"]["omitted"] == 1
        assert value["source_coverage"]["omitted"] == 0
        assert claim not in {r["claim_id"] for r in records(store, project)}
        row = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim,)).fetchone()
        assert _claim(store, row)["rule_qualification"]["status"] == "unverifiable"
    assert list(store.db.iterdump()) == before and store.revision() == revision


def test_human_override_does_not_rewrite_historical_view(store, tmp_path):
    project, _, claim, action, _, _ = legacy_action(store, tmp_path, scoped=True)
    rule_references.assess(store, claim)
    until = store.db.execute(
        "SELECT recorded_at FROM review_actions WHERE action_id=?", (action,)
    ).fetchone()[0]
    store.review(claim, "confirm", "human:公开独立复核", store.revision())
    current = next(r for r in Reader(store, project, {}).claims() if r["claim_id"] == claim)
    historic = next(
        r for r in Reader(store, project, {"known_until": until}).claims() if r["claim_id"] == claim
    )
    assert current["rule_qualification"]["status"] == "not_required" and current["input_eligible"]
    assert (
        historic["rule_qualification"]["status"] == "unverifiable"
        and not historic["input_eligible"]
    )
    assert historic["review"]["actor"] == "rule:explicit-user-v1"


def test_recovery_checks_append_and_readonly_assessment_does_not_write(store, tmp_path):
    _, _, claim, _, _, _ = legacy_action(store, tmp_path)
    assert rule_references.assess(store, claim)["status"] == "verified"
    event = store.db.execute(
        "SELECT event_id FROM evidence_spans JOIN claim_evidence USING(span_id) WHERE claim_id=?",
        (claim,),
    ).fetchone()[0]
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    path, original = store.objects.path(sha), store.objects.path(sha).read_bytes()
    before = list(store.db.iterdump())
    path.unlink()
    with closing(Store(store.root, readonly=True)) as readonly:
        assert rule_references.assess(readonly, claim, refresh=True)["status"] == "unavailable"
    assert list(store.db.iterdump()) == before
    assert rule_references.assess(store, claim, refresh=True)["status"] == "unavailable"
    path.write_bytes(original)
    assert rule_references.assess(store, claim, refresh=True)["status"] == "verified"
    assert store.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0] == 3
    revision = store.revision()
    rule_references.assess(store, claim, refresh=True)
    assert store.revision() == revision
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE rule_reference_checks SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM rule_reference_checks")
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "unknown"),
        ("unverifiable", "unknown"),
        ("unavailable", "unknown"),
        ("invalid", "accepted"),
    ],
)
def test_newer_uncertain_proof_blocks_fallback_and_invalid_does_not(status, expected):
    good = {
        "claim_id": 1,
        "payload": {"action": "accepted"},
        "effective_state": "confirmed",
        "replaced": False,
        "occurred_at": "2026-10-10T10:00:00+00:00",
        "occurred_time_unknown": False,
        "input_eligible": True,
        "source_qualification": {"status": "not_required", "input_eligible": True},
        "rule_qualification": {"status": "verified", "input_eligible": True},
    }
    newer = good | {
        "claim_id": 2,
        "payload": {"action": "withdrawn"},
        "occurred_at": "2026-10-10T11:00:00+00:00",
        "input_eligible": False,
        "rule_qualification": {"status": status, "input_eligible": False},
    }
    result = event_state([good, newer], "action", "unknown")
    assert result["state"] == expected
    assert result["rule_issues"] == [{"claim_id": 2, "status": status}]
    newer["source_qualification"] = {"status": "excluded", "input_eligible": False}
    assert event_state([good, newer], "action", "unknown")["state"] == "accepted"


def test_persistent_cursor_checks_old_reviews_and_retries_after_source_recovery(store, tmp_path):
    _, _, claim, action, _, _ = legacy_action(store, tmp_path)
    second = store.db.execute(
        "INSERT INTO review_actions(claim_id,action,reason,actor,expected_revision,recorded_at) "
        "SELECT claim_id,action,reason,actor,?,? FROM review_actions WHERE action_id=?",
        (store.revision(), now(), action),
    ).lastrowid
    with patch.object(store, "raw", side_effect=OSError("公开合成源暂时不可用")):
        assert rule_references.check_batch(store, 1)["rule_references_checked"] == 1
        assert rule_references.check_batch(store, 1)["rule_references_checked"] == 1
    reviews = {r[0] for r in store.db.execute("SELECT review_action_id FROM rule_reference_checks")}
    assert reviews == {action, second}
    assert rule_references.check_batch(store, 1)["rule_references_changed"] == 1
    assert rule_references.check_batch(store, 1)["rule_references_changed"] == 1
    assert rule_references.recorded(store, claim)["status"] == "verified"
    with closing(Store(store.root, readonly=True)) as readonly:
        cursor = readonly.db.execute(
            "SELECT payload FROM jobs WHERE kind='rule_reference_cursor'"
        ).fetchone()[0]
        assert str(second) in cursor


def test_v26_migration_is_atomic_and_never_rewrites_proof_or_reads_raw(tmp_path, monkeypatch):
    from rg.store import migrations

    root = tmp_path / "legacy-v26"
    with monkeypatch.context() as old_version:
        old_version.setattr(migrations, "LATEST_VERSION", 26)
        with closing(Store(root)) as old:
            project, _, claim, _, _, _ = legacy_action(old, tmp_path)
            originals = {
                name: [tuple(r) for r in old.db.execute("SELECT * FROM " + name)]
                for name in ("claims", "review_actions", "raw_events")
            }
            revision = old.revision()
            registered_roots = old.db.execute("SELECT count(*) FROM source_roots").fetchone()[0]
    with monkeypatch.context() as failure:
        failure.setitem(migrations.MIGRATIONS, 27, (*migrations.MIGRATIONS[27], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 26
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='rule_reference_checks'"
            ).fetchall()
            == []
        )
    with patch.object(Store, "raw", side_effect=AssertionError("迁移和只读视图不能打开正文")):
        with closing(Store(root)) as current:
            # 迁移27使旧确认依据失效，迁移37另为旧根登记追加历史基线。
            expected_revision = revision + 1 + registered_roots
            assert current.revision() == expected_revision
            assert current.db.execute(
                "SELECT count(*) FROM source_root_observations WHERE basis='legacy_baseline'"
            ).fetchone()[0] == registered_roots
            for name, rows in originals.items():
                assert [tuple(r) for r in current.db.execute("SELECT * FROM " + name)] == rows
            row = next(r for r in Reader(current, project, {}).claims() if r["claim_id"] == claim)
            assert row["rule_qualification"]["status"] == "pending"
            assert (
                current.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0] == 0
            )
        with closing(Store(root)) as repeated:
            assert repeated.revision() == expected_revision


def test_rule_policy_invalidates_model_stages_and_configuration_without_repeating_pass1(
    store, tmp_path, monkeypatch
):
    from rg.extract.provider import ModelResult
    from rg.extract.schemas import PASS1_SCHEMA, PASS2_SCHEMA
    from rg.extract.segmenter import Segment
    from rg.extract.worker import Worker
    from tests.conftest import FakeProvider

    project, _, _, _ = source_object(store, tmp_path)
    empty = dumps(
        {"segment_id": "public-rule-policy", "claims": [], "lookup_terms": [], "unresolved": []}
    )
    provider = FakeProvider(
        [ModelResult('{"candidates":[]}', 10, 10, "stop")]
        + [ModelResult(empty, 10, 10, "stop")] * 4,
        count=10,
    )
    worker, item = Worker(store, provider), Segment("public-rule-policy", [{"event_id": 1}])
    config = worker.configuration()
    stages = {
        stage: worker.invoke(
            stage, item, project, "[]", PASS2_SCHEMA if stage != "pass1" else PASS1_SCHEMA, []
        )[0]
        for stage in ("pass1", "pass2", "overview")
    }
    monkeypatch.setattr(rule_references, "POLICY_VERSION", "public-future-policy")
    assert worker.configuration() != config
    for stage, previous in stages.items():
        current = worker.invoke(
            stage, item, project, "[]", PASS2_SCHEMA if stage != "pass1" else PASS1_SCHEMA, []
        )[0]
        assert (current == previous) is (stage == "pass1")
    assert provider.calls == 5


def test_backup_and_whole_project_clear_include_rule_diagnostics_and_keep_other_project(tmp_path):
    from uuid import uuid4

    from rg.store import clear
    from rg.store.backup import backup

    root, destination = tmp_path / "managed", tmp_path / "backup"
    with closing(Store(root)) as saved:
        project, _, claim, _, _, _ = legacy_action(saved, tmp_path)
        other = saved.project("公开保留项目", [tmp_path / "other"])
        rule_references.assess(saved, claim)
        original = [tuple(r) for r in saved.db.execute("SELECT * FROM rule_reference_checks")]
        backup(saved, destination)
    with closing(Store(destination)) as copied:
        assert [
            tuple(r) for r in copied.db.execute("SELECT * FROM rule_reference_checks")
        ] == original
        assert rule_references.recorded(copied, claim)["status"] == "verified"
    info = clear.preview(root, project)
    assert info["rows"]["rule_reference_checks"] == 1 and not info["blockers"]
    clear.execute(root, project, str(uuid4()), info["preview_sha256"])
    with closing(Store(root)) as remaining:
        assert remaining.db.execute("SELECT project_id FROM projects").fetchone()[0] == other
        assert remaining.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0] == 0
        assert remaining.db.execute("PRAGMA foreign_key_check").fetchall() == []
    # 用户持有的外部备份仍由用户保留，不冒称已清掉所有副本。
    with closing(Store(destination, readonly=True)) as copied:
        assert copied.db.execute("SELECT count(*) FROM rule_reference_checks").fetchone()[0] == 1


def test_completed_pipeline_cannot_reuse_earlier_rule_policy(store, tmp_path, monkeypatch):
    from rg.extract.pipeline import Stages
    from rg.extract.worker import Worker
    from tests.golden.test_links_overview import DerivedProvider, two_objects

    def public_lines(path, values):
        path.write_bytes("".join(dumps(value) + "\n" for value in values).encode("utf-8"))

    with patch("tests.golden.test_links_overview.lines", public_lines):
        project, _, _ = two_objects(store, tmp_path)
    provider = DerivedProvider("none")
    with monkeypatch.context() as legacy:
        legacy.setattr(rule_references, "POLICY_VERSION", "public-previous-policy")
        previous = Stages(Worker(store, provider), project, None, 50)
        first = previous.run(20)
        assert first.get("link_done") == 1, first
        assert previous.run(20).get("link_processed", 0) == 0
    calls = provider.calls
    current = Stages(Worker(store, provider), project, None, 50)
    assert all(current.configs[stage] != previous.configs[stage] for stage in current.configs)
    result = current.run(20)
    assert result["link_done"] == 1 and result["overview_done"] == 3
    assert provider.calls == calls + 4
    assert store.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"wrong_key": 1},
        {"last_review": "private-body"},
        {"last_review": 1, "other": "private-body"},
        {"last_review": -1},
        {"last_review": True},
        [],
    ],
)
def test_only_exact_numeric_global_cursor_is_safe_to_keep_during_clear(store, tmp_path, payload):
    from rg.store import clear

    project = store.project("公开清除边界", [])
    store.db.execute(
        "INSERT INTO jobs(kind,payload,state,updated_at) "
        "VALUES('rule_reference_cursor',?,'done',?)",
        (dumps(payload), now()),
    )
    store.close()
    assert "存在无法确定归属的任务" in clear.preview(store.root, project)["blockers"]
