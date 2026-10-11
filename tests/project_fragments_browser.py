"""物理片段归属的公开合成来源；只使用真实扫描和生产 API。"""

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
from rg.store.database import Store, dumps, now
from tests.golden.test_read_tools import original, review

TOKEN = "synthetic-project-fragments-token"


def dependency_source(store: Store, directory: Path, project: str, root: Path) -> dict:
    """真实扫描额外来源，保存公开科研引用以验收只读依赖闭包。"""
    path = directory / "public-dependencies.jsonl"
    records = [{"type": "session_meta", "payload": {"id": str(uuid4()), "cwd": str(root)}}]
    records.extend(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"公开科研依赖记录 {n}"}],
            },
        }
        for n in range(1, 27)
    )
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    scan_file(store, path, "codex", project)
    source = dict(
        store.db.execute(
            "SELECT file_instance_id,session_pk,path,parser FROM source_files WHERE path=?",
            (str(path),),
        ).fetchone()
    )
    source["events"] = [
        dict(row)
        for row in store.db.execute(
            "SELECT event_id,byte_start,byte_end,object_sha256 FROM raw_events "
            "WHERE file_instance_id=? AND record_index=0 ORDER BY byte_start",
            (source["file_instance_id"],),
        )
    ]
    entity, candidate = original(store, project)
    confirmed_entity, confirmed = original(store, project)
    review(store, confirmed, "confirm")
    # 两条科研记录引用全部已存物理记录；请求预检时不执行任何模型调用。
    for event in source["events"]:
        raw = store.raw(event["event_id"])
        span = store.db.execute(
            "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) "
            "VALUES (?,0,?,?)",
            (event["event_id"], len(raw), sha256(raw).hexdigest()),
        ).lastrowid
        for claim in (candidate, confirmed):
            store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim, span))
    store.db.execute(
        "INSERT INTO extraction_runs(job_key,stage,input_event_ids,working_set_ids,"
        "status,created_at) VALUES (?,'pass2',?,?,'ok',?)",
        (str(uuid4()), dumps([e["event_id"] for e in source["events"]]), dumps([entity]), now()),
    )
    run, version = str(uuid4()), str(uuid4())
    first, last = source["events"][0]["event_id"], source["events"][-1]["event_id"]
    store.db.execute(
        "INSERT INTO runs(run_id,project_id,session_pk,state,request_event_id) "
        "VALUES (?,?,?,'requested',?)",
        (run, project, source["session_pk"], first),
    )
    store.db.execute(
        "INSERT INTO run_observations(run_id,event_id,state,details,recorded_at) "
        "VALUES (?,?,'requested','{}',?)",
        (run, last, now()),
    )
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
        "evidence_event_id,observed_at) "
        "VALUES (?,?,'public.py','sha256',?,'agent_edit',?,?)",
        (version, project, sha256(b"public candidate").hexdigest(), first, now()),
    )
    store.db.execute(
        "INSERT INTO run_io(run_id,version_id,direction,basis) VALUES (?,?,'out','manual')",
        (run, version),
    )
    edit = str(uuid4())
    store.db.execute(
        "INSERT INTO edit_records(edit_id,project_id,session_pk,request_event_id,result_event_id,"
        "path,operation,after_version,recorded_at) VALUES (?,?,?,?,?,'public.py','edit',?,?)",
        (edit, project, source["session_pk"], first, last, version, now()),
    )
    source["claims"] = {"candidate": candidate, "confirmed": confirmed}
    source["entities"] = [entity, confirmed_entity]
    source["run_id"], source["version_id"], source["edit_id"] = run, version, edit
    return source


