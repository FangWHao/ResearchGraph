"""原文来源与独立规则证明分别诊断，共同约束模型输入。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from rg.derive import claim_sources, rule_references
from rg.store.database import Store


def policy() -> dict[str, str]:
    return {**claim_sources.policy(), **rule_references.policy()}


def recorded(
    store: Store,
    claim: int,
    visible: Callable[[dict[str, Any]], bool] | None = None,
    *,
    review: Mapping[str, Any] | None = None,
    selected: bool = False,
) -> dict[str, Any]:
    source = claim_sources.recorded(store, claim, visible)
    rule = rule_references.recorded(store, claim, review, selected=selected)
    return {
        "source_qualification": source,
        "rule_qualification": rule,
        "input_eligible": source["input_eligible"] and rule["input_eligible"],
    }


def assess(store: Store, claim: int, *, refresh: bool = False) -> dict[str, Any]:
    source = claim_sources.assess(store, claim, refresh=refresh)
    rule = rule_references.assess(store, claim, refresh=refresh)
    return {
        "source_qualification": source,
        "rule_qualification": rule,
        "input_eligible": source["input_eligible"] and rule["input_eligible"],
    }
