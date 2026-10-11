from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

import pytest

from rg.derive.claim_sources import assess, check_batch, recorded
from rg.extract.overview import records
from rg.extract.worker import Worker
from rg.extract.working_set import working_set
from rg.query.context import bounded, context
from rg.query.reader import Reader
from rg.query.tokenizer import LocalCounter
from rg.store.database import Store, dumps, now
from tests.golden.test_links_overview import DerivedProvider, add_object
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, append, original, review
from tests.injected_sources import legacy_candidate


class Counter:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def count_text(self, text: str) -> int:
        self.texts.append(text)
        return 1


@pytest.mark.parametrize("confirmed", [False, True])
def test_legacy_injected_label_never_reaches_working_set_counter(store, confirmed):
    project, claim, _ = legacy_candidate(store, "codex", "0.162-native-mcp", confirmed)
    entity = store.db.execute("SELECT entity_id FROM claims WHERE claim_id=?", (claim,)).fetchone()[
        0
    ]
    before = [tuple(r) for r in store.db.execute("SELECT * FROM claims")]
    counter = Counter()
    result = working_set(store, project, "budget", counter)
    assert entity not in {r["id"] for r in result}
    assert all("budget 合成假说" not in text for text in counter.texts)
    assert [tuple(r) for r in store.db.execute("SELECT * FROM claims")] == before
    assert store.claim_state(claim) == ("confirmed" if confirmed else "candidate")


def test_confirmed_model_injection_cannot_compute_current_adoption(store):
    project, claim, _ = legacy_candidate(store, "claude", "2.1.280-native-mcp", True)
    base = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim,)).fetchone()
    entity = base["entity_id"]
    decision = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at,extraction_run_id) "
        "VALUES ('decision_event',?,?,?,'model_inference','model:synthetic','candidate',?,?,?)",
        (
            entity,
            dumps({"target": entity, "action": "accepted"}),
            base["scope"],
            now(),
            now(),
            base["extraction_run_id"],
        ),
    ).lastrowid
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (decision, claim),
    )
    review(store, decision, "confirm", recorded=now())
    before = list(store.db.iterdump())
    with patch.object(store, "raw", side_effect=AssertionError("图查询不能打开正文")):
        reader = Reader(store, project, {})
        state = reader.states(entity, {"dataset": "synthetic"})["adoption"]
        assert state["state"] != "accepted"
        assert (
            next(r for r in reader.claims() if r["claim_id"] == decision)["effective_state"]
            == "confirmed"
        )
    assert store.claim_state(decision) == "confirmed"
    assert list(store.db.iterdump()) == before


def good_claim(store: Store, tmp_path) -> tuple[str, int, int]:
    project = store.project("公开来源核对", [])
    claim, event = add_object(store, tmp_path, project, str(uuid4()), kind="question")
    return project, claim, event


def test_metadata_reads_omit_injection_but_retain_immutable_human_history(store):
    project, claim, _ = legacy_candidate(store, "codex", "0.162-native-mcp", True)
    before, revision = list(store.db.iterdump()), store.revision()
    with patch.object(store, "raw", side_effect=AssertionError("只读接口不能打开保存正文")):
        assert records(store, project) == []
        reader = Reader(store, project, {})
        value = context(reader)
        text, count = bounded(value, 2000, LocalCounter())
        assert count <= 2000 and "budget 合成假说" not in text
        assert value["source_coverage"]["omitted"] == 1
        assert value["source_coverage"]["statuses"]["excluded"] == 1
        saved = reader.claims()[0]
        assert saved["effective_state"] == "confirmed" and not saved["input_eligible"]
        assert saved["source_qualification"]["status"] == "excluded"
    assert list(store.db.iterdump()) == before and store.revision() == revision
    assert store.claim_state(claim) == "confirmed"


def test_source_checks_append_recovery_without_duplicate_or_rewriting_claim(store, tmp_path):
    _, claim, event = good_claim(store, tmp_path)
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    path = store.objects.path(sha)
    saved = path.read_bytes()
    before = [tuple(r) for r in store.db.execute("SELECT * FROM claims")]
    revision = store.revision()
    assert assess(store, claim, refresh=True)["status"] == "not_excluded"
    assert store.revision() == revision
    path.unlink()
    assert assess(store, claim, refresh=True)["status"] == "unavailable"
    assert not recorded(store, claim)["input_eligible"]
    path.write_bytes(saved)
    assert assess(store, claim, refresh=True)["status"] == "not_excluded"
    payloads = [
        r[0] for r in store.db.execute("SELECT payload FROM claim_source_checks ORDER BY check_id")
    ]
    assert len(payloads) == 3 and payloads[0] == payloads[2] != payloads[1]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE claim_source_checks SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM claim_source_checks")
    assert [tuple(r) for r in store.db.execute("SELECT * FROM claims")] == before
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_readonly_assessment_never_persists_diagnostics(store, tmp_path):
    project, claim, event = good_claim(store, tmp_path)
    store.db.execute("INSERT INTO dedupe_links VALUES (?,?,'public duplicate')", (event, event))
    before = list(store.db.iterdump())
    with closing(Store(store.root, readonly=True)) as reader:
        assert recorded(reader, claim)["status"] == "pending"
        assert assess(reader, claim)["status"] == "excluded"
        assert (
            Reader(reader, project, {}).claims()[0]["source_qualification"]["status"] == "pending"
        )
    assert list(store.db.iterdump()) == before


