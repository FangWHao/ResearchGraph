"""显式查询当前归属和指定时间的归属；调用者自行选择历史时间。"""

from __future__ import annotations

import sqlite3

from rg.store.ownership_schema import OWNED_TABLES


def read_table(db: sqlite3.Connection, table: str) -> str:
    if table not in (*OWNED_TABLES, "source_roots"):
        raise ValueError("归属表不在固定合同中")
    return "effective_" + table if db.execute("PRAGMA user_version").fetchone()[0] >= 33 else table


def projects_table(db: sqlite3.Connection) -> str:
    return (
        "active_projects" if db.execute("PRAGMA user_version").fetchone()[0] >= 33 else "projects"
    )


def record_owner(
    db: sqlite3.Connection, table: str, key: str, value: str | int, at: str | None = None
) -> str | None:
    if table not in OWNED_TABLES or key not in {
        r[1] for r in db.execute("SELECT * FROM pragma_table_info(?)", (table,))
    }:
        raise ValueError("归属标识不在固定列合同中")
    row = db.execute(f'SELECT rowid FROM {table} WHERE "{key}"=?', (value,)).fetchone()
    return owner_at(db, table, row[0], at) if row else None


def relation(db: sqlite3.Connection, table: str) -> str:
    """清除使用物理行号及当前归属；名称只来自内部固定合同。"""
    if table not in OWNED_TABLES:
        return '"' + table.replace('"', '""') + '"'
    keys = [r[1] for r in db.execute("SELECT * FROM pragma_table_info(?)", (table,)) if r[5]]
    join = " AND ".join(f'r."{key}"=e."{key}"' for key in keys)
    return f"(SELECT r.rowid AS rowid,e.* FROM {table} r JOIN effective_{table} e ON {join})"


def owner_at(db: sqlite3.Connection, table: str, rowid: int, at: str | None = None) -> str | None:
    if table not in OWNED_TABLES:
        raise ValueError("归属表不在固定合同中")
    result = (
        db.execute(
            "SELECT project_id FROM science_ownership WHERE table_name=? AND record_rowid=? "
            "AND (? IS NULL OR recorded_at<=?) ORDER BY ownership_id DESC LIMIT 1",
            (table, rowid, at, at),
        ).fetchone()
        if db.execute("PRAGMA user_version").fetchone()[0] >= 33
        else None
    )
    if result is not None:
        return result[0]
    columns = {r[1] for r in db.execute("SELECT * FROM pragma_table_info(?)", (table,))}
    if "project_id" not in columns:
        return None
    row = db.execute(f"SELECT project_id FROM {table} WHERE rowid=?", (rowid,)).fetchone()
    return row[0] if row else None


def entity_owner(db: sqlite3.Connection, entity: str, at: str | None = None) -> str | None:
    row = db.execute("SELECT rowid FROM entities WHERE entity_id=?", (entity,)).fetchone()
    return owner_at(db, "entities", row[0], at) if row else None


def event_owner(db: sqlite3.Connection, event: int, at: str | None = None) -> str | None:
    current = db.execute(
        "SELECT s.project_id FROM effective_raw_events r JOIN "
        "sessions s USING(session_pk) WHERE r.event_id=?",
        (event,),
    ).fetchone()
    if db.execute("PRAGMA user_version").fetchone()[0] < 33:
        return current[0] if current else None
    first = db.execute(
        "SELECT f.event_id FROM raw_events r JOIN raw_events f ON "
        "f.file_instance_id=r.file_instance_id AND f.byte_start=r.byte_start "
        "AND f.record_index=0 WHERE r.event_id=?",
        (event,),
    ).fetchone()
    if first is None:
        row = db.execute(
            "SELECT s.project_id FROM effective_raw_events r JOIN "
            "sessions s USING(session_pk) WHERE r.event_id=?",
            (event,),
        ).fetchone()
        return row[0] if row else None
    rows = db.execute(
        "SELECT * FROM source_project_history WHERE first_event_id=? ORDER BY history_id",
        (first[0],),
    ).fetchall()
    if rows:
        visible = [row for row in rows if at is None or row["recorded_at"] <= at]
        return visible[-1]["project_id"] if visible else rows[0]["previous_project_id"]
    row = db.execute(
        "SELECT s.project_id FROM effective_raw_events r JOIN sessions s USING(session_pk) "
        "WHERE r.event_id=?",
        (event,),
    ).fetchone()
    return row[0] if row else None


def event_session(db: sqlite3.Connection, event: int, at: str) -> int | None:
    rows = db.execute(
        "SELECT h.* FROM source_project_history h JOIN raw_events f ON f.event_id=h.first_event_id "
        "JOIN raw_events r ON r.file_instance_id=f.file_instance_id AND r.byte_start=f.byte_start "
        "WHERE r.event_id=? ORDER BY h.history_id",
        (event,),
    ).fetchall()
    if rows:
        visible = [row for row in rows if row["recorded_at"] <= at]
        return visible[-1]["session_pk"] if visible else rows[0]["previous_session_pk"]
    row = db.execute(
        "SELECT session_pk FROM effective_raw_events WHERE event_id=?", (event,)
    ).fetchone()
    return row[0] if row else None


def historical_source(db: sqlite3.Connection, row: dict, at: str) -> dict:
    if db.execute("PRAGMA user_version").fetchone()[0] < 33:
        return dict(row)
    result = dict(row)
    result["session_pk"] = event_session(db, row["event_id"], at)
    physical = db.execute(
        "SELECT session_pk,alias_of,exclude_reason FROM raw_events WHERE event_id=?",
        (row["event_id"],),
    ).fetchone()
    if physical is None:
        return result
    alias = physical["alias_of"]
    canonical = (
        db.execute("SELECT session_pk FROM raw_events WHERE event_id=?", (alias,)).fetchone()
        if alias
        else None
    )
    same = alias is not None and (
        event_session(db, alias, at) == result["session_pk"]
        or (
            result["session_pk"] == physical["session_pk"]
            and canonical
            and event_session(db, alias, at) == canonical[0]
        )
    )
    result["alias_of"] = alias if same else None
    result["exclude_reason"] = (
        None
        if alias and not same and physical["exclude_reason"] == "duplicate"
        else physical["exclude_reason"]
    )
    if "duplicate" in result:
        link = db.execute(
            "SELECT canonical_id FROM dedupe_links WHERE alias_id=?", (row["event_id"],)
        ).fetchone()
        result["duplicate"] = (
            link[0] if link and event_session(db, link[0], at) == result["session_pk"] else None
        )
    return result


def snapshot_storage(db: sqlite3.Connection, snapshot: int) -> str:
    row = db.execute(
        "SELECT project_id FROM workspace_snapshots WHERE snapshot_id=?", (snapshot,)
    ).fetchone()
    if row is None:
        raise ValueError("快照不存在")
    return row[0]
