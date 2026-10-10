"""完整 L2 语义图；布局、版本显示选择和提醒都不改变记录事实。"""

from __future__ import annotations

from typing import Any

from rg.query.reader import Reader, page
from rg.store.database import ConflictError, Store, dumps
from rg.store.objects import digest
from rg.store.scopes import known_scope

COLLECTIONS = ("nodes", "edges", "joins", "merges", "state_events")


def node_id(entity: str, scope: Any) -> str:
    return digest(dumps([entity, scope]).encode())


def active(claim: dict[str, Any]) -> bool:
    return claim["effective_state"] != "dismissed" and not claim["replaced"]


def provenance(reader: Reader, claim: dict[str, Any]) -> dict[str, Any]:
    refs, offset = [], 0
    while True:
        found = reader.references(claim["claim_id"], {"limit": 100, "offset": offset})
        refs.extend(found["items"])
        offset = found["next_offset"]
        if offset is None:
            break
    return {
        key: claim[key]
        for key in (
            "claim_id",
            "claim_state",
            "effective_state",
            "basis",
            "actor",
            "review",
            "scope",
            "occurred_at",
            "recorded_at",
            "occurred_at_utc",
            "recorded_at_utc",
            "occurred_time_unknown",
            "replaces_claim",
            "replacement_ids",
            "replaced",
        )
    } | {
        "citation_id": f"C{claim['claim_id']}",
        "active": active(claim),
        "evidence": refs,
    }


