"""后台流式采集脱敏钩子错误；错误正文不进入研究事件或对象库。"""

from __future__ import annotations

import builtins
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from typing import BinaryIO

from rg.store.database import Store, now
from rg.store.locking import exclusive
from rg.store.objects import digest

MAX_LINE = 4096
MAX_RECORDS = 1000
SAFE_CLASSES = frozenset(
    name for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
) | {"TaskBusy", "Deadline", "JSONDecodeError"}
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
CLASS = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}")


def metadata(raw: bytes) -> tuple[str, str | None, str | None, str | None, str | None]:
    try:
        stamp, stage, kind = raw.removesuffix(b"\n").decode("ascii").split(" ")
        if STAMP.fullmatch(stamp) is None or stage != "hook" or CLASS.fullmatch(kind) is None:
            raise ValueError
        timestamp = datetime.fromisoformat(stamp).astimezone(UTC).isoformat()
    except (ValueError, UnicodeError, OverflowError):
        return "unknown_record", None, None, None, None
    return (
        "reported", timestamp, "hook", kind if kind in SAFE_CLASSES else None,
        digest(kind.encode()) if kind not in SAFE_CLASSES else None,
    )


def _check(
    db: sqlite3.Connection, status: str, source: int | None = None,
    size: int | None = None, offset: int | None = None,
) -> None:
    highwater = db.execute(
        "SELECT coalesce(max(report_id),0) FROM hook_error_reports"
    ).fetchone()[0]
    values = (source, status, size, offset, highwater)
    previous = db.execute(
        "SELECT source_id,status,source_bytes,committed_offset,report_highwater "
        "FROM hook_error_checks ORDER BY check_id DESC LIMIT 1"
    ).fetchone()
    if previous is None or tuple(previous) != values:
        db.execute(
            "INSERT INTO hook_error_checks(source_id,status,source_bytes,committed_offset,"
            "report_highwater,recorded_at) VALUES (?,?,?,?,?,?)", (*values, now()),
        )


def _batch(store: Store, stream: BinaryIO, fault: Callable[[], None] | None) -> int:
    before = os.fstat(stream.fileno())
    token = f"{before.st_dev}:{before.st_ino}"
    previous = store.db.execute(
        "SELECT * FROM hook_error_sources ORDER BY source_id DESC LIMIT 1"
    ).fetchone()
    last_check = store.db.execute(
        "SELECT status FROM hook_error_checks ORDER BY check_id DESC LIMIT 1"
    ).fetchone()
    same = (
        previous is not None and previous["file_token"] == token
        and (last_check is None or last_check[0] != "missing")
    )
    if same:
        assert previous is not None
        same = before.st_size >= previous["committed_offset"]
        stream.seek(0)
        same = same and digest(stream.read(previous["prefix_length"])) == previous["prefix_sha256"]
        stream.seek(max(0, previous["committed_offset"] - previous["boundary_length"]))
        same = same and (
            digest(stream.read(previous["boundary_length"])) == previous["boundary_sha256"]
        )
    offset = previous["committed_offset"] if same and previous is not None else 0
    stream.seek(0)
    prefix = stream.read(min(before.st_size, MAX_LINE))
    stream.seek(offset)
    records: list[tuple[int, int, bytes]] = []
    status = "synced"
    for _ in range(MAX_RECORDS):
        start = stream.tell()
        remaining = before.st_size - start
        if not remaining:
            break
        raw = stream.readline(min(MAX_LINE + 1, remaining))
        if len(raw) > MAX_LINE:
            status = "oversized_line"
            break
        if not raw.endswith(b"\n"):
            status = "partial_line"
            break
        records.append((start, start + len(raw), raw))
        offset = start + len(raw)
    else:
        status = "backlog" if offset < before.st_size else "synced"
    stream.seek(max(0, offset - MAX_LINE))
    boundary = stream.read(min(offset, MAX_LINE))
    after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        with store.transaction() as db:
            _check(db, "changed_during_read")
        return 0
    with store.transaction() as db:
        if same:
            assert previous is not None
            source = previous["source_id"]
        else:
            source = db.execute(
                "INSERT INTO hook_error_sources(file_token,prefix_sha256,prefix_length,"
                "committed_offset,boundary_sha256,boundary_length,first_seen) "
                "VALUES (?,?,?,0,?,0,?) RETURNING source_id",
                (token, digest(prefix), len(prefix), digest(b""), now()),
            ).fetchone()[0]
        for start, end, raw in records:
            db.execute(
                "INSERT INTO hook_error_reports(source_id,byte_start,byte_end,line_sha256,"
                "status,occurred_at,stage,exception_class,exception_class_sha256,recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (source, start, end, digest(raw), *metadata(raw), now()),
            )
        if fault is not None:
            fault()
        db.execute(
            "UPDATE hook_error_sources SET committed_offset=?,boundary_sha256=?,boundary_length=? "
            "WHERE source_id=?", (offset, digest(boundary), len(boundary), source),
        )
        _check(db, status, source, before.st_size, offset)
    return len(records)


def collect(store: Store, *, fault: Callable[[], None] | None = None) -> dict[str, int]:
    if store.db.execute("PRAGMA user_version").fetchone()[0] < 20:
        return {}
    with exclusive(store.root / "locks" / "hook-errors.lock", "钩子错误账本正在采集"):
        directory_fd = None
        try:
            # 不跟随日志目录或文件链接；无法提供这些保证的平台明确记未知。
            if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
                raise ValueError("unsafe")
            directory_fd = os.open(
                store.root / "logs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            descriptor = os.open(
                "hook-errors.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("unsafe")
                return {"hook_error_records": _batch(store, stream, fault)}
        except FileNotFoundError:
            status = "missing"
        except ValueError:
            status = "unsafe"
        except OSError:
            status = "read_error"
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
        with store.transaction() as db:
            _check(db, status)
        return {"hook_error_records": 0}
