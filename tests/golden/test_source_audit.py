from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from rg.api.views import NotFound
from rg.query.source_audit import query
from rg.store.database import ConflictError, Store
from tests.golden.test_exports import evidence
from tests.golden.test_read_tools import original, review
from tests.injected_sources import legacy_candidate


def model_record(store, project):
    entity, manual = original(store, project)
    claim = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,scope,basis,actor,claim_state,"
        "recorded_at) "
        "SELECT claim_type,entity_id,payload,scope,'model_inference','model:合成','candidate',"
        "recorded_at FROM claims WHERE claim_id=?",
        (manual,),
    ).lastrowid
    return entity, claim


def test_audit_reports_model_only_and_preserves_review_and_originals(store):
    project, claim, _ = legacy_candidate(store, "claude", "2.1.280-native-mcp", True)
    original(store, project)
    before = list(store.db.iterdump())
    with closing(Store(store.root, readonly=True)) as reader:
        result = query(reader, {"project": project})
    assert result["total_references"] == 1
    item = result["items"][0]
    assert item["claim_id"] == claim and item["claim_state"] == "candidate"
    assert item["effective_state"] == "confirmed"
    assert item["exclude_reason"] is None and item["input_exclude_reason"] == "injected_by_rg"
    assert result["page_counts"] == {"excluded": 1, "not_excluded": 0, "unavailable": 0}
    assert not ({"payload", "actor", "path", "text", "quote", "review"} & item.keys())
    assert list(store.db.iterdump()) == before


def test_pages_read_only_selected_objects_and_refuse_changed_metadata(store):
    project = store.project("合成审计分页", [])
    _, claim = model_record(store, project)
    ids = [evidence(store, project, claim, "公开合成证据" + str(i))[0] for i in range(3)]
    before = list(store.db.iterdump())
    with patch.object(store, "raw", wraps=store.raw) as raw:
        first = query(store, {"project": project, "limit": 1})
        assert [call.args[0] for call in raw.call_args_list] == ids[:1]
        second = query(
            store,
            {"project": project, "limit": 1, "offset": 1, "expected_audit": first["audit_id"]},
        )
        assert second["audit_id"] == first["audit_id"] and second["next_offset"] == 2
        assert [call.args[0] for call in raw.call_args_list] == ids[:2]
    assert first["items"][0]["status"] == "not_excluded"
    assert list(store.db.iterdump()) == before
    review(store, claim, "confirm")
    with patch.object(store, "raw", side_effect=AssertionError("拒绝拼页前不能开对象")):
        with pytest.raises(ConflictError):
            query(store, {"project": project, "expected_audit": first["audit_id"]})


def test_unavailable_foreign_and_absent_references_do_not_leak_or_change_state(store):
    project = store.project("合成缺口", [])
    _, empty = model_record(store, project)
    _, missing = model_record(store, project)
    event, _, _ = evidence(store, project, missing, "公开合成缺失对象")
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    store.objects.path(sha).unlink()
    other = store.project("合成外项目", [])
    _, foreign = model_record(store, project)
    other_event, _, _ = evidence(store, other, foreign, "绝不可读的外项目合成资料")
    before = list(store.db.iterdump())
    with patch.object(store, "raw", wraps=store.raw) as raw:
        result = query(store, {"project": project})
        assert other_event not in [call.args[0] for call in raw.call_args_list]
    items = {r["claim_id"]: r for r in result["items"]}
    assert items[empty]["input_exclude_reason"] == "no_evidence"
    assert items[missing]["input_exclude_reason"] == "source_unavailable"
    assert items[missing]["error_class"] == "FileNotFoundError"
    assert items[foreign]["input_exclude_reason"] == "source_project_mismatch"
    assert "绝不可读" not in json.dumps(result, ensure_ascii=False)
    assert list(store.db.iterdump()) == before


def test_actual_cli_uses_readonly_store_and_same_diagnostic(store):
    project, _, _ = legacy_candidate(store, "codex", "0.162-native-mcp")
    before = list(store.db.iterdump())
    process = subprocess.run(
        [
            str(Path(sys.executable).with_name("rg")),
            "--data-dir",
            str(store.root),
            "source-audit",
            "--project",
            project,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["items"] == query(store, {"project": project})["items"]
    assert result["policy"]["slim"] == "5"
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("fault", ["bounds", "digest", "utf8"])
def test_invalid_saved_quote_is_not_reported_as_eligible(store, fault):
    project = store.project("合成损坏引用", [])
    entity, claim = model_record(store, project)
    event, _, raw = evidence(store, project, claim, "公开中文")
    new = store.db.execute(
        "INSERT INTO claims(claim_type,entity_id,payload,basis,actor,recorded_at) "
        "VALUES ('entity_version',?,'{}','model_inference','model:合成','2026-01-01')",
        (entity,),
    ).lastrowid
    if fault == "bounds":
        left, right, sha = 0, len(raw) + 1, "0" * 64
    elif fault == "digest":
        left, right, sha = 0, 1, "0" * 64
    else:
        from rg.store.objects import digest

        left = raw.index("公".encode()) + 1
        right, sha = left + 1, digest(raw[left : left + 1])
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,?,?,?)",
        (event, left, right, sha),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (new, span))
    result = query(store, {"project": project})
    items = {row["claim_id"]: row for row in result["items"]}
    assert items[claim]["status"] == "not_excluded"
    assert items[new]["status"] == "unavailable"
    assert items[new]["input_exclude_reason"] == "source_unavailable"


def test_duplicate_link_is_excluded_without_opening_mirror_object(store):
    project = store.project("合成重复引用", [])
    _, claim = model_record(store, project)
    one, _, _ = evidence(store, project, claim, "公开合成原引用")
    two, _, _ = evidence(store, project, claim, "公开合成副本")
    store.db.execute("INSERT INTO dedupe_links VALUES (?,?,'synthetic')", (two, one))
    with patch.object(store, "raw", wraps=store.raw) as raw:
        result = query(store, {"project": project})
    assert [call.args[0] for call in raw.call_args_list] == [one]
    assert result["items"][1]["input_exclude_reason"] == "duplicate_event"


@pytest.mark.parametrize(
    "options",
    [{"limit": True}, {"limit": 101}, {"offset": -1}, {"expected_audit": "bad"}, {"unknown": 1}],
)
def test_invalid_audit_never_reads_objects(store, options):
    project = store.project("合成无效参数", [])
    with patch.object(store, "raw", side_effect=AssertionError("参数检查前不能开对象")):
        with pytest.raises(ValueError):
            query(store, {"project": project} | options)
        with pytest.raises(NotFound):
            query(store, {"project": "absent"})
