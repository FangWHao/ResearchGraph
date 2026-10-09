from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from rg.snapshot.capture import capture
from rg.snapshot.config import export_registry, registry_path
from rg.store.database import Store, dumps
from rg.store.objects import atomic_write


def snapshot(store: Store, project: str, root: Path, benchmark: int | None) -> dict[str, Any]:
    if benchmark is not None and (type(benchmark) is not int or not 5 <= benchmark <= 100):
        raise ValueError("p95 基准须至少 5 次、最多 100 次")
    rows = store.db.execute(
        "SELECT root_id,path FROM source_roots WHERE project_id=? AND host_id='local'",
        (project,),
    ).fetchall()
    chosen = [row for row in rows if Path(row["path"]).resolve() == root.resolve()]
    if len(chosen) != 1:
        raise ValueError("手工快照必须指定已经登记的唯一根目录")
    samples = [
        capture(store.root, project, chosen[0]["root_id"], root) for _ in range(benchmark or 1)
    ]
    if benchmark is None:
        return samples[0]
    durations = sorted(value["duration_ms"] for value in samples)
    p95 = durations[math.ceil(len(durations) * 0.95) - 1]
    # 有失败不能把很快返回的错误当成同步性能证明；保守切为异步并显示缺口。
    skipped = sum(value["skipped"] is not None for value in samples)
    mode = "async" if p95 > 300 or skipped else "sync"
    values = export_registry(store)
    for value in values:
        if value["root_id"] == chosen[0]["root_id"]:
            value["snapshot_mode"] = mode
    atomic_write(registry_path(store.root), dumps(values).encode())
    return {
        "samples": len(samples),
        "p95_ms": p95,
        "skipped": skipped,
        "snapshot_mode": mode,
        "pending_registration": len(samples),
    }
