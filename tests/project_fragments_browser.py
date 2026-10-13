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
from rg.api.views import review as api_review
from rg.ingest.scanner import scan_file
from rg.query.project_fragments import query
from rg.store import clear
from rg.store.database import Store, dumps, now
from rg.store.fragments import assign
from tests.golden.test_read_tools import original, review

TOKEN = "synthetic-project-fragments-token"


def assignment(store: Store, source: dict, event: int, project: str) -> dict:
    """生产归属入口只处理选定的完整公开物理记录。"""
    page = query(
        store,
        {"session": str(source["session_pk"]), "source": str(source["file_instance_id"])},
    )
    return assign(
        store,
        {
            "request_id": str(uuid4()),
            "session": source["session_pk"],
            "source": source["file_instance_id"],
            "first_event": event,
            "last_event": event,
            "project_id": project,
            "expected_revision": page["revision"],
            "expected_scope_key": page["scope_key"],
            "snapshot": page["snapshot"],
            "observation_snapshot": page["observation_snapshot"],
            "actor": "human:公开清除续采验收",
        },
    )


def saved_source(store: Store, path: Path) -> dict:
    source = dict(
        store.db.execute(
            "SELECT file_instance_id,session_pk,path,parser FROM source_files "
            "WHERE path=? ORDER BY file_instance_id DESC LIMIT 1",
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
    return source


def prepare_clear_source(store: Store, directory: Path, project: str, root: Path) -> dict:
    path = directory / "public-clear-continuation.jsonl"
    records = [{"type": "session_meta", "payload": {"id": str(uuid4()), "cwd": str(root)}}]
    records.extend(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"公开清除续采记录 {n}"}],
            },
        }
        for n in range(1, 28)
    )
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    scan_file(store, path, "codex", project)
    source = saved_source(store, path)
    removed = source["events"][2]
    target_root = directory / "public-only-cleared"
    target_root.mkdir()
    target = store.project("验收仅清除中间公开记录项目", [target_root])
    receipt = assignment(store, source, removed["event_id"], target)
    return {"source": source, "removed": removed, "target": target, "assignment": receipt}


def continue_clear_source(directory: Path, project: str, prepared: dict) -> dict:
    """关闭库后真实清除，换 inode 重扫相同公开字节，禁止修改扫描游标。"""
    root = directory / "store"
    preview = clear.preview(root, prepared["target"])
    assert preview["blockers"] == []
    receipt = clear.execute(root, prepared["target"], str(uuid4()), preview["preview_sha256"])
    assert receipt["state"] == "complete"
    path = Path(prepared["source"]["path"])
    original = path.read_bytes()
    previous_inode = path.stat().st_ino
    replacement = directory / "replacement.tmp"
    replacement.write_bytes(original)
    replacement.replace(path)
    assert path.stat().st_ino != previous_inode
    with closing(Store(root)) as store:
        counts = scan_file(store, path, "codex", None)
        assert counts["privacy_skipped_records"] == 1
        assert counts["aliases"] == 27
        source = saved_source(store, path)
        assert source["file_instance_id"] != prepared["source"]["file_instance_id"]
        removed = prepared["removed"]
        assert len(source["events"]) == 27
        assert not any(e["byte_start"] == removed["byte_start"] for e in source["events"])
        assert not store.objects.path(removed["object_sha256"]).exists()
        aliases = [
            dict(row)
            for row in store.db.execute(
                "SELECT event_id,alias_of FROM raw_events WHERE file_instance_id=? "
                "AND record_index=0 ORDER BY byte_start",
                (source["file_instance_id"],),
            )
        ]
        assert [r["alias_of"] for r in aliases] == [
            e["event_id"] for e in prepared["source"]["events"] if e != removed
        ]
        # 只将新实例一条保留原件明确归属给健康页项目，缺口后其它记录仍收件箱。
        kept_assignment = assignment(store, source, source["events"][2]["event_id"], project)
        page = query(
            store,
            {"session": str(source["session_pk"]), "source": str(source["file_instance_id"])},
        )
        assert page["privacy_skipped_records"] == 1
        assert page["privacy_skipped_bytes"] == removed["byte_end"] - removed["byte_start"]
        assert page["records_total"] == page["records_observed"] == 27
        assert page["observations_complete"] is True
        assert all(
            r["context"]["reason"] == "privacy_gap" and r["context"]["cwd"] is None
            for r in page["records"][2:]
        )
        assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
    return source | {
        "removed": removed,
        "old_source": prepared["source"],
        "initial_assignment": prepared["assignment"],
        "kept_assignment": kept_assignment,
        "clear_preview": preview,
        "clear_receipt": receipt,
        "scan_counts": counts,
        "first_page": page,
        "aliases": aliases,
        "input_sha256": sha256(original).hexdigest(),
        "previous_inode": previous_inode,
        "current_inode": path.stat().st_ino,
    }


