from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from rg.store.database import ConflictError, Store, dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest

ACTIVE_SENDS: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("privacy_sends", default=())


def patterns(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > 32
        or any(not isinstance(p, str) or not p or len(p) > 1000 for p in value)
    ):
        raise ValueError("遮盖正则需为最多 32 项非空字符串，每项不超过 1000 字符")
    try:
        for pattern in value:
            re.compile(pattern)
    except re.error:
        raise ValueError("遮盖正则无效") from None
    return tuple(value)


@dataclass(frozen=True)
class Policy:
    project: str
    rule_id: int
    patterns: tuple[str, ...]

    @property
    def identity(self) -> str:
        return digest(
            dumps(["project-privacy1", self.project, self.rule_id, self.patterns]).encode()
        )

    def metadata(self, store: Store) -> dict[str, Any]:
        return {
            "project_id": self.project,
            "revision": store.revision(),
            "rule_id": self.rule_id,
            "policy_id": self.identity,
            "patterns": list(self.patterns),
            "builtin_masking": True,
            "raw_unchanged": True,
        }

    def check(self, store: Store, remote: bool = False) -> None:
        if read(store, self.project) != self:
            raise ConflictError("项目遮盖规则已变化，请使用新配置重新准备输入")
        if (
            remote
            and not store.db.execute(
                "SELECT remote_model_allowed FROM projects WHERE project_id=?", (self.project,)
            ).fetchone()[0]
        ):
            raise PermissionError("本项目未允许远程模型，计数请求也不会发送")

    @contextmanager
    def sending(self, store: Store, remote: bool = False) -> Iterator[None]:
        identity = (str(store.root.resolve()), self.project)
        if identity in ACTIVE_SENDS.get():
            self.check(store, remote)
            yield
            self.check(store, remote)
            return
        with gate(store, self.project, shared=True):
            self.check(store, remote)
            token = ACTIVE_SENDS.set((*ACTIVE_SENDS.get(), identity))
            try:
                yield
                # 直接数据库写入也不能使旧配置的结果继续发布。
                self.check(store, remote)
            finally:
                ACTIVE_SENDS.reset(token)


def read(store: Store, project: str) -> Policy:
    if (
        not isinstance(project, str)
        or not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()
    ):
        raise ValueError("项目不存在")
    row = store.db.execute(
        "SELECT rule_id,patterns FROM project_privacy WHERE project_id=? ORDER BY rule_id DESC "
        "LIMIT 1",
        (project,),
    ).fetchone()
    return Policy(project, row[0] if row else 0, patterns(json.loads(row[1])) if row else ())


@contextmanager
def gate(store: Store, project: str, *, shared: bool = False) -> Iterator[None]:
    try:
        with exclusive(
            store.root / "locks" / "privacy" / (digest(project.encode()) + ".lock"),
            "本项目正在发送或修改配置，请稍后重试",
            shared=shared,
        ):
            yield
    except TaskBusy as error:
        raise ConflictError(str(error)) from None


def update(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    if set(body) != {"project_id", "patterns", "actor", "expected_revision"}:
        raise ValueError("项目遮盖配置仅接受项目、正则、人工身份及记录版本")
    configured = patterns(body["patterns"])
    project, actor, expected = body["project_id"], body["actor"], body["expected_revision"]
    if (
        not isinstance(actor, str)
        or not actor.startswith("human:")
        or not actor[6:].strip()
        or len(actor) > 120
        or type(expected) is not int
        or expected < 0
    ):
        raise ValueError("保存遮盖配置需人工身份及非负记录版本")
    read(store, project)
    with gate(store, project), store.transaction():
        if expected != store.revision():
            raise ConflictError("研究记录或遮盖配置已变化，请刷新后保存")
        store.db.execute(
            "INSERT INTO project_privacy(project_id,patterns,actor,recorded_at) VALUES (?,?,?,?)",
            (project, dumps(configured), actor, now()),
        )
        # CLI 的样例回执没有图修订字段，规则变化后不得用旧回执开许可。
        store.db.execute("DELETE FROM remote_previews WHERE project_id=?", (project,))
        return read(store, project).metadata(store)
