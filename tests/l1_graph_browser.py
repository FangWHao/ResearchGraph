"""独立 L1 浏览器合成项目；虚拟来源，不增加物理日志或读取真实资料。"""

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from rg.record.manifest import record
from rg.store.database import Store, dumps
from rg.store.objects import digest
from tests.golden.test_read_tools import append

SCOPE = {"data": "synthetic_v1", "step": "文件运行图"}
T0 = "2026-10-09T08:58:00+00:00"
T1 = "2026-10-09T09:00:00+00:00"
T2 = "2026-10-09T09:01:00+00:00"
T3 = "2026-10-09T09:04:00+00:00"


def seed_l1_graph(store: Store) -> str:
    project = store.project("验收L1文件运行图项目", [Path("/synthetic/file-runs")])
    root = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (project,)
    ).fetchone()[0]
    session = store.db.execute(
        "INSERT INTO sessions(tool,native_session_id,project_id,project_basis) "
        "VALUES ('rg',?,?,'manual')",
        (str(uuid4()), project),
    ).lastrowid
    file = store.db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,parser,parser_version,"
        "first_seen,status) "
        "VALUES (?,?,'virtual','synthetic_fixture','synthetic-l1',?,'virtual')",
        (session, "rg:l1-graph:" + project, T0),
    ).lastrowid
    seq = 0

    def event(text, kind, tool, call, stamp):
        nonlocal seq
        seq += 1
        raw = dumps({"synthetic": True, "text": text}).encode()
        sha = store.objects.put(raw)
        identity = store.db.execute(
            "INSERT INTO raw_events(session_pk,file_instance_id,record_index,byte_start,byte_end,"
            "object_sha256,seq,kind,role,tool_name,call_id,occurred_at,recorded_at,line_sha256) "
            "VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?,?)",
            (
                session,
                file,
                seq,
                len(raw),
                sha,
                seq,
                kind,
                "assistant" if kind in {"tool_call", "file_edit"} else "user",
                tool,
                call,
                stamp,
                stamp,
                sha,
            ),
        ).lastrowid
        return identity

    input_event = event("合成输入版本 v1", "user_text", None, None, T0)
    request = event(
        "synthetic --never-execute <script>literal</script>；合成原文。" * 220,
        "tool_call",
        "Bash",
        "synthetic-command",
        T1,
    )
    exited = event(
        "原生退出码为 3；与清单报告 0 独立。", "tool_result", "Bash", "synthetic-command", T3
    )
    identity = "l1:" + digest((project + "run").encode())
    store.db.execute(
        "INSERT INTO runs(run_id,project_id,session_pk,call_id,command,cwd,state,"
        "request_event_id,root_id,requested_at) "
        "VALUES (?,?,?,?,?,?,'requested',?,?,?)",
        (
            identity,
            project,
            session,
            "synthetic-command",
            "synthetic --never-execute <script>literal</script>",
            "/synthetic/file-runs",
            request,
            root,
            T1,
        ),
    )
    for eid, state, code, stamp in ((request, "requested", None, T1), (exited, "exited", 3, T3)):
        store.db.execute(
            "INSERT INTO run_observations VALUES (?,?,?,?,?,NULL,NULL,'{}',?,?)",
            (digest((project + state).encode()), identity, eid, state, code, stamp, stamp),
        )
    before, after = ["l1:" + digest((project + role).encode()) for role in ("before", "after")]
    for version, text in ((before, "old\n"), (after, "new\n")):
        store.db.execute(
            "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,size,source,"
            "evidence_event_id,observed_at,content_sha256,root_id,basis,claim_state,"
            "representation) VALUES (?,?,?,'sha256:tool-utf8',?,?,"
            "'agent_edit',?,?,?,?,'direct_record',"
            "'candidate','tool_reported_utf8')",
            (
                version,
                project,
                "/synthetic/file-runs/code.py",
                digest(text.encode()),
                len(text.encode()),
                input_event if version == before else exited,
                T0 if version == before else T3,
                store.objects.put(text.encode()),
                root,
            ),
        )
    edit_call = event("合成编辑请求", "file_edit", "Edit", "synthetic-edit", T2)
    edit_result = event("合成编辑报告", "tool_result", "Edit", "synthetic-edit", T3)
    store.db.execute(
        "INSERT INTO edit_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            digest((project + "edit").encode()),
            project,
            session,
            "synthetic-edit",
            edit_call,
            edit_result,
            "/synthetic/file-runs/code.py",
            root,
            "update",
            store.objects.put(b"-old\n+new\n"),
            before,
            after,
            None,
            0,
            T3,
            T3,
        ),
    )
    attempt = str(uuid4())
    store.db.execute("INSERT INTO entities VALUES (?,?, 'attempt',NULL,?)", (attempt, project, T0))
    for label in ("合成尝试内容 A", "合成尝试内容 B"):
        append(
            store,
            attempt,
            "entity_version",
            {"label": label, "content": label},
            scope=SCOPE,
            state="confirmed",
            occurred=T0,
            recorded=T0,
        )
    snap = store.db.execute(
        "INSERT INTO workspace_snapshots(project_id,root_id,trigger,skipped,taken_at,"
        "recorded_at,metadata) "
        "VALUES (?,?,'synthetic','timeout',?,?,'{}')",
        (project, root, T1, T1),
    ).lastrowid
    with patch("rg.record.manifest.now", return_value=T2):
        record(
            store,
            project,
            {
                "request_id": str(uuid4()),
                "run_id": identity,
                "attempt_id": attempt,
                "scope": SCOPE,
                "snapshot_id": snap,
                "inputs": [before] * 105 + ["synthetic-version-missing"],
                "scripts": [],
                "outputs": [after],
                "parameters": {
                    "literal": "<script>literal</script>",
                    "command": "$(never_execute)",
                },
                "exit_code": 0,
                "occurred_at": T2,
            },
        )
        record(
            store,
            project,
            {
                "request_id": str(uuid4()),
                "run_id": "synthetic-run-not-observed",
                "inputs": None,
                "scripts": [],
                "outputs": [],
                "occurred_at": T2,
            },
        )
    return project
