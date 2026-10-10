from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rg.api.views import NotFound, _utf8_window
from rg.record.schema import validate_scope
from rg.store.database import Store, dumps, now
from rg.store.objects import digest
from rg.store.scopes import known_scope


def instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def cutoff(value: Any, default: str) -> str:
    parsed = instant(default if value is None else value)
    if parsed is None:
        raise ValueError("截止时间需为带时区的 ISO 时间")
    return parsed.isoformat()


def pagination(values: dict[str, Any]) -> tuple[int, int]:
    limit, offset = values.get("limit", 20), values.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("页长为 1 到 100")
    if type(offset) is not int or not 0 <= offset <= 2147483647:
        raise ValueError("偏移需为非负整数")
    return limit, offset


def page(items: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
    limit, offset = pagination(values)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


def event_state(rows: list[dict[str, Any]], field: str, unknown: str) -> dict[str, Any]:
    eligible = [r for r in rows if r["effective_state"] == "confirmed" and not r["replaced"]]
    if not eligible:
        return {"state": unknown, "claim_ids": []}
    if any(r["occurred_time_unknown"] for r in eligible):
        return {"state": "time_unknown", "claim_ids": [r["claim_id"] for r in eligible]}
    times = [instant(r["occurred_at"]) for r in eligible]
    latest = max(t for t in times if t is not None)
    current = [r for r in eligible if instant(r["occurred_at"]) == latest]
    states = {r["payload"].get(field, unknown) for r in current}
    return {
        "state": next(iter(states)) if len(states) == 1 else "conflict",
        "claim_ids": [r["claim_id"] for r in current],
    }


def state_card(states: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for axis, value in states.items():
        result[axis] = dict(value)
        if len(value["claim_ids"]) > 20:
            result[axis] |= {
                "claim_ids": value["claim_ids"][:20],
                "claim_ids_total": len(value["claim_ids"]),
                "claim_ids_partial": True,
                "details": "research.history",
            }
    return result


class Reader:
    def __init__(self, store: Store, project: str, values: dict[str, Any]):
        if (
            not isinstance(project, str)
            or not project
            or not store.db.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project,)
            ).fetchone()
        ):
            raise NotFound("项目不存在")
        self.store, self.project, self.values = store, project, values
        sampled = now()
        self.occurred_until = cutoff(values.get("occurred_until"), sampled)
        self.known_until = cutoff(values.get("known_until"), sampled)
        self.occurred_cutoff = datetime.fromisoformat(self.occurred_until)
        self.known_cutoff = datetime.fromisoformat(self.known_until)
        self.scope = validate_scope(values.get("scope"))
        self.scope_filter = "scope" in values
        self.unknown_recorded: set[tuple[str, Any]] = set()
        self._cached: list[dict[str, Any]] | None = None
        self._event_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._state_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def visible(
        self, occurred: str | None, recorded: str | None, identity: tuple[str, Any]
    ) -> bool:
        known = instant(recorded)
        if known is None:
            self.unknown_recorded.add(identity)
            return False
        happened = instant(occurred)
        return known <= self.known_cutoff and (happened is None or happened <= self.occurred_cutoff)

    def metadata(self) -> dict[str, Any]:
        return {
            "project_id": self.project,
            "revision": self.store.revision(),
            "scope": self.scope,
            "scope_filter": self.scope_filter,
            "occurred_until": self.occurred_until,
            "known_until": self.known_until,
            "unknown_recorded_time_excluded": len(self.unknown_recorded),
        }

    def claims(self) -> list[dict[str, Any]]:
        if self._cached is not None:
            return self._cached
        reviews: dict[int, dict[str, Any]] = {}
        for row in self.store.db.execute(
            "SELECT a.* FROM review_actions a JOIN claims c USING(claim_id) "
            "JOIN entities e USING(entity_id) WHERE e.project_id=? ORDER BY a.action_id",
            (self.project,),
        ):
            recorded = instant(row["recorded_at"])
            if recorded is not None and recorded <= self.known_cutoff:
                reviews[row["claim_id"]] = dict(row)
        result = []
        for row in self.store.db.execute(
            "SELECT c.*,e.kind FROM claims c JOIN entities e USING(entity_id) "
            "WHERE e.project_id=? ORDER BY c.claim_id",
            (self.project,),
        ):
            if not self.visible(row["occurred_at"], row["recorded_at"], ("C", row["claim_id"])):
                continue
            item = dict(row)
            item["payload"] = json.loads(row["payload"])
            item["scope"] = json.loads(row["scope"]) if row["scope"] else None
            review = reviews.get(row["claim_id"])
            item["effective_state"] = (
                ("confirmed" if review["action"] == "confirm" else "dismissed")
                if review
                else row["claim_state"]
            )
            item["review"] = review
            item["occurred_time_unknown"] = instant(row["occurred_at"]) is None
            result.append(item)
        replacements: dict[int, list[int]] = {}
        for item in result:
            if item["replaces_claim"]:
                replacements.setdefault(item["replaces_claim"], []).append(item["claim_id"])
        for item in result:
            item["replacement_ids"] = replacements.get(item["claim_id"], [])
            item["replaced"] = bool(item["replacement_ids"])
            target = item["payload"].get("target")
            if target is not None:
                self._event_index.setdefault((target, dumps(item["scope"])), []).append(item)
        self._cached = result
        return result

    def scoped(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if not self.scope_filter or r["scope"] == self.scope]

    def references(self, claim_id: int, values: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = []
        for row in self.store.db.execute(
            "SELECT s.*,ce.role,r.occurred_at,r.recorded_at FROM claim_evidence ce "
            "JOIN evidence_spans s USING(span_id) JOIN raw_events r USING(event_id) "
            "JOIN sessions se USING(session_pk) WHERE ce.claim_id=? AND se.project_id=? "
            "ORDER BY s.span_id",
            (claim_id, self.project),
        ):
            if self.visible(row["occurred_at"], row["recorded_at"], ("E", row["event_id"])):
                rows.append(dict(row) | {"citation_id": f"S{row['span_id']}"})
        return page(rows, values or {})

    def card(self, item: dict[str, Any]) -> dict[str, Any]:
        return item | {
            "citation_id": f"C{item['claim_id']}",
            "evidence": self.references(item["claim_id"]),
        }

    def entity(self, entity_id: str) -> None:
        if not self.store.db.execute(
            "SELECT 1 FROM entities WHERE entity_id=? AND project_id=?",
            (entity_id, self.project),
        ).fetchone():
            raise NotFound("当前项目中没有这个对象")

    def related(self, entity_id: str) -> list[dict[str, Any]]:
        self.entity(entity_id)
        return self.scoped(
            [
                r
                for r in self.claims()
                if r["entity_id"] == entity_id
                or r["payload"].get("source") == entity_id
                or any(i.get("ref") == entity_id for i in r["payload"].get("inputs", []))
            ]
        )

    def states(self, entity_id: str, scope: Any) -> dict[str, Any]:
        self.claims()
        key = (entity_id, dumps(scope))
        if key in self._state_cache:
            return self._state_cache[key]
        rows = self._event_index.get(key, [])
        result = {
            "adoption": event_state(
                [r for r in rows if r["claim_type"] == "decision_event"], "action", "unknown"
            )
            if known_scope(scope)
            else {"state": "unknown_scope", "claim_ids": []},
            "evidence_state": event_state(
                [r for r in rows if r["claim_type"] == "evidence_event"], "state", "unassessed"
            )
            if known_scope(scope)
            else {"state": "needs_review", "claim_ids": []},
        }
        self._state_cache[key] = result
        return result

    def history(self, entity_id: str) -> dict[str, Any]:
        rows = self.related(entity_id)
        result = page(rows, self.values)
        result["items"] = [self.card(r) for r in result["items"]]
        return self.metadata() | result

    def node(self, entity_id: str) -> dict[str, Any]:
        rows = self.related(entity_id)
        own = [
            r for r in rows if r["claim_type"] == "entity_version" and r["entity_id"] == entity_id
        ]
        groups = {}
        for row in own:
            groups[dumps(row["scope"])] = row["scope"]
        scopes = list(groups.values())
        result = page(own, self.values)
        result["items"] = [self.card(r) for r in result["items"]]
        states = page(
            [{"scope": scope} | state_card(self.states(entity_id, scope)) for scope in scopes],
            self.values,
        )
        return (
            self.metadata()
            | result
            | {
                "entity_id": entity_id,
                "states": states["items"],
                "state_pagination": {k: v for k, v in states.items() if k != "items"},
                "states_are_for_entire_history": True,
            }
        )

    def search(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("搜索文本需为 1 到 500 字符")
        source = self.values.get("source", "claims")
        if source == "events" and self.scope_filter:
            raise ValueError("原始事件没有统一范围；范围检索请选择 claims")
        if source == "claims":
            candidates = [r for r in self.scoped(self.claims()) if text in dumps(r["payload"])]
            result = page(candidates, self.values)
            result["items"] = [self.card(r) for r in result["items"]]
            return self.metadata() | result
        hits = []
        for row in self.store.db.execute(
            "SELECT r.*,x.text FROM event_search x JOIN raw_events r USING(event_id) "
            "JOIN sessions s USING(session_pk) WHERE s.project_id=? AND instr(x.text,?)>0 "
            "AND r.exclude_reason IS NULL AND r.alias_of IS NULL ORDER BY r.event_id",
            (self.project, text),
        ):
            if not self.visible(row["occurred_at"], row["recorded_at"], ("E", row["event_id"])):
                continue
            position = row["text"].find(text)
            hits.append(
                {
                    "citation_id": f"E{row['event_id']}",
                    "event_id": row["event_id"],
                    "occurred_at": row["occurred_at"],
                    "recorded_at": row["recorded_at"],
                    "preview": row["text"][max(0, position - 100) : position + len(text) + 150],
                    "preview_is_original_byte_window": False,
                }
            )
        return self.metadata() | page(hits, self.values)

    def evidence(self) -> dict[str, Any]:
        values = self.values
        if "version_id" in values:
            if self.scope_filter:
                raise ValueError("文件观察没有统一范围，请先查看关联记录")
            version = self.store.db.execute(
                "SELECT v.*,r.occurred_at,r.recorded_at FROM artifact_versions v "
                "LEFT JOIN raw_events r ON r.event_id=v.evidence_event_id "
                "WHERE v.version_id=? AND v.project_id=?",
                (values["version_id"], self.project),
            ).fetchone()
            if version is None or not self.visible(
                version["occurred_at"], version["recorded_at"], ("V", values["version_id"])
            ):
                raise NotFound("当前查询中没有这个版本观察")
            return self.metadata() | {
                "citation_id": f"V:{version['version_id']}",
                "version": dict(version),
                "notice": "文件版本观察；不读取当前文件，也不推断该版本内容完整或运行成功。",
            }
        if "claim_id" in values:
            if not any(r["claim_id"] == values["claim_id"] for r in self.scoped(self.claims())):
                raise NotFound("当前查询中没有这个记录")
            result = self.references(values["claim_id"], values)
            return self.metadata() | result
        if self.scope_filter:
            raise ValueError("原件没有统一范围；按范围先查询 claim_id 的引用")
        span = None
        if "span_id" in values:
            span = self.store.db.execute(
                "SELECT s.* FROM evidence_spans s JOIN raw_events r USING(event_id) "
                "JOIN sessions se USING(session_pk) WHERE s.span_id=? AND se.project_id=?",
                (values["span_id"], self.project),
            ).fetchone()
            if span is None:
                raise NotFound("当前项目中没有这个引用")
            event_id = span["event_id"]
        else:
            event_id = values["event_id"]
        row = self.store.db.execute(
            "SELECT r.* FROM raw_events r JOIN sessions s USING(session_pk) "
            "WHERE r.event_id=? AND s.project_id=?",
            (event_id, self.project),
        ).fetchone()
        if row is None or not self.visible(
            row["occurred_at"], row["recorded_at"], ("E", row["event_id"])
        ):
            raise NotFound("当前查询中没有这个原文事件")
        raw = self.store.raw(event_id)
        left, right = (span["byte_start"], span["byte_end"]) if span else (0, len(raw))
        if span:
            if not 0 <= left < right <= len(raw):
                raise ValueError("引用范围超出原件")
            raw[left:right].decode("utf-8")
        if span and digest(raw[left:right]) != span["quote_sha256"]:
            raise ValueError("引用摘要与原文不一致")
        offset, size = values.get("byte_offset", 0), values.get("max_bytes", 4000)
        if type(offset) is not int or not 0 <= offset <= right - left:
            raise ValueError("原文字节偏移无效")
        if type(size) is not int or not 4 <= size <= 24000:
            raise ValueError("原文窗口为 4 到 24000 字节")
        start, end = _utf8_window(raw, left + offset, min(right, left + offset + size))
        if start != left + offset:
            raise ValueError("字节偏移必须位于 UTF8 边界")
        result = {
            "event_id": event_id,
            "span_id": span["span_id"] if span else None,
            "citation_id": f"S{span['span_id']}" if span else f"E{event_id}",
            "occurred_at": row["occurred_at"],
            "recorded_at": row["recorded_at"],
            "exclude_reason": row["exclude_reason"],
            "alias_of": row["alias_of"],
            "source_byte_start": row["byte_start"],
            "source_byte_end": row["byte_end"],
            "object_sha256": row["object_sha256"],
            "total_bytes": len(raw),
            "range_start": left,
            "range_end": right,
            "window_start": start,
            "window_end": end,
            "window_sha256": digest(raw[start:end]),
            "quote_sha256": span["quote_sha256"] if span else None,
            "text": raw[start:end].decode("utf-8"),
            "next_byte_offset": end - left if end < right else None,
            "window_truncated": start > left or end < right,
            "artifact_versions": page(
                [
                    dict(version) | {"citation_id": f"V:{version['version_id']}"}
                    for version in self.store.db.execute(
                        "SELECT * FROM artifact_versions "
                        "WHERE evidence_event_id=? AND project_id=? "
                        "ORDER BY version_id",
                        (event_id, self.project),
                    )
                ],
                values,
            ),
        }
        return self.metadata() | result
