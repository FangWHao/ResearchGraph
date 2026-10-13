from __future__ import annotations

from pathlib import Path

import pytest

from rg.extract.linker import pairs
from rg.extract.path_context import PathContext
from rg.extract.paths import file_paths
from rg.extract.segmenter import Segment
from rg.extract.worker import Worker
from rg.extract.working_set import working_set
from rg.ingest.scanner import scan_file
from rg.store import roots
from rg.store.database import Store, dumps
from rg.store.fragments import assign
from tests.conftest import FakeProvider
from tests.golden.test_fragment_assignments import intent
from tests.golden.test_ingestion import lines, record
from tests.golden.test_links_overview import add_object
from tests.golden.test_project_roots import git, scene


def registered(store: Store, tmp_path: Path):
    project, repo, linked = scene(store, tmp_path)
    roots.register(store, project, linked)
    return project, repo, linked


def test_absolute_and_relative_paths_share_repository_key_but_keep_physical_identity(
    store, tmp_path
):
    project, repo, linked = registered(store, tmp_path)
    context = PathContext(store, project)
    main = context.keys({str(repo / "analysis/x.py")})
    branch = context.keys({"../analysis/./x.py"}, str(linked / "nested"))
    assert main & branch == {("repository", (repo / ".git").as_posix(), "analysis/x.py")}
    assert ("absolute", project, (repo / "analysis/x.py").as_posix()) in main
    assert ("absolute", project, (linked / "analysis/x.py").as_posix()) in branch
    assert not main & context.keys({"analysis/x.py"})  # 缺少原生目录，不猜项目根。
    assert not main & context.keys({"../outside/x.py"}, str(linked))


def test_subdirectory_root_uses_actual_worktree_prefix_and_data_root_blocks_git_alias(
    store, tmp_path
):
    project, repo, linked = registered(store, tmp_path)
    nested = linked / "analysis"
    nested.mkdir()
    roots.register(store, project, nested, "repo")
    assert PathContext(store, project).keys({str(nested / "x.py")}) & PathContext(
        store, project
    ).keys({str(repo / "analysis/x.py")})
    data = linked / "data"
    data.mkdir()
    roots.register(store, project, data, "data")
    context = PathContext(store, project)
    assert not context.keys({str(data / "x.csv")}) & context.keys({str(repo / "data/x.csv")})


def test_independent_repositories_and_other_projects_do_not_share_path_keys(store, tmp_path):
    project, repo, linked = registered(store, tmp_path)
    unrelated = tmp_path / "independent"
    unrelated.mkdir()
    git(unrelated, "init", "-b", "main")
    roots.register(store, project, unrelated)
    context = PathContext(store, project)
    assert not context.keys({"analysis/x.py"}, str(repo)) & context.keys(
        {"analysis/x.py"}, str(unrelated)
    )
    extra = tmp_path / "other-worktree"
    git(repo, "worktree", "add", "-b", "other", str(extra))
    store.project("独立项目", [extra])
    context = PathContext(store, project)
    assert not context.keys({str(repo / "analysis/x.py")}) & context.keys(
        {str(linked / "analysis/x.py")}
    )  # 共同目录跨项目登记后不再提供统一仓库键。


