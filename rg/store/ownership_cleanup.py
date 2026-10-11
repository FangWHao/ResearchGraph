"""隐私事务中回收已经没有保留引用的来源身份；不复用历史编号。"""

from __future__ import annotations

import sqlite3

from rg.store.clear_selection import TABLES, identifier


def cleanup(db: sqlite3.Connection) -> None:
    foreign = [
        (t, r[3], r[2], r[4])
        for t in TABLES
        for r in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (t,))
    ]
    foreign.append(("workspace_snapshots", "root_id", "source_roots", "root_id"))
    while True:
        before = db.total_changes
        for table, key in (
            ("source_roots", "root_id"),
            ("sessions", "session_pk"),
            ("projects", "project_id"),
        ):
            references = []
            for child, column, parent, parent_key in foreign:
                if parent != table or child == "project_tombstones":
                    continue
                references.append(
                    f"NOT EXISTS (SELECT 1 FROM {identifier(child)} c WHERE "
                    f"c.{identifier(column)}=p.{identifier(parent_key or key)})"
                )
            condition = " AND ".join(references) or "1"
            for (identity,) in db.execute(
                f"SELECT p.{key} FROM {table} p WHERE p.project_id IN "
                f"(SELECT project_id FROM project_tombstones) AND {condition}"
            ).fetchall():
                if table == "projects":
                    db.execute("DELETE FROM project_tombstones WHERE project_id=?", (identity,))
                db.execute(f"DELETE FROM {table} WHERE {key}=?", (identity,))
        if before == db.total_changes:
            break
