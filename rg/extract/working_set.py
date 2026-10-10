from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from typing import Any

from rg.extract.paths import file_paths
from rg.extract.privacy import ProjectCounter
from rg.slim.tokens import TokenCounter
from rg.store.database import Store, dumps
from rg.store.scopes import known_scope


def working_set(
    store: Store,
    project_id: str,
    text: str,
    counter: TokenCounter,
    terms: list[str] | None = None,
    budget: int = 3000,
    before_event: int | None = None,
    scope: dict[str, str] | None = None,
    paths: set[str] | None = None,
    before_offset: int | None = None,
) -> list[dict[str, Any]]:
    if scope is not None and (
        not scope
        or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in scope.items()
        )
    ):
        raise ValueError("显式范围必须为非空字符串字段")
    rows = store.db.execute(
        "SELECT e.entity_id, e.kind, c.claim_id, c.payload, c.scope FROM entities e JOIN claims c "
        "USING(entity_id) WHERE e.project_id = ? AND c.claim_type = ? "
        "ORDER BY c.claim_id DESC",
        (project_id, "entity_version"),
    ).fetchall()
    candidates = []
    matches = _label_matches(
        [(r["claim_id"], json.loads(r["payload"]).get("label", "")) for r in rows],
        text,
        terms or [],
    )
    seen = set()
    boundary = (
        store.db.execute(
            "SELECT session_pk, seq FROM raw_events WHERE event_id = ?", (before_event,)
        ).fetchone()
        if before_event
        else None
    )
    for row in rows:
        row_scope = json.loads(row["scope"] or "{}")
        if scope is not None and row_scope != scope:
            continue
        entity_id = row["entity_id"]
        if entity_id in seen or store.claim_state(row["claim_id"]) == "dismissed":
            continue
        if boundary and store.claim_state(row["claim_id"]) == "candidate":
            same_or_later = store.db.execute(
                "SELECT 1 FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
                "JOIN raw_events r USING(event_id) WHERE ce.claim_id = ? AND r.session_pk = ? "
                "AND (r.seq > ? OR (r.seq = ? AND es.byte_end > ?)) LIMIT 1",
                (row["claim_id"], boundary[0], boundary[1], boundary[1], before_offset or 0),
            ).fetchone()
            if same_or_later:
                continue
        seen.add(entity_id)
        payload = json.loads(row["payload"])
        label = payload.get("label", "")
        prior = False
        if boundary:
            prior = (
                store.db.execute(
                    "SELECT 1 FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
                    "JOIN raw_events r USING(event_id) WHERE ce.claim_id = ? AND r.session_pk = ? "
                    "AND (r.seq < ? OR (r.seq = ? AND es.byte_end <= ?)) LIMIT 1",
                    (row["claim_id"], boundary[0], boundary[1], boundary[1], before_offset or 0),
                ).fetchone()
                is not None
            )
        rank = (
            0
            if entity_id in text
            else 1
            if row["claim_id"] in matches
            else 2
            if (paths or set()) & file_paths(label + " " + payload.get("content", ""))
            else 3
            if prior
            else 4
        )
        if rank == 4:
            continue
        state = "proposed" if known_scope(row_scope) else "unknown_scope"
        # 人工或独立原话规则确认后才改变当前采用状态，候选不能悄悄成为事实。
        decisions = store.db.execute(
            "SELECT claim_id, payload, scope FROM claims WHERE entity_id = ? "
            "AND claim_type = 'decision_event' ORDER BY "
            "occurred_at DESC, claim_id DESC",
            (entity_id,),
        ).fetchall()
        for decision in decisions:
            if (
                known_scope(row_scope)
                and known_scope(json.loads(decision["scope"] or "null"))
                and json.loads(decision["scope"] or "{}") == json.loads(row["scope"] or "{}")
                and store.claim_state(decision["claim_id"]) == "confirmed"
            ):
                state = json.loads(decision["payload"])["action"]
                break
        if row["kind"] not in {"question", "approach", "decision"} and rank != 0:
            continue
        if state in {"rejected", "withdrawn", "superseded"} and rank != 0:
            continue
        candidates.append(
            (
                rank,
                -row["claim_id"],
                {
                    "id": entity_id,
                    "kind": row["kind"],
                    "label": counter.safe_slice(label, 0, 30)
                    if isinstance(counter, ProjectCounter)
                    else label[:30],
                    "state": state,
                    "scope": row_scope,
                },
            )
        )
    result: list[dict[str, Any]] = []
    for _, _, candidate in sorted(candidates, key=lambda x: (x[0], x[1])):
        if counter.count_text(dumps(result + [candidate])) <= budget:
            result.append(candidate)
    return result


def _label_matches(labels: list[tuple[int, str]], text: str, lookup_terms: list[str]) -> set[int]:
    # FTS 运算符由程序生成，用户文字逐项编码成字面量；临时索引不改变语义数据。
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = None
    if isinstance(decoded, list):
        text = "\n".join(x.get("text", "") for x in decoded if isinstance(x, dict))
    texts = [text, *lookup_terms]
    words = set(re.findall(r"[A-Za-z0-9_]{3,}", " ".join(texts)))
    for run in re.findall(r"[\u3400-\u9fff]+", " ".join(texts)):
        words.update(run[i : i + 3] for i in range(max(0, len(run) - 2)))
    matches = {claim for claim, label in labels if label and any(label in t for t in texts)}
    with closing(sqlite3.connect(":memory:")) as index:
        index.execute("CREATE VIRTUAL TABLE labels USING fts5(label, tokenize='trigram')")
        index.executemany("INSERT INTO labels(rowid, label) VALUES (?, ?)", labels)
        ordered = sorted(words)
        for offset in range(0, len(ordered), 64):
            literals = [
                '"' + word.replace('"', '""') + '"' for word in ordered[offset : offset + 64]
            ]
            query = " OR ".join(literals)
            matches.update(
                row[0]
                for row in index.execute(
                    "SELECT rowid FROM labels WHERE labels MATCH ?",
                    (query,),
                )
            )
    return matches
