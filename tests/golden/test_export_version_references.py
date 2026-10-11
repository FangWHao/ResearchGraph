from __future__ import annotations

import pytest

from rg.export.privacy import Privacy

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