def test_unknown_git_identity_retains_exact_path_only_and_never_probes_disk(
    store, tmp_path, monkeypatch
):
    project, repo, linked = registered(store, tmp_path)
    git(repo, "remote", "add", "origin", "https://example.invalid/same.git")
    git(linked, "remote", "set-url", "origin", "https://example.invalid/same.git")
    store.db.execute("UPDATE source_roots SET git_metadata='{}' WHERE path=?", (str(linked),))
    dump = list(store.db.iterdump())
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("不应读取当前工作区"))
    monkeypatch.setattr(Path, "stat", lambda *a, **k: pytest.fail("不应读取当前工作区"))
    context = PathContext(store, project)
    first = context.keys({str(repo / "analysis/x.py")})
    second = context.keys({str(linked / "analysis/x.py")})
    assert not first & second
    assert second == {("absolute", project, (linked / "analysis/x.py").as_posix())}
    assert list(store.db.iterdump()) == dump


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_native_record_context_changes_and_invalid_declaration_blocks_inheritance(
    store, tmp_path, tool
):
    project, repo, linked = registered(store, tmp_path)
    path = tmp_path / "contexts.jsonl"
    if tool == "claude":
        records = [
            record("第一轮", "one", cwd=str(repo)),
            record("第二轮", "two", cwd=str(linked)),
            record("坏目录", "bad", cwd="relative-directory"),
            record("无新声明", "three"),
        ]
    else:
        records = [
            {"type": "session_meta", "payload": {"id": "contexts", "cwd": str(repo)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "第一轮"}},
            {"type": "turn_context", "payload": {"cwd": str(linked)}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "第二轮"}},
            {"type": "turn_context", "payload": {"cwd": "relative-directory"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "无新声明"}},
        ]
    lines(path, records)
    scan_file(store, path, tool, project)
    context = PathContext(store, project)
    ids = [r[0] for r in store.db.execute("SELECT event_id FROM raw_events WHERE kind='user_msg'")]
    assert context.cwd(ids[0]) == repo.as_posix()
    assert context.cwd(ids[1]) == linked.as_posix()
    assert context.cwd(ids[-1]) is None
    explicit = dumps({"workdir": str(repo / "nested"), "file_path": "../analysis/x.py"})
    assert ("repository", (repo / ".git").as_posix(), "analysis/x.py") in context.event_keys(
        ids[1], explicit
    )
    invalid = dumps({"workdir": "relative", "file_path": "analysis/x.py"})
    assert all(key[0] == "literal" for key in context.event_keys(ids[1], invalid))


def test_context_does_not_inherit_across_source_files_or_logical_fragments(store, tmp_path):
    project, repo, _ = registered(store, tmp_path)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    lines(first, [record("第一条", "first", cwd=str(repo)), record("同来源后续", "next")])
    lines(second, [record("另一来源", "second")])
    scan_file(store, first, "claude", project)
    scan_file(store, second, "claude", project)
    assert PathContext(store, project).cwd(2) == repo.as_posix()
    assert PathContext(store, project).cwd(3) is None
    assign(store, intent(store, project, first=2, last=2))
    assert PathContext(store, project).cwd(2) is None


def test_legacy_unobserved_record_does_not_use_mutable_session_directory(store, tmp_path):
    project, repo, _ = registered(store, tmp_path)
    source = tmp_path / "legacy.jsonl"
    lines(source, [record("已有观测", "observed", cwd=str(repo))])
    scan_file(store, source, "claude", project)
    # 模拟旧解析器保存过原件、但尚未写入目录账本的一条物理记录；原记录不改写。
    cursor = store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,object_sha256,"
        "seq,kind,role,recorded_at,line_sha256) "
        "SELECT session_pk,file_instance_id,byte_end+1,byte_end+2,object_sha256,seq+1,kind,"
        "role,recorded_at,line_sha256 FROM raw_events WHERE event_id=1"
    )
    assert PathContext(store, project).cwd(cursor.lastrowid) is None


@pytest.mark.parametrize("style", ["drive", "unc"])
def test_windows_paths_resolve_lexically_without_native_os_or_case_guessing(store, tmp_path, style):
    project = store.project("合成Windows项目", [tmp_path / "placeholder"])
    if style == "drive":
        main, branch, common = r"D:\合成 仓库", r"D:\另一 工作树", r"D:\合成 仓库\.git"
    else:
        main, branch, common = (
            r"\\server\share\main",
            r"\\server\share\branch",
            r"\\server\share\main\.git",
        )
    for index, value in enumerate((main, branch)):
        store.db.execute(
            "INSERT INTO source_roots(root_id,project_id,host_id,path,kind,git_common_dir,"
            "git_metadata) "
            "VALUES (?,?,'local',?,'repo',?,?)",
            (
                str(index),
                project,
                value,
                common,
                dumps({"status": "registered", "worktree": value, "git_common_dir": common}),
            ),
        )
    context = PathContext(store, project)
    first = context.keys({main + r"\analysis\x.py"})
    other = context.keys({r"..\analysis\x.py"}, branch.replace("\\", "/") + "/nested")
    assert len(first & other) == 1
    assert not first & context.keys({"analysis/X.py"}, branch.replace("\\", "/"))


