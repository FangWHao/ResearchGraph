from __future__ import annotations

import re
import uuid
from typing import Any

from jsonschema import Draft202012Validator

from rg.extract.redact import redact
from rg.extract.schemas import PASS2_SCHEMA
from rg.ingest.common import RG_BLOCK
from rg.store.database import Store, dumps, now
from rg.store.objects import digest


class InvalidClaim(ValueError):
    pass


def validate(
    store: Store,
    output: dict[str, Any],
    project_id: str,
    allowed_ids: set[str],
    event_ids: set[int],
    segment_id: str,
    windows: dict[int, list[tuple[int, int]]] | None = None,
    owned_windows: dict[int, list[tuple[int, int]]] | None = None,
    expected_scope: dict[str, str] | None = None,
) -> None:
    errors = list(Draft202012Validator(PASS2_SCHEMA).iter_errors(output))
    if errors:
        raise InvalidClaim("结构化输出不符合本地 schema")
    if output["segment_id"] != segment_id:
        raise InvalidClaim("片段 ID 不匹配")
    kinds: dict[str, str] = {}
    scopes: dict[str, dict[str, str]] = {}
    for entity_id in allowed_ids:
        row = store.db.execute(
            "SELECT kind, project_id FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if not row or row["project_id"] != project_id:
            raise InvalidClaim("工作集对象不存在或来自其他项目")
        kinds[entity_id] = row["kind"]
    for item in output["claims"]:
        if expected_scope is not None and item["scope"] != expected_scope:
            raise InvalidClaim("输出范围与本次显式范围不一致")
        if item["claim_type"] == "entity_version":
            temp = item["temp_id"]
            if not temp.startswith("new:") or temp in kinds:
                raise InvalidClaim("临时 ID 重复或格式非法")
            kinds[temp] = item["kind"]
            scopes[temp] = item["scope"]
    for item in output["claims"]:
        has_owned_evidence = owned_windows is None
        refs = [item[key] for key in ["source", "target"] if key in item]
        if item["claim_type"] == "join_ports":
            refs += [x["ref"] for x in item["inputs"]]
            if kinds.get(item["target"]) != "join":
                raise InvalidClaim("汇合端口必须属于 join")
            ports = [x["port"] for x in item["inputs"]]
            if len(ports) != len(set(ports)):
                raise InvalidClaim("端口名称重复")
            if item["semantics"] == "compare_then_select":
                if item["selected"] not in refs[1:]:
                    raise InvalidClaim("选中对象必须来自输入端口")
            elif item["selected"] is not None:
                raise InvalidClaim("非比较汇合不能声明选中对象")
        if any(ref not in kinds for ref in refs):
            raise InvalidClaim("引用对象不在工作集或本次临时 ID 中")
        if item["claim_type"] == "evidence_event" and kinds[item["target"]] != "finding":
            raise InvalidClaim("证据状态只能属于 finding")
        if item["claim_type"] == "relation":
            pair = (kinds[item["source"]], kinds[item["target"]])
            if item["relation"] in {"consumes", "produces"}:
                raise InvalidClaim("L1 运行和产物引用尚未实现，不能用 L2 对象冒充")
            if item["relation"] == "supersedes" and pair[0] != pair[1]:
                raise InvalidClaim("替代关系的对象类型必须一致")
            valid_pairs = {
                "part_of": {("attempt", "approach"), ("approach", "question")},
                "supports": {("finding", "question"), ("finding", "finding")},
                "challenges": {("finding", "question"), ("finding", "finding")},
                "selects": {("join", x) for x in ["approach", "attempt", "finding"]},
            }
            if item["relation"] in valid_pairs and pair not in valid_pairs[item["relation"]]:
                raise InvalidClaim("关系方向非法")
        for evidence in item["evidence"]:
            event_id = evidence["event_id"]
            if event_id not in event_ids:
                raise InvalidClaim("引用事件不在本次原文窗口中")
            row = store.db.execute(
                "SELECT r.*, s.project_id FROM raw_events r JOIN sessions s "
                "USING(session_pk) WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if not row or row["exclude_reason"] or row["project_id"] != project_id:
                raise InvalidClaim("引用事件不存在、被排除或不属于本项目")
            if store.db.execute(
                "SELECT 1 FROM dedupe_links WHERE alias_id = ?", (event_id,)
            ).fetchone():
                raise InvalidClaim("不能引用镜像事件")
            raw = store.raw(event_id)
            start, end = evidence["byte_start"], evidence["byte_end"]
            try:
                redact(raw).original_span(start, end)
            except (ValueError, UnicodeError):
                raise InvalidClaim("引用越界、切断 UTF-8 或落在遮盖区域") from None
            if windows and not any(a <= start < end <= b for a, b in windows.get(event_id, [])):
                raise InvalidClaim("引用范围不在发送窗口中")
            if owned_windows is not None and any(
                a <= start < end <= b for a, b in owned_windows.get(event_id, [])
            ):
                has_owned_evidence = True
            quote = raw[start:end]
            if evidence["quote"].encode() != quote:
                raise InvalidClaim("引用原话与字节范围不一致")
            if "quote_sha256" in evidence and evidence["quote_sha256"] != digest(quote):
                raise InvalidClaim("引用摘要不一致")
            for match in RG_BLOCK.finditer(raw.decode()):
                a = len(raw.decode()[: match.start()].encode())
                b = len(raw.decode()[: match.end()].encode())
                if start < b and end > a:
                    raise InvalidClaim("不能引用 rg 注入块")
            if re.search(r"\\u003[cC]rg-context", quote.decode()):
                raise InvalidClaim("不能引用编码后的 rg 注入块")
        if not has_owned_evidence:
            raise InvalidClaim("候选只引用重叠上下文，不能重复写入旧决定")


def persist(
    store: Store,
    output: dict[str, Any],
    project_id: str,
    run_id: int,
    allowed_ids: set[str],
    event_ids: set[int],
    segment_id: str,
    windows: dict[int, list[tuple[int, int]]] | None = None,
    owned_windows: dict[int, list[tuple[int, int]]] | None = None,
    expected_scope: dict[str, str] | None = None,
) -> list[int]:
    validate(
        store,
        output,
        project_id,
        allowed_ids,
        event_ids,
        segment_id,
        windows,
        owned_windows,
        expected_scope,
    )
    mapping = {
        x["temp_id"]: str(uuid.uuid4())
        for x in output["claims"]
        if x["claim_type"] == "entity_version"
    }
    ids = []
    with store.transaction() as db:
        for item in output["claims"]:
            if item["claim_type"] == "entity_version":
                db.execute(
                    "INSERT INTO entities VALUES (?, ?, ?, NULL, ?)",
                    (mapping[item["temp_id"]], project_id, item["kind"], now()),
                )
        for item in output["claims"]:
            payload = dict(item)
            evidence = payload.pop("evidence")
            for key in ["source", "target", "selected", "temp_id"]:
                if payload.get(key) in mapping:
                    payload[key] = mapping[payload[key]]
            if "inputs" in payload:
                payload["inputs"] = [
                    {**x, "ref": mapping.get(x["ref"], x["ref"])} for x in payload["inputs"]
                ]
            entity_id = payload.get("temp_id") or payload.get("target")
            first = db.execute(
                "SELECT occurred_at FROM raw_events WHERE event_id = ?", (evidence[0]["event_id"],)
            ).fetchone()
            cursor = db.execute(
                "INSERT INTO claims (claim_type, entity_id, payload, scope, basis, actor, "
                "claim_state, occurred_at, recorded_at, extraction_run_id) "
                "VALUES (?, ?, ?, ?, 'model_inference', ?, 'candidate', ?, ?, ?)",
                (
                    item["claim_type"],
                    entity_id,
                    dumps(payload),
                    dumps(item["scope"]),
                    f"model:{run_id}",
                    first[0],
                    now(),
                    run_id,
                ),
            )
            claim_id = cursor.lastrowid
            if claim_id is None:
                raise RuntimeError("候选写入失败")
            ids.append(claim_id)
            for span in evidence:
                quote = store.raw(span["event_id"])[span["byte_start"] : span["byte_end"]]
                span_id = db.execute(
                    "INSERT INTO evidence_spans "
                    "(event_id, byte_start, byte_end, quote_sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (span["event_id"], span["byte_start"], span["byte_end"], digest(quote)),
                ).lastrowid
                db.execute(
                    "INSERT INTO claim_evidence VALUES (?, ?, ?)",
                    (claim_id, span_id, span.get("role", "support")),
                )
        db.execute(
            "UPDATE extraction_runs SET status = 'ok' WHERE extraction_run_id = ?", (run_id,)
        )
    return ids
