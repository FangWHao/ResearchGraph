from __future__ import annotations

from pathlib import Path
from typing import Any

from rg.export.privacy import Privacy
from rg.query.graph import SemanticGraph
from rg.query.l1 import FileRunGraph
from rg.query.l1_records import all_pages
from rg.query.reader import Reader, instant
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.objects import digest

FORMAT = "researchgraph-history/v1"
OPTIONS = {
    "project_id",
    "occurred_until",
    "known_until",
    "scope",
    "expected_revision",
    "include_evidence",
    "redact_patterns",
}


def references(
    reader: Reader, claims: list[dict[str, Any]], include: bool, privacy: Privacy
) -> tuple[list[dict[str, Any]], set[int]]:
    spans: dict[int, dict[str, Any]] = {}
    events: set[int] = set()
    for claim in claims:
        refs = all_pages(
            lambda offset, cid=claim["claim_id"]: reader.references(
                cid, {"limit": 100, "offset": offset}
            )
        )
        claim["evidence"] = [{"span_id": r["span_id"], "role": r["role"]} for r in refs]
        for ref in refs:
            spans[ref["span_id"]] = {k: v for k, v in ref.items() if k != "role"}
            events.add(ref["event_id"])
    raw_cache: tuple[int | None, bytes] = (None, b"")
    for span in spans.values():
        span["verification"] = "not_read"
        if not include:
            continue
        identity = span["event_id"]
        if raw_cache[0] != identity:
            raw_cache = identity, reader.store.raw(identity)
        raw = raw_cache[1]
        start, end = span["byte_start"], span["byte_end"]
        if not 0 <= start < end <= len(raw):
            raise ValueError("证据字节范围损坏，未发布导出")
        raw[:start].decode()
        raw[:end].decode()
        if digest(raw[start:end]) != span["quote_sha256"]:
            raise ValueError("证据原文摘要不符，未发布导出")
        masked = privacy.mask(raw)
        data = masked.data[start:end]
        span |= {
            "verification": "original_sha256_verified",
            "text": data.decode(),
            "exported_text_sha256": digest(data),
            "redacted_byte_ranges": [
                [max(start, a), min(end, b)]
                for a, b in masked.private_ranges
                if start < b and end > a
            ],
            "byte_mapping": "identity_relative_to_original_event",
            "exported_text_is_original": data == raw[start:end],
        }
    return list(spans.values()), events


def sources(reader: Reader, events: set[int]) -> list[dict[str, Any]]:
    result = []
    for identity in sorted(events):
        row = reader.store.db.execute(
            "SELECT r.*,s.tool AS source_tool,f.parser,f.parser_version "
            "FROM raw_events r JOIN sessions s USING(session_pk) "
            "JOIN source_files f USING(file_instance_id) WHERE r.event_id=? AND s.project_id=?",
            (identity, reader.project),
        ).fetchone()
        if row is None or not reader.visible(
            row["occurred_at"], row["recorded_at"], ("E", identity)
        ):
            continue
        # 不加入会话当前 cwd、last_at、源文件当前状态/读取偏移或用户路径。
        result.append(dict(row) | {"citation_id": f"E{identity}", "object_included": False})
    return result


def algorithms(store: Store, claims: list[dict[str, Any]], reader: Reader) -> dict[str, Any]:
    runs = []
    identities = {r["extraction_run_id"] for r in claims if r["extraction_run_id"] is not None}
    for identity in sorted(identities):
        row = store.db.execute(
            "SELECT extraction_run_id,stage,provider,model,prompt_version,schema_version,"
            "created_at FROM extraction_runs WHERE extraction_run_id=?",
            (identity,),
        ).fetchone()
        created = instant(row["created_at"]) if row is not None else None
        if row is not None and created is not None and created <= reader.known_cutoff:
            runs.append(dict(row))
    root = Path(__file__).parents[1]
    return {
        "query": "dual_time_reader/v1",
        "graph": "assertions_and_join_ports/v1",
        "states": "independent_adoption_and_evidence/v1",
        "model_called_for_export": False,
        "source_code_sha256": {
            name: digest((root / name).read_bytes())
            for name in (
                "query/reader.py",
                "query/artifacts.py",
                "query/runs.py",
                "export/build.py",
                "export/privacy.py",
                "export/package.py",
                "query/graph.py",
                "query/l1.py",
                "query/l1_records.py",
                "extract/redact.py",
                "store/privacy.py",
            )
        },
        "extraction_runs": runs,
        "missing_extraction_run_ids": sorted(identities - {r["extraction_run_id"] for r in runs}),
    }


