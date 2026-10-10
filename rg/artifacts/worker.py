from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from rg.artifacts.files import ChangedDuringRead, current_file, relative_path
from rg.snapshot.capture import MAX_FILE_BYTES, identifier
from rg.snapshot.git import Git
from rg.store.database import Store, dumps, now
from rg.store.locking import TaskBusy, exclusive
from rg.store.objects import digest


class Worker:
    """本地物理版本工作队列；慢读取不占 SQLite 写事务，不推断运行 I/O。"""

    def __init__(self, store: Store, project: str | None = None):
        self.store, self.project = store, project

    def _journal(self, job: int, kind: str, owner: str | None, details: dict) -> None:
        self.store.db.execute(
            "INSERT INTO artifact_job_events(job_id,kind,owner_id,details,recorded_at) "
            "VALUES (?,?,?,?,?)",
            (job, kind, owner, dumps(details), now()),
        )

    def _root(self, project: str, root: str, path: str) -> Path:
        identifier(project)
        identifier(root)
        registered = self.store.db.execute(
            "SELECT path FROM source_roots WHERE project_id=? AND root_id=? AND host_id='local'",
            (project, root),
        ).fetchone()
        if not registered or registered[0] != path or not Path(path).is_absolute():
            raise ValueError("物理版本根目录没有明确登记或已变化")
        return Path(path)

    def _enqueue(self, snapshot: Any, kind: str, payload: dict) -> None:
        key = digest(dumps([snapshot["snapshot_id"], kind, payload]).encode())
        cursor = self.store.db.execute(
            "INSERT OR IGNORE INTO artifact_jobs(job_key,project_id,root_id,snapshot_id,kind,"
            "input_json,state,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued',?,?)",
            (
                key,
                snapshot["project_id"],
                snapshot["root_id"],
                snapshot["snapshot_id"],
                kind,
                dumps(payload),
                now(),
                now(),
            ),
        )
        if cursor.rowcount and cursor.lastrowid:
            self._journal(cursor.lastrowid, "enqueued", None, {"kind": kind})

    def discover(self, counts: Counter) -> None:
        snapshots = self.store.db.execute(
            "SELECT s.* FROM workspace_snapshots s WHERE NOT EXISTS "
            "(SELECT 1 FROM artifact_discoveries d WHERE d.snapshot_id=s.snapshot_id) "
            "AND (? IS NULL OR s.project_id=?) ORDER BY coalesce("
            "(SELECT max(a.attempt_id) FROM artifact_discovery_attempts a "
            "WHERE a.snapshot_id=s.snapshot_id),0),s.snapshot_id LIMIT 20",
            (self.project, self.project),
        ).fetchall()
        for snapshot in snapshots:
            tasks: list[tuple[str, dict]] = []
            excluded: Counter = Counter()
            status, details = "done", {}
            try:
                if not snapshot["record_sha256"] or not snapshot["metadata"]:
                    raise ValueError("legacy_snapshot_metadata_unknown")
                payload = json.loads(self.store.objects.get(snapshot["record_sha256"]))
                if payload != json.loads(snapshot["metadata"]):
                    raise ValueError("snapshot_record_mismatch")
                self._root(snapshot["project_id"], snapshot["root_id"], payload["worktree"])
                common = {"worktree": payload["worktree"]}
                commit = snapshot["shadow_commit"]
                if commit:
                    if not re.fullmatch(r"[a-f0-9]{40}", commit):
                        raise ValueError("invalid_shadow_commit")
                    repository = (
                        self.store.root.resolve()
                        / "snapshots"
                        / f"{identifier(snapshot['project_id'])}.git"
                    )
                    if repository.is_symlink():
                        raise ValueError("shadow_repository_is_link")
                    with exclusive(repository.with_suffix(".lock"), "影子快照正在写入"):
                        listing = Git(
                            self.store.root.resolve(), time.monotonic() + 30, repository
                        ).run("ls-tree", "-r", "-z", "--long", commit)
                    for entry in listing.split(b"\0"):
                        if not entry:
                            continue
                        header, name = entry.split(b"\t", 1)
                        mode, kind, oid, size = header.split()
                        try:
                            path = relative_path(name.decode("utf-8"))
                        except (ValueError, UnicodeError):
                            excluded["path_not_supported"] += 1
                            continue
                        if kind != b"blob" or mode not in {b"100644", b"100755", b"120000"}:
                            excluded["non_file_entry"] += 1
                            continue
                        if not re.fullmatch(rb"[a-f0-9]{40}", oid) or not size.isdigit():
                            raise ValueError("invalid_git_object_metadata")
                        tasks.append(
                            (
                                "snapshot_blob",
                                common
                                | {
                                    "path": path,
                                    "mode": mode.decode(),
                                    "git_oid": oid.decode(),
                                    "size": int(size),
                                    "shadow_commit": commit,
                                },
                            )
                        )
                for item in payload.get("omitted_files", []):
                    if item.get("reason") != "large_file":
                        excluded["snapshot_omission"] += 1
                        continue
                    path = relative_path(item["path"])
                    if type(item.get("size")) is not int or type(item.get("mtime_ns")) is not int:
                        raise ValueError("invalid_large_file_hint")
                    tasks.append(
                        (
                            "file_hash",
                            common
                            | {
                                "path": path,
                                "size_hint": item["size"],
                                "mtime_ns_hint": item["mtime_ns"],
                            },
                        )
                    )
                status = "partial" if excluded or snapshot["skipped"] else "done"
                details = {
                    "tasks": len(tasks),
                    "excluded": dict(excluded),
                    "snapshot_skipped": snapshot["skipped"],
                }
            except TaskBusy:
                self._discovery_attempt(snapshot["snapshot_id"], "busy")
                counts["discovery_busy"] += 1
                continue
            except (OSError, RuntimeError):
                # 读取故障仍待发现，下一轮接续；不能证明原记录永久无效。
                self._discovery_attempt(snapshot["snapshot_id"], "waiting")
                counts["discovery_waiting"] += 1
                continue
            except (ValueError, KeyError, TypeError) as error:
                status, details, tasks = "unknown", {"reason": type(error).__name__}, []
            with self.store.transaction():
                for kind, task in tasks:
                    self._enqueue(snapshot, kind, task)
                self.store.db.execute(
                    "INSERT INTO artifact_discoveries VALUES (?,?,?,?)",
                    (snapshot["snapshot_id"], status, dumps(details), now()),
                )
            counts["discovered_snapshots"] += 1

    def _discovery_attempt(self, snapshot: int, status: str) -> None:
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO artifact_discovery_attempts(snapshot_id,status,recorded_at) "
                "VALUES (?,?,?)",
                (snapshot, status, now()),
            )

    def _recover(self) -> int:
        with self.store.transaction() as db:
            jobs = db.execute(
                "SELECT job_id,owner_id FROM artifact_jobs WHERE state='running'"
            ).fetchall()
            for job in jobs:
                db.execute(
                    "UPDATE artifact_jobs SET state='queued',owner_id=NULL,updated_at=? "
                    "WHERE job_id=?",
                    (now(), job[0]),
                )
                self._journal(
                    job[0], "recovered", job[1], {"proof": "exclusive_process_lock_acquired"}
                )
        return len(jobs)

    def _claim(self, job: int) -> tuple[Any, str] | None:
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM artifact_jobs WHERE job_id=?", (job,)).fetchone()
            if row is None or row["state"] not in {"queued", "paused"}:
                return None
            if row["next_attempt_at"] and row["next_attempt_at"] > now():
                return None
            owner = str(uuid4())
            db.execute(
                "UPDATE artifact_jobs SET "
                "state='running',owner_id=?,attempts=attempts+1,updated_at=? WHERE job_id=?",
                (owner, now(), job),
            )
            self._journal(job, "claimed", owner, {})
            return row, owner

    def _read(self, job: Any) -> dict:
        value = json.loads(job["input_json"])
        root = self._root(job["project_id"], job["root_id"], value["worktree"])
        relative = relative_path(value["path"])
        if job["kind"] == "file_hash":
            measured = current_file(self.store, root, relative, job["root_id"])
            return measured | {
                "algo": "sha256",
                "digest": measured["sha256"],
                "source": "current_file",
                "representation": "physical_file_bytes",
                "snapshot_id": None,
                "details": {
                    "complete": True,
                    "content_copied": False,
                    "matches_discovery_hint": measured["signature"][3:5]
                    == [value["size_hint"], value["mtime_ns_hint"]],
                },
            }
        if not 0 <= value["size"] <= MAX_FILE_BYTES:
            raise ValueError("快照对象超过原捕获上限")
        repository = (
            self.store.root.resolve() / "snapshots" / f"{identifier(job['project_id'])}.git"
        )
        if repository.is_symlink():
            raise ValueError("影子仓库不能是链接")
        with exclusive(repository.with_suffix(".lock"), "影子快照正在写入"):
            raw = Git(self.store.root.resolve(), time.monotonic() + 30, repository).run(
                "cat-file", "blob", value["git_oid"]
            )
        git_sha = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
        ).hexdigest()
        if len(raw) != value["size"] or git_sha != value["git_oid"]:
            raise ValueError("快照对象字节与 Git 身份不符")
        content = self.store.objects.put(raw)
        return {
            "algo": "git-sha1",
            "digest": git_sha,
            "size": len(raw),
            "mode": value["mode"],
            "source": "shadow_snapshot",
            "content_sha256": content,
            "representation": "symlink_target_bytes"
            if value["mode"] == "120000"
            else "physical_file_bytes",
            "snapshot_id": job["snapshot_id"],
            "signature": None,
            "hash_started_at": None,
            "hash_finished_at": None,
            "cache_reused": False,
            "cached_from": None,
            "details": {
                "complete": True,
                "content_copied": True,
                "shadow_commit": value["shadow_commit"],
            },
        }

    def _finish(
        self, job: Any, owner: str, result: dict | None, state: str, error: str | None
    ) -> None:
        value = json.loads(job["input_json"])
        version = None
        ready = (
            (datetime.fromisoformat(now()) + timedelta(seconds=5)).isoformat()
            if state == "paused"
            else None
        )
        with self.store.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM artifact_jobs WHERE job_id=? AND owner_id=? AND state='running'",
                (job["job_id"], owner),
            ).fetchone():
                raise RuntimeError("文件版本任务归属已变化")
            if result is not None:
                self._root(job["project_id"], job["root_id"], value["worktree"])
                path = str(Path(value["worktree"]) / relative_path(value["path"]))
                version = "physical:" + digest(
                    dumps(
                        [
                            job["project_id"],
                            job["root_id"],
                            path,
                            result["algo"],
                            result["digest"],
                            result["mode"],
                        ]
                    ).encode()
                )
                snapshot = db.execute(
                    "SELECT taken_at FROM workspace_snapshots WHERE snapshot_id=?",
                    (job["snapshot_id"],),
                ).fetchone()
                db.execute(
                    "INSERT OR IGNORE INTO "
                    "artifact_versions(version_id,project_id,path,algo,digest,size,source,"
                    "observed_at,content_sha256,phase,root_id,basis,claim_state,representation) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'observed',?,'direct_record','candidate',?)",
                    (
                        version,
                        job["project_id"],
                        path,
                        result["algo"],
                        result["digest"],
                        result["size"],
                        result["source"],
                        snapshot[0] if result["snapshot_id"] else result["hash_finished_at"],
                        result["content_sha256"],
                        job["root_id"],
                        result["representation"],
                    ),
                )
                observation = "physical-observation:" + digest(
                    dumps([job["job_key"], result]).encode()
                )
                db.execute(
                    "INSERT INTO artifact_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        observation,
                        version,
                        job["job_id"],
                        result["snapshot_id"],
                        job["snapshot_id"],
                        result["mode"],
                        dumps(result["signature"]) if result["signature"] else None,
                        result["hash_started_at"],
                        result["hash_finished_at"],
                        int(result["cache_reused"]),
                        result["cached_from"],
                        dumps(result["details"]),
                        now(),
                    ),
                )
                if result["source"] == "current_file" and not result["cache_reused"]:
                    db.execute(
                        "INSERT OR IGNORE INTO file_hash_cache VALUES (?,?)",
                        (result["cache_key"], observation),
                    )
            receipt = {
                "version_id": version,
                "cache_reused": bool(result and result["cache_reused"]),
            }
            db.execute(
                "UPDATE artifact_jobs SET "
                "state=?,owner_id=NULL,error=?,result=?,next_attempt_at=?,updated_at=? "
                "WHERE job_id=?",
                (state, error, dumps(receipt), ready, now(), job["job_id"]),
            )
            self._journal(
                job["job_id"],
                "finished",
                owner,
                receipt | {"state": state, "reason": error, "next_attempt_at": ready},
            )

    def run(self, limit: int = 20, retry_failed: bool = False) -> dict[str, int]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("文件版本每轮任务上限为 1 到 1000")
        with exclusive(self.store.root / "locks" / "artifacts.lock", "文件版本工作队列正在运行"):
            counts: Counter = Counter(recovered=self._recover())
            self.discover(counts)
            if retry_failed:
                with self.store.transaction() as db:
                    rows = db.execute(
                        "SELECT job_id FROM artifact_jobs WHERE state='failed' AND (? IS NULL "
                        "OR project_id=?)",
                        (self.project, self.project),
                    ).fetchall()
                    for row in rows:
                        db.execute(
                            "UPDATE artifact_jobs SET state='queued',error=NULL,updated_at=? WHERE "
                            "job_id=?",
                            (now(), row[0]),
                        )
                        self._journal(row[0], "explicit_retry", None, {})
            rows = self.store.db.execute(
                "SELECT job_id FROM artifact_jobs WHERE state IN ('queued','paused') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) AND (? IS NULL OR "
                "project_id=?) "
                "ORDER BY updated_at,job_id LIMIT ?",
                (now(), self.project, self.project, limit),
            ).fetchall()
            for row in rows:
                claimed = self._claim(row[0])
                if claimed is None:
                    continue
                job, owner = claimed
                result, state, error = None, "done", None
                try:
                    result = self._read(job)
                except (ChangedDuringRead, TaskBusy):
                    state, error = "paused", "source_changed_or_busy"
                except (OSError, ValueError, RuntimeError) as failure:
                    state, error = "failed", type(failure).__name__
                self._finish(job, owner, result, state, error)
                counts["processed"] += 1
                counts[state] += 1
                counts["cache_reused"] += int(bool(result and result["cache_reused"]))
            return dict(counts)
