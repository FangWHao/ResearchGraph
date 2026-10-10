from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rg.extract.validate import InvalidClaim, persist
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup
from tests.golden.test_ingestion import record


def test_scope_and_priority_do_not_promote_unrelated_or_other_scope_claims(
    store: Store,
    tmp_path: Path,
):
    project, output, run = setup(store, tmp_path)
    definitions = []
    for index, (label, content, scope) in enumerate(
        [
            ("直接对象", "方案", "data_v2"),
            ("临床队列方案", "方案", "data_v2"),
            ("路径方案", "文件 /work/analysis/x.py", "data_v2"),
            ("刚产生的方案", "方案", "data_v2"),
            ("临床队列异范围", "方案", "data_v3"),
        ]
    ):
        definition = deepcopy(output["claims"][0])
        definition.update(
            temp_id=f"new:{index}", label=label, content=content, scope={"dataset_version": scope}
        )
        definitions.append(definition)
    output["claims"] = definitions
    ids = persist(store, output, project, run, set(), {1}, "test-segment")
    direct = store.db.execute(
        "SELECT entity_id FROM claims WHERE claim_id=?",
        (ids[0],),
    ).fetchone()[0]
    path = tmp_path / "input.jsonl"
    with path.open("a") as stream:
        stream.write(dumps(record("下一回合", "u2")) + "\n")
    scan_file(store, path, "claude", project)
    selected = working_set(
        store,
        project,
        f"[{direct}] 临床队列对照",
        FakeProvider(),
        before_event=2,
        scope={"dataset_version": "data_v2"},
        paths={"/work/analysis/x.py"},
    )
    assert [item["label"] for item in selected] == [
        "直接对象",
        "临床队列方案",
        "路径方案",
        "刚产生的方案",
    ]
    assert all(item["scope"] == {"dataset_version": "data_v2"} for item in selected)
    assert working_set(store, project, "无关内容", FakeProvider()) == []


def test_lookup_matches_literal_labels_without_fts_operator_injection(
    store: Store,
    tmp_path: Path,
):
    project, output, run = setup(store, tmp_path)
    persist(store, output, project, run, set(), {1}, "test-segment")
    assert working_set(store, project, '" OR * NOT 未出现', FakeProvider()) == []
    selected = working_set(store, project, "无关内容", FakeProvider(), terms=["方法甲"])
    assert selected[0]["label"] == "方法甲"
    assert FakeProvider().count_text(dumps(selected)) <= 3000


def test_prior_chunk_of_same_event_can_enter_working_set_but_future_cannot(
    store: Store,
    tmp_path: Path,
):
    project, output, run = setup(store, tmp_path)
    persist(store, output, project, run, set(), {1}, "test-segment")
    end = output["claims"][0]["evidence"][0]["byte_end"]
    assert not working_set(
        store, project, "方法甲", FakeProvider(), before_event=1, before_offset=end - 1
    )
    assert (
        working_set(store, project, "无关内容", FakeProvider(), before_event=1, before_offset=end)[
            0
        ]["label"]
        == "方法甲"
    )


def test_explicit_scope_rejects_extra_or_renamed_model_fields(store: Store, tmp_path: Path):
    project, output, run = setup(store, tmp_path)
    scope = {"dataset_version": "data_v2"}
    output["claims"][1]["scope"]["axis"] = "采用状态"
    with pytest.raises(InvalidClaim, match="显式范围"):
        persist(store, output, project, run, set(), {1}, "test-segment", expected_scope=scope)
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
