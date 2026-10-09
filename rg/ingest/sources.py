from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rg.store.database import Store, now


@dataclass(frozen=True)
class Source:
    path: Path
    tool: str
    project: str | None
    kind: str


def register(store: Store, path: Path, tool: str, project: str | None = None) -> Source:
    if tool not in {"claude", "codex"}:
        raise ValueError("扫描来源必须为 claude 或 codex")
    path = path.expanduser().resolve()
    if not path.is_file() and not path.is_dir():
        raise ValueError("首次登记的来源必须是已有文件或目录")
    if path.is_file() and path.suffix != ".jsonl":
        raise ValueError("扫描文件必须使用 .jsonl 后缀")
    if (
        project is not None
        and not store.db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone()
    ):
        raise ValueError("项目不存在")
    previous = store.db.execute(
        "SELECT * FROM ingest_sources WHERE path=?", (str(path),)
    ).fetchone()
    if project:
        for row in store.db.execute(
            "SELECT f.path,s.project_id FROM source_files f JOIN sessions s USING(session_pk) "
            "WHERE s.project_id IS NOT NULL AND s.project_id!=?",
            (project,),
        ):
            known = Path(row["path"])
            if known == path or (path.is_dir() and known.is_relative_to(path)):
                raise ValueError("来源含已属于其他项目的日志，不能静默改归属")
    if previous:
        if previous["tool"] != tool or (
            previous["project_id"] and project and previous["project_id"] != project
        ):
            raise ValueError("来源已有不同的解析器或项目归属，不能静默替换")
        project = project or previous["project_id"]
    kind = "directory" if path.is_dir() else "file"
    with store.transaction() as db:
        db.execute(
            "INSERT INTO ingest_sources VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE "
            "SET project_id=excluded.project_id,kind=excluded.kind",
            (str(path), tool, project, kind, now()),
        )
    return Source(path, tool, project, kind)


def sources(store: Store) -> list[Source]:
    result = [
        Source(Path(r["path"]), r["tool"], r["project_id"], r["kind"])
        for r in store.db.execute("SELECT * FROM ingest_sources ORDER BY path")
    ]
    # 已经显式导入的路径同样获得读取授权；保持实际记录的解析器与项目。
    seen = {s.path for s in result}
    for row in store.db.execute(
        "SELECT f.path,f.parser,s.project_id FROM source_files f JOIN sessions s USING(session_pk) "
        "WHERE f.parser IN ('claude','codex') ORDER BY f.file_instance_id DESC"
    ):
        path = Path(row["path"])
        covered = [
            s
            for s in result
            if path == s.path or (s.kind == "directory" and path.is_relative_to(s.path))
        ]
        if covered and all(
            s.tool == row["parser"]
            and (row["project_id"] is None or s.project == row["project_id"])
            for s in covered
        ):
            continue
        if path not in seen:
            result.append(Source(path, row["parser"], row["project_id"], "file"))
            seen.add(path)
    return result


def authorized(path: Path, values: list[Source], tool: str | None = None) -> Source:
    path = path.expanduser().resolve()
    matches = [
        s
        for s in values
        if path == s.path or (s.kind == "directory" and path.is_relative_to(s.path))
    ]
    if not matches or path.suffix != ".jsonl":
        raise ValueError("日志路径未在显式扫描来源中")
    identities = {(s.tool, s.project) for s in matches}
    if len(identities) != 1 or (tool is not None and matches[0].tool != tool):
        raise ValueError("日志路径的解析器或项目归属存在歧义")
    return matches[0]


def files(values: list[Source]) -> list[Path]:
    result: set[Path] = set()
    for source in values:
        if source.kind == "file":
            result.add(source.path)
        elif source.path.is_dir():
            result.update(source.path.rglob("*.jsonl"))
    return sorted(result)