def test_background_rotation_retries_unavailable_without_starving_later_claims(store, tmp_path):
    _, first, event = good_claim(store, tmp_path)
    _, second, second_event = good_claim(store, tmp_path)
    store.db.execute(
        "UPDATE jobs SET payload=? WHERE kind='claim_source_cursor'", (dumps({"last_claim": 0}),)
    )
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    path, saved = store.objects.path(sha), store.objects.path(sha).read_bytes()
    path.unlink()
    assess(store, first, refresh=True)
    # 第一条不可用时照样推进游标；恢复后仍先推进后一条。
    assert check_batch(store, 1)["claim_sources_checked"] == 1
    assert recorded(store, first)["status"] == "unavailable"
    path.write_bytes(saved)
    with patch.object(store, "raw", wraps=store.raw) as opened:
        assert check_batch(store, 1)["claim_sources_checked"] == 1
        assert {call.args[0] for call in opened.call_args_list} == {second_event}
    assert recorded(store, second)["status"] == "not_excluded"
    assert check_batch(store, 1)["claim_sources_checked"] == 1
    assert recorded(store, first)["status"] == "not_excluded"


@pytest.mark.parametrize("status", ["pending", "unavailable", "excluded"])
@pytest.mark.parametrize("when", [None, T1, T3])
def test_unknown_newer_confirmed_source_does_not_roll_back_adoption(store, status, when):
    from rg.query.reader import event_state

    project = store.project("公开状态核对", [])
    entity, _ = original(store, project)
    old = append(
        store,
        entity,
        "decision_event",
        {"target": entity, "action": "accepted"},
        scope=SCOPE,
        state="confirmed",
        occurred=T2,
    )
    row = Reader(store, project, {}).claims()[-1]
    suspect = dict(row) | {
        "claim_id": old + 1,
        "occurred_at": when,
        "occurred_time_unknown": when is None,
        "input_eligible": False,
        "source_qualification": {"status": status},
        "payload": {"target": entity, "action": "withdrawn"},
    }
    result = event_state([row, suspect], "action", "unknown")
    assert result["state"] == ("unknown" if status != "excluded" and when != T1 else "accepted")
    assert result["source_issues"] == [{"claim_id": old + 1, "status": status}]


def test_manual_independent_record_is_not_demoted_by_model_source_filter(store):
    project, polluted, _ = legacy_candidate(store, "claude", "2.1.280-native-mcp", True)
    entity = store.db.execute(
        "SELECT entity_id FROM claims WHERE claim_id=?", (polluted,)
    ).fetchone()[0]
    manual = append(
        store,
        entity,
        "decision_event",
        {"target": entity, "action": "accepted"},
        scope=SCOPE,
        state="confirmed",
        occurred=T2,
    )
    assert assess(store, manual)["status"] == "not_required"
    assert Reader(store, project, {}).states(entity, SCOPE)["adoption"]["state"] == "accepted"


def test_completed_pipeline_does_not_reuse_old_claim_policy(store, tmp_path, monkeypatch):
    from rg.derive import claim_sources
    from rg.extract.pipeline import Stages
    from tests.golden.test_links_overview import two_objects

    with monkeypatch.context() as legacy:
        legacy.setattr(claim_sources, "POLICY_VERSION", "public-old-policy")
        project, _, _ = two_objects(store, tmp_path)
        provider = DerivedProvider("none")
        old = Stages(Worker(store, provider), project, None, 50)
        assert old.run(20)["link_done"] == 1
    assert records(store, project) == []  # 旧资格不能借新策略进入概览。
    assert sum(check_batch(store, 20)["claim_sources_changed"] for _ in range(2)) >= 2
    assert len(records(store, project)) == 2
    calls = provider.calls
    current = Stages(Worker(store, provider), project, None, 50)
    assert all(current.configs[s] != old.configs[s] for s in current.configs)
    result = current.run(20)
    assert result["link_done"] == 1 and result["overview_done"] == 3
    assert provider.calls == calls + 4  # 一条关联、项目及两个会话概览。
    assert store.db.execute("SELECT count(*) FROM link_progress").fetchone()[0] == 2
    assert all(r[0] == "done" for r in store.db.execute("SELECT state FROM pipeline_queue"))


