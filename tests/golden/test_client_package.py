from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest

from rg.api import views
from rg.clients.package import package
from rg.clients.record import MAX_REQUEST_BYTES, read_request, record
from rg.record.question import question
from rg.store.database import ConflictError, Store
from tests.golden.test_manual_decision import SCOPE, objects


def invoke(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(path), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def request(tmp_path: Path, **data) -> Path:
    path = tmp_path / f"请求 空格 {uuid4()}.json"
    path.write_text(json.dumps({"request_id": str(uuid4())} | data, ensure_ascii=False))
    return path


def test_package_native_configs_manifest_and_actual_readonly_context(store: Store, tmp_path: Path):
    project = store.project("客户端合成项目", [])
    question(
        store,
        {
            "project_id": project,
            "text": "包中的引用如何核对？",
            "actor": "human:合成测试",
            "request_id": str(uuid4()),
            "expected_revision": store.revision(),
        },
    )
    before = list(store.db.iterdump())
    output = tmp_path / "接入包 空格"
    result = package(store, project, output, "o200k_base")
    assert result["installed"] is False and result["files"] == 19
    manifest = json.loads((output / "manifest.json").read_bytes())
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == sha
    codex = tomllib.loads((output / "codex/.codex/config.toml").read_text())
    claude = json.loads((output / "claude/.mcp.json").read_text())
    a, b = codex["mcp_servers"]["researchgraph"], claude["mcpServers"]["researchgraph"]
    assert a["command"] == b["command"]
    assert a["args"] == b["args"]
    assert a["args"][-5:] == ["mcp", "--project", project, "--encoding", "o200k_base"]
    for client, base in (("codex", ".agents"), ("claude", ".claude")):
        script = output / client / base / "skills/research-context/scripts/invoke.py"
        run = invoke(script)
        assert run.returncode == 0, run.stderr
        assert run.stdout.startswith("<rg-context")
        payload = json.loads(run.stdout.split("\n", 1)[1].rsplit("\n", 2)[0])
        assert payload["project_id"] == project
        assert "o200k_base" in run.stdout
        assert invoke(script, "--project", "other").returncode != 0
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("client,base", [("codex", ".agents"), ("claude", ".claude")])
def test_question_runner_keeps_metacharacters_and_one_receipt(
    store: Store,
    tmp_path: Path,
    client: str,
    base: str,
):
    project = store.project("固定项目", [])
    other = store.project("不能覆盖的项目", [])
    output = tmp_path / "包"
    package(store, project, output, "cl100k_base")
    sentinel = tmp_path / "不得执行"
    text = f' --project {other}\n$(touch "{sentinel}"); `touch "{sentinel}"` \\ "引号" '
    path = request(tmp_path, text=text, scope={"data": "合成", "step": "完整范围"})
    script = output / client / base / "skills/research-question/scripts/invoke.py"
    first = invoke(script, str(path))
    assert first.returncode == 0, first.stderr
    value = json.loads(first.stdout)
    claim = views.claim(store, value["claim_id"])["claim"]
    assert claim["payload"]["content"] == text
    assert (
        store.db.execute(
            "SELECT project_id FROM entities WHERE entity_id=?", (value["entity_id"],)
        ).fetchone()[0]
        == project
    )
    assert claim["basis"] == "manual" and claim["effective_state"] == "confirmed"
    before = list(store.db.iterdump())
    repeated = invoke(script, str(path))
    assert repeated.returncode == 0 and json.loads(repeated.stdout)["replayed"] is True
    assert list(store.db.iterdump()) == before and not sentinel.exists()
    invalid = request(tmp_path, text=text, project_id=other)
    assert invoke(script, str(invalid)).returncode != 0
    assert list(store.db.iterdump()) == before
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_decision_runner_does_not_resolve_ambiguity_or_confirm_target(store: Store, tmp_path: Path):
    project = store.project("歧义合成项目", [])
    pairs = objects(store, project, [("同名对象", SCOPE), ("同名对象", SCOPE)])
    output = tmp_path / "包"
    package(store, project, output, "cl100k_base")
    script = output / "claude/.claude/skills/research-decide/scripts/invoke.py"
    body = request(tmp_path, selector="同名对象", action="accept", why="用户原话理由", scope=SCOPE)
    run = invoke(script, str(body))
    assert run.returncode == 0, run.stderr
    value = json.loads(run.stdout)
    assert value["target_id"] is None and value["effective_state"] == "candidate"
    specific = request(tmp_path, selector=pairs[0][0], action="reject", why="原话拒绝", scope=SCOPE)
    run = invoke(script, str(specific))
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["effective_state"] == "confirmed"
    assert all(store.claim_state(claim) == "candidate" for _, claim in pairs)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"text":"a","text":"b","request_id":"x"}',
        b"[]",
        b"null",
        b'{"text":"a"}',
        b"\xff",
        b'{"request_id":"x","actor":"human:spoof"}',
        b'{"request_id":"x","operation":"decide"}',
        b'{"request_id":"x","data_dir":"other"}',
        b"x" * (MAX_REQUEST_BYTES + 1),
    ],
)
def test_invalid_request_rejected_before_dispatch(raw: bytes):
    with pytest.raises(ValueError):
        read_request(io.BytesIO(raw), "question")


