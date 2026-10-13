from __future__ import annotations

import pytest

from rg.derive.path_checks import check_batch
from rg.export.package import archive
from rg.export.privacy import Privacy
from tests.golden.test_exports import unchanged, unpack
from tests.golden.test_l1_path_repair import LATER, legacy
from tests.golden.test_read_tools import original
from tests.multiedit import seed_multiedit_store

# 公开合成摘要，刻意含手机号形状以稳定复现导出引用失配。
HASH = "a" * 24 + "17250477826" + "b" * 29
FIELDS = ("before_version", "after_version", "reported_after_version")


@pytest.mark.parametrize("prefix", ["", "l1:", "physical:"])
def test_version_reference_ids_remain_identical_across_exported_edit_record(prefix):
    value = prefix + HASH
    record = {field: value for field in FIELDS} | {"content": value}
    exported = Privacy().walk({"nodes": [{"kind": "edit_record", "record": record}]})
    result = exported["nodes"][0]["record"]
    assert all(result[field] == value for field in FIELDS)
    assert result["content"] != value


def test_version_field_is_not_a_general_exemption_for_clinical_or_free_text():
    raw = {field: "合成编号 17250477826" for field in FIELDS}
    result = Privacy().walk(raw)
    assert all(result[field] != raw[field] for field in FIELDS)


def test_explicit_custom_rule_cannot_silently_break_version_references():
    with pytest.raises(ValueError):
        Privacy(["17250477826"]).walk({"before_version": HASH})


def test_generated_path_check_digest_survives_export_but_user_fields_still_mask(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.derive.path_checks.fingerprint", lambda *_: HASH)
    project, _ = seed_multiedit_store(store, tmp_path)
    original(store, project, scope={"input_signature": HASH})
    before = unchanged(store)
    _, data = archive(store, {"project_id": project})
    exported = unpack(data)
    edits = [
        row["record"] for row in exported["graph.json"]["l1"]["nodes"]
        if row["kind"] == "edit_record"
    ]
    assert len(edits) == 4
    assert all(row["path_validation"]["input_signature"] == HASH for row in edits)
    assert exported["claims.json"][0]["scope"]["input_signature"] != HASH
    assert Privacy().walk({"input_signature": HASH})["input_signature"] != HASH
    assert unchanged(store) == before


def test_custom_rule_matching_generated_path_digest_rejects_export_without_changes(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.derive.path_checks.fingerprint", lambda *_: HASH)
    project, _ = seed_multiedit_store(store, tmp_path)
    before = unchanged(store)
    with pytest.raises(ValueError, match="摘要"):
        archive(store, {"project_id": project, "redact_patterns": ["17250477826"]})
    assert unchanged(store) == before


def test_native_path_check_digest_is_identical_in_run_and_graph_exports(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr("rg.derive.path_checks.fingerprint", lambda *_: HASH)
    project, _, _, _ = legacy(store, tmp_path, monkeypatch)
    assert check_batch(store)["l1_path_checked"] == 2
    before = unchanged(store)
    _, data = archive(store, {
        "project_id": project,
        "known_until": LATER,
        "occurred_until": "2099-01-01T00:00:00+00:00",
    })
    exported = unpack(data)
    native = exported["runs.json"][0]["native"]
    graphed = next(
        node["record"] for node in exported["graph.json"]["l1"]["nodes"]
        if node["kind"] == "native_run"
    )
    assert native["path_validation"]["input_signature"] == HASH
    assert graphed["path_validation"] == native["path_validation"]
    assert native["state"] == graphed["state"] == "exited"
    assert native["exit_code"] == graphed["exit_code"] == 4
    assert unchanged(store) == before
