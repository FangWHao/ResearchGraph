from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from contextlib import closing

import httpx
import pytest

from rg.api.server import LocalServer
from rg.export.package import archive
from rg.export.privacy import Privacy
from rg.query.graph import node_id, query
from rg.store.database import ConflictError, Store
from tests.golden.test_exports import evidence, unchanged, unpack
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, T4, append, decision, original, review
from tests.graph_browser import seed_graph


def values(collection="nodes", **kwargs):
    return {"collection": collection, "known_until": T4, "occurred_until": T4} | kwargs


def relation(store, source, target, kind="supports", **kwargs):
    return append(
        store,
        source,
        "relation",
        {
            "source": source,
            "target": target,
            "relation": kind,
        },
        **kwargs,
    )


def join(store, owner, first, second, semantics="all_required", **kwargs):
    return append(
        store,
        owner,
        "join_ports",
        {
            "target": owner,
            "semantics": semantics,
            "inputs": [{"port": "B", "ref": first}, {"port": "C", "ref": second}],
            "selected": first if semantics == "compare_then_select" else None,
        },
        **kwargs,
    )


def test_complete_graph_preserves_cycles_versions_scopes_and_never_chooses_current_content(store):
    project = store.project("合成完整语义", [])
    first, version = original(store, project, kind="finding")
    second, _ = original(store, project, kind="finding")
    duplicate = append(store, first, "entity_version", {"label": "同范围新内容"})
    other = append(store, first, "entity_version", {"label": "别的范围"}, scope={"data": "v2"})
    one = relation(store, first, second)
    two = relation(store, second, first, "challenges")
    nodes = query(store, project, values())
    assert nodes["counts"]["nodes"] == 3
    first_node = next(n for n in nodes["items"] if n["node_id"] == node_id(first, SCOPE))
    assert first_node["active_claim_ids"] == [version, duplicate]
    assert first_node["multiple_active_versions"]
    assert first_node["selected_content_claim_id"] is None
    assert any(n["active_claim_ids"] == [other] for n in nodes["items"])
    edges = query(store, project, values("edges"))["items"]
    assert [e["claim_id"] for e in edges] == [one, two]
    assert all(e["resolved"] and e["basis"] == "manual" for e in edges)
    assert edges[0]["source"]["node_id"] == edges[1]["target"]["node_id"]
    assert nodes["semantic_graph_complete"] and not nodes["projection"]
    assert not nodes["same_topic_propagates"] and not nodes["l1_dependencies_complete"]


def test_versions_review_replacement_and_four_state_axes_keep_historical_truth(store):
    project = store.project("合成替换历史", [])
    entity, original_id = original(store, project, kind="finding")
    review(store, original_id, "confirm", T2)
    replacement = append(
        store,
        entity,
        "entity_version",
        {"label": "人工更正"},
        state="confirmed",
        recorded=T3,
        replaces=original_id,
    )
    accepted = decision(store, entity, state="confirmed", occurred=T1, recorded=T1)
    rejected = decision(store, entity, "rejected", occurred=T3, recorded=T3)
    refuted = append(
        store,
        entity,
        "evidence_event",
        {"target": entity, "state": "refuted"},
        state="confirmed",
        occurred=T1,
        recorded=T1,
    )
    before = query(store, project, values(known_until=T2))["items"][0]
    assert before["active_claim_ids"] == [original_id]
    assert before["versions"][0]["effective_state"] == "confirmed"
    after = query(store, project, values())["items"][0]
    assert after["active_claim_ids"] == [replacement]
    assert after["versions"][0]["replaced"]
    assert after["states"]["adoption"] == {"state": "accepted", "claim_ids": [accepted]}
    assert after["states"]["evidence_state"] == {"state": "refuted", "claim_ids": [refuted]}
    assert (
        next(
            s
            for s in query(store, project, values("state_events"))["items"]
            if s["claim_id"] == rejected
        )["effective_state"]
        == "candidate"
    )
    assert "execution" not in after["states"]  # L2 不能补造运行事实。


@pytest.mark.parametrize("semantics", ["all_required", "compare_then_select", "evidence_synthesis"])
def test_join_semantics_named_ports_and_unselected_comparison_are_explicit(store, semantics):
    project = store.project("合成共同输入", [])
    first, _ = original(store, project)
    second, _ = original(store, project)
    owner, _ = original(store, project, kind="join")
    identity = join(store, owner, first, second, semantics)
    result = query(store, project, values("joins"))["items"][0]
    assert result["claim_id"] == identity and result["valid_semantics"] and result["resolved"]
    assert [i["port"] for i in result["inputs"]] == ["B", "C"]
    assert all(i["required"] == (semantics == "all_required") for i in result["inputs"])
    assert not result["statistical_independence_asserted"]
    if semantics == "compare_then_select":
        assert result["inputs"][0]["selected"] and result["inputs"][0]["role"] == "input"
        assert not result["inputs"][1]["selected"] and result["inputs"][1]["role"] == "compared"
    else:
        assert result["selected"] is None
    assert query(store, project, values("edges"))["items"] == []  # 不捏造两条平边。


