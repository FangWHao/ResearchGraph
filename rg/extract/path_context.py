"""只用已保存的目录声明和登记身份检索路径，不访问当前工作区。"""

from __future__ import annotations

import json
import ntpath
import posixpath
import re
from typing import Any

from rg.extract.paths import file_paths
from rg.store.database import Store

PathKey = tuple[str, str, str]


def policy() -> dict[str, str]:
    return {"path_identity": "registered-common-dir-v1", "path_context": "native-record-v1"}


def absolute(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 16384
        or any(ord(char) < 32 for char in value)
    ):
        return None
    if re.match(r"^[A-Za-z]:[/\\]", value) or value.startswith(("\\\\", "//")):
        if value.startswith(("\\\\?\\", "\\\\.\\")):
            return None
        normalized = ntpath.normpath(value).replace("\\", "/")
        return normalized[0].upper() + normalized[1:] if normalized[1:2] == ":" else normalized
    if value.startswith("/") and not value.startswith("//"):
        return posixpath.normpath(value)
    return None


def within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class PathContext:
    def __init__(self, store: Store, project: str):
        self.store, self.project = store, project
        # 借用的科研来源根不构成本项目的工作区定位授权。
        self.roots = [
            dict(row)
            for row in store.db.execute(
                "SELECT r.* FROM source_roots r JOIN active_projects p USING(project_id) "
                "WHERE host_id='local'"
            )
        ]
        self._cwds: dict[int, str | None] = {}

    def cwd(self, event: int) -> str | None:
        if event in self._cwds:
            return self._cwds[event]
        # 只在同一逻辑片段和物理来源内继承；缺失、非法或不可用声明阻断继承。
        row = self.store.db.execute(
            "SELECT o.state,o.cwd FROM effective_raw_events current "
            "JOIN effective_raw_events r ON r.file_instance_id=current.file_instance_id "
            "AND r.session_pk=current.session_pk AND r.byte_start<=current.byte_start "
            "AND r.record_index=0 LEFT JOIN project_context_observations o "
            "ON o.observation_id=(SELECT max(c.observation_id) "
            "FROM project_context_observations c WHERE c.event_id=r.event_id) "
            "WHERE current.event_id=? AND (o.observation_id IS NULL OR o.state!='absent') "
            "ORDER BY r.byte_start DESC LIMIT 1",
            (event,),
        ).fetchone()
        result = absolute(row["cwd"]) if row and row["state"] == "declared" else None
        self._cwds[event] = result
        return result

    def keys(self, paths: set[str], cwd: str | None = None) -> set[PathKey]:
        result: set[PathKey] = set()
        for path in paths:
            resolved = absolute(path)
            if resolved is None and cwd and not re.match(r"^[A-Za-z]:|^[\\/]", path):
                resolved = absolute(posixpath.join(cwd, path.replace("\\", "/")))
            if resolved is None:
                # 旧文字匹配保留为独立命名空间，不能与已定位的文件身份混用。
                if path and "\0" not in path:
                    result.add(("literal", self.project, path))
                continue
            result.add(("absolute", self.project, resolved))
            matches = [
                row
                for row in self.roots
                if (root := absolute(row["path"])) is not None and within(resolved, root)
            ]
            if not matches:
                continue
            root = max(matches, key=lambda row: len(row["path"]))
            if root["project_id"] != self.project or root["kind"] not in {"repo", "worktree"}:
                continue
            try:
                metadata = json.loads(root["git_metadata"])
            except ValueError:
                continue
            if not isinstance(metadata, dict):
                continue
            common = absolute(root["git_common_dir"])
            worktree = absolute(metadata.get("worktree"))
            owners = {
                row["project_id"]
                for row in self.roots
                if common is not None and absolute(row["git_common_dir"]) == common
            }
            if (
                metadata.get("status") == "registered"
                and common is not None
                and absolute(metadata.get("git_common_dir")) == common
                and worktree is not None
                and within(resolved, worktree)
                and owners == {self.project}
            ):
                relative = resolved[len(worktree.rstrip("/")) :].lstrip("/")
                if relative:
                    result.add(("repository", common, relative))
        return result

    def event_keys(self, event: int, text: str) -> set[PathKey]:
        cwd = self.cwd(event)
        try:
            arguments = json.loads(text)
        except ValueError:
            arguments = None
        if isinstance(arguments, dict):
            # 固定参数字段可以覆盖原生目录；不解释 shell 的 cd、变量或命令替换。
            for field in ("workdir", "cwd"):
                if field in arguments:
                    cwd = absolute(arguments[field])
                    break
        return self.keys(file_paths(text), cwd)

    def claim_keys(self, claim: int, text: str) -> set[PathKey]:
        paths = file_paths(text)
        events = self.store.db.execute(
            "SELECT DISTINCT es.event_id FROM claim_evidence ce "
            "JOIN evidence_spans es USING(span_id) WHERE ce.claim_id=?",
            (claim,),
        ).fetchall()
        if not events:
            return self.keys(paths)
        return {key for row in events for key in self.keys(paths, self.cwd(row[0]))}