class SemanticGraph:
    def __init__(self, reader: Reader):
        self.reader = reader
        self.claims = reader.scoped(reader.claims())
        self.collections: dict[str, list[dict[str, Any]]] = {k: [] for k in COLLECTIONS}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.unresolved: list[dict[str, Any]] = []
        self._nodes()
        self._relations()

    def _nodes(self) -> None:
        for claim in self.claims:
            if claim["claim_type"] != "entity_version":
                continue
            identity = node_id(claim["entity_id"], claim["scope"])
            node = self.nodes.setdefault(
                identity,
                {
                    "node_id": identity,
                    "entity_id": claim["entity_id"],
                    "kind": claim["kind"],
                    "scope": claim["scope"],
                    "scope_known": known_scope(claim["scope"]),
                    "versions": [],
                    "active_claim_ids": [],
                    "states": self.reader.states(claim["entity_id"], claim["scope"]),
                    "selected_content_claim_id": None,
                },
            )
            node["versions"].append(provenance(self.reader, claim) | {"payload": claim["payload"]})
            if active(claim):
                node["active_claim_ids"].append(claim["claim_id"])
        for node in self.nodes.values():
            node["active"] = bool(node["active_claim_ids"])
            node["multiple_active_versions"] = len(node["active_claim_ids"]) > 1
        self.collections["nodes"] = list(self.nodes.values())

    def endpoint(self, entity: Any, scope: Any, claim: dict[str, Any], role: str) -> dict[str, Any]:
        identity = node_id(entity, scope) if isinstance(entity, str) and entity else None
        node = self.nodes.get(identity or "")
        result = {
            "entity_id": entity,
            "node_id": identity,
            "resolved": node is not None,
            "active": bool(node and node["active"]),
        }
        if node is None:
            self.unresolved.append(
                {
                    "claim_id": claim["claim_id"],
                    "role": role,
                    "entity_id": entity,
                    "scope": scope,
                    "reason": "same_scope_visible_version_missing",
                }
            )
        return result

    def _relations(self) -> None:
        for claim in self.claims:
            kind = claim["claim_type"]
            if kind == "entity_version":
                continue
            payload, scope = claim["payload"], claim["scope"]
            proof = provenance(self.reader, claim)
            if kind in {"relation", "merge"}:
                source = self.endpoint(payload.get("source"), scope, claim, "source")
                target = self.endpoint(payload.get("target"), scope, claim, "target")
                source_kind = self.nodes.get(source["node_id"], {}).get("kind")
                target_kind = self.nodes.get(target["node_id"], {}).get("kind")
                pair = source_kind, target_kind
                relation = payload.get("relation") if kind == "relation" else "merge"
                valid_pairs = {
                    "part_of": {("attempt", "approach"), ("approach", "question")},
                    "supports": {("finding", "question"), ("finding", "finding")},
                    "challenges": {("finding", "question"), ("finding", "finding")},
                    "selects": {("join", k) for k in ("approach", "attempt", "finding")},
                }
                valid = (
                    (pair in valid_pairs[relation])
                    if relation in valid_pairs
                    else (
                        source_kind is not None and source_kind == target_kind
                        if relation in {"supersedes", "merge"}
                        else relation == "same_topic"
                    )
                )
                self.collections["edges" if kind == "relation" else "merges"].append(
                    proof
                    | {
                        "edge_id": f"C{claim['claim_id']}",
                        "source": source,
                        "target": target,
                        "relation": relation,
                        "valid_direction": valid,
                        "resolved": source["resolved"] and target["resolved"],
                        "retrieval_only": payload.get("relation") == "same_topic",
                    }
                )
            elif kind == "join_ports":
                target = self.endpoint(payload.get("target"), scope, claim, "target")
                inputs = [
                    dict(item)
                    | {
                        "endpoint": self.endpoint(
                            item.get("ref"), scope, claim, item.get("port", "")
                        ),
                        "required": payload.get("semantics") == "all_required",
                        "selected": item.get("ref") == payload.get("selected"),
                        "role": "compared"
                        if payload.get("semantics") == "compare_then_select"
                        and item.get("ref") != payload.get("selected")
                        else "input",
                    }
                    for item in payload.get("inputs", [])
                ]
                semantics = payload.get("semantics")
                selected = payload.get("selected")
                valid = semantics in {"all_required", "compare_then_select", "evidence_synthesis"}
                valid = (
                    valid
                    and len(inputs) >= 2
                    and len({i.get("port") for i in inputs}) == len(inputs)
                )
                valid = valid and (
                    (selected in {i.get("ref") for i in inputs})
                    if semantics == "compare_then_select"
                    else selected is None
                )
                owner = self.nodes.get(target["node_id"])
                valid = valid and bool(owner and owner["kind"] == "join")
                self.collections["joins"].append(
                    proof
                    | {
                        "join_id": f"C{claim['claim_id']}",
                        "target": target,
                        "semantics": semantics,
                        "inputs": inputs,
                        "selected": selected,
                        "valid_semantics": valid,
                        "resolved": target["resolved"]
                        and all(i["endpoint"]["resolved"] for i in inputs),
                        "statistical_independence_asserted": False,
                    }
                )
            elif kind in {"decision_event", "evidence_event"}:
                self.collections["state_events"].append(
                    proof
                    | {
                        "claim_type": kind,
                        "payload": payload,
                        "target": self.endpoint(payload.get("target"), scope, claim, "target"),
                    }
                )

    def metadata(self) -> dict[str, Any]:
        return self.reader.metadata() | {
            "layer": "L2",
            "projection": False,
            "semantic_graph_complete": True,
            "claims_total": len(self.claims),
            "counts": {k: len(v) for k, v in self.collections.items()},
            "unresolved_total": len(self.unresolved),
            "same_topic_propagates": False,
            "content_version_selection_is_view_only": True,
            "l1_dependencies_complete": False,
        }

    def query(self, values: dict[str, Any]) -> dict[str, Any]:
        collection = values.get("collection", "nodes")
        if collection not in COLLECTIONS + ("unresolved",):
            raise ValueError("语义图集合无效")
        items = self.unresolved if collection == "unresolved" else self.collections[collection]
        return self.metadata() | page(items, values) | {"collection": collection}

    def snapshot(self) -> dict[str, Any]:
        return self.metadata() | self.collections | {"unresolved": self.unresolved}


def query(store: Store, project: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "collection",
        "limit",
        "offset",
        "scope",
        "occurred_until",
        "known_until",
        "expected_revision",
    }
    if set(values) - allowed:
        raise ValueError("未知语义图查询参数")
    expected = values.get("expected_revision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision 需为非负整数")
    with store.snapshot():
        reader = Reader(store, project, values)
        if expected is not None and expected != store.revision():
            raise ConflictError("图已变化，请固定双时间后重新读取")
        if values.get("collection") == "claims":
            from rg.query.graph_records import records

            return records(reader, values)
        return SemanticGraph(reader).query(values)
