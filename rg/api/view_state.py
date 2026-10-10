"""个人研究图视图：只保存显示参数，不写入研究状态。"""

from __future__ import annotations

import json
import math
from typing import Any

from rg.api.views import NotFound
from rg.clients.record import unique_pairs
from rg.query.reader import Reader
from rg.query.time import instant
from rg.store.database import ConflictError, Store, dumps, now

MAX_BYTES = 65536
MAX_INTEGER = 2**53 - 1


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= MAX_INTEGER


def _fields(value: Any, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("个人视图字段无效")


def _text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value


def _coordinate(value: Any) -> bool:
    return type(value) in {int, float} and abs(value) <= 10**7 and math.isfinite(value)


def _identity(store: Store, project: Any, user: Any) -> tuple[str, str]:
    if not _text(project, 100) or not _text(user, 100) or not user.strip():
        raise ValueError("需指定项目及有效的本机个人视图标识")
    if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
        raise NotFound("项目不存在")
    return project, user.strip()


def _shape(view: Any, project: str) -> dict[str, Any]:
    _fields(
        view,
        {
            "format_version",
            "reading",
            "show_candidates",
            "positions",
            "viewport",
            "selected_versions",
            "process_group",
        },
    )
    if type(view["format_version"]) is not int or view["format_version"] != 1:
        raise ValueError("个人视图格式版本不可用")
    reading = view["reading"]
    _fields(reading, {"project_id", "revision", "occurred_until", "known_until"})
    if reading["project_id"] != project or not _integer(reading["revision"]):
        raise ValueError("个人视图项目或修订无效")
    for key in ("occurred_until", "known_until"):
        if not isinstance(reading[key], str) or instant(reading[key]) is None:
            raise ValueError("个人视图双截止须为带时区的时间")
    if type(view["show_candidates"]) is not bool:
        raise ValueError("个人视图候选显示标记无效")
    _fields(view["viewport"], {"x", "y", "zoom"})
    viewport = view["viewport"]
    if not all(_coordinate(viewport[k]) for k in ("x", "y", "zoom")) or not (
        0.12 <= viewport["zoom"] <= 1.5
    ):
        raise ValueError("个人视图阅读视角无效")
    for field in ("positions", "selected_versions"):
        if not isinstance(view[field], dict) or any(not _text(k, 4096) for k in view[field]):
            raise ValueError("个人视图节点标识无效")
    for position in view["positions"].values():
        _fields(position, {"x", "y"})
        if not all(_coordinate(position[k]) for k in ("x", "y")):
            raise ValueError("个人视图节点位置无效")
    if any(not _integer(v, 1) for v in view["selected_versions"].values()):
        raise ValueError("个人视图显示版本无效")
    group = view["process_group"]
    if group is not None:
        _fields(group, {"name", "members"})
        members = group["members"]
        if (
            not _text(group["name"], 60)
            or not group["name"].strip()
            or not isinstance(members, list)
            or len(members) < 2
            or any(not _text(m, 4096) for m in members)
            or len(set(members)) != len(members)
            or not view["show_candidates"]
        ):
            raise ValueError("个人过程组成员或名称无效")
    return view


def _node(identity: str) -> tuple[str, str]:
    entity, separator, text = identity.partition("::")
    if not separator or not entity:
        raise ValueError("个人视图节点标识无效")
    if text == "unknown":
        return entity, dumps(None)
    try:
        pairs = json.loads(text, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as error:
        raise ValueError("个人视图节点范围无效") from error
    if (
        not isinstance(pairs, list)
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(v, str) for v in pair)
            for pair in pairs
        )
        or len({pair[0] for pair in pairs}) != len(pairs)
    ):
        raise ValueError("个人视图节点范围无效")
    # 前端按本地字符串排序；校核范围内容，不依赖中英文排序方式。
    return entity, dumps(dict(pairs))


def _visible(store: Store, project: str, view: dict[str, Any]) -> None:
    reading = view["reading"]
    if reading["revision"] != store.revision():
        raise ConflictError("研究图已变化，个人视图未保存；请主动重新读取")
    reader = Reader(store, project, {k: reading[k] for k in ("occurred_until", "known_until")})
    metadata = reader.metadata()
    view["reading"] = {key: metadata[key] for key in reading}
    eligible: dict[tuple[str, str], set[int]] = {}
    for claim in reader.claims():
        if (
            claim["claim_type"] == "entity_version"
            and not claim["replaced"]
            and claim["effective_state"] != "dismissed"
            and (view["show_candidates"] or claim["effective_state"] == "confirmed")
        ):
            eligible.setdefault((claim["entity_id"], dumps(claim["scope"])), set()).add(
                claim["claim_id"]
            )
    group = view["process_group"]
    for identities in (
        list(view["positions"]),
        list(view["selected_versions"]),
        group["members"] if group else [],
    ):
        normalized = [_node(i) for i in identities]
        if len(normalized) != len(set(normalized)) or any(n not in eligible for n in normalized):
            raise ValueError("个人视图引用了不可见、重复或其它项目的节点")
    for identity, version in view["selected_versions"].items():
        if version not in eligible[_node(identity)]:
            raise ValueError("显示版本不属于该项目及范围的可见节点")


def _latest(store: Store, project: str, user: str) -> Any:
    return store.db.execute(
        "SELECT * FROM view_states WHERE project_id=? AND user=? ORDER BY view_id DESC LIMIT 1",
        (project, user),
    ).fetchone()


def _result(store: Store, project: str, user: str, row: Any) -> dict[str, Any]:
    view, reason = None, None
    if row is not None:
        try:
            if not isinstance(row["payload"], str) or len(row["payload"].encode()) > MAX_BYTES:
                raise ValueError("旧个人视图不可用")
            view = _shape(json.loads(row["payload"], object_pairs_hook=unique_pairs), project)
            if view["reading"]["revision"] != row["graph_revision"]:
                raise ValueError("旧个人视图修订矛盾")
        except (ValueError, UnicodeError, TypeError, OverflowError):
            view, reason = None, "已保存的个人视图格式不可用；可保存当前视图替代它"
    return {
        "project_id": project,
        "user": user,
        "view_id": row["view_id"] if row else None,
        "saved_at": row["updated_at"] if row else None,
        "current_revision": store.revision(),
        "view": view,
        "unavailable_reason": reason,
    }


def read(store: Store, values: dict[str, Any]) -> dict[str, Any]:
    _fields(values, {"project", "user"})
    with store.snapshot():
        project, user = _identity(store, values["project"], values["user"])
        return _result(store, project, user, _latest(store, project, user))


def save(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    _fields(body, {"project_id", "user", "expected_view_id", "view"})
    expected = body["expected_view_id"]
    if expected is not None and not _integer(expected, 1):
        raise ValueError("个人视图保存身份无效")
    try:
        size = len(dumps(body).encode())
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("个人视图不能保存为JSON") from error
    if size > MAX_BYTES:
        raise ValueError("个人视图请求超过65536字节，未保存或截断")
    with store.transaction():
        project, user = _identity(store, body["project_id"], body["user"])
        view = _shape(body["view"], project).copy()
        row = _latest(store, project, user)
        if (row["view_id"] if row else None) != expected:
            raise ConflictError("个人视图已被其它页面保存；请重新读取，未覆盖它")
        _visible(store, project, view)
        store.db.execute(
            "INSERT INTO view_states(user,project_id,payload,graph_revision,updated_at) "
            "VALUES (?,?,?,?,?)",
            (user, project, dumps(view), view["reading"]["revision"], now()),
        )
        return _result(store, project, user, _latest(store, project, user))
