"""清除后防止自动重导入；仅持久保存命名空间摘要。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rg.store.clear_files import path_hash, read_json, session_hash
from rg.store.objects import digest


def denied(
    root: Path,
    *,
    project: str | None = None,
    tool: str | None = None,
    native: str | None = None,
    paths: list[str] | None = None,
) -> bool:
    directory = root / "clear-records"
    if directory.is_symlink():
        raise ValueError("清除记录目录不能为符号链接")
    if not directory.exists():
        return False
    for record in directory.glob("*.json"):
        values = read_json(record).get("denials", {})
        if project and digest(project.encode()) in values.get("projects", []):
            return True
        if tool and native and session_hash(tool, native) in values.get("sessions", []):
            return True
        for path in paths or []:
            if not Path(path).is_absolute():
                continue
            current = Path(path).resolve()
            if path_hash(str(current)) in values.get("paths", []):
                return True
            if any(
                path_hash(str(p)) in values.get("prefixes", []) for p in (current, *current.parents)
            ):
                return True
    return False


def check_payload(root: Path, tool: str | None, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    paths = [
        v
        for k in ("cwd", "transcript_path", "agent_transcript_path")
        if isinstance(v := payload.get(k), str)
    ]
    if denied(
        root,
        project=payload.get("project_id") if isinstance(payload.get("project_id"), str) else None,
        tool=tool,
        native=next(
            (
                payload[k]
                for k in ("session_id", "native_session_id")
                if isinstance(payload.get(k), str)
            ),
            None,
        ),
        paths=paths,
    ):
        raise PermissionError("该来源已整项目清除，停止自动重新导入")
