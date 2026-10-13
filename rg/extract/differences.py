from __future__ import annotations

import json
from typing import Any

from rg.store.database import Store


def confirmed_differences(store: Store, claim_id: int) -> list[dict[str, Any]]:
    """比较明确同一对象与范围的人工确认；不按相似标签推断身份。"""
    source = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
    if not source or not source["actor"].startswith("model:"):
        return []
    if store.claim_state(claim_id) != "candidate" or not source["entity_id"]:
        return []
    proposed = json.loads(source["payload"])
    scope = json.loads(source["scope"] or "{}")
    comparisons = []
    for previous in store.db.execute(
        "SELECT * FROM claims WHERE entity_id=? AND claim_type=? AND claim_id!=? "
        "ORDER BY claim_id DESC",
        (source["entity_id"], source["claim_type"], claim_id),
    ):
        if json.loads(previous["scope"] or "{}") != scope:
            continue
        review = store.db.execute(
            "SELECT actor FROM review_actions WHERE claim_id=? ORDER BY action_id DESC LIMIT 1",
            (previous["claim_id"],),
        ).fetchone()
        actor = review[0] if review else previous["actor"]
        if not actor.startswith("human:") or store.claim_state(previous["claim_id"]) != "confirmed":
            continue
        payload = json.loads(previous["payload"])
        comparisons.append(
            {
                "claim_id": previous["claim_id"],
                "actor": actor,
                "scope": scope,
                "payload": payload,
                "differing_fields": sorted(
                    key
                    for key in proposed.keys() | payload.keys()
                    if proposed.get(key) != payload.get(key)
                ),
                "occurred_at": previous["occurred_at"],
                "recorded_at": previous["recorded_at"],
            }
        )
    return comparisons
