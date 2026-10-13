"""在固定双时间与范围内查询下游核对项，不修改科研状态。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from rg.query.graph import SemanticGraph, node_id
from rg.query.l1 import FileRunGraph
from rg.query.reader import Reader, page
from rg.store.database import ConflictError, Store, dumps
from rg.store.scopes import known_scope

RELATIONS = {"supports", "challenges", "selects"}


def _eligible(proof: dict, candidates: bool) -> bool:
    return bool(
        proof.get("active")
        and proof.get("input_eligible")
        and known_scope(proof.get("scope"))
        and (
            proof.get("effective_state") == "confirmed"
            or candidates
            and proof.get("effective_state") == "candidate"
        )
    )


def query(store: Store, project: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "entity_id",
        "version_id",
        "include_candidates",
        "include_reported",
        "max_depth",
        "limit",
        "offset",
        "scope",
        "occurred_until",
        "known_until",
        "expected_revision",
        "collection",
    }
    if set(values) - allowed:
        raise ValueError("未知影响查询参数")
    collection = values.get("collection", "affected")
    if collection not in {"affected", "paths"}:
        raise ValueError("影响查询集合无效")
    identities = [values.get("entity_id"), values.get("version_id")]
    if sum(value is not None for value in identities) != 1:
        raise ValueError("影响查询需且只能指定一个对象或文件版本")
    if any(value is not None and (not isinstance(value, str) or not value) for value in identities):
        raise ValueError("对象或版本编号需为非空字符串")
    candidates, reported = (
        values.get("include_candidates", False),
        values.get("include_reported", False),
    )
    if type(candidates) is not bool or type(reported) is not bool:
        raise ValueError("候选与报告开关需为布尔值")
    depth_limit = values.get("max_depth", 32)
    if type(depth_limit) is not int or not 1 <= depth_limit <= 64:
        raise ValueError("影响深度需为 1 到 64")
    expected = values.get("expected_revision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision 需为非负整数")
    with store.snapshot():
        reader = Reader(store, project, values)
        if expected is not None and expected != store.revision():
            raise ConflictError("研究图已变化，请固定双时间重新查询")
        semantic = SemanticGraph(reader)
        nodes = {
            key: {
                "node_id": key,
                "layer": "L2",
                "kind": node["kind"],
                "entity_id": node["entity_id"],
                "scope": node["scope"],
                "content_claim_ids": [
                    version["claim_id"]
                    for version in node["versions"]
                    if _eligible(version, candidates)
                ],
                "labels": sorted(
                    {
                        str(version["payload"].get("label", node["entity_id"]))
                        for version in node["versions"]
                        if _eligible(version, candidates)
                    }
                ),
            }
            for key, node in semantic.nodes.items()
        }
        eligible = {
            key
            for key, node in semantic.nodes.items()
            if any(_eligible(version, candidates) for version in node["versions"])
        }
        arcs: list[dict] = []
        excluded: dict[str, int] = defaultdict(int)

        def add(edge: dict, source: str | None, target: str | None, potential: bool, **extra):
            if source not in nodes or target not in nodes:
                excluded["unresolved_endpoint"] += 1
                return
            arcs.append(
                {
                    "source": source,
                    "target": target,
                    "potential": potential,
                    "relation": edge.get("relation", "join_input"),
                    "claim_id": edge.get("claim_id"),
                    "edge_id": edge.get("edge_id"),
                    "basis": edge.get("basis"),
                    "scope": edge.get("scope"),
                    "evidence": edge.get("evidence", []),
                    "evidence_event_ids": edge.get("evidence_event_ids", []),
                    **extra,
                }
            )

        for edge in semantic.collections["edges"]:
            if edge["relation"] not in RELATIONS:
                excluded["non_dependency_relation"] += 1
                continue
            source, target = edge["source"]["node_id"], edge["target"]["node_id"]
            if not edge["valid_direction"] or not _eligible(edge, candidates):
                excluded["unqualified_relation"] += 1
                continue
            if source not in eligible or target not in eligible:
                excluded["unqualified_endpoint"] += 1
                continue
            potential = edge["effective_state"] != "confirmed" or any(
                not any(_eligible(v, False) for v in semantic.nodes[key]["versions"])
                for key in (source, target)
            )
            add(edge, source, target, potential)
        for join in semantic.collections["joins"]:
            target = join["target"]["node_id"]
            if (
                not join["valid_semantics"]
                or not _eligible(join, candidates)
                or target not in eligible
            ):
                excluded["unqualified_join"] += 1
                continue
            for entry in join["inputs"]:
                if join["semantics"] == "compare_then_select" and not entry["selected"]:
                    excluded["unselected_comparison_input"] += 1
                    continue
                source = entry["endpoint"]["node_id"]
                if source not in eligible:
                    excluded["unqualified_endpoint"] += 1
                    continue
                potential = join["effective_state"] != "confirmed" or any(
                    not any(_eligible(v, False) for v in semantic.nodes[key]["versions"])
                    for key in (source, target)
                )
                add(
                    join, source, target, potential, semantics=join["semantics"], port=entry["port"]
                )
        l1 = None
        if reported or values.get("version_id") is not None:
            l1 = FileRunGraph(reader)
            for key, node in l1.nodes.items():
                if node["kind"] == "attempt":
                    continue
                record = node["record"]
                nodes[key] = {
                    "node_id": key,
                    "layer": "L1",
                    "kind": node["kind"],
                    "scope": node["scope"],
                    "version_id": record.get("version_id"),
                    "run_id": record.get("run_id"),
                    "request_id": record.get("request_id"),
                }
            for edge in l1.collections["edges"]:
                if edge["relation"] == "describes_attempt":
                    if reported and edge["resolved"] and known_scope(edge["scope"]):
                        attempt = l1.nodes[edge["target"]["node_id"]]["record"]
                        target = node_id(attempt["entity_id"], attempt["scope"])
                        if target in eligible:
                            add(edge, edge["source"]["node_id"], target, True)
                    continue
                if edge["relation"] not in {"consumes", "produces"}:
                    continue
                if not reported:
                    excluded["reported_io_not_enabled"] += 1
                elif edge.get("association_gap") or not edge["resolved"]:
                    excluded["unresolved_reported_io"] += 1
                else:
                    add(
                        edge,
                        edge["source"]["node_id"],
                        edge["target"]["node_id"],
                        True,
                        association_state=edge["association_state"],
                        role=edge.get("role"),
                    )
        seeds = [
            key
            for key, node in nodes.items()
            if (
                values.get("entity_id") is not None and node.get("entity_id") == values["entity_id"]
            )
            or (
                values.get("version_id") is not None
                and node.get("version_id") == values["version_id"]
            )
        ]
        if not seeds:
            raise ValueError("指定对象或版本在本项目及读取条件下不可见")
        if len(seeds) != 1:
            raise ValueError("对象有多个范围，请用 scope 指定一个完整范围")
        seed = seeds[0]
        adjacency: dict[str, list[dict]] = defaultdict(list)
        for arc in arcs:
            adjacency[arc["source"]].append(arc)
        reached: dict[tuple[str, bool], int] = {(seed, False): 0}
        queue = deque([(seed, False)])
        used: dict[str, dict] = {}
        limited = False
        while queue:
            source, potential = queue.popleft()
            depth = reached[source, potential]
            for arc in adjacency[source]:
                target, weaker = arc["target"], potential or arc["potential"]
                state = target, weaker
                if state in reached:
                    used[dumps(arc)] = arc
                    continue
                if depth >= depth_limit or len(reached) >= 10000:
                    limited = True
                    continue
                reached[state] = depth + 1
                queue.append(state)
                used[dumps(arc)] = arc
        items = []
        for key in sorted({key for key, _ in reached} - {seed}):
            proven = (key, False) in reached
            depth = reached[key, False] if proven else reached[key, True]
            items.append(
                nodes[key]
                | {
                    "depth": depth,
                    "impact_basis": "confirmed_relation" if proven else "potential_dependency",
                    "action": "review_dependency",
                }
            )
        items.sort(key=lambda item: (item["depth"], item["node_id"]))
        paths = list(used.values())
        return (
            reader.metadata()
            | page(items if collection == "affected" else paths, values)
            | {
                "seed": nodes[seed],
                "collection": collection,
                "affected_total": len(items),
                "paths_total": len(paths),
                "excluded": dict(excluded),
                "traversal_complete": not limited,
                "max_depth": depth_limit,
                "state_changes": False,
                "actual_io_completeness": "unknown",
                "include_candidates": candidates,
                "include_reported": reported,
                "semantics": "downstream_review_only",
                "l1_dependencies_complete": False,
            }
        )
