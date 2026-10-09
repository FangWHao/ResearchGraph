from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from rg.derive.edits import edit
from rg.derive.records import Event, block, calls, event
from rg.derive.runtime import EXEC_TOOLS, POLL_TOOLS, apply_result, request
from rg.ingest.common import RG_BLOCK
from rg.store.database import Store, dumps, now
from rg.store.locking import exclusive


def derive(
    store: Store,
    limit: int = 100,
    session: int | None = None,
    retry_failed: bool = False,
    fault: Callable[[], None] | None = None,
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise ValueError("L1 单次任务上限为 1 到 1000")
    with exclusive(store.root / "locks" / "derive.lock", "L1 派生任务正在运行"):
        return _derive(store, limit, session, retry_failed, fault)


def _derive(
    store: Store,
    limit: int,
    session: int | None,
    retry_failed: bool,
    fault: Callable[[], None] | None,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    with store.transaction() as db:
        db.execute(
            "INSERT OR IGNORE INTO l1_derivations "
            "SELECT event_id,'queued',NULL,? FROM raw_events "
            "WHERE alias_of IS NULL AND ((kind IN ('tool_call','file_edit','tool_result') "
            "AND exclude_reason IS NULL) OR "
            "(kind='meta' AND tool_name='exec_command' AND exclude_reason='execution_metadata'))",
            (now(),),
        )
        if retry_failed:
            db.execute(
                "UPDATE l1_derivations SET state='queued',error=NULL WHERE state='failed' "
                "AND (? IS NULL OR event_id IN "
                "(SELECT event_id FROM raw_events WHERE session_pk=?))",
                (session, session),
            )
    rows = store.db.execute(
        "SELECT d.event_id FROM l1_derivations d JOIN raw_events r USING(event_id) "
        "WHERE d.state IN ('queued','waiting') AND (? IS NULL OR r.session_pk=?) "
        "ORDER BY d.updated_at,d.event_id LIMIT ?",
        (session, session, limit),
    ).fetchall()
    for row in rows:
        try:
            with store.transaction() as db:
                value = event(store, row[0])
                state, reason = process(store, value)
                if fault:
                    fault()
                db.execute(
                    "UPDATE l1_derivations SET state=?,error=?,updated_at=? WHERE event_id=?",
                    (state, reason, now(), row[0]),
                )
            counts[f"l1_{state}"] += 1
        except (OSError, ValueError, UnicodeError) as error:
            with store.transaction() as db:
                db.execute(
                    "UPDATE l1_derivations SET state='failed',error=?,updated_at=? "
                    "WHERE event_id=?",
                    (type(error).__name__, now(), row[0]),
                )
            counts["l1_failed"] += 1
    counts["l1_remaining"] = store.db.execute(
        "SELECT count(*) FROM l1_derivations d JOIN raw_events r USING(event_id) "
        "WHERE d.state IN ('queued','waiting') AND (? IS NULL OR r.session_pk=?)",
        (session, session),
    ).fetchone()[0]
    return dict(counts)


def process(store: Store, value: Event) -> tuple[str, str | None]:
    if not value["project_id"]:
        return "waiting", "project_unassigned"
    if contaminated(value):
        return "done", "rg_context_in_tool_payload"
    if value["kind"] in {"tool_call", "file_edit", "meta"}:
        request(store, value)
        return "done", None
    matched = calls(store, value)
    if not matched:
        return "waiting", "call_not_recorded"
    if len(matched) > 1:
        return "failed", "ambiguous_call_id"
    call = matched[0]
    if contaminated(call):
        return "done", "rg_context_in_tool_payload"
    if call["kind"] == "file_edit":
        edit(store, call, value)
    elif call["tool_name"] in EXEC_TOOLS | POLL_TOOLS:
        reason = apply_result(store, call, value)
        if reason:
            return "waiting", reason
    else:
        return "done", "unsupported_tool_fact"
    return "done", None


def contaminated(value: Event) -> bool:
    return bool(
        RG_BLOCK.search(dumps(block(value)))
        or RG_BLOCK.search(dumps(value["record"].get("toolUseResult")))
    )
