from __future__ import annotations

from typing import Any


def obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRING = {"type": "string", "minLength": 1}
EVIDENCE = obj(
    {
        "event_id": {"type": "integer", "minimum": 1},
        "byte_start": {"type": "integer", "minimum": 0},
        "byte_end": {"type": "integer", "minimum": 1},
        "quote": STRING,
        "quote_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "role": {"enum": ["support", "against", "context"]},
    },
    ["event_id", "byte_start", "byte_end", "quote"],
)
BASE: dict[str, Any] = {
    "scope": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "string"}},
    "evidence": {"type": "array", "minItems": 1, "items": EVIDENCE},
}


def claim(kind: str, fields: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return obj(
        {"claim_type": {"const": kind}, **BASE, **fields},
        ["claim_type", "scope", "evidence", *required],
    )


CLAIM_SCHEMA = {
    "oneOf": [
        claim(
            "entity_version",
            {
                "temp_id": STRING,
                "kind": {
                    "enum": ["question", "approach", "attempt", "finding", "decision", "join"]
                },
                "label": STRING,
                "content": STRING,
            },
            ["temp_id", "kind", "label", "content"],
        ),
        claim(
            "decision_event",
            {
                "target": STRING,
                "action": {
                    "enum": [
                        "proposed",
                        "accepted",
                        "deferred",
                        "rejected",
                        "withdrawn",
                        "superseded",
                    ]
                },
                "reason": STRING,
                "speaker": {"enum": ["user", "assistant"]},
                "explicitness": {"enum": ["explicit", "implicit"]},
                "referent_unique": {"type": "boolean"},
            },
            ["target", "action", "reason", "speaker", "explicitness", "referent_unique"],
        ),
        claim(
            "relation",
            {
                "source": STRING,
                "target": STRING,
                "relation": {
                    "enum": [
                        "part_of",
                        "supports",
                        "challenges",
                        "supersedes",
                        "selects",
                        "consumes",
                        "produces",
                    ]
                },
            },
            ["source", "target", "relation"],
        ),
        claim(
            "evidence_event",
            {
                "target": STRING,
                "state": {
                    "enum": [
                        "unassessed",
                        "supported",
                        "contested",
                        "refuted",
                        "insufficient",
                        "needs_review",
                    ]
                },
                "reason": STRING,
            },
            ["target", "state", "reason"],
        ),
        claim(
            "join_ports",
            {
                "target": STRING,
                "semantics": {
                    "enum": ["all_required", "compare_then_select", "evidence_synthesis"]
                },
                "inputs": {
                    "type": "array",
                    "minItems": 2,
                    "items": obj({"port": STRING, "ref": STRING}, ["port", "ref"]),
                },
                "selected": {"type": ["string", "null"]},
            },
            ["target", "semantics", "inputs", "selected"],
        ),
        claim("merge", {"source": STRING, "target": STRING}, ["source", "target"]),
    ]
}
PASS2_SCHEMA = obj(
    {
        "segment_id": STRING,
        "claims": {"type": "array", "items": CLAIM_SCHEMA},
        "lookup_terms": {"type": "array", "maxItems": 10, "items": STRING},
        "unresolved": {
            "type": "array",
            "items": obj({"event_id": {"type": "integer"}, "note": STRING}, ["event_id", "note"]),
        },
    },
    ["segment_id", "claims", "lookup_terms", "unresolved"],
)
PASS1_SCHEMA = obj(
    {
        "candidates": {
            "type": "array",
            "items": obj(
                {
                    "event_id": {"type": "integer"},
                    "byte_range": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "integer", "minimum": 0},
                    },
                    "cue": {
                        "enum": ["decision", "retraction", "comparison", "finding", "question"]
                    },
                },
                ["event_id", "byte_range", "cue"],
            ),
        }
    },
    ["candidates"],
)