def dependency_source(
    store: Store,
    directory: Path,
    project: str,
    root: Path,
    name: str = "public-dependencies.jsonl",
    record_count: int = 26,
    production_review: bool = False,
) -> dict:
    """真实扫描额外来源，保存公开科研引用以验收只读依赖闭包。"""
    path = directory / name
    prefix = (
        "公开科研依赖记录" if name == "public-dependencies.jsonl" else f"公开迁移 {path.stem} 记录"
    )
    records = [{"type": "session_meta", "payload": {"id": str(uuid4()), "cwd": str(root)}}]
    records.extend(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"{prefix} {n}"}],
            },
        }
        for n in range(1, record_count + 1)
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
    if production_review:
        api_review(
            store,
            {
                "claim_ids": [confirmed],
                "action": "confirm",
                "actor": "human:公开科研迁移验收",
                "expected_revision": store.revision(),
            },
        )
    else:
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
        migrations = {
            name: dependency_source(
                store,
                directory,
                first,
                root_a,
                f"public-migration-{name}.jsonl",
                26 if name == "complete" else 2,
                True,
            )
            for name in ("complete", "stale", "late", "late401")
        }
        clear_source = prepare_clear_source(store, directory, first, root_a)
        index = {
            "first_project": first,
            "second_project": second,
            "sources": sources,
            "candidate": candidate,
            "confirmed": confirmed,
            "dependency_source": dependent,
            "migration_sources": migrations,
        }
    index["privacy_source"] = continue_clear_source(directory, first, clear_source)
    (directory / "index.json").write_text(dumps(index), encoding="utf-8")
    return index


def audit(directory: Path) -> dict:
    dependent = json.loads((directory / "index.json").read_text())["dependency_source"]
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
                        "SELECT input_event_ids,working_set_ids,status FROM extraction_runs "
                        "WHERE input_event_ids=?",
                        (dumps([e["event_id"] for e in dependent["events"]]),),
                    )
                ],
                "run_io": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT run_id,version_id,direction,basis FROM run_io WHERE run_id=?",
                        (dependent["run_id"],),
                    )
                ],
                "runs": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT run_id,project_id,session_pk,state,request_event_id FROM runs "
                        "WHERE run_id=?",
                        (dependent["run_id"],),
                    )
                ],
                "edits": [
                    list(row)
                    for row in store.db.execute(
                        "SELECT edit_id,project_id,session_pk,request_event_id,result_event_id,"
                        "after_version FROM edit_records WHERE edit_id=?",
                        (dependent["edit_id"],),
                    )
                ],
            },
            "privacy_metadata": {
                name: [
                    list(row) for row in store.db.execute(f"SELECT * FROM {name} ORDER BY rowid")
                ]
                for name in ("source_record_skips", "source_continuations")
            },
            "qualification_metadata": {
                name: [
                    list(row) for row in store.db.execute(f"SELECT * FROM {name} ORDER BY rowid")
                ]
                for name in ("claim_source_checks", "rule_reference_checks")
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
        (cache / "frontend-fragment-migration-index.json").write_text(
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
            "privacy_metadata",
        ]
        intact = all(before[key] == after[key] for key in protected)
        diagnostic_history = all(
            after["qualification_metadata"][name][: len(rows)] == rows
            for name, rows in before["qualification_metadata"].items()
        )
        (cache / "frontend-fragment-migration-audit.json").write_text(
            json.dumps(
                {
                    "服务前": before,
                    "服务后": after,
                    "原件候选审核未改写": intact,
                    "原诊断历史未改写": diagnostic_history,
                    "诊断追加数量": {
                        name: len(after["qualification_metadata"][name]) - len(rows)
                        for name, rows in before["qualification_metadata"].items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not intact or not diagnostic_history:
            raise RuntimeError("原件、候选或审核历史被改写")


if __name__ == "__main__":
    main()
