"""按明确开启的项目策略发现 Git 工作树；不猜会话归属，不读取研究文件。"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from rg.snapshot.git import Git
from rg.store import roots
from rg.store.database import ConflictError, Store, dumps, now
from rg.store.locking import exclusive
from rg.store.objects import digest

MAX_LIST_BYTES = 1024 * 1024
MAX_WORKTREES = 512
PAGE_SIZE = 16


def enabled(store: Store, project: str) -> bool:
    row = store.db.execute(
        "SELECT enabled FROM worktree_follow_actions WHERE project_id=? "
        "ORDER BY action_id DESC LIMIT 1",
        (project,),
    ).fetchone()
    return bool(row[0]) if row else False


def configure(store: Store, project: str, follow: bool, expected: int) -> dict[str, Any]:
    if not isinstance(project, str) or not project:
        raise ValueError("项目参数非法")
    if type(follow) is not bool or type(expected) is not int or expected < 0:
        raise ValueError("跟踪开关及修订参数非法")
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
            raise ValueError("项目不存在")
        if store.revision() != expected:
            raise ConflictError("记录已变化，请重新读取项目工作树")
        if enabled(store, project) != follow:
            db.execute(
                "INSERT INTO worktree_follow_actions(project_id,enabled,recorded_at) "
                "VALUES (?,?,?)",
                (project, int(follow), now()),
            )
        return {"project_id": project, "follow_enabled": follow, "revision": store.revision()}


def listing(
    store: Store,
    project: str,
    limit: int = 100,
    offset: int = 0,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 200 or offset < 0:
        raise ValueError("工作树分页参数非法")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValueError("工作树修订参数非法")
    with store.snapshot():
        if not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
            raise ValueError("项目不存在")
        revision = store.revision()
        if expected_revision is not None and expected_revision != revision:
            raise ConflictError("工作树记录已变化，请重新读取第一页")
        policy = store.db.execute(
            "SELECT * FROM worktree_follow_actions WHERE project_id=? "
            "ORDER BY action_id DESC LIMIT 1",
            (project,),
        ).fetchone()
        total = store.db.execute(
            "SELECT count(*) FROM source_roots WHERE project_id=? AND kind IN ('repo','worktree')",
            (project,),
        ).fetchone()[0]
        rows = store.db.execute(
            "SELECT * FROM source_roots WHERE project_id=? "
            "AND kind IN ('repo','worktree') ORDER BY root_id LIMIT ? OFFSET ?",
            (project, limit, offset),
        ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["git_metadata"] = json.loads(value["git_metadata"]) or {"status": "not_recorded"}
            observed = store.db.execute(
                "SELECT * FROM worktree_discovery_observations "
                "WHERE root_id=? ORDER BY observation_id DESC LIMIT 1",
                (row["root_id"],),
            ).fetchone()
            value["discovery"] = (
                (dict(observed) | {"details": json.loads(observed["details"])})
                if observed
                else {"status": "not_observed"}
            )
            values.append(value)
        return {
            "project_id": project,
            "revision": revision,
            "follow_enabled": bool(policy["enabled"]) if policy else False,
            "policy": dict(policy) if policy else None,
            "roots": values,
            "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }


def parse_porcelain(raw: bytes) -> list[dict[str, str]]:
    if not raw or len(raw) > MAX_LIST_BYTES or not raw.endswith(b"\0\0"):
        raise ValueError("工作树清单为空、超限或未完整结束")
    result = []
    for record in raw[:-2].split(b"\0\0"):
        item: dict[str, str] = {}
        for field in record.split(b"\0"):
            key, _, value = field.partition(b" ")
            name = key.decode("ascii")
            if (
                name not in {"worktree", "HEAD", "branch", "bare", "detached", "locked", "prunable"}
                or name in item
            ):
                raise ValueError("工作树清单字段未知或重复")
            if not item and name != "worktree":
                raise ValueError("工作树清单首字段非法")
            item[name] = value.decode("utf-8")
        path = item.get("worktree", "")
        if not path or len(path.encode()) > 16384 or not Path(path).is_absolute():
            raise ValueError("工作树路径非法")
        if item.get("bare") != "" and "bare" in item:
            raise ValueError("裸仓库标记非法")
        if "detached" in item and item["detached"]:
            raise ValueError("脱离分支标记非法")
        if "bare" not in item and not re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", item.get("HEAD", "")
        ):
            raise ValueError("工作树 HEAD 标记非法")
        result.append(item)
        if len(result) > MAX_WORKTREES:
            raise ValueError("工作树清单条数超限")
    if len({item["worktree"] for item in result}) != len(result):
        raise ValueError("工作树路径重复")
    return result


def _unique(store: Store, row: dict[str, Any]) -> bool:
    projects = {
        r[0]
        for r in store.db.execute(
            "SELECT DISTINCT project_id FROM source_roots "
            "WHERE host_id='local' AND git_common_dir=?",
            (row["git_common_dir"],),
        )
    }
    return projects == {row["project_id"]}


def _observe(
    store: Store, row: dict[str, Any], status: str, details: dict[str, Any], sha: str | None = None
) -> None:
    encoded = dumps(details)
    last = store.db.execute(
        "SELECT status,details,input_sha256 FROM worktree_discovery_observations "
        "WHERE root_id=? ORDER BY observation_id DESC LIMIT 1",
        (row["root_id"],),
    ).fetchone()
    if last and tuple(last) == (status, encoded, sha):
        return
    store.db.execute(
        "INSERT INTO worktree_discovery_observations(project_id,root_id,status,"
        "details,input_sha256,recorded_at) VALUES (?,?,?,?,?,?)",
        (row["project_id"], row["root_id"], status, encoded, sha, now()),
    )


def _probe(
    store: Store, row: dict[str, Any], deadline: float
) -> tuple[str, str | None, list[dict[str, str]]]:
    if not _unique(store, row):
        return "ambiguous", None, []
    path = Path(row["path"])
    if path.resolve() != path or not path.is_dir():
        return "missing_or_changed_path", None, []
    metadata = roots._git_identity(path, deadline)
    if metadata.get("status") != "registered":
        return "unavailable", None, []
    if metadata.get("git_common_dir") != row["git_common_dir"] or metadata.get("worktree") != str(
        path
    ):
        return "changed_identity", None, []
    raw = Git(path, deadline).run("worktree", "list", "--porcelain", "-z")
    sha = digest(raw)
    try:
        items = parse_porcelain(raw)
    except (ValueError, UnicodeError):
        return "invalid_or_oversized_list", sha, []
    return "observed", sha, items


def _path_state(store: Store, row: dict[str, Any], path: Path) -> str | None:
    from rg.store.clear_denials import denied

    if denied(store.root, project=row["project_id"], paths=[str(path)]):
        return "privacy_blocked"
    known = store.db.execute(
        "SELECT project_id FROM source_roots WHERE host_id='local' AND path=?", (str(path),)
    ).fetchone()
    if known:
        return "known" if known[0] == row["project_id"] else "conflict"
    for other in store.db.execute(
        "SELECT path FROM source_roots WHERE host_id='local' AND project_id!=?",
        (row["project_id"],),
    ):
        registered = Path(other[0])
        if path.is_relative_to(registered) or registered.is_relative_to(path):
            return "conflict"
    return None


def _prepare(
    store: Store, row: dict[str, Any], item: dict[str, str], deadline: float
) -> tuple[str, dict | None]:
    if "bare" in item or "prunable" in item:
        return "unavailable", None
    path = Path(item["worktree"])
    if not path.is_dir() or path.resolve() != path:
        return "unavailable", None
    state = _path_state(store, row, path)
    if state:
        return state, None
    value = roots.prepare(store, path, deadline=deadline)
    metadata = json.loads(value["git_metadata"])
    if metadata.get("status") != "registered":
        return "unavailable", None
    if metadata.get("git_common_dir") != row["git_common_dir"] or metadata.get("worktree") != str(
        path
    ):
        return "changed_identity", None
    metadata.update({"discovered_from": row["root_id"], "discovery_basis": "unique_git_common_dir"})
    value["git_metadata"] = dumps(metadata)
    return "ready", value


def _discover_root(store: Store, row: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    deadline = time.monotonic() + 2
    status, sha, items = _probe(store, row, deadline)
    if status != "observed":
        with store.transaction():
            _observe(store, row, status, {"created": 0, "list_complete": False}, sha)
        counts["worktrees_held"] += 1
        return counts
    membership_sha = digest(
        dumps(
            sorted((item["worktree"], "bare" in item, "prunable" in item) for item in items)
        ).encode()
    )
    progress = store.db.execute(
        "SELECT * FROM worktree_discovery_progress WHERE root_id=?", (row["root_id"],)
    ).fetchone()
    after = progress["after_path"] if progress and progress["list_sha256"] == membership_sha else ""
    ordered = sorted((digest(item["worktree"].encode()), item) for item in items)
    remaining = [(key, item) for key, item in ordered if key > after]
    if not remaining:
        remaining = ordered
    page = remaining[:PAGE_SIZE]
    values = []
    details: Counter[str] = Counter()
    for _, item in page:
        try:
            state, value = _prepare(store, row, item, deadline)
        except (OSError, ValueError, RuntimeError):
            state, value = "unavailable", None
        details[state] += 1
        if value:
            values.append(value)
    with store.transaction() as db:
        if not enabled(store, row["project_id"]) or not _unique(store, row):
            _observe(
                store,
                row,
                "policy_or_identity_changed",
                {"created": 0, "list_complete": False},
                sha,
            )
            return Counter({"worktrees_held": 1})
        for value in values:
            # Git 探测发生在事务外；提交前重核期间新增的交叉根与清除屏障。
            state = _path_state(store, row, Path(value["path"]))
            if state:
                details["ready"] -= 1
                details[state] += 1
                continue
            roots.insert(db, row["project_id"], value)
            db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
            counts["worktrees_created"] += 1
        complete = len(remaining) <= PAGE_SIZE
        next_key = "" if complete else page[-1][0]
        db.execute(
            "INSERT INTO worktree_discovery_progress VALUES (?,?,?,?) ON CONFLICT(root_id) "
            "DO UPDATE SET list_sha256=excluded.list_sha256,after_path=excluded.after_path",
            (row["root_id"], row["project_id"], membership_sha, next_key),
        )
        description = dict(details) | {
            "created": counts["worktrees_created"],
            "list_total": len(items),
            "page_count": len(page),
            "list_complete": complete,
        }
        _observe(store, row, "observed" if complete else "partial", description, sha)
    counts["worktrees_probed"] += 1
    return counts


def discover(store: Store, project: str | None = None, limit: int = 4) -> dict[str, int]:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("工作树发现批量需为1至20")
    if (
        project is not None
        and not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()
    ):
        raise ValueError("项目不存在")
    with exclusive(store.root / "locks" / "worktrees.lock", "工作树发现正在运行"):
        cursor_key = project or "global"
        cursor = store.db.execute(
            "SELECT last_root FROM worktree_discovery_cursors WHERE cursor_key=?", (cursor_key,)
        ).fetchone()
        position = cursor["last_root"] if cursor else 0
        sql = (
            "SELECT r.rowid AS root_rowid,r.* FROM source_roots r "
            "WHERE r.host_id='local' AND r.kind IN ('repo','worktree') "
            "AND r.git_common_dir IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM worktree_follow_actions a WHERE a.project_id=r.project_id "
            "AND a.action_id=(SELECT max(x.action_id) FROM worktree_follow_actions x "
            "WHERE x.project_id=r.project_id) AND a.enabled=1) "
            "AND (? IS NULL OR r.project_id=?) AND r.rowid>? ORDER BY r.rowid LIMIT ?"
        )
        rows = store.db.execute(sql, (project, project, position, limit)).fetchall()
        if not rows and position:
            rows = store.db.execute(sql, (project, project, 0, limit)).fetchall()
        counts: Counter[str] = Counter()
        for row in rows:
            try:
                counts.update(_discover_root(store, dict(row)))
            except (OSError, ValueError, RuntimeError):
                with store.transaction():
                    _observe(
                        store, dict(row), "unavailable", {"created": 0, "list_complete": False}
                    )
                counts["worktrees_held"] += 1
        if rows:
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO worktree_discovery_cursors VALUES (?,?,?) ON CONFLICT(cursor_key) "
                    "DO UPDATE SET last_root=excluded.last_root",
                    (cursor_key, project, rows[-1]["root_rowid"]),
                )
        if rows:
            # 新根登记与快照清单发布可中断；每轮再核对，失败不会永久漏掉新根。
            from rg.snapshot.config import export_registry

            export_registry(store)
        return dict(counts)
