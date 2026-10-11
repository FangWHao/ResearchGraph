"""只读展开片段的科研依赖；摘要不包含正文，也不授予迁移权限。"""

from __future__ import annotations

import sqlite3
from collections import Counter, deque
from typing import Any

from rg.store.clear_selection import TABLES
from rg.store.clear_selection import identifier as ident
from rg.store.database import ConflictError, Store, dumps
from rg.store.fragment_dependency_contract import (
    JSON_LINKS,
    PLAIN_LINKS,
    SCIENCE,
    SCIENCE_SCHEMA_SHA256,
    json_sql,
    reference_issues,
    schema_sha256,
)
from rg.store.fragments import FIELDS, _protect_science, _validate, selection
from rg.store.objects import digest

MAX_NODES = 4000
MAX_ROW_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_SQL_STEPS = 20_000_000
Node = tuple[str, int]


class _Limit(Exception):
    pass


class Closure:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.nodes: dict[Node, str] = {}
        self.rows: dict[Node, dict[str, Any]] = {}
        self.pending: deque[Node] = deque()
        self.edges: set[tuple[Node, Node]] = set()
        self.context: set[tuple[str, str]] = set()
        self.issues: set[str] = set()
        self.total_bytes = 0
        self.steps = 0
        actual = {
            r[1]
            for r in db.execute("PRAGMA main.table_list")
            if r[2] == "table" and not r[1].startswith("sqlite_")
        }
        if actual != TABLES or schema_sha256(db) != SCIENCE_SCHEMA_SHA256:
            self.issues.add("schema_contract_changed")
            self.links: list[tuple[str, str, str, str]] = []
            return
        columns = {t: list(db.execute("SELECT * FROM pragma_table_info(?)", (t,))) for t in SCIENCE}
        self.keys = {
            t: next((r[1] for r in cols if r[5] == 1), "rowid") for t, cols in columns.items()
        }
        self.sizes = {
            t: "+".join(f"coalesce(length(CAST({ident(r[1])} AS BLOB)),0)" for r in cols)
            for t, cols in columns.items()
        }
        self.links = list(PLAIN_LINKS)
        for table in SCIENCE:
            for ref in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,)):
                if ref[2] in SCIENCE:
                    self.links.append((table, ref[3], ref[2], ref[4] or self.keys[ref[2]]))

    def interrupt(self) -> bool:
        self.steps += 1000
        return self.steps > MAX_SQL_STEPS

    def add(self, table: str, rowid: int, source: Node | None = None) -> None:
        node = (table, rowid)
        if source is not None:
            self.edges.add((source, node) if source <= node else (node, source))
            if len(self.edges) > MAX_NODES * 8:
                raise _Limit("edge_budget")
        if node in self.nodes:
            return
        if len(self.nodes) >= MAX_NODES:
            raise _Limit("node_budget")
        size = self.db.execute(
            f"SELECT {self.sizes[table]} FROM {ident(table)} WHERE rowid=?", (rowid,)
        ).fetchone()
        if size is None:
            self.issues.add("missing_reference")
            return
        if size[0] > MAX_ROW_BYTES or self.total_bytes + size[0] > MAX_TOTAL_BYTES:
            raise _Limit("metadata_byte_budget")
        row = dict(
            self.db.execute(f"SELECT * FROM {ident(table)} WHERE rowid=?", (rowid,)).fetchone()
        )
        safe = {
            key: {"blob_sha256": digest(value)} if isinstance(value, bytes) else value
            for key, value in row.items()
        }
        self.nodes[node] = digest(dumps(safe).encode())
        self.rows[node] = row
        self.total_bytes += size[0]
        self.pending.append(node)
        for key in ("project_id", "root_id", "session_pk", "physical_session_pk"):
            if row.get(key) is not None:
                self.context.add((key, str(row[key])))

    def find(self, table: str, key: str, value: Any, source: Node) -> None:
        if value is None:
            return
        cursor = self.db.execute(
            f"SELECT rowid FROM {ident(table)} WHERE {ident(key)}=? ORDER BY rowid LIMIT ?",
            (value, MAX_NODES + 1),
        )
        found = False
        for row in cursor:
            found = True
            self.add(table, row[0], source)
        if not found:
            self.issues.add("missing_reference")

    def check_json(self) -> None:
        for link in JSON_LINKS:
            self.issues.update(reference_issues(self.db, link, MAX_ROW_BYTES))

    def expand(self) -> None:
        while self.pending:
            node = self.pending.popleft()
            table, rowid = node
            row = self.rows[node]
            if table == "raw_events":
                for sibling in self.db.execute(
                    "SELECT event_id FROM raw_events WHERE file_instance_id=? AND byte_start=? "
                    "AND byte_end=? ORDER BY event_id LIMIT ?",
                    (row["file_instance_id"], row["byte_start"], row["byte_end"], MAX_NODES + 1),
                ):
                    self.add("raw_events", sibling[0], node)
                if row["kind"] == "tool_result" and row["call_id"] is not None:
                    # 保存来源资格会核对关联调用；所有同身份候选都保留，不能猜唯一一个。
                    for call in self.db.execute(
                        "SELECT event_id FROM effective_raw_events WHERE session_pk="
                        "(SELECT session_pk FROM effective_raw_events WHERE event_id=?) "
                        "AND call_id=? AND kind IN ('tool_call','file_edit','plan_update') "
                        "ORDER BY event_id LIMIT ?",
                        (rowid, row["call_id"], MAX_NODES + 1),
                    ):
                        self.add("raw_events", call[0], node)
            for child, column, parent, key in self.links:
                if child == table:
                    self.find(parent, key, row[column], node)
                if parent == table:
                    # 没有子行是正常的；只有实际声明的父引用才要求存在。
                    for ref in self.db.execute(
                        f"SELECT rowid FROM {ident(child)} "
                        f"WHERE {ident(column)}=? ORDER BY rowid LIMIT ?",
                        (row.get(key, rowid), MAX_NODES + 1),
                    ):
                        self.add(child, ref[0], node)
            for link in JSON_LINKS:
                each, value, kind = json_sql(link, MAX_ROW_BYTES)
                if table == link.table:
                    for ref in self.db.execute(
                        f"SELECT {value} FROM {ident(table)} c,{each} "
                        f"WHERE c.rowid=? AND {kind}=? LIMIT ?",
                        (rowid, link.value_type, MAX_NODES + 1),
                    ):
                        self.find(link.parent, link.key, ref[0], node)
                if table == link.parent:
                    for ref in self.db.execute(
                        f"SELECT DISTINCT c.rowid "
                        f"FROM {ident(link.table)} c,{each} "
                        f"WHERE {kind}=? AND {value}=? ORDER BY c.rowid LIMIT ?",
                        (link.value_type, row[link.key], MAX_NODES + 1),
                    ):
                        self.add(link.table, ref[0], node)