def test_backup_retains_checks_and_project_clear_removes_managed_copy(tmp_path):
    from rg.store import clear
    from rg.store.backup import backup

    root = tmp_path / "managed"
    with closing(Store(root)) as store:
        project, claim, _ = good_claim(store, tmp_path)
        other = store.project("保留项目", [])
        checks = [tuple(r) for r in store.db.execute("SELECT * FROM claim_source_checks")]
        archive = tmp_path / "managed-backup"
        backup(store, archive)
    with closing(Store(archive, readonly=True)) as saved:
        assert [tuple(r) for r in saved.db.execute("SELECT * FROM claim_source_checks")] == checks
        assert recorded(saved, claim)["status"] == "not_excluded"
    # 备份位于管理目录之外，清除不越界删除它；原数据诊断按项目清除。
    preview = clear.preview(root, project)
    assert preview["blockers"] == []
    result = clear.execute(root, project, str(uuid4()), preview["preview_sha256"])
    assert result["state"] == "complete" and result["rows"]["claim_source_checks"] == 1
    with closing(Store(root)) as remaining:
        assert remaining.db.execute("SELECT count(*) FROM claim_source_checks").fetchone()[0] == 0
        assert remaining.db.execute("SELECT project_id FROM projects").fetchone()[0] == other
        assert remaining.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert archive.exists()


def test_v25_upgrade_only_adds_pending_diagnostics_and_rolls_back_atomically(tmp_path, monkeypatch):
    from rg.store import migrations
    from tests.test_migrations import legacy_store

    root, raw = legacy_store(tmp_path, monkeypatch, version=25)
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "LATEST_VERSION", 25)
        with closing(Store(root)) as old:
            project = old.project("旧来源记录", [])
            entity, _ = original(old, project)
            claim = old.db.execute(
                "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
                "occurred_at,recorded_at) VALUES ('decision_event',?,?,?,"
                "'model_inference','model:legacy','candidate',?,?)",
                (entity, dumps({"target": entity, "action": "accepted"}), dumps(SCOPE), T1, T1),
            ).lastrowid
            review(old, claim, "confirm", recorded=T2)
            claims = [tuple(r) for r in old.db.execute("SELECT * FROM claims")]
            reviews = [tuple(r) for r in old.db.execute("SELECT * FROM review_actions")]
            revision = old.revision()
    with monkeypatch.context() as failure:
        failure.setitem(migrations.MIGRATIONS, 26, (*migrations.MIGRATIONS[26], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 25
        assert (
            old.execute(
                "SELECT name FROM sqlite_master WHERE name='claim_source_checks'"
            ).fetchall()
            == []
        )
    with patch.object(Store, "raw", side_effect=AssertionError("迁移不能打开保存正文")):
        with closing(Store(root)) as current:
            assert current.revision() == revision + 1
            assert [tuple(r) for r in current.db.execute("SELECT * FROM claims")] == claims
            assert [tuple(r) for r in current.db.execute("SELECT * FROM review_actions")] == reviews
            assert recorded(current, claim)["status"] == "pending"
            assert current.db.execute("SELECT count(*) FROM claim_source_checks").fetchone()[0] == 0
            assert (
                Reader(current, project, {}).states(entity, SCOPE)["adoption"]["state"] == "unknown"
            )
    with closing(Store(root)) as current:
        assert current.raw(1) == raw


@pytest.mark.parametrize("cutoff", ["known_until", "occurred_until"])
def test_qualification_does_not_borrow_future_injected_call(store, tmp_path, cutoff):
    from rg.ingest.scanner import scan_file
    from rg.store.objects import digest
    from tests.golden.test_read_tools import T4

    project = store.project("公开迟到来源", [])
    entity, _ = original(store, project)
    path = tmp_path / "public-late.jsonl"
    path.write_text(
        dumps(
            {
                "type": "session_meta",
                "timestamp": T1,
                "payload": {"id": "synthetic-qualification-late"},
            }
        )
        + "\n"
        + dumps(
            {
                "type": "response_item",
                "timestamp": T1,
                "payload": {
                    "type": "function_call_output",
                    "call_id": "synthetic-late-call",
                    "output": "public source",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with patch("rg.ingest.scanner.now", return_value=T1):
        scan_file(store, path, "codex", project)
    event = store.db.execute("SELECT event_id FROM raw_events WHERE kind='tool_result'").fetchone()[
        0
    ]
    raw = store.raw(event)
    start = raw.index(b"public source")
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,?,?,?)",
        (event, start, start + 13, digest(b"public source")),
    ).lastrowid
    claim = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at) VALUES ('decision_event',?,?,?,"
        "'model_inference','model:synthetic','candidate',?,?)",
        (entity, dumps({"target": entity, "action": "accepted"}), dumps(SCOPE), T1, T1),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim, span))
    review(store, claim, "confirm", recorded=T2)
    assert assess(store, claim)["status"] == "not_excluded"
    with path.open("a", encoding="utf-8") as out:
        out.write(
            dumps(
                {
                    "type": "response_item",
                    "timestamp": T3,
                    "payload": {
                        "type": "function_call",
                        "namespace": "mcp__researchgraph",
                        "name": "research_context",
                        "call_id": "synthetic-late-call",
                        "arguments": "{}",
                    },
                }
            )
            + "\n"
        )
    with patch("rg.ingest.scanner.now", return_value=T3):
        scan_file(store, path, "codex", project)
    assert assess(store, claim)["status"] == "excluded"
    before = list(store.db.iterdump())
    with patch.object(store, "raw", side_effect=AssertionError("历史查询不打开保存正文")):
        earlier = Reader(store, project, {"known_until": T4, "occurred_until": T4, cutoff: T2})
        assert earlier.states(entity, SCOPE)["adoption"]["state"] == "accepted"
        assert Reader(store, project, {}).states(entity, SCOPE)["adoption"]["state"] == "unknown"
    assert list(store.db.iterdump()) == before


def test_working_set_does_not_label_old_acceptance_current_when_new_source_missing(store, tmp_path):
    project, claim, event = good_claim(store, tmp_path)
    base = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim,)).fetchone()
    entity, scope = base["entity_id"], json.loads(base["scope"])
    append(
        store,
        entity,
        "decision_event",
        {"target": entity, "action": "accepted"},
        scope=scope,
        state="confirmed",
        occurred=T1,
    )
    newer = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at) VALUES ('decision_event',?,?,?,"
        "'model_inference','model:synthetic','candidate',?,?)",
        (entity, dumps({"target": entity, "action": "withdrawn"}), base["scope"], T3, T3),
    ).lastrowid
    # 新决定引用独立合成对象；节点自身原件仍可读。
    path = tmp_path / "other-public.jsonl"
    other, other_event = add_object(store, tmp_path, project, path.stem)
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (newer, other),
    )
    review(store, newer, "confirm", recorded=T3)
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (other_event,)
    ).fetchone()[0]
    store.objects.path(sha).unlink()
    result = working_set(store, project, entity, Counter())
    assert next(r for r in result if r["id"] == entity)["state"] == "unknown"