def test_multiple_join_claims_are_retained_and_invalid_existing_semantics_are_flagged(store):
    project = store.project("合成汇合歧义", [])
    first, _ = original(store, project)
    second, _ = original(store, project)
    owner, _ = original(store, project, kind="join")
    join(store, owner, first, second, "all_required")
    join(store, owner, first, second, "evidence_synthesis")
    broken = append(
        store,
        owner,
        "join_ports",
        {
            "target": owner,
            "semantics": "compare_then_select",
            "inputs": [{"port": "same", "ref": first}, {"port": "same", "ref": second}],
            "selected": "not-an-input",
        },
    )
    result = query(store, project, values("joins"))
    assert result["total"] == 3
    assert not next(j for j in result["items"] if j["claim_id"] == broken)["valid_semantics"]


def test_scope_does_not_fallback_to_another_version_and_foreign_metadata_is_not_exposed(store):
    project = store.project("合成范围隔离", [])
    foreign = store.project("绝不可读取的合成项目", [])
    one, _ = original(store, project)
    two, _ = original(store, project, scope={"data": "v2"})
    other, _ = original(store, foreign)
    absent = relation(store, one, two)
    forbidden = relation(store, one, other)
    response = query(store, project, values("edges"))
    assert all(not item["resolved"] for item in response["items"])
    missing = query(store, project, values("unresolved"))["items"]
    assert {item["claim_id"] for item in missing} == {absent, forbidden}
    assert all(item["reason"] == "same_scope_visible_version_missing" for item in missing)
    assert foreign not in json.dumps(response, ensure_ascii=False)
    scoped = query(store, project, values(scope=SCOPE))
    assert len(scoped["items"]) == 1 and scoped["items"][0]["entity_id"] == one


@pytest.mark.parametrize("scope", [None, {"step": "?"}, {"data": "unknown"}])
def test_unknown_scope_stays_unknown_instead_of_current_adoption(store, scope):
    project = store.project("合成未知范围", [])
    entity, _ = original(store, project, scope=scope)
    decision(store, entity, state="confirmed", scope=scope)
    node = query(store, project, values(scope=scope))["items"][0]
    assert node["scope"] == scope and not node["scope_known"]
    assert node["states"]["adoption"]["state"] == "unknown_scope"


def test_two_cutoffs_also_filter_sources_and_unknown_recorded_time(store):
    project = store.project("合成双时间图", [])
    one, _ = original(store, project)
    two, _ = original(store, project)
    early = relation(store, one, two, occurred=T1, recorded=T1)
    evidence(store, project, early, "晚到原话不能进入当时已知", recorded=T3)
    relation(store, one, two, recorded=T3)
    relation(store, one, two, occurred=T3)
    relation(store, one, two, recorded="unknown")
    response = query(store, project, values("edges", known_until=T2, occurred_until=T2))
    assert response["total"] == 1 and response["items"][0]["claim_id"] == early
    assert response["items"][0]["evidence"] == []
    assert response["unknown_recorded_time_excluded"] == 1


def test_all_claims_and_all_reference_pages_are_available_without_2000_projection_cap(store):
    project = store.project("合成全图分页", [])
    entity, first = original(store, project)
    for i in range(2004):
        append(store, entity, "entity_version", {"label": f"保留版本{i}"})
    for i in range(105):
        evidence(store, project, first, f"保留证据{i}")
    result = query(store, project, values())
    assert result["claims_total"] == 2005
    assert len(result["items"][0]["versions"]) == 2005
    assert len(result["items"][0]["versions"][0]["evidence"]) == 105
    for _ in range(104):
        relation(store, entity, entity)
    first_page = query(store, project, values("edges", limit=100))
    second_page = query(
        store,
        project,
        values("edges", limit=100, offset=100, expected_revision=first_page["revision"]),
    )
    assert len(first_page["items"]) == 100 and len(second_page["items"]) == 4
    assert first_page["next_offset"] == 100 and second_page["next_offset"] is None


def test_same_topic_and_manual_merge_never_erase_or_confirm_entities(store):
    project = store.project("合成主题合并", [])
    one, _ = original(store, project)
    two, _ = original(store, project)
    relation(store, one, two, "same_topic")
    merge = append(store, one, "merge", {"source": one, "target": two}, state="confirmed")
    assert query(store, project, values("edges"))["items"][0]["retrieval_only"]
    assert query(store, project, values("merges"))["items"][0]["claim_id"] == merge
    assert len(query(store, project, values())["items"]) == 2
    assert all(
        v["effective_state"] == "candidate"
        for n in query(store, project, values())["items"]
        for v in n["versions"]
    )


