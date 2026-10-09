from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from rg.extract.locator import _bounded_prefix
from rg.extract.redact import redact
from rg.extract.schemas import STRING, obj
from rg.extract.segmenter import Segment
from rg.extract.validate import InvalidClaim, persist
from rg.extract.worker import Worker, _permission
from rg.extract.working_set import _label_matches
from rg.slim.tokens import DailyBudgetExceeded
from rg.store.database import Store, dumps, now
from rg.store.locking import exclusive
from rg.store.objects import digest

LINK_SCHEMA = obj(
    {
        "pair_id": STRING,
        "relation": {
            "enum": ["none", "merge", "part_of", "supersedes", "supports", "challenges", "selects"]
        },
        "source": {"type": ["string", "null"]},
        "target": {"type": ["string", "null"]},
        "reason": STRING,
    },
    ["pair_id", "relation", "source", "target", "reason"],
)
LINK_SCHEMA["allOf"] = [
    {
        "if": {"properties": {"relation": {"const": "none"}}, "required": ["relation"]},
        "then": {"properties": {"source": {"const": None}, "target": {"const": None}}},
        "else": {"properties": {"source": STRING, "target": STRING}},
    }
]


@dataclass
class Pair:
    pair_id: str
    cards: list[dict[str, Any]]
    basis: list[str]
    scope: dict[str, str]


def pairs(
    store: Store, project: str, limit: int = 50, scope: dict[str, str] | None = None
) -> list[Pair]:
    if not 1 <= limit <= 1000:
        raise ValueError("候选对页长必须为 1 到 1000")
    return list(islice(iter_pairs(store, project, scope), limit))


def iter_pairs(store: Store, project: str, scope: dict[str, str] | None = None) -> Iterator[Pair]:
    if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
        raise ValueError("项目不存在")
    rows = store.db.execute(
        "SELECT c.*, e.kind FROM claims c JOIN entities e USING(entity_id) "
        "WHERE e.project_id = ? AND c.claim_type='entity_version' ORDER BY c.claim_id DESC",
        (project,),
    ).fetchall()
    cards: list[dict[str, Any]] = []
    sessions: dict[int, set[int]] = {}
    versions: dict[int, set[tuple[str, str, str]]] = {}
    seen = set()
    for row in rows:
        entity = row["entity_id"]
        if store.claim_state(row["claim_id"]) == "dismissed":
            continue
        current_scope = json.loads(row["scope"] or "{}")
        if not current_scope or any(
            not isinstance(value, str) or not value.strip() or value.strip().lower() == "unknown"
            for value in current_scope.values()
        ):
            continue
        if scope is not None and current_scope != scope:
            continue
        identity = (entity, dumps(current_scope))
        if identity in seen:
            continue
        seen.add(identity)
        payload = json.loads(row["payload"])
        cards.append(
            {
                "id": entity,
                "claim_id": row["claim_id"],
                "kind": row["kind"],
                "label": payload.get("label", "")[:30],
                "scope": current_scope,
                "claim_state": store.claim_state(row["claim_id"]),
                "occurred_at": row["occurred_at"] or "unknown",
            }
        )
        sessions[row["claim_id"]] = {
            r[0]
            for r in store.db.execute(
                "SELECT r.session_pk FROM claim_evidence ce JOIN evidence_spans es USING(span_id) "
                "JOIN raw_events r USING(event_id) WHERE ce.claim_id=?",
                (row["claim_id"],),
            )
        }
        versions[row["claim_id"]] = {
            tuple(r)
            for r in store.db.execute(
                "SELECT av.path, av.algo, av.digest FROM artifact_versions av "
                "JOIN evidence_spans es ON es.event_id=av.evidence_event_id "
                "JOIN claim_evidence ce USING(span_id) WHERE ce.claim_id=? AND av.project_id=?",
                (row["claim_id"], project),
            )
            if all(value and value.strip().lower() != "unknown" for value in r)
        }
    for index, first in enumerate(cards):
        eligible = [
            second
            for second in cards[index + 1 :]
            if second["scope"] == first["scope"]
            and sessions[first["claim_id"]]
            and sessions[second["claim_id"]]
            and not sessions[first["claim_id"]] & sessions[second["claim_id"]]
        ]
        matches = _label_matches(
            [(c["claim_id"], c["label"]) for c in eligible], first["label"], []
        )
        for second in eligible:
            basis = []
            if second["claim_id"] in matches:
                basis.append("label_fts")
            if versions[first["claim_id"]] & versions[second["claim_id"]]:
                basis.append("same_file_version")
            if not basis:
                continue
            identity = dumps([first, second, basis])
            yield Pair(digest(identity.encode()), [first, second], basis, first["scope"])


