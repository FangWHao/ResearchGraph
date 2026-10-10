from __future__ import annotations

import shutil
import socket
import sqlite3
from contextlib import closing

import pytest

from rg.artifacts.worker import Worker
from rg.mcp.tools import ToolService
from rg.query.context import context
from rg.query.reader import Reader
from rg.query.tokenizer import LocalCounter
from rg.store import migrations
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps
from tests.golden.test_artifacts import large_file, snapshot
from tests.golden.test_links_overview import add_object
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, T4, content
from tests.golden.test_snapshots_hooks import project
from tests.test_migrations import legacy_store


def scene(store, tmp_path, source="current_file", observed=T4):
    work = tmp_path / "synthetic-work"
    work.mkdir(exist_ok=True)
    owner = store.project("合成文件查询", [work])
    root = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (owner,)
    ).fetchone()[0]
    identity = "version:" + owner
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,size,source,"
        "observed_at,root_id,basis,claim_state,representation) "
        "VALUES (?,?,?,'sha256',?,5,?,?,?,'direct_record','candidate','file_bytes')",
        (identity, owner, str(work / "file.bin"), "a" * 64, source, observed, root),
    )
    return owner, root, identity


def captured(store, owner, root, occurred=T1, recorded=T1):
    return store.db.execute(
        "INSERT INTO workspace_snapshots(project_id,root_id,trigger,shadow_commit,taken_at,"
        "recorded_at,metadata) VALUES (?,?,'synthetic',?,?,?,'{}')",
        (owner, root, "a" * 40, occurred, recorded),
    ).lastrowid


def observed(
    store,
    owner,
    root,
    version,
    snap,
    identity="one",
    *,
    recorded=T2,
    started=T1,
    finished=T1,
    archived=False,
    cached_from=None,
):
    job = store.db.execute(
        "INSERT INTO artifact_jobs(job_key,project_id,root_id,snapshot_id,kind,input_json,"
        "state,created_at,updated_at) VALUES (?,?,?,?,'file_hash','{}','done',?,?)",
        ("job:" + owner + identity, owner, root, snap, recorded, recorded),
    ).lastrowid
    store.db.execute(
        "INSERT INTO artifact_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            version,
            job,
            snap if archived else None,
            snap,
            "100644",
            dumps([1, 2, 3]),
            started,
            finished,
            int(cached_from is not None),
            cached_from,
            dumps({"complete": True, "content_copied": archived}),
            recorded,
        ),
    )


def evidence(store, owner, version, **values):
    return content(
        ToolService(store, owner).call("research.evidence", {"version_id": version} | values)
    )


def test_current_hash_requires_catalog_knowledge_not_first_version_time(store, tmp_path):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, recorded=T3, started=T1, finished=T2)
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, owner, version, known_until=T2)
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, owner, version, occurred_until=T1)
    value = evidence(store, owner, version, occurred_until=T2, known_until=T3)
    assert value["version"]["observed_at"] == T2  # 存储列 T4 不回流到旧视图。
    assert value["version"]["recorded_at"] == T3
    assert value["version"]["claim_state"] == "candidate"
    assert value["version"]["evidence_event_id"] is None
    observation = value["observations"]["items"][0]
    assert observation["snapshot_id"] is None and observation["snapshot"] is None
    assert observation["discovery_snapshot"]["snapshot_id"] == snap
    assert observation["hash_started_at"] == T1 and observation["hash_finished_at"] == T2


def test_archived_snapshot_knowledge_is_required_and_never_substituted(store, tmp_path):
    owner, root, version = scene(store, tmp_path, "shadow_snapshot")
    snap = captured(store, owner, root, occurred=T1, recorded=T3)
    observed(store, owner, root, version, snap, archived=True, recorded=T2)
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, owner, version, known_until=T2)
    value = evidence(store, owner, version, occurred_until=T1, known_until=T3)
    assert value["version"]["occurred_at"] == T1
    assert value["version"]["recorded_at"] == T3
    item = value["observations"]["items"][0]
    assert item["catalog_recorded_at"] == T2 and item["recorded_at"] == T3
    assert item["snapshot"]["shadow_commit"] == "a" * 40


def test_observation_pages_hide_future_counts_and_preserve_cache_read_window(store, tmp_path):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, "first", recorded=T2)
    observed(store, owner, root, version, snap, "cache", recorded=T3, cached_from="first")
    observed(store, owner, root, version, snap, "future", recorded=T4, finished=T4)
    old = evidence(store, owner, version, known_until=T2)
    assert old["observations"]["total"] == old["version"]["observations_total"] == 1
    page = evidence(store, owner, version, known_until=T3, limit=1)
    assert page["observations"]["total"] == 2 and page["observations"]["next_offset"] == 1
    item = page["observations"]["items"][0]
    assert item["cache_reused"] and item["cached_from"] == "first"
    assert item["occurred_at"] == item["hash_finished_at"] == T1
    assert item["recorded_at"] == T3
    later = evidence(store, owner, version, known_until=T3, limit=1, offset=1)
    assert later["observations"]["items"][0]["observation_id"] == "first"
    assert later["observations"]["next_offset"] is None


def test_context_and_evidence_share_visibility_and_scope_is_not_guessed(store, tmp_path):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, recorded=T3)
    assert context(Reader(store, owner, {"known_until": T2}))["items"] == []
    visible = context(Reader(store, owner, {"known_until": T3}))["items"]
    assert len(visible) == 1 and visible[0]["citation_id"] == "V:" + version
    assert visible[0]["recorded_at"] == T3 and visible[0]["priority"] == 5
    assert context(Reader(store, owner, {"scope": SCOPE}))["items"] == []
    with pytest.raises(ValueError, match="没有统一范围"):
        evidence(store, owner, version, scope=SCOPE)
    other = store.project("其他项目", [])
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, other, version)