def test_invalid_existing_relation_direction_is_preserved_and_explicitly_flagged(store):
    project = store.project("合成旧方向问题", [])
    approach, _ = original(store, project)
    finding, _ = original(store, project, kind="finding")
    bad = relation(store, approach, finding, state="confirmed")
    good = relation(store, finding, finding, state="confirmed")
    response = query(store, project, values("edges"))["items"]
    assert [e["claim_id"] for e in response] == [bad, good]
    assert not response[0]["valid_direction"] and response[1]["valid_direction"]


@pytest.mark.parametrize(
    "bad",
    [
        {"collection": "layout"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"expected_revision": True},
        {"known_until": "2026-01-01"},
        {"unknown": "field"},
    ],
)
def test_invalid_query_is_rejected_without_writes(store, bad):
    project = store.project("合成查询校验", [])
    before = unchanged(store)
    with pytest.raises(ValueError):
        query(store, project, values() | bad)
    assert unchanged(store) == before


def test_revision_conflict_and_offline_readonly_never_read_raw_or_call_model(store, monkeypatch):
    project = store.project("合成只读图", [])
    entity, first = original(store, project)
    evidence(store, project, first, "只读定位而非打开原文")
    revision = store.revision()
    append(store, entity, "entity_version", {"label": "新版本"})
    with pytest.raises(ConflictError):
        query(store, project, values(expected_revision=revision))
    before = unchanged(store)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("不允许联网"))
    monkeypatch.setattr(Store, "raw", lambda *a, **k: pytest.fail("不应读取完整原文"))
    with closing(Store(store.root, readonly=True)) as readonly:
        result = query(readonly, project, values())
        assert result["counts"]["nodes"] == 1
    assert unchanged(store) == before


def test_export_uses_the_same_complete_semantic_snapshot_and_masks_scopes_without_breaking_ids(
    store,
):
    project = store.project("合成语义导出", [])
    scope = {"data": "ZY12345", "step": "合成"}
    one, _ = original(store, project, scope=scope)
    two, _ = original(store, project, kind="finding", scope=scope)
    relation(store, one, two, scope=scope)
    conditions = {"known_until": T4, "occurred_until": T4, "scope": scope}
    _, data = archive(store, {"project_id": project, "redact_patterns": [r"ZY\d+"]} | conditions)
    exported = unpack(data)["graph.json"]["semantic"]
    assert exported["counts"]["nodes"] == 2
    assert exported["semantic_graph_complete"] and not exported["l1_dependencies_complete"]
    assert exported["nodes"][0]["scope"] != scope
    assert exported["edges"][0]["source"]["node_id"] == node_id(one, scope)
    direct = query(store, project, values("edges", scope=scope))["items"][0]
    assert direct["source"]["node_id"] == exported["edges"][0]["source"]["node_id"]
    assert "ZY12345" not in json.dumps(exported, ensure_ascii=False)


def test_generated_uuid_phone_like_digits_do_not_break_structure_or_bypass_clinical_rules():
    identity = "1aaa3ee7-1ca2-45ca-b0a4-14173004339c"
    result = Privacy().walk({"project_id": identity, "label": "号码 14173004339"})
    assert result["project_id"] == identity
    assert "14173004339" not in result["label"]
    with pytest.raises(ValueError, match="UUID"):
        Privacy([r"14173004339"]).walk({"project_id": identity})
    with pytest.raises(ValueError, match="结构标识符"):
        Privacy().walk({"patient_id": identity})


def test_cli_entry_and_real_authenticated_http_share_the_complete_graph(store, tmp_path):
    seed_graph(store)
    project = store.db.execute(
        "SELECT project_id FROM projects WHERE name='验收语义图项目'"
    ).fetchone()[0]
    command = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "graph",
            "--project",
            project,
            "--collection",
            "joins",
        ],
        capture_output=True,
        text=True,
    )
    assert command.returncode == 0, command.stderr
    assert len(json.loads(command.stdout)["items"]) == 3
    (tmp_path / "index.html").write_text("合成静态页")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = unchanged(store)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            params = {"project": project, "collection": "joins"}
            assert client.get("/api/semantic-graph", params=params).status_code == 401
            client.headers["Authorization"] = "Bearer synthetic-token"
            response = client.get("/api/semantic-graph", params=params)
            assert response.status_code == 200
            assert response.json()["items"] == json.loads(command.stdout)["items"]
            assert (
                client.get(
                    "/api/semantic-graph", params=params | {"expected_revision": 0}
                ).status_code
                == 409
            )
            assert (
                client.get(
                    "/api/semantic-graph", params=params | {"scope": '{"data":"a","data":"b"}'}
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/semantic-graph", params=params, headers={"Host": "unknown.test"}
                ).status_code
                == 403
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert unchanged(store) == before
