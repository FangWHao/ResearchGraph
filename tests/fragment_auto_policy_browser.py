"""未来片段归属的独立公开验收服务；API与扫描均走生产实现。"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from rg.api.server import Handler, LocalServer
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import query
from rg.record.question import question
from rg.store.database import Store, dumps
from rg.store.roots import insert, prepare, register

TOKEN = "synthetic-fragment-auto-policy-token"


def add_record(path: Path, native: str, cwd: str | None, text: str, *, unknown=False) -> None:
    record = {
        "type": "synthetic-unknown" if unknown else "user",
        "uuid": str(uuid4()),
        "sessionId": native,
        "timestamp": "2026-10-12T01:00:00Z",
        "version": "2.1.280",
        "message": {"role": "user", "content": text},
    }
    if cwd is not None:
        record["cwd"] = cwd
    with path.open("ab") as stream:
        stream.write((dumps(record) + "\n").encode())


def source(store: Store, path: Path) -> dict:
    return dict(
        store.db.execute(
            "SELECT session_pk,file_instance_id,path,parser AS tool FROM source_files "
            "WHERE path=? ORDER BY file_instance_id DESC LIMIT 1",
            (str(path),),
        ).fetchone()
    )


def seed(directory: Path) -> dict:
    primary, second = directory / "公开甲目录", directory / "公开乙目录"
    shared = directory / "公开重叠目录"
    paths = [primary, shared, *(directory / f"公开登记-{n:03}" for n in range(101))]
    for path in [*paths, second, shared / "另一登记"]:
        path.mkdir(parents=True)
    transcript, manual = directory / "public.jsonl", directory / "public-manual.jsonl"
    native, manual_native = str(uuid4()), str(uuid4())
    with closing(Store(directory / "store")) as store:
        project = store.project("验收未来片段自动归属项目", [])
        other = store.project("验收未来片段自动归属项目二", [])
        with store.transaction() as db:
            for path in paths:
                insert(db, project, prepare(store, path, "data"))
            for path in [second, shared / "另一登记"]:
                insert(db, other, prepare(store, path, "data"))
            db.execute("UPDATE graph_clock SET revision=revision+1 WHERE id=1")
        add_record(transcript, native, str(primary), "开关启用前的公开原件，不能补分。")
        scan_file(store, transcript, "claude")
        add_record(manual, manual_native, str(primary), "明确人工绑定乙项目的公开来源。")
        scan_file(store, manual, "claude", other)
        result = question(
            store,
            {
                "project_id": project,
                "text": "既有公开科研问题；策略不能修改原文与审核。",
                "scope": None,
                "actor": "human:公开种子",
                "request_id": str(uuid4()),
                "expected_revision": store.revision(),
            },
        )
        index = {
            "project_id": project,
            "second_project": other,
            "primary": str(primary),
            "shared": str(shared),
            "future_root": str(directory / "公开后来登记"),
            "transcript": str(transcript),
            "manual_transcript": str(manual),
            "native": native,
            "manual_native": manual_native,
            "source": source(store, transcript),
            "manual_source": source(store, manual),
            "old_event_id": store.db.execute(
                "SELECT min(event_id) FROM raw_events WHERE file_instance_id=?",
                (source(store, transcript)["file_instance_id"],),
            ).fetchone()[0],
            "baseline_max_event": store.db.execute(
                "SELECT max(event_id) FROM raw_events"
            ).fetchone()[0],
            "question_claim_id": result["claim_id"],
            "initial_total": 103,
        }
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(root: Path, index: dict) -> dict:
    with closing(Store(root)) as store, store.snapshot():
        tables = ("claims", "claim_evidence", "evidence_spans", "review_actions")
        rows = {
            table: [list(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables
        }
        raw = [
            list(row)
            for row in store.db.execute(
                "SELECT * FROM raw_events WHERE event_id<=? ORDER BY event_id",
                (index["baseline_max_event"],),
            )
        ]
        return {
            "scientific_sha256": sha256(dumps(rows).encode()).hexdigest(),
            "original_raw_sha256": sha256(dumps(raw).encode()).hexdigest(),
            "scientific_counts": {table: len(value) for table, value in rows.items()},
            "model_calls": store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0],
            "raw_events": store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0],
            "revision": store.revision(),
        }


def append(store: Store, index: dict, case: str) -> dict:
    paths = {
        "registered": index["primary"],
        "future": index["future_root"],
        "overlap": str(Path(index["shared"]) / "另一登记"),
        "unknown": index["primary"],
        "stopped": index["primary"],
        "manual": index["primary"],
    }
    if case not in paths:
        raise ValueError("未知公开合成场景")
    manual = case == "manual"
    path = Path(index["manual_transcript"] if manual else index["transcript"])
    native = index["manual_native"] if manual else index["native"]
    add_record(
        path,
        native,
        paths[case],
        f"公开新导入场景 {case}，<script>window.syntheticExecuted=true</script> 仅为原文。",
        unknown=case == "unknown",
    )
    scan_file(store, path, "claude")
    current = source(store, path)
    page = query(
        store,
        {"session": str(current["session_pk"]), "source": str(current["file_instance_id"])},
    )
    return {"case": case, "source": current, "page": page}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9798)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    if args.prepare:
        args.fixture_dir.mkdir(parents=True, exist_ok=True)
        index = seed(args.fixture_dir)
        (cache / "frontend-fragment-auto-policy-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("公开两项目、103项登记规则及旧原件已准备；未启动服务。", flush=True)
        return
    index = json.loads((args.fixture_dir / "index.json").read_text(encoding="utf-8"))
    root = args.fixture_dir / "store"
    before = audit(root, index)

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-fragment-auto-policy/audit":
                return super().do_GET()
            if self._authorized():
                self._json(200, audit(root, index))

        def do_POST(self):
            path = urlsplit(self.path).path
            if not path.startswith("/synthetic-fragment-auto-policy/"):
                return super().do_POST()
            if not self._authorized():
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with closing(Store(root)) as store:
                    if path.endswith("/append"):
                        result = append(store, index, body["case"])
                    elif path.endswith("/register"):
                        new = Path(index["future_root"])
                        new.mkdir(exist_ok=True)
                        result = register(store, index["project_id"], new, "data")
                    else:
                        self._json(404, {"error": "未知合成控制"})
                        return
                self._json(200, result)
            except (KeyError, ValueError, OSError) as error:
                self._json(400, {"error": str(error)})

    server = LocalServer(root, args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        after = audit(root, index)
        intact = all(
            before[key] == after[key]
            for key in (
                "scientific_sha256",
                "original_raw_sha256",
                "scientific_counts",
                "model_calls",
            )
        )
        (cache / "frontend-fragment-auto-policy-audit.json").write_text(
            json.dumps(
                {"准备后": before, "关闭后": after, "旧原件与科研记录未改写": intact},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not intact:
            raise RuntimeError("公开旧原件或科研记录被改写")


if __name__ == "__main__":
    main()