def test_structured_and_quoted_paths_keep_spaces_and_backslashes():
    path = r"D:\合成 仓库\analysis\x.py"
    assert file_paths(dumps({"file_path": path})) == {"D:/合成 仓库/analysis/x.py"}
    assert file_paths(f"讨论 `{path}` 的变化") == {"D:/合成 仓库/analysis/x.py"}
    assert file_paths('文件 "/work/合成 仓库/x.py"') == {"/work/合成 仓库/x.py"}


@pytest.mark.parametrize("relative", [False, True])
def test_working_set_selects_cross_worktree_path_with_native_context_and_exact_scope(
    store, tmp_path, relative
):
    project, repo, linked = registered(store, tmp_path)
    mentioned = "analysis/x.py" if relative else str(repo / "analysis/x.py")
    claim, event = add_object(
        store,
        tmp_path,
        project,
        "甲",
        label="独立方案",
        content=f'文件 "{mentioned}"',
        cwd=str(repo),
    )
    path = tmp_path / "request.jsonl"
    call = {
        "type": "assistant",
        "uuid": "call",
        "sessionId": "request",
        "cwd": str(linked),
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "id": "read",
                    "input": {"file_path": "analysis/x.py"},
                }
            ]
        },
    }
    lines(path, [call])
    scan_file(store, path, "claude", project)
    query = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
    item = Segment("paths", [{"event_id": query, "kind": "tool_call"}])
    worker = Worker(store, FakeProvider())
    keys = worker._paths(item, project)
    cards = working_set(
        store,
        project,
        "无关键词",
        FakeProvider(),
        path_keys=keys,
        scope={"dataset_version": "data_v2"},
    )
    assert [card["label"] for card in cards] == ["独立方案"]
    assert cards[0]["state"] == "proposed"
    assert (
        working_set(
            store,
            project,
            "无关键词",
            FakeProvider(),
            path_keys=keys,
            scope={"dataset_version": "data_v3"},
        )
        == []
    )
    assert store.claim_state(claim) == "candidate" and event != query


def test_cross_worktree_link_requires_equal_digest_algorithm_and_repo_relative_path(
    store, tmp_path
):
    project, repo, linked = registered(store, tmp_path)
    first = add_object(store, tmp_path, project, "甲", label="独立甲")
    second = add_object(store, tmp_path, project, "乙", label="独立乙")
    for index, (root, event) in enumerate(((repo, first[1]), (linked, second[1]))):
        store.db.execute(
            "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,source,"
            "evidence_event_id) "
            "VALUES (?, ?, ?, 'sha256', 'same-bytes', 'agent_edit', ?)",
            (str(index), project, str(root / "analysis/x.py"), event),
        )
    before = list(store.db.iterdump())
    assert pairs(store, project)[0].basis == ["same_file_version"]
    assert list(store.db.iterdump()) == before
    for field, value in (
        ("digest", "different"),
        ("algo", "sha256:tool-utf8"),
        ("path", str(linked / "analysis/y.py")),
    ):
        original = store.db.execute(
            f"SELECT {field} FROM artifact_versions WHERE version_id='1'"
        ).fetchone()[0]
        store.db.execute(f"UPDATE artifact_versions SET {field}=? WHERE version_id='1'", (value,))
        assert pairs(store, project) == []
        store.db.execute(
            f"UPDATE artifact_versions SET {field}=? WHERE version_id='1'", (original,)
        )
    assert store.claim_state(first[0]) == store.claim_state(second[0]) == "candidate"


def test_path_policy_participates_in_persisted_worker_configuration(store):
    worker = Worker(store, FakeProvider())
    assert any(
        isinstance(value, dict) and value.get("path_identity") for value in worker.configuration()
    )
