from __future__ import annotations

import pytest

from rg.api.graph import impact
from rg.query.impact import query
from rg.store.database import ConflictError
from tests.golden.test_exports import unchanged
from tests.golden.test_read_tools import SCOPE, T2, T3, append, original, review
from tests.golden.test_semantic_graph import join, relation
from tests.l1_graph_browser import T2 as L1_CUTOFF
from tests.l1_graph_browser import seed_l1_graph


def scene(store):
    project = store.project("公开影响项目", [])
    seeds = []
    for kind in ("finding", "finding", "question"):
        entity, claim = original(store, project, kind=kind)
        review(store, claim, "confirm", T2)
        seeds.append(entity)
    return project, seeds


def confirmed(store, claim):
    review(store, claim, "confirm", T2)
    return claim


def test_confirmed_downstream_cycles_literal_same_topic_and_no_state_mutation(store):
    project, (a, b, c) = scene(store)
    first = confirmed(store, relation(store, a, b))
    confirmed(store, relation(store, b, a, "challenges"))
    confirmed(store, relation(store, b, c, "same_topic"))
    before = unchanged(store)
    result = query(store, project, {"entity_id": a})
    assert [item["entity_id"] for item in result["items"]] == [b]
    assert result["items"][0]["impact_basis"] == "confirmed_relation"
    assert result["traversal_complete"] and not result["state_changes"]
    paths = query(store, project, {"entity_id": a, "collection": "paths"})
    assert first in {path["claim_id"] for path in paths["items"]}
    assert unchanged(store) == before


def test_candidate_path_remains_potential_and_dismissed_edges_do_not_propagate(store):
    project, (a, b, c) = scene(store)
    first = relation(store, a, b)
    confirmed(store, relation(store, b, c, "supports"))
    assert not query(store, project, {"entity_id": a})["items"]
    result = query(store, project, {"entity_id": a, "include_candidates": True})
    assert {item["entity_id"] for item in result["items"]} == {b, c}
    assert all(item["impact_basis"] == "potential_dependency" for item in result["items"])
    review(store, first, "dismiss", T3)
    assert not query(store, project, {"entity_id": a, "include_candidates": True})["items"]


@pytest.mark.parametrize("semantics", ["all_required", "compare_then_select", "evidence_synthesis"])
def test_join_preserves_ports_and_unselected_comparison_does_not_propagate(store, semantics):
    project, (a, b, _) = scene(store)
    target, claim = original(store, project, kind="join")
    confirmed(store, claim)
    ports = confirmed(store, join(store, target, a, b, semantics))
    values = {"entity_id": b}
    result = query(store, project, values)
    if semantics == "compare_then_select":
        assert not result["items"]
        return
    assert [item["entity_id"] for item in result["items"]] == [target]
    paths = query(store, project, values | {"collection": "paths"})["items"]
    assert paths[0]["claim_id"] == ports and paths[0]["port"] == "C"
    assert paths[0]["semantics"] == semantics


def test_scope_history_revision_and_depth_are_explicit(store):
    project, (a, b, c) = scene(store)
    edge = relation(store, a, b)
    review(store, edge, "confirm", T3)
    confirmed(store, relation(store, b, c))
    assert not query(store, project, {"entity_id": a, "known_until": T2})["items"]
    limited = query(store, project, {"entity_id": a, "max_depth": 1})
    assert [item["entity_id"] for item in limited["items"]] == [b]
    assert not limited["traversal_complete"]
    append(store, a, "entity_version", {"label": "其它范围"}, scope={"data": "v2"})
    with pytest.raises(ValueError, match="多个范围"):
        query(store, project, {"entity_id": a})
    assert query(store, project, {"entity_id": a, "scope": SCOPE})["affected_total"] == 2
    with pytest.raises(ConflictError):
        query(store, project, {"entity_id": a, "scope": SCOPE, "expected_revision": 0})
    other = store.project("其它项目", [])
    with pytest.raises(ValueError, match="不可见"):
        query(store, other, {"entity_id": a})


def test_reported_file_io_is_opt_in_potential_and_pages_have_same_read_conditions(store):
    project = seed_l1_graph(store)
    version = store.db.execute(
        "SELECT version_id FROM artifact_versions ORDER BY rowid"
    ).fetchone()[0]
    values = {"version_id": version, "known_until": L1_CUTOFF, "include_reported": True, "limit": 1}
    before = unchanged(store)
    ordinary = query(store, project, {"version_id": version})
    assert ordinary["affected_total"] == 0
    result = query(store, project, values)
    assert result["affected_total"] >= 1
    assert result["items"][0]["impact_basis"] == "potential_dependency"
    assert not result["state_changes"] and result["actual_io_completeness"] == "unknown"
    assert unchanged(store) == before


def test_http_parameters_reject_truthy_strings_and_unknown_fields(store):
    project, (a, _, _) = scene(store)
    assert impact(store, {"project": project, "entity_id": a, "max_depth": "2"})["max_depth"] == 2
    for extra in ({"include_candidates": "yes"}, {"max_depth": "1e3"}, {"unknown": "1"}):
        with pytest.raises(ValueError):
            impact(store, {"project": project, "entity_id": a, **extra})
