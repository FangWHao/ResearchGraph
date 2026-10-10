"""逐物理记录保存Claude父UUID和旁支标记，不改正文去重或原始事件。"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

import zstandard

from rg.ingest.parents import native_id
from rg.store.database import Store, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest

BACKFILL_BATCH = 256


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    identity, parent = native_id(record.get("uuid")), native_id(record.get("parentUuid"))
    marker = record.get("isSidechain")
    return {
        "native_uuid": identity,
        "parent_uuid": parent,
        "uuid_state": "valid" if identity else "invalid" if "uuid" in record else "missing",
        "parent_state": "missing"
        if "parentUuid" not in record
        else ("null" if record["parentUuid"] is None else "declared" if parent else "invalid"),
        "sidechain": int(marker) if type(marker) is bool else None,
        "sidechain_state": "declared"
        if type(marker) is bool
        else ("invalid" if "isSidechain" in record else "missing"),
    }


def observe(db: sqlite3.Connection, file_id: int, first_event: int, record: dict[str, Any]) -> None:
    rows = db.execute(
        "SELECT r.byte_start,r.byte_end,c.object_sha256,c.record_index FROM raw_events r "
        "JOIN raw_events c ON c.event_id=coalesce(r.alias_of,r.event_id) "
        "WHERE r.file_instance_id=? AND r.byte_start=(SELECT byte_start FROM raw_events "
        "WHERE event_id=?) ORDER BY r.record_index",
        (file_id, first_event),
    ).fetchall()
    if not rows:
        raise ValueError("Claude父链没有对应原始记录")
    value = metadata(record)
    # 仅以既有正文别名组成身份；父声明／旁支不同不抹掉，也不重新判定正文相等。
    body = digest(json.dumps([(r["object_sha256"], r["record_index"]) for r in rows]).encode())
    db.execute(
        "INSERT OR IGNORE INTO claude_chain_records(event_id,file_instance_id,byte_start,"
        "native_uuid,parent_uuid,uuid_state,parent_state,sidechain,sidechain_state,body_key,"
        "recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            first_event,
            file_id,
            rows[0]["byte_start"],
            value["native_uuid"],
            value["parent_uuid"],
            value["uuid_state"],
            value["parent_state"],
            value["sidechain"],
            value["sidechain_state"],
            body,
            now(),
        ),
    )
    db.execute(
        "UPDATE source_files SET chain_observed_offset=? WHERE file_instance_id=? "
        "AND chain_observed_offset=?",
        (rows[0]["byte_end"], file_id, rows[0]["byte_start"]),
    )


def backfill_file(store: Store, file_id: int) -> int:
    """每次最多补记256条已存原件；新尾部不越过旧缺口推进元数据游标。"""
    rows = store.db.execute(
        "SELECT r.event_id FROM raw_events r JOIN source_files f USING(file_instance_id) "
        "WHERE f.file_instance_id=? AND r.record_index=0 "
        "AND r.byte_start>=f.chain_observed_offset AND r.byte_end<=f.committed_offset "
        "ORDER BY r.byte_start LIMIT ?",
        (file_id, BACKFILL_BATCH),
    ).fetchall()
    if not rows:
        return 0
    with store.transaction() as db:
        for row in rows:
            raw = store.raw(row[0])
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeError):
                record = {}
            if not isinstance(record, dict):
                record = {}
            observe(db, file_id, row[0], record)
    return len(rows)


def backfill_saved(store: Store, *, skip_files: set[int] | None = None) -> dict[str, int]:
    """只查已存对象；逐来源加锁，原件消失也能补记，失败来源不阻塞其他来源。"""
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 22:
        return {}
    counts: Counter[str] = Counter()
    rows = store.db.execute(
        "SELECT file_instance_id,path FROM source_files WHERE parser='claude' "
        "AND chain_observed_offset<committed_offset ORDER BY file_instance_id"
    ).fetchall()
    for row in rows:
        if row["file_instance_id"] in (skip_files or set()):
            continue
        try:
            key = digest(row["path"].encode())
            with exclusive(store.root / "locks" / "sources" / (key + ".lock"), "来源正在扫描"):
                counts["chain_backfilled"] += backfill_file(store, row["file_instance_id"])
        except TaskBusy:
            counts["chain_busy"] += 1
        except (OSError, ValueError, RuntimeError, zstandard.ZstdError):
            counts["chain_errors"] += 1
    return dict(counts)