def test_mixed_good_and_excluded_references_cannot_send_model_label(store, tmp_path):
    project, injected, _ = legacy_candidate(store, "codex", "0.162-native-mcp", True)
    claim, _ = add_object(store, tmp_path, project, "mixed-reference", "公开独立标签")
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (claim, injected),
    )
    assert recorded(store, claim)["status"] == "pending"
    result = assess(store, claim)
    assert result["status"] == "excluded" and len(result["references"]) == 2
    counter = Counter()
    assert working_set(store, project, "公开独立标签 budget", counter) == []
    assert counter.texts == []
    assert records(store, project) == []


def test_unqualified_second_link_card_is_rejected_before_counting_good_first_source(store):
    from rg.extract.linker import link, pairs
    from tests.golden.test_link_source_qualification import legacy_pair

    project, injected, _ = legacy_pair(store, "codex", "0.162-native-mcp", True)
    peer = pairs(store, project)[0].cards[1]["claim_id"]
    latest = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "occurred_at,recorded_at) SELECT claim_type,entity_id,payload,scope,basis,actor,"
        "claim_state,occurred_at,recorded_at FROM claims WHERE claim_id=?",
        (peer,),
    ).lastrowid
    store.db.execute(
        "INSERT INTO claim_evidence SELECT ?,span_id,role FROM claim_evidence WHERE claim_id=?",
        (latest, peer),
    )
    assert [c["claim_id"] for c in pairs(store, project)[0].cards] == [latest, injected]
    provider = DerivedProvider("none")
    with patch.object(provider, "count_text", side_effect=AssertionError("不能计数任何卡片或原文")):
        result = link(Worker(store, provider), project)
    assert result["manual"] == 1 and provider.calls == provider.counts == 0