@pytest.mark.parametrize("recorded", ["invalid", "2026-01-02T00:00:00"])
def test_unknown_recorded_time_is_excluded_and_counted(store, tmp_path, recorded):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, recorded=recorded)
    value = context(Reader(store, owner, {}))
    assert value["items"] == [] and value["unknown_recorded_time_excluded"] == 1


@pytest.mark.parametrize("started,finished", [(None, None), (T3, T1), (T1, "invalid")])
def test_unknown_or_invalid_read_window_remains_unknown(store, tmp_path, started, finished):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, started=started, finished=finished)
    value = evidence(store, owner, version)
    assert value["version"]["occurred_time_unknown"]
    assert value["version"]["observed_at"] is None
    assert value["observations"]["items"][0]["provenance_warnings"]


def test_timezone_order_uses_instants_and_future_discovery_is_not_exposed(store, tmp_path):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root, occurred=T4, recorded=T4)
    observed(store, owner, root, version, snap, "older", recorded="2026-01-02T09:00:00+08:00")
    observed(store, owner, root, version, snap, "newer", recorded="2026-01-02T02:00:00+00:00")
    value = evidence(store, owner, version, occurred_until=T2, known_until=T3)
    assert [r["observation_id"] for r in value["observations"]["items"]] == ["newer", "older"]
    assert all(r["discovery_snapshot"] is None for r in value["observations"]["items"])
    assert "shadow_commit" not in dumps(value)


def test_snapshot_from_other_project_cannot_be_used_as_archived_evidence(store, tmp_path):
    owner, root, version = scene(store, tmp_path, "shadow_snapshot")
    other = store.project("其他项目", [])
    snap = captured(store, other, None)
    observed(store, owner, root, version, snap, archived=True)
    assert context(Reader(store, owner, {}))["items"] == []
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, owner, version)


def test_new_observation_invalidates_pagination_even_if_version_is_unchanged(store, tmp_path):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap)
    before = store.revision()
    observed(store, owner, root, version, snap, "later", recorded=T3)
    assert store.revision() == before + 1
    with pytest.raises(ConflictError):
        evidence(store, owner, version, offset=1, expected_revision=before)


def test_legacy_version_cannot_borrow_another_projects_raw_event(store, tmp_path):
    owner = store.project("合成版本所属项目", [])
    other = store.project("原文所属项目", [])
    _, event = add_object(store, tmp_path, other, "foreign-version")
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
        "evidence_event_id) VALUES ('legacy',?,'/synthetic/a','sha256','unknown','agent_edit',?)",
        (owner, event),
    )
    assert context(Reader(store, owner, {}))["items"] == []
    with pytest.raises(ValueError, match="没有这个版本"):
        evidence(store, owner, "legacy")


def test_observation_response_has_no_fixed_token_cap_and_context_uses_local_measurement(
    store, tmp_path
):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    for index in range(8):
        observed(store, owner, root, version, snap, str(index))
    service = ToolService(store, owner)
    result = service.call("research.evidence", {"version_id": version})
    assert LocalCounter().count(result["content"][0]["text"]) > 1500
    assert content(result)["observations"]["total"] == 8
    card = service.call("research.context", {"budget": 16000})
    assert card["_meta"]["researchgraph/textTokens"] == LocalCounter().count(
        card["content"][0]["text"]
    )
    assert content(card)["items"][0]["citation_id"] == "V:" + version
    assert content(card)["tokenizer"] == "cl100k_base"


def test_readonly_query_survives_workspace_loss_and_backup_without_network(
    store, tmp_path, monkeypatch
):
    owner, root, work = project(store, tmp_path)
    large_file(work)
    snapshot(store, owner, root, work)
    assert Worker(store).run()["done"] == 2
    versions = [r[0] for r in store.db.execute("SELECT version_id FROM artifact_versions")]
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    destination = tmp_path / "backup"
    backup(store, destination)
    shutil.rmtree(work)
    with closing(Store(destination, readonly=True)) as restored:
        before = list(restored.db.iterdump())
        monkeypatch.setattr(restored.objects, "get", lambda _: pytest.fail("metadata only"))
        monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("offline query"))
        for version in versions:
            assert evidence(restored, owner, version)["version"]["claim_state"] == "candidate"
        assert list(restored.db.iterdump()) == before
        assert [tuple(r) for r in restored.db.execute("SELECT * FROM raw_events")] == raw
        assert restored.db.execute("SELECT count(*) FROM run_io").fetchone()[0] == 0
        assert restored.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_v13_migration_is_atomic_preserves_raw_and_invalidates_old_version_view(
    tmp_path, monkeypatch
):
    path, raw = legacy_store(tmp_path, monkeypatch, version=13)
    db = sqlite3.connect(path / "rg.db")
    db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source) "
        "VALUES ('legacy','synthetic','/synthetic/a','sha256','unknown','agent_edit')"
    )
    before = db.execute("SELECT revision FROM graph_clock").fetchone()[0]
    db.commit()
    db.close()
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 14, (*migrations.MIGRATIONS[14], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(path)
    db = sqlite3.connect(path / "rg.db")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 13
    assert db.execute("SELECT revision FROM graph_clock").fetchone()[0] == before
    assert not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='artifact_observation_revision'"
    ).fetchone()
    db.close()
    with closing(Store(path)) as upgraded:
        assert upgraded.raw(1) == raw and upgraded.revision() == before + 1
    with closing(Store(path)) as repeated:
        assert repeated.revision() == before + 1
