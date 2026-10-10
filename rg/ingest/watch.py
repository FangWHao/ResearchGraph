from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Iterator

from rg.ingest.scanner import mark_deleted, scan_file
from rg.ingest.sources import authorized, files, sources
from rg.ingest.spool import consume, register
from rg.store.database import Store
from rg.store.locking import TaskBusy, exclusive


def cycle(
    store: Store, retry_failed: bool = False, fault: Callable[[], None] | None = None
) -> dict[str, int]:
    with exclusive(store.root / "locks" / "scan.lock", "扫描守护进程正在运行"):
        counts: Counter[str] = Counter()
        scanned_files: set[int] = set()
        values = sources(store)
        for path in files(values):
            try:
                source = authorized(path, values)
                if not path.is_file():
                    continue
                counts.update(scan_file(store, path, source.tool, source.project))
                latest = store.db.execute(
                    "SELECT file_instance_id FROM source_files WHERE path=? "
                    "ORDER BY file_instance_id DESC LIMIT 1",
                    (str(path.resolve()),),
                ).fetchone()
                if latest is not None:
                    scanned_files.add(latest[0])
                counts["files"] += 1
            except TaskBusy:
                counts["busy"] += 1
            except (OSError, ValueError, RuntimeError):
                counts["errors"] += 1
        counts["deleted"] = mark_deleted(store)
        from rg.ingest.claude_chain import backfill_saved
        from rg.ingest.hook_errors import collect

        counts.update(backfill_saved(store, skip_files=scanned_files))
        from rg.ingest.parents import backfill_saved as backfill_parents

        counts.update(backfill_parents(store, skip_files=scanned_files))
        counts.update(collect(store))
        counts.update(register(store))
        counts.update(consume(store, sources(store), retry_failed, fault=fault))
        from rg.derive.worker import derive

        try:
            counts.update(derive(store, retry_failed=retry_failed))
        except TaskBusy:
            counts["l1_busy"] += 1
        return dict(counts)


def watch(
    store: Store, interval: float = 2, retry_failed: bool = False
) -> Iterator[dict[str, int]]:
    from rg.artifacts.service import Service

    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("扫描间隔必须为有限正数")
    with (
        exclusive(store.root / "locks" / "watch.lock", "该数据目录已有扫描守护进程"),
        Service(store) as service,
    ):
        while True:
            try:
                result = cycle(store, retry_failed)
            except TaskBusy:
                result = {"busy": 1}
            service.tick()
            yield result
            time.sleep(interval)