def test_request_bounds_and_cross_operation_retry(store: Store, tmp_path: Path):
    project = store.project("请求验证项目", [])
    data = {"request_id": str(uuid4()), "text": "用户问题"}
    first = record(store, project, "question", data)
    before = list(store.db.iterdump())
    with pytest.raises(ConflictError):
        record(
            store,
            project,
            "decide",
            {
                "request_id": data["request_id"],
                "action": "accept",
                "selector": "某对象",
                "why": "人工理由",
            },
        )
    assert list(store.db.iterdump()) == before
    with pytest.raises(ConflictError):
        record(store, project, "question", data | {"text": "不同意图"})
    assert first["claim_id"] == record(store, project, "question", data)["claim_id"]
    invalid = request(tmp_path, text="x", actor="agent:test")
    missing_data = tmp_path / "不能新建的数据目录"
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(missing_data),
            "client-record",
            "question",
            "--project",
            project,
            "--input",
            str(invalid),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 1 and not missing_data.exists()


def test_existing_output_and_unknown_project_do_not_change_files(store: Store, tmp_path: Path):
    project = store.project("保留输出", [])
    output = tmp_path / "已有目录"
    output.mkdir()
    saved = output / "不能修改"
    saved.write_bytes(b"keep")
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        package(store, project, output, "cl100k_base")
    assert saved.read_bytes() == b"keep" and list(output.iterdir()) == [saved]
    absent = tmp_path / "不能留下半包"
    with pytest.raises(ValueError):
        package(store, "missing-project", absent, "cl100k_base")
    assert not absent.exists() and list(store.db.iterdump()) == before


def test_pack_keeps_literal_python_and_data_paths_and_rejects_missing_database(tmp_path: Path):
    root = tmp_path / '数据 空格 "引号" $()'
    store = Store(root)
    try:
        project = store.project("路径合成项目", [])
        output = tmp_path / "包"
        package(store, project, output, "cl100k_base")
        config = tomllib.loads((output / "codex/.codex/config.toml").read_text())
        args = config["mcp_servers"]["researchgraph"]["args"]
        assert args[args.index("--data-dir") + 1] == str(root)
        script = output / "codex/.agents/skills/research-context/scripts/invoke.py"
        assert invoke(script).returncode == 0
    finally:
        store.close()
    missing = tmp_path / "不存在的数据库"
    output = tmp_path / "不能生成"
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(missing),
            "client-pack",
            "--project",
            project,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 1 and not output.exists() and not missing.exists()