def seed(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    root_a, root_b = directory / "public-alpha", directory / "public-beta"
    root_a.mkdir()
    root_b.mkdir()
    paths = [directory / "public-codex.jsonl", directory / "public-claude.jsonl"]
    codex = [{"type": "session_meta", "payload": {"id": str(uuid4()), "cwd": str(root_a)}}]
    for n in range(1, 24):
        if n == 10:
            codex.append({"type": "turn_context", "payload": {"cwd": str(root_b)}})
        else:
            codex.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"公开片段甲 {n}"}],
                    },
                }
            )
    native = str(uuid4())
    claude = [
        {
            "type": "assistant",
            "uuid": str(uuid4()),
            "sessionId": native,
            "cwd": str(root_a if n < 2 else root_b),
            "message": {
                "content": [
                    {"type": "text", "text": f"公开片段乙 {n} 第一块"},
                    {"type": "text", "text": f"公开片段乙 {n} 第二块 <script>字面</script>"},
                ]
            },
        }
        for n in range(5)
    ]
    for path, records in zip(paths, [codex, claude], strict=True):
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
    with closing(Store(directory / "store")) as store:
        first = store.project("验收物理片段项目甲", [root_a])
        second = store.project("验收物理片段项目乙", [root_b])
        for tool, path in zip(["codex", "claude"], paths, strict=True):
            scan_file(store, path, tool, first)
        # 与选定原文无依赖的公开候选/人工确认，核对归属操作不改科研史。
        _, candidate = original(store, first)
        _, confirmed = original(store, second)
        review(store, confirmed, "confirm")
        sources = [
            dict(row)
            for row in store.db.execute(
                "SELECT file_instance_id,session_pk,path,parser FROM source_files "
                "WHERE parser IN ('codex','claude') ORDER BY file_instance_id"
            )
        ]
        for source in sources:
            source["events"] = [
                dict(row)
                for row in store.db.execute(
                    "SELECT event_id,byte_start,byte_end,object_sha256 FROM raw_events "
                    "WHERE file_instance_id=? AND record_index=0 ORDER BY byte_start",
                    (source["file_instance_id"],),
                )
            ]
        dependent = dependency_source(store, directory, first, root_a)
        index = {
            "first_project": first,
            "second_project": second,
            "sources": sources,
            "candidate": candidate,
            "confirmed": confirmed,
            "dependency_source": dependent,
        }
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(directory: Path) -> dict:
    with closing(Store(directory / "store")) as store:
        tables = (
            "raw_events",
            "claims",
            "entities",
            "review_actions",
            "evidence_spans",
            "claim_evidence",
            "model_attempts",
            "extraction_runs",
            "runs",
            "run_observations",
            "run_io",
            "artifact_versions",
            "edit_records",
        )
        rows = {
            name: [list(r) for r in store.db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in tables
        }
        result = {
            "database_sha256": sha256("\n".join(store.db.iterdump()).encode()).hexdigest(),
            "research_sha256": sha256(dumps(rows).encode()).hexdigest(),
            "counts": {name: len(items) for name, items in rows.items()},
            "revision": store.revision(),
            "claim_states": [
                [r[0], store.claim_state(r[0])]
                for r in store.db.execute("SELECT claim_id FROM claims ORDER BY claim_id")
            ],
            "objects": {
                row[0]: sha256(store.raw(row[1])).hexdigest()
                for row in store.db.execute(
                    "SELECT object_sha256,min(event_id) FROM raw_events GROUP BY object_sha256"
                )
            },
            "dependency_metadata": {
                "extraction": [
                    {
                        "input_event_ids": json.loads(row[0]),
                        "working_set_ids": json.loads(row[1]),
                        "status": row[2],
                    }
                    for row in store.db.execute(
                        "SELECT input_event_ids,working_set_ids,status FROM extraction_runs"
                    )
                ],
                "run_io": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT run_id,version_id,direction,basis FROM run_io"
                    )
                ],
                "runs": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT run_id,project_id,session_pk,state,request_event_id FROM runs"
                    )
                ],
                "edits": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT edit_id,project_id,session_pk,request_event_id,result_event_id,"
                        "after_version FROM edit_records"
                    )
                ],
            },
        }
    result["input_files"] = {
        path.name: sha256(path.read_bytes()).hexdigest() for path in directory.glob("*.jsonl")
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--port", type=int, default=9809)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    cache = Path(".cache")
    cache.mkdir(exist_ok=True)
    if args.prepare:
        index = seed(args.fixture_dir)
        (cache / "frontend-project-fragments-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("两个公开项目与额外27条依赖记录已真实扫描；科研引用仅为公开合成。", flush=True)
        return
    before = audit(args.fixture_dir)

    class FixtureHandler(Handler):
        def do_GET(self):
            if urlsplit(self.path).path != "/synthetic-fragments/audit":
                return super().do_GET()
            if self._authorized():
                self._json(200, audit(args.fixture_dir))

    server = LocalServer(args.fixture_dir / "store", args.web_dir, args.port, token=TOKEN)
    server.RequestHandlerClass = FixtureHandler
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        after = audit(args.fixture_dir)
        protected = [
            "research_sha256",
            "counts",
            "claim_states",
            "objects",
            "input_files",
            "dependency_metadata",
        ]
        intact = all(before[key] == after[key] for key in protected)
        (cache / "frontend-project-fragments-audit.json").write_text(
            json.dumps(
                {"服务前": before, "服务后": after, "原件候选审核未改写": intact},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not intact:
            raise RuntimeError("原件、候选或审核历史被改写")


if __name__ == "__main__":
    main()