def preview(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    optional = {"limit", "offset", "expected_plan_sha256"}
    if set(body) - FIELDS - optional:
        raise ValueError("依赖预检含未知字段")
    request = {key: value for key, value in body.items() if key in FIELDS}
    _validate(request)
    limit, offset = body.get("limit", 50), body.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("依赖预检分页参数无效")
    expected = body.get("expected_plan_sha256")
    if expected is not None and (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(c not in "0123456789abcdef" for c in expected)
    ):
        raise ValueError("依赖摘要必须为规范SHA256")
    if offset and expected is None:
        raise ValueError("依赖续页必须携带首轮依赖摘要")
    with store.snapshot():
        _, chosen = selection(store, request, max_records=MAX_NODES)
        return _preview(store, request, chosen, limit, offset, expected)


def _preview(
    store: Store,
    request: dict[str, Any],
    chosen: list[dict[str, Any]],
    limit: int,
    offset: int,
    expected: str | None,
) -> dict[str, Any]:
    graph = Closure(store.db)
    protected = True
    if not graph.issues:
        store.db.set_progress_handler(graph.interrupt, 1000)
        try:
            try:
                _protect_science(store, request["session"])
                protected = False
            except ConflictError:
                pass
            graph.check_json()
            for record in chosen:
                graph.add("raw_events", record["event_id"])
            graph.expand()
        except _Limit as error:
            graph.issues.add(str(error))
        except sqlite3.OperationalError as error:
            if str(error) != "interrupted":
                raise
            graph.issues.add("sql_step_budget")
        finally:
            store.db.set_progress_handler(None, 0)
    records = _records(graph, {r["event_id"] for r in chosen})
    complete = not graph.issues
    counts = Counter(table for table, _ in graph.nodes if table != "raw_events")
    plan = digest(
        dumps(
            {
                "policy": ["fragment-dependencies/v1", SCIENCE_SCHEMA_SHA256],
                "request": {k: v for k, v in request.items() if k not in {"request_id", "actor"}},
                "nodes": sorted((*node, sha) for node, sha in graph.nodes.items()),
                "edges": sorted(graph.edges),
                "issues": sorted(graph.issues),
                "protected": protected,
            }
        ).encode()
    )
    if expected is not None and expected != plan:
        raise ConflictError("科研依赖已变化，请重新预检")
    available = complete and not protected and not counts
    mode = "direct" if available else "dependency_migration" if complete else "unverifiable"
    return {
        "revision": store.revision(),
        "scope_key": request["expected_scope_key"],
        "snapshot": request["snapshot"],
        "observation_snapshot": request["observation_snapshot"],
        "plan_sha256": plan,
        "complete": complete,
        "closure_closed": complete and not records,
        "assignment_available": available,
        "write_mode": mode,
        "selected_records": len(chosen),
        "dependencies": dict(sorted(counts.items())),
        "shared_context": dict(sorted(Counter(key for key, _ in graph.context).items())),
        "required_records": records[offset : offset + limit],
        "required_records_total": len(records),
        "next_offset": offset + limit if offset + limit < len(records) else None,
        "issues": sorted(graph.issues),
        "migration_applied": False,
    }


def _records(graph: Closure, selected: set[int]) -> list[dict[str, Any]]:
    records: dict[tuple[int, int], dict[str, Any]] = {}
    for (table, _), row in graph.rows.items():
        if table != "raw_events":
            continue
        first = graph.db.execute(
            "SELECT r.event_id,r.session_pk,r.file_instance_id,r.byte_start,r.byte_end,"
            "s.project_id "
            "FROM raw_events r JOIN effective_raw_events e USING(event_id) "
            "JOIN sessions s ON s.session_pk=e.session_pk WHERE r.file_instance_id=? "
            "AND r.byte_start=? AND r.byte_end=? AND r.record_index=0",
            (row["file_instance_id"], row["byte_start"], row["byte_end"]),
        ).fetchone()
        if first is None:
            graph.issues.add("missing_physical_first_record")
        elif first["event_id"] not in selected:
            record = dict(first)
            record["first_event_id"] = record.pop("event_id")
            records[(first["file_instance_id"], first["byte_start"])] = record
    return [records[key] for key in sorted(records)]
