"""只读查看即时父线程关系及其头记录依据，不跨项目公开父线程内容。"""

from __future__ import annotations

from typing import Any

from rg.ingest.parents import native_id, resolve
from rg.store.database import Store


def query(store: Store, values: dict[str, str]) -> dict[str, Any]:
    if set(values) - {"session", "project"} or "session" not in values:
        raise ValueError("父线程查询需要 session，可选 project")
    try:
        pk = int(values["session"])
    except ValueError as exc:
        raise ValueError("会话编号必须为正整数") from exc
    if not 0 < pk <= 9223372036854775807:
        raise ValueError("会话编号超出范围")
    with store.snapshot():
        session = store.db.execute("SELECT * FROM sessions WHERE session_pk=?", (pk,)).fetchone()
        if session is None or ("project" in values and values["project"] != session["project_id"]):
            raise ValueError("会话不存在或不属于指定项目")
        view: dict[str, Any] = {
            "session_pk": pk,
            "tool": session["tool"],
            "native_id": native_id(session["native_session_id"]),
            "state": "unsupported",
            "parent_session_pk": None,
            "parent_native_id": None,
            "parent_event_id": None,
            "observations": [],
            "observations_total": 0,
            "observations_partial": False,
            "observation_highwater": 0,
            "source_metadata_complete": None,
            "identity_metadata_complete": None,
        }
        if session["tool"] != "codex":
            return view
        relations, sessions = resolve(store.db)
        relation = relations[pk]
        rows = relation["rows"]
        view.update(
            state=relation["state"],
            observations_total=len(rows),
            observations_partial=len(rows) > 20,
            observation_highwater=max((r["event_id"] for r in rows), default=0),
            source_metadata_complete=relation["source_metadata_complete"],
            identity_metadata_complete=relation["identity_metadata_complete"],
        )
        view["observations"] = [
            {
                k: row[k]
                for k in (
                    "event_id",
                    "state",
                    "basis",
                    "reason",
                    "native_id",
                    "parent_id",
                    "other_parent_id",
                    "recorded_at",
                )
            }
            for row in rows[-20:]
        ]
        parent = relation["parent_session_pk"]
        if parent is not None:
            if sessions[parent]["project_id"] != session["project_id"]:
                view["state"] = "outside_project"
            else:
                view.update(
                    parent_session_pk=parent,
                    parent_native_id=native_id(sessions[parent]["native_session_id"]),
                )
                view["parent_event_id"] = next(
                    (
                        r["event_id"]
                        for r in relations[parent]["rows"]
                        if r["native_id"] == view["parent_native_id"]
                    ),
                    None,
                )
        return view