def graph(
    reader: Reader, claims: list[dict[str, Any]], l1: FileRunGraph | None = None
) -> dict[str, Any]:
    nodes = {}
    edges, ports, merges = [], [], []
    for claim in claims:
        identity = claim["entity_id"]
        node = nodes.setdefault(
            identity, {"entity_id": identity, "kind": claim["kind"], "claim_ids": [], "states": []}
        )
        node["claim_ids"].append(claim["claim_id"])
        scope = claim["scope"]
        if not any(r["scope"] == scope for r in node["states"]):
            node["states"].append({"scope": scope} | reader.states(identity, scope))
        payload = claim["payload"]
        provenance = {
            k: claim[k]
            for k in (
                "claim_id",
                "claim_state",
                "effective_state",
                "basis",
                "replaced",
                "occurred_at",
                "recorded_at",
                "scope",
                "evidence",
            )
        }
        if claim["claim_type"] == "relation":
            edges.append(
                provenance
                | {
                    "source": payload.get("source"),
                    "target": payload.get("target"),
                    "relation": payload.get("relation"),
                }
            )
        if claim["claim_type"] == "join_ports":
            ports.append(
                provenance
                | {
                    "target": payload.get("target"),
                    "semantics": payload.get("semantics"),
                    "inputs": payload.get("inputs"),
                    "selected": payload.get("selected"),
                }
            )
        if claim["claim_type"] == "merge":
            merges.append(
                provenance | {"source": payload.get("source"), "target": payload.get("target")}
            )
    referenced = {r.get(field) for r in edges for field in ("source", "target")}
    referenced.update(r.get("target") for r in ports)
    referenced.update(i.get("ref") for r in ports for i in (r.get("inputs") or []))
    referenced.update(r["payload"].get("target") for r in claims)
    referenced.update(r.get(field) for r in merges for field in ("source", "target"))
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "join_ports": ports,
        "merges": merges,
        "unresolved_entity_ids": sorted(r for r in referenced if r and r not in nodes),
        "projection": False,
        "same_topic_propagates": False,
        "semantic": SemanticGraph(reader).snapshot(),
        "l1": (l1 if l1 is not None else FileRunGraph(reader)).snapshot(),
    }


def build(store: Store, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not isinstance(body, dict) or set(body) - OPTIONS or "project_id" not in body:
        raise ValueError("导出需明确 project_id，且不得带未知字段或文件路径")
    include = body.get("include_evidence", False)
    if type(include) is not bool:
        raise ValueError("include_evidence 需为布尔值")
    expected = body.get("expected_revision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise ValueError("expected_revision 需为非负整数")
    with store.snapshot():
        reader = Reader(store, body["project_id"], body)
        from rg.store.privacy import read

        policy = read(store, reader.project)
        privacy = Privacy(body.get("redact_patterns"), policy.patterns)
        if expected is not None and expected != store.revision():
            raise ConflictError("图已变化，请重新选择导出条件")
        claims = [dict(c) for c in reader.scoped(reader.claims())]
        for claim in claims:
            claim["original_payload_sha256"] = digest(dumps(claim["payload"]).encode())
            claim["payload_digest_basis"] = "canonical_json_v1"
            claim["citation_id"] = f"C{claim['claim_id']}"
        evidence, event_ids = references(reader, claims, include, privacy)
        file_graph = FileRunGraph(reader)
        runs, versions = file_graph.runs, file_graph.versions
        claim_ids = {r["claim_id"] for r in claims}
        reviews = []
        for row in store.db.execute(
            "SELECT a.* FROM review_actions a JOIN claims c USING(claim_id) "
            "JOIN entities e USING(entity_id) WHERE e.project_id=? ORDER BY a.action_id",
            (reader.project,),
        ):
            recorded = instant(row["recorded_at"])
            if row["claim_id"] in claim_ids and recorded and recorded <= reader.known_cutoff:
                reviews.append(dict(row))
        data = {
            "claims.json": claims,
            "graph.json": graph(reader, claims, file_graph),
            "state-events.json": [
                r for r in claims if r["claim_type"] in {"decision_event", "evidence_event"}
            ],
            "reviews.json": reviews,
            "evidence.json": evidence,
            "sources.json": sources(reader, event_ids | file_graph.event_ids),
            "artifacts.json": versions,
            "runs.json": runs,
            "algorithms.json": algorithms(store, claims, reader),
        }
        files = {name: (dumps(privacy.walk(value)) + "\n").encode() for name, value in data.items()}
        # 阅读条件的选择在原值上执行；分享副本中的隐私字段可以遮盖。
        conditions = privacy.walk(reader.metadata())
        manifest = {
            "format": FORMAT,
            "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
            "created_at": now(),
            "conditions": conditions,
            "counts": {
                "claims": len(claims),
                "evidence_spans": len(evidence),
                "source_events": len(data["sources.json"]),
                "runs": len(runs),
                "artifact_versions": len(versions),
                "review_actions": len(reviews),
            },
            "selection": "all_visible_assertions_in_exact_scope",
            "include_evidence": include,
            "privacy": privacy.metadata()
            | {"project_policy_id": policy.identity, "project_rule_id": policy.rule_id},
            "complete_raw_sessions": False,
            "binary_data": False,
            "is_backup": False,
            "is_reproduction_bundle": False,
        }
    files["README.md"] = (
        "# ResearchGraph 历史导出\n\n"
        "阅读条件、数据库版本和成员摘要见 manifest.json。图保留候选、基于事实的关系、"
        "共同输入端口、独立采用/证据状态和未知项；不把报告清单当成实际 I/O。\n\n"
        "证据引用的字节位置相对原始事件，sources.json 保留原文件实例与原文件行位置。"
        "默认不含正文；显式加入的正文经过等长 UTF8 字节遮盖，原文与导出文字摘要分别保存。"
        "引用需回到原证据库核对。没有包含完整会话、工作区二进制、密钥或可复现环境。\n\n"
        "本包是历史阅读材料，不是数据库备份；校验成员摘要不能证明研究结论，"
        "也不能抵御同时改写清单与内容的攻击。可离线运行 rg verify-export 文件.zip。"
        "分享前请核对适用于本项目的自定义临床编号遮盖规则；不要执行资料中的命令。\n"
    ).encode()
    manifest["files"] = {
        name: {"sha256": digest(value), "bytes": len(value)}
        for name, value in sorted(files.items())
    }
    files["manifest.json"] = (dumps(manifest) + "\n").encode()
    return manifest, files