def link(
    worker: Worker,
    project: str,
    limit: int = 50,
    scope: dict[str, str] | None = None,
    retry_failed: bool = False,
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise ValueError("候选对页长必须为 1 到 1000")
    _permission(worker.store, project, worker.provider)
    with exclusive(worker.store.root / "locks" / (digest(project.encode()) + ".link")):
        return _schedule(worker, project, limit, scope, retry_failed)


def _schedule(
    worker: Worker, project: str, limit: int, scope: dict[str, str] | None, retry_failed: bool
) -> dict[str, int]:
    store = worker.store
    prompt = Path(__file__).with_name("prompts").joinpath("link.txt").read_text()
    header = worker.provider.request(prompt, "", LINK_SCHEMA, min(worker.output_budget, 1000))
    processing = [worker.provider.provider, worker.provider.model, header, 3000, "link-scheduler1"]
    stats = {
        "pairs": 0,
        "claims": 0,
        "cached": 0,
        "manual": 0,
        "processed": 0,
        "skipped_failed": 0,
        "paused": 0,
        "has_more": 0,
    }
    for pair in iter_pairs(store, project, scope):
        key = digest(dumps([processing, pair.pair_id]).encode())
        previous = store.db.execute(
            "SELECT * FROM link_progress WHERE job_key=?", (key,)
        ).fetchone()
        if previous and previous["state"] == "done":
            stats["pairs"] += 1
            stats["cached"] += 1
            continue
        if previous and previous["extraction_run_id"] is not None:
            successful = store.db.execute(
                "SELECT 1 FROM extraction_runs WHERE extraction_run_id=? AND status='ok'",
                (previous["extraction_run_id"],),
            ).fetchone()
            if successful:
                _done(store, key, previous["extraction_run_id"])
                stats["pairs"] += 1
                stats["cached"] += 1
                continue
        if previous and previous["state"] == "failed" and not retry_failed:
            stats["pairs"] += 1
            stats["skipped_failed"] += 1
            continue
        if stats["processed"] >= limit:
            stats["has_more"] = 1
            break
        stats["pairs"] += 1
        stats["processed"] += 1
        store.db.execute(
            "INSERT OR IGNORE INTO link_progress "
            "(job_key,project_id,pair_id,provider,model,source_cards,state,updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                key,
                project,
                pair.pair_id,
                worker.provider.provider,
                worker.provider.model,
                dumps(pair.cards),
                now(),
            ),
        )
        store.db.execute(
            "UPDATE link_progress SET state='pending', attempts=attempts+1, error=NULL, "
            "updated_at=? WHERE job_key=?",
            (now(), key),
        )
        run: int | None = None
        try:
            # 仅第一侧取原文，另一侧只有结构化卡片；没有两段会话共同输入。
            span = store.db.execute(
                "SELECT es.* FROM evidence_spans es JOIN claim_evidence ce USING(span_id) "
                "WHERE ce.claim_id=? ORDER BY es.span_id LIMIT 1",
                (pair.cards[0]["claim_id"],),
            ).fetchone()
            if not span:
                raise InvalidClaim("链接对象缺少原文引用")
            raw = store.raw(span["event_id"])
            a, b = span["byte_start"], span["byte_end"]
            redact(raw).original_span(a, b)
            if digest(raw[a:b]) != span["quote_sha256"]:
                raise InvalidClaim("既有引用摘要不一致")
            a, b = _bounded_prefix(raw, a, b, worker.provider)
            evidence = {
                "event_id": span["event_id"],
                "byte_start": a,
                "byte_end": b,
                "quote": raw[a:b].decode(),
            }
            content = {
                "pair_id": pair.pair_id,
                "cards": pair.cards,
                "retrieval_basis": pair.basis,
                "source_window": evidence,
            }
            item = Segment("link:" + pair.pair_id, [{"event_id": span["event_id"]}])
            ids = {card["id"] for card in pair.cards}
            run, output = worker.invoke(
                "link",
                item,
                project,
                dumps(content),
                LINK_SCHEMA,
                sorted(ids),
                input_limit=3000,
                output_limit=1000,
            )
            store.db.execute(
                "UPDATE link_progress SET extraction_run_id=? WHERE job_key=?", (run, key)
            )
            if output["pair_id"] != pair.pair_id:
                raise InvalidClaim("链接候选对 ID 不一致")
            if output["relation"] == "none":
                if output["source"] is not None or output["target"] is not None:
                    raise InvalidClaim("无链接时不能声明端点")
            elif {output["source"], output["target"]} != ids:
                raise InvalidClaim("链接端点不属于候选对")
            current = store.db.execute(
                "SELECT status FROM extraction_runs WHERE extraction_run_id=?",
                (run,),
            ).fetchone()[0]
            if current == "ok":
                stats["cached"] += 1
                _done(store, key, run)
                continue
            if output["relation"] != "none":
                claim = {
                    "claim_type": "relation",
                    "source": output["source"],
                    "target": output["target"],
                    "relation": output["relation"],
                    "scope": pair.scope,
                    "evidence": [evidence],
                }
                if output["relation"] == "merge":
                    if (
                        pair.cards[0]["kind"] != pair.cards[1]["kind"]
                        or pair.cards[0]["kind"] == "attempt"
                    ):
                        raise InvalidClaim("类型不同或独立尝试不能自动提出合并")
                    claim.pop("relation")
                    claim["claim_type"] = "merge"
                result = {
                    "segment_id": item.segment_id,
                    "claims": [claim],
                    "lookup_terms": [],
                    "unresolved": [],
                }
                stats["claims"] += len(
                    persist(
                        store,
                        result,
                        project,
                        run,
                        ids,
                        {span["event_id"]},
                        item.segment_id,
                        {span["event_id"]: [(a, b)]},
                        expected_scope=pair.scope,
                    )
                )
            else:
                store.db.execute(
                    "UPDATE extraction_runs SET status='ok' WHERE extraction_run_id=?", (run,)
                )
            _done(store, key, run)
        except DailyBudgetExceeded:
            store.db.execute(
                "UPDATE link_progress SET error='daily_budget', updated_at=? WHERE job_key=?",
                (now(), key),
            )
            stats["paused"] = stats["has_more"] = 1
            break
        except RuntimeError as error:
            # 提供方或计数不可用时保留当前项，不让故障消耗其余页或冒充人工失败。
            store.db.execute(
                "UPDATE link_progress SET error=?, updated_at=? WHERE job_key=?",
                (type(error).__name__, now(), key),
            )
            stats["paused"] = stats["has_more"] = 1
            break
        except ValueError as error:
            if run is not None:
                worker._status(
                    run,
                    "invalid" if isinstance(error, InvalidClaim) else "failed",
                    "链接校验或预算失败，结果未入库",
                )
            with store.transaction() as db:
                row = db.execute(
                    "SELECT review_job_id FROM link_progress WHERE job_key=?", (key,)
                ).fetchone()
                review = row[0]
                if review is None:
                    review = db.execute(
                        "INSERT INTO jobs (kind, payload, state, attempts, error, updated_at) "
                        "VALUES ('link_review', ?, 'failed', 1, ?, ?)",
                        (
                            dumps(
                                {
                                    "pair_id": pair.pair_id,
                                    "entity_ids": [c["id"] for c in pair.cards],
                                }
                            ),
                            type(error).__name__,
                            now(),
                        ),
                    ).lastrowid
                else:
                    db.execute(
                        "UPDATE jobs SET state='failed', attempts=attempts+1, error=?, "
                        "updated_at=? "
                        "WHERE job_id=?",
                        (type(error).__name__, now(), review),
                    )
                db.execute(
                    "UPDATE link_progress SET state='failed', review_job_id=?, error=?, "
                    "updated_at=? "
                    "WHERE job_key=?",
                    (review, type(error).__name__, now(), key),
                )
            stats["manual"] += 1
    return stats


def _done(store: Store, key: str, run: int) -> None:
    with store.transaction() as db:
        row = db.execute(
            "SELECT review_job_id FROM link_progress WHERE job_key=?", (key,)
        ).fetchone()
        if row[0] is not None:
            db.execute(
                "UPDATE jobs SET state='done', error=NULL, updated_at=? WHERE job_id=?",
                (now(), row[0]),
            )
        db.execute(
            "UPDATE link_progress SET state='done', extraction_run_id=?, error=NULL, updated_at=? "
            "WHERE job_key=?",
            (run, now(), key),
        )
