from __future__ import annotations

import re
from typing import Any

import zstandard

from rg.query.reader import Reader, state_card
from rg.store.database import Store, dumps

MAX_MATCHES = 200
MAX_REFERENCES = 20


def terms(question: str) -> list[str]:
    words = set(re.findall(r"[A-Za-z0-9_./:-]{2,}", question.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", question):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return sorted(words, key=lambda word: (-len(word), word))[:80]


def score(text: str, words: list[str]) -> int:
    text = text.casefold()
    return sum(len(word) for word in words if word in text)


def _record(reader: Reader, row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"]
    text = dumps(payload)
    # 记录预览不是原文证据；证据从校验过的对象窗口单独取回。
    target = payload.get("target") or row["entity_id"]
    return {
        key: row[key]
        for key in (
            "claim_id",
            "claim_type",
            "entity_id",
            "scope",
            "basis",
            "actor",
            "claim_state",
            "effective_state",
            "occurred_at",
            "recorded_at",
            "occurred_time_unknown",
            "replaced",
            "replacement_ids",
        )
    } | {
        "payload_preview": text[:4000],
        "payload_preview_truncated": len(text) > 4000,
        "computed_states": state_card(reader.states(target, row["scope"])),
        "review": row["review"],
    }


def _source(reader: Reader, selector: dict[str, Any], size: int) -> dict[str, Any]:
    source = Reader(
        reader.store,
        reader.project,
        reader.values
        | selector
        | {
            "max_bytes": size,
        },
    ).evidence()
    if source["exclude_reason"] or source["alias_of"] is not None:
        raise ValueError("排除或镜像事件不能作为问答证据")
    if reader.store.db.execute(
        "SELECT 1 FROM dedupe_links WHERE alias_id=?", (source["event_id"],)
    ).fetchone():
        raise ValueError("重复事件不能作为问答证据")
    return {
        key: source[key]
        for key in (
            "citation_id",
            "event_id",
            "span_id",
            "text",
            "occurred_at",
            "recorded_at",
            "source_byte_start",
            "source_byte_end",
            "object_sha256",
            "quote_sha256",
            "window_start",
            "window_end",
            "window_sha256",
            "window_truncated",
            "next_byte_offset",
            "range_start",
            "range_end",
        )
    } | {"records": []}


def retrieve(
    store: Store,
    project: str,
    question: str,
    k: int = 12,
    values: dict[str, Any] | None = None,
    max_bytes: int = 4000,
) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError("问题需为 1 到 2000 字符")
    if type(k) is not int or not 1 <= k <= 100:
        raise ValueError("证据条数需为 1 到 100")
    if type(max_bytes) is not int or not 4 <= max_bytes <= 24000:
        raise ValueError("证据窗口需为 4 到 24000 UTF8 字节")
    values = dict(values or {})
    with store.snapshot():
        reader = Reader(store, project, values)
        from rg.store.database import ConflictError

        expected = values.get("expected_revision")
        if expected is not None:
            if type(expected) is not int or expected < 0:
                raise ValueError("预期版本需为非负整数")
            if expected != store.revision():
                raise ConflictError("研究记录已变化，请重新检索")
        words = terms(question)
        rows = reader.scoped(reader.claims())
        named: dict[str, int] = {}
        for row in rows:
            if row["claim_type"] == "entity_version":
                label = row["payload"].get("label", "")
                rank = score(label, words)
                if label and label.casefold() in question.casefold():
                    rank += 1000
                named[row["entity_id"]] = max(rank, named.get(row["entity_id"], 0))
        ranked = []
        for row in rows:
            rank = score(dumps(row["payload"]), words)
            rank += named.get(row["entity_id"], 0)
            rank += named.get(row["payload"].get("target"), 0)
            if row["entity_id"] in question:
                rank += 1000
            if rank:
                ranked.append((rank, row))
        ranked.sort(
            key=lambda pair: (
                -pair[0],
                pair[1]["claim_type"] != "decision_event",
                pair[1]["claim_id"],
            )
        )
        frozen = {
            "occurred_until": reader.occurred_until,
            "known_until": reader.known_until,
        }
        raw_reader = Reader(store, project, frozen)
        sources: dict[str, dict[str, Any]] = {}
        gaps: list[dict[str, Any]] = []
        pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for _, row in ranked[:MAX_MATCHES]:
            references = reader.references(row["claim_id"], {"limit": MAX_REFERENCES})
            if not references["items"]:
                gaps.append({"claim_id": row["claim_id"], "reason": "没有可见原文引用"})
            if references["next_offset"] is not None:
                gaps.append({"claim_id": row["claim_id"], "reason": "引用清单超过检索上限"})
            pending.extend((row, reference) for reference in references["items"])
        # 每条记录先取一条引用，再取额外引用，避免一个多引用记录占满 K。
        first, rest, seen_claims = [], [], set()
        for row, reference in pending:
            (rest if row["claim_id"] in seen_claims else first).append((row, reference))
            seen_claims.add(row["claim_id"])
        for row, reference in first + rest:
            citation = reference["citation_id"]
            if citation not in sources:
                if len(sources) >= k:
                    continue
                try:
                    sources[citation] = _source(
                        raw_reader, {"span_id": reference["span_id"]}, max_bytes
                    )
                except (ValueError, OSError, RuntimeError, zstandard.ZstdError):
                    gaps.append({"claim_id": row["claim_id"], "reason": "原文引用校验失败"})
                    continue
            records = sources[citation]["records"]
            if len(records) < 20 and not any(r["claim_id"] == row["claim_id"] for r in records):
                records.append(_record(reader, row))
            elif len(records) >= 20:
                sources[citation]["records_partial"] = True
        raw_matches = 0
        raw_limit_reached = False
        # 还没有结构化记录时仍可检索原文；原件没有统一范围，显式范围下不猜归属。
        if len(sources) < k and words and not reader.scope_filter:
            clauses = " OR ".join("instr(lower(es.text),?)>0" for _ in words)
            matches = store.db.execute(
                "SELECT r.event_id,r.occurred_at,r.recorded_at,es.text FROM event_search es "
                "JOIN raw_events r USING(event_id) JOIN sessions s USING(session_pk) "
                "WHERE s.project_id=? AND r.exclude_reason IS NULL AND r.alias_of IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM dedupe_links d WHERE d.alias_id=r.event_id) "
                f"AND ({clauses}) ORDER BY r.event_id LIMIT ?",
                (project, *words, MAX_MATCHES + 1),
            ).fetchall()
            raw_limit_reached = len(matches) > MAX_MATCHES
            visible = [
                r
                for r in matches
                if raw_reader.visible(
                    r["occurred_at"],
                    r["recorded_at"],
                    ("E", r["event_id"]),
                )
            ]
            raw_matches = len(visible)
            visible.sort(key=lambda r: (-score(r["text"], words), r["event_id"]))
            covered = {s["event_id"] for s in sources.values()}
            for event in visible[:MAX_MATCHES]:
                if len(sources) >= k:
                    break
                if event["event_id"] in covered:
                    continue
                try:
                    raw = store.raw(event["event_id"])
                    decoded = raw.decode()
                    positions = [decoded.casefold().find(word) for word in words]
                    position = min((p for p in positions if p >= 0), default=0)
                    offset = len(decoded[: max(0, position - 80)].encode())
                    source = _source(
                        raw_reader,
                        {
                            "event_id": event["event_id"],
                            "byte_offset": offset,
                        },
                        max_bytes,
                    )
                    sources[source["citation_id"]] = source
                except (ValueError, OSError, RuntimeError, zstandard.ZstdError):
                    gaps.append({"event_id": event["event_id"], "reason": "原文窗口校验失败"})
        reader.unknown_recorded.update(raw_reader.unknown_recorded)
        return reader.metadata() | {
            "question": question,
            "k": k,
            "max_bytes": max_bytes,
            "sources": list(sources.values()),
            "gaps": gaps[:100],
            "gaps_total": len(gaps),
            "matched_claims": len(ranked),
            "raw_matches_examined": raw_matches,
            "retrieval_partial": len(ranked) > MAX_MATCHES
            or raw_limit_reached
            or len(sources) >= k
            or bool(gaps),
            "retrieval_limits": {"claims": MAX_MATCHES, "references_per_claim": MAX_REFERENCES},
            "notice": "有限字面量检索，不保证找齐历史。窗口和记录预览有边界，候选不等于事实。",
        }
