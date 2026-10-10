"""文件、执行、编辑和报告的完整图；报告关系不替代实际运行事实。"""

from __future__ import annotations

from typing import Any

from rg.query.artifacts import snapshot
from rg.query.graph import SemanticGraph
from rg.query.l1_records import artifacts, run_records
from rg.query.reader import Reader, instant, page
from rg.store.database import ConflictError, Store, dumps
from rg.store.objects import digest

COLLECTIONS = ("nodes", "edges", "observations", "evidence", "unresolved")


def node_id(kind: str, identity: Any, scope: Any = None) -> str:
    return digest(dumps(["L1", kind, identity, scope]).encode())


class FileRunGraph:
    def __init__(self, reader: Reader):
        self.reader = reader
        self.collections: dict[str, list[dict[str, Any]]] = {k: [] for k in COLLECTIONS}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.event_ids: set[int] = set()
        self.snapshot_ids: set[int] = set()
        self.runs, events, requested = run_records(reader)
        self.versions, version_events = artifacts(reader, requested)
        self.event_ids.update(events | version_events)
        self._versions()
        self._runs()
        self._edits(requested)
        self._snapshots()
        self._evidence()
        self.collections["nodes"] = list(self.nodes.values())

    def add(self, kind: str, identity: Any, record: dict[str, Any], scope: Any = None) -> str:
        key = node_id(kind, identity, scope)
        self.nodes[key] = {"node_id": key, "kind": kind, "scope": scope, "record": record}
        return key

    def endpoint(self, kind: str, identity: Any, scope: Any = None) -> dict[str, Any]:
        key = node_id(kind, identity, scope) if identity is not None else None
        return {"node_id": key, "kind": kind, "record_id": identity, "resolved": key in self.nodes}

    def edge(
        self,
        identity: Any,
        relation: str,
        source: dict[str, Any],
        target: dict[str, Any],
        proof: dict[str, Any],
    ) -> None:
        resolved = source["resolved"] and target["resolved"]
        value = {
            "edge_id": digest(dumps(["L1", identity]).encode()),
            "relation": relation,
            "source": source,
            "target": target,
            "resolved": resolved,
        } | proof
        self.collections["edges"].append(value)
        if not resolved:
            self.collections["unresolved"].append(
                {
                    "edge_id": value["edge_id"],
                    "source": source,
                    "target": target,
                    "reason": "same_project_visible_record_missing",
                }
            )

    def _versions(self) -> None:
        for value in self.versions:
            record = value["version"] | {"scope_basis": value["scope_basis"]}
            owner = self.add("artifact_version", record["version_id"], record)
            for observed in value["observations"]:
                self.collections["observations"].append(
                    observed | {"observation_kind": "artifact", "owner_node_id": owner}
                )
                for name in ("snapshot", "discovery_snapshot"):
                    captured = observed.get(name)
                    if captured:
                        self.snapshot_ids.add(captured["snapshot_id"])

    @staticmethod
    def proof(record: dict[str, Any], events: list[int], state: str) -> dict[str, Any]:
        return {
            "basis": record.get("basis", "direct_record"),
            "claim_state": record.get("claim_state"),
            "association_state": state,
            "scope": record.get("scope"),
            "occurred_at": record.get("occurred_at"),
            "recorded_at": record.get("recorded_at"),
            "evidence_event_ids": events,
            "actual_io_completeness": "unknown",
        }

    def _runs(self) -> None:
        attempts = {
            (n["entity_id"], dumps(n["scope"])): n
            for n in SemanticGraph(self.reader).collections["nodes"]
            if n["kind"] == "attempt"
        }
        for value in self.runs:
            execution = value["native"]
            if execution:
                record = {k: v for k, v in execution.items() if k != "observations"}
                record["scope_basis"] = value["scope_basis"]
                owner = self.add("native_run", value["run_id"], record)
                for observed in execution["observations"]:
                    self.collections["observations"].append(
                        observed | {"observation_kind": "execution", "owner_node_id": owner}
                    )
            for manifest in value["manifests"]:
                record = {k: v for k, v in manifest.items() if k != "io"}
                owner = self.add("run_manifest", manifest["request_id"], record, manifest["scope"])
                report = self.endpoint("run_manifest", manifest["request_id"], manifest["scope"])
                proof = self.proof(manifest, [manifest["evidence_event_id"]], "reported_only")
                self.edge(
                    [owner, "run"],
                    "describes_run",
                    report,
                    self.endpoint("native_run", manifest["run_id"]),
                    proof,
                )
                for entry in manifest["io"]["items"]:
                    version = self.endpoint("artifact_version", entry["requested_version_id"])
                    output = entry["direction"] == "out"
                    self.edge(
                        [owner, entry["role"], entry["ordinal"]],
                        "produces" if output else "consumes",
                        report if output else version,
                        version if output else report,
                        proof
                        | {
                            "role": entry["role"],
                            "ordinal": entry["ordinal"],
                            "io_id": entry["io_id"],
                            "resolution": entry["resolution"],
                        },
                    )
                if manifest["attempt_id"] is not None:
                    attempt = attempts.get((manifest["attempt_id"], dumps(manifest["scope"])))
                    if attempt:
                        self.add("attempt", manifest["attempt_id"], attempt, manifest["scope"])
                    self.edge(
                        [owner, "attempt"],
                        "describes_attempt",
                        report,
                        self.endpoint("attempt", manifest["attempt_id"], manifest["scope"]),
                        proof,
                    )
                captured = manifest["declared_snapshot"]
                if captured:
                    self.snapshot_ids.add(captured["snapshot_id"])

    def _event(self, identity: int) -> dict[str, Any] | None:
        row = self.reader.store.db.execute(
            "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE r.event_id=? AND s.project_id=?",
            (identity, self.reader.project),
        ).fetchone()
        if row is None or not self.reader.visible(
            row["occurred_at"], row["recorded_at"], ("E", identity)
        ):
            return None
        return dict(row)

    def _edits(self, requested: set[str]) -> None:
        for row in self.reader.store.db.execute(
            "SELECT * FROM edit_records WHERE project_id=? ORDER BY edit_id",
            (self.reader.project,),
        ):
            record = dict(row)
            if self.reader.scope_filter and not (
                record["before_version"] in requested or record["after_version"] in requested
            ):
                continue
            request = self._event(record["request_event_id"])
            result = self._event(record["result_event_id"])
            if (
                request is None
                or result is None
                or not self.reader.visible(
                    record["occurred_at"], record["recorded_at"], ("D", record["edit_id"])
                )
            ):
                continue
            candidates = self.reader.store.db.execute(
                "SELECT event_id,kind,occurred_at,recorded_at FROM raw_events WHERE session_pk=? "
                "AND call_id=? AND alias_of IS NULL AND "
                "((kind IN ('tool_call','file_edit') AND exclude_reason IS NULL) OR "
                "(kind='meta' AND tool_name='apply_patch' "
                "AND exclude_reason='execution_metadata'))",
                (record["session_pk"], record["call_id"]),
            )
            calls = [
                r
                for r in candidates
                if self.reader.visible(r["occurred_at"], r["recorded_at"], ("E", r["event_id"]))
            ]
            native_calls = sum(r["kind"] == "meta" for r in calls)
            ambiguous = native_calls > 1 or len(calls) - native_calls > 1
            record["association_gap"] = "ambiguous_call_id" if ambiguous else None
            record["scope_basis"] = (
                "referenced_version_only" if self.reader.scope_filter else "unassigned"
            )
            owner = self.add("edit_record", record["edit_id"], record)
            edited = self.endpoint("edit_record", record["edit_id"])
            events = [record["request_event_id"], record["result_event_id"]]
            self.event_ids.update(events)
            proof = self.proof(record, events, "tool_reported") | {
                "claim_state": "candidate",
                "association_gap": record["association_gap"],
            }
            for key, relation in (("before_version", "consumes"), ("after_version", "produces")):
                version = self.endpoint("artifact_version", record[key])
                output = relation == "produces"
                self.edge(
                    [owner, key],
                    relation,
                    edited if output else version,
                    version if output else edited,
                    proof | {"role": key},
                )

    def _snapshots(self) -> None:
        observations: dict[str, list[dict[str, Any]]] = {}
        for observed in self.collections["observations"]:
            observations.setdefault(observed["owner_node_id"], []).append(observed)
        if not self.reader.scope_filter:
            self.snapshot_ids.update(
                r[0]
                for r in self.reader.store.db.execute(
                    "SELECT snapshot_id FROM workspace_snapshots WHERE project_id=?",
                    (self.reader.project,),
                )
            )
        for identity in sorted(self.snapshot_ids):
            row = self.reader.store.db.execute(
                "SELECT root_id FROM workspace_snapshots WHERE snapshot_id=? AND project_id=?",
                (identity, self.reader.project),
            ).fetchone()
            captured = snapshot(self.reader, identity, row[0]) if row else None
            if captured:
                self.add("workspace_snapshot", identity, captured | {"scope_basis": "unassigned"})
        for node in list(self.nodes.values()):
            record = node["record"]
            if node["kind"] == "artifact_version":
                for observed in observations.get(node["node_id"], []):
                    for name in ("snapshot", "discovery_snapshot"):
                        captured = observed.get(name)
                        if captured:
                            self.edge(
                                [node["node_id"], observed["observation_id"], name],
                                "captured_in" if name == "snapshot" else "discovered_from",
                                self.endpoint("artifact_version", record["version_id"]),
                                self.endpoint("workspace_snapshot", captured["snapshot_id"]),
                                {
                                    "basis": "direct_record",
                                    "claim_state": None,
                                    "association_state": "saved_snapshot"
                                    if name == "snapshot"
                                    else "discovery_only",
                                    "evidence_event_ids": [],
                                    "observation_id": observed["observation_id"],
                                },
                            )
            elif node["kind"] == "run_manifest" and record["snapshot_id"] is not None:
                self.edge(
                    [node["node_id"], "snapshot"],
                    "declares_snapshot",
                    self.endpoint("run_manifest", record["request_id"], node["scope"]),
                    self.endpoint("workspace_snapshot", record["snapshot_id"]),
                    self.proof(record, [record["evidence_event_id"]], "reported_only"),
                )

    def _evidence(self) -> None:
        for identity in sorted(self.event_ids):
            row = self._event(identity)
            if row:
                self.collections["evidence"].append(
                    {
                        key: row[key]
                        for key in (
                            "event_id",
                            "session_pk",
                            "file_instance_id",
                            "byte_start",
                            "byte_end",
                            "object_sha256",
                            "line_sha256",
                            "occurred_at",
                            "recorded_at",
                            "role",
                            "tool_name",
                            "exclude_reason",
                            "alias_of",
                        )
                    }
                    | {
                        "citation_id": f"E{identity}",
                        "occurred_time_unknown": instant(row["occurred_at"]) is None,
                    }
                )

    def metadata(self) -> dict[str, Any]:
        return self.reader.metadata() | {
            "layer": "L1",
            "projection": False,
            "l1_graph_complete": True,
            "l1_dependencies_complete": False,
            "actual_io_completeness": "unknown",
            "counts": {k: len(v) for k, v in self.collections.items()},
            "node_kinds": {
                k: sum(n["kind"] == k for n in self.nodes.values())
                for k in (
                    "artifact_version",
                    "native_run",
                    "run_manifest",
                    "edit_record",
                    "workspace_snapshot",
                    "attempt",
                )
            },
            "report_is_execution_fact": False,
            "content_version_selection_is_view_only": True,
        }

    def query(self, values: dict[str, Any]) -> dict[str, Any]:
        collection = values.get("collection", "nodes")
        if collection not in COLLECTIONS:
            raise ValueError("L1 图集合无效")
        return (
            self.metadata()
            | page(self.collections[collection], values)
            | {"collection": collection}
        )

    def snapshot(self) -> dict[str, Any]:
        return self.metadata() | self.collections


def query(store: Store, project: str, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - {
        "collection",
        "limit",
        "offset",
        "scope",
        "occurred_until",
        "known_until",
        "expected_revision",
    }:
        raise ValueError("未知 L1 图查询参数")
    expected = values.get("expected_revision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision 需为非负整数")
    with store.snapshot():
        reader = Reader(store, project, values)
        if expected is not None and expected != store.revision():
            raise ConflictError("L1 图已变化，请固定双时间后重新读取")
        return FileRunGraph(reader).query(values)
