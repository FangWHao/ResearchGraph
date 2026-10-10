from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from rg.cli.main import parser, run
from rg.ingest.scanner import scan_file
from rg.snapshot.config import choose, export_registry, load_registry
from rg.snapshot.git import Deadline, Git
from rg.store import clear, migrations, roots
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps
from rg.store.objects import digest


def git(path: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True).stdout


def repository(tmp_path: Path) -> Path:
    path = tmp_path / "合成 仓库"
    path.mkdir()
    git(path, "init", "-b", "main")
    (path / "text.txt").write_bytes(b"original\n")
    git(path, "add", ".")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "合成")
    return path


def scene(store: Store, tmp_path: Path):
    repo = repository(tmp_path)
    linked = tmp_path / "另一 工作树"
    git(repo, "worktree", "add", "-b", "another", str(linked))
    project = store.project("多工作树合成项目", [repo])
    return project, repo, linked


def cli(root: Path, *args: str):
    return subprocess.run(
        [str(Path(sys.executable).with_name("rg")), "--data-dir", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_and_linked_worktree_keep_distinct_roots_with_same_git_identity(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    old = roots.listing(store, project)
    first = old["roots"][0]
    result = roots.register(store, project, linked)
    second = result["root"]
    assert result["created"] and result["revision"] == old["revision"] + 1
    assert first["kind"] == "repo" and second["kind"] == "worktree"
    assert first["git_common_dir"] == second["git_common_dir"] == str(repo / ".git")
    assert first["root_id"] != second["root_id"]
    assert first["git_metadata"]["git_dir"] != second["git_metadata"]["git_dir"]
    assert second["git_metadata"]["worktree"] == str(linked)
    export_registry(store)
    assert choose(store.root, str(linked / "child"))["root_id"] == second["root_id"]
    assert choose(store.root, str(repo))["root_id"] == first["root_id"]
    with pytest.raises(ConflictError):
        roots.listing(store, project, expected_revision=old["revision"])


def test_remote_urls_are_only_exact_digests_and_never_in_db_or_cli_output(store, tmp_path):
    repo = repository(tmp_path)
    url = "https://synthetic-user:synthetic-test-secret@example.invalid/group/repo.git?token=fake"
    git(repo, "remote", "add", "origin", url)
    git(repo, "config", "--add", "remote.origin.url", "ssh://git@example.invalid/group/repo.git")
    project = store.project("合成远端标识", [repo])
    view = roots.listing(store, project)
    metadata = view["roots"][0]["git_metadata"]
    assert metadata["status"] == "registered"
    assert metadata["remote_scope"] == "local_without_includes"
    assert {r["url_sha256"] for r in metadata["remotes"]} == {
        digest(url.encode()),
        digest(b"ssh://git@example.invalid/group/repo.git"),
    }
    assert {r["name"] for r in metadata["remotes"]} == {"origin"}
    output = cli(store.root, "project", "roots", "--project", project)
    assert output.returncode == 0 and json.loads(output.stdout) == view
    for text in (dumps(view), "\n".join(store.db.iterdump()), output.stdout, output.stderr):
        assert "synthetic-test-secret" not in text and url not in text


def test_git_registration_does_not_run_repository_commands_or_change_user_index(
    store,
    tmp_path,
    monkeypatch,
):
    repo = repository(tmp_path)
    marker = tmp_path / "must-not-exist"
    git(repo, "config", "core.fsmonitor", f"touch '{marker}'")
    git(repo, "config", "filter.bad.clean", f"touch '{marker}'")
    git(repo, "config", "alias.rev-parse", f"!touch '{marker}'")
    git(repo, "remote", "add", "malicious", f"ext::sh -c 'touch {marker}'")
    (repo / "text.txt").write_bytes(b"staged\n")
    git(repo, "-c", "core.fsmonitor=false", "add", "text.txt")
    (repo / "text.txt").write_bytes(b"unstaged\n")
    before = {
        p: p.read_bytes()
        for p in (repo / ".git" / "index", repo / ".git" / "config", repo / "text.txt")
    }
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "incorrect.git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch '{marker}'")
    project = store.project("不执行仓库命令", [repo])
    assert roots.listing(store, project)["roots"][0]["git_common_dir"] == str(repo / ".git")
    assert not marker.exists() and all(path.read_bytes() == data for path, data in before.items())
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_config_includes_are_not_read(store, tmp_path):
    repo = repository(tmp_path)
    included = tmp_path / "outside.config"
    included.write_text('[remote "external"]\nurl = https://example.invalid/must-not-be-read\n')
    git(repo, "config", "include.path", str(included))
    project = store.project("不展开配置引用", [repo])
    assert roots.listing(store, project)["roots"][0]["git_metadata"]["remotes"] == []


def test_paths_with_quotes_newlines_and_shell_syntax_are_not_executed(store, tmp_path):
    repo = repository(tmp_path)
    unusual = tmp_path / " '仓库\n$(touch NEVER_EXECUTE) "
    repo.rename(unusual)
    project = store.project("原样路径", [unusual])
    row = roots.listing(store, project)["roots"][0]
    assert row["path"] == str(unusual)
    assert row["git_metadata"]["worktree"] == str(unusual)
    assert row["git_common_dir"] == str(unusual / ".git")
    assert not (tmp_path / "NEVER_EXECUTE").exists()


def test_oversized_metadata_is_unavailable_without_storing_partial_remote_ids(store, tmp_path):
    repo = repository(tmp_path)
    git(repo, "config", "remote.large.url", "https://example.invalid/" + "x" * 65536)
    project = store.project("超限元数据", [repo])
    row = roots.listing(store, project)["roots"][0]
    assert row["git_common_dir"] is None and row["git_metadata"] == {"status": "unavailable"}


def test_common_git_directory_does_not_override_explicit_project_registration(store, tmp_path):
    first, repo, linked = scene(store, tmp_path)
    second = store.project("单独登记的研究项目", [])
    registered = roots.register(store, second, linked)["root"]
    assert registered["project_id"] == second
    assert registered["git_common_dir"] == roots.listing(store, first)["roots"][0]["git_common_dir"]
    assert roots.listing(store, first)["total"] == roots.listing(store, second)["total"] == 1


def test_repeat_preserves_root_id_metadata_and_revision_after_git_source_changes(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    first = roots.register(store, project, linked)
    before = list(store.db.iterdump())
    git(repo, "remote", "add", "new", "https://example.invalid/new")
    second = roots.register(store, project, linked, "worktree")
    assert second["root"] == first["root"] and not second["created"]
    assert second["revision"] == first["revision"] and list(store.db.iterdump()) == before


@pytest.mark.parametrize("kind", ["data", "alias"])
def test_non_git_data_and_alias_registration_are_explicit(store, tmp_path, monkeypatch, kind):
    project = store.project("非 Git", [])
    path = tmp_path / "registered-data"
    path.mkdir()
    if kind == "data":

        def forbidden(*args, **kwargs):
            raise AssertionError("数据根不应读取 Git 配置")

        monkeypatch.setattr(Git, "run", forbidden)
    row = roots.register(store, project, path, kind)["root"]
    assert row["kind"] == kind and row["git_common_dir"] is None
    assert row["git_metadata"]["status"] == ("not_probed" if kind == "data" else "not_git")
    export_registry(store)
    assert (row["root_id"] in {v["root_id"] for v in load_registry(store.root)}) == (
        kind == "alias"
    )


def test_initial_cli_alias_keeps_alias_kind(store, tmp_path):
    repo = repository(tmp_path)
    alias = tmp_path / "alias"
    alias.mkdir()
    output = run(
        parser().parse_args(
            [
                "project",
                "add",
                "合成别名",
                "--root",
                str(repo),
                "--alias",
                str(alias),
            ]
        ),
        store,
    )
    values = roots.listing(store, output["project_id"])["roots"]
    assert {(v["path"], v["kind"]) for v in values} == {(str(repo), "repo"), (str(alias), "alias")}


def test_missing_root_stays_missing_without_creating_or_reading_directories(store, tmp_path):
    project = store.project("未来目录", [])
    path = tmp_path / "not-yet-created"
    value = roots.register(store, project, path)["root"]
    assert value["git_metadata"] == {"status": "missing"}
    assert not path.exists()


@pytest.mark.parametrize("kind", ["data", "alias", "repo"])
def test_duplicate_path_cannot_move_project_or_change_explicit_kind(store, tmp_path, kind):
    first = store.project("第一项目", [])
    second = store.project("第二项目", [])
    path = tmp_path / "registered"
    roots.register(store, first, path, kind)
    before = list(store.db.iterdump())
    with pytest.raises(ValueError, match="不能静默替换"):
        roots.register(store, second, path, kind)
    other_kind = "alias" if kind != "alias" else "data"
    with pytest.raises(ValueError, match="不能静默替换"):
        roots.register(store, first, path, other_kind)
    assert list(store.db.iterdump()) == before


def test_equal_remote_digests_do_not_merge_projects_or_guess_session_ownership(store, tmp_path):
    repo1 = repository(tmp_path)
    parent = tmp_path / "other"
    parent.mkdir()
    repo2 = repository(parent)
    for repo in (repo1, repo2):
        git(repo, "remote", "add", "origin", "https://example.invalid/same")
    one = store.project("第一项目", [repo1])
    two = store.project("第二项目", [repo2])
    assert one != two
    source = tmp_path / "synthetic.jsonl"
    source.write_text(
        dumps(
            {
                "type": "user",
                "uuid": "synthetic-root-event",
                "sessionId": "synthetic-session",
                "cwd": str(repo1),
                "message": {"content": "应留在收件箱"},
            }
        )
        + "\n"
    )
    scan_file(store, source, "claude")
    row = store.db.execute("SELECT project_id,project_basis FROM sessions").fetchone()
    assert tuple(row) == (None, "unassigned")
    assert (
        roots.listing(store, one)["roots"][0]["git_common_dir"]
        != roots.listing(store, two)["roots"][0]["git_common_dir"]
    )


def test_listing_is_paginated_revision_checked_and_does_not_touch_sources(
    store, tmp_path, monkeypatch
):
    project = store.project("分页", [])
    other = store.project("隔离", [tmp_path / "other"])
    for i in range(3):
        roots.register(store, project, tmp_path / f"root{i}")
    before = list(store.db.iterdump())

    def forbidden(*args, **kwargs):
        raise AssertionError("只读清单不得探测文件系统")

    monkeypatch.setattr(roots, "_git_identity", forbidden)
    with closing(Store(store.root, readonly=True)) as readonly:
        first = roots.listing(readonly, project, 2)
        second = roots.listing(readonly, project, 2, first["next_offset"], first["revision"])
    assert first["total"] == 3 and first["next_offset"] == 2 and second["next_offset"] is None
    assert len({v["root_id"] for v in first["roots"] + second["roots"]}) == 3
    assert all(v["project_id"] != other for v in first["roots"] + second["roots"])
    assert list(store.db.iterdump()) == before


@pytest.mark.parametrize("limit,offset", [(0, 0), (201, 0), (1, -1)])
def test_invalid_pagination_is_rejected(store, limit, offset):
    with pytest.raises(ValueError, match="分页"):
        roots.listing(store, store.project("合成", []), limit, offset)


@pytest.mark.parametrize("kind", ["worktree", "invalid"])
def test_invalid_registration_does_not_write_anything(store, tmp_path, kind):
    project = store.project("合成", [])
    path = tmp_path / "plain"
    path.mkdir()
    before = list(store.db.iterdump())
    with pytest.raises(ValueError):
        roots.register(store, project, path, kind)
    assert list(store.db.iterdump()) == before


def test_unknown_project_and_regular_file_are_rejected_before_registration(
    store, tmp_path, monkeypatch
):
    project = store.project("合成", [])
    path = tmp_path / "file"
    path.write_text("不是目录")
    before = list(store.db.iterdump())

    def forbidden(*args, **kwargs):
        raise AssertionError("非法登记不得探测 Git")

    monkeypatch.setattr(roots, "_git_identity", forbidden)
    with pytest.raises(ValueError, match="项目不存在"):
        roots.register(store, "missing", tmp_path)
    with pytest.raises(ValueError, match="必须是目录"):
        roots.register(store, project, path)
    assert list(store.db.iterdump()) == before


def test_probe_failure_is_unknown_and_cannot_fabricate_worktree_identity(
    store, tmp_path, monkeypatch
):
    project = store.project("失败探测", [])
    path = tmp_path / "work"
    path.mkdir()

    def timeout(*args, **kwargs):
        raise Deadline("synthetic-private-message")

    monkeypatch.setattr(Git, "run", timeout)
    row = roots.register(store, project, path)["root"]
    assert row["git_common_dir"] is None and row["git_metadata"] == {"status": "unavailable"}
    assert "synthetic-private-message" not in "\n".join(store.db.iterdump())
    with pytest.raises(ValueError, match="worktree"):
        roots.register(store, project, tmp_path, "worktree")


def test_v17_migration_is_atomic_preserves_l0_and_does_not_probe_old_roots(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    source = tmp_path / "synthetic.jsonl"
    source.write_text(
        dumps({"type": "user", "sessionId": "legacy", "message": {"content": "原文"}}) + "\n"
    )
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "LATEST_VERSION", 17)
        with closing(Store(root)) as store:
            project = store.project("旧登记", [tmp_path / "old-root"])
            scan_file(store, source, "claude", project)
            raw = store.raw(1)
            old_root = tuple(store.db.execute("SELECT * FROM source_roots").fetchone())
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 18, (*migrations.MIGRATIONS[18], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17
        assert "git_metadata" not in {v[1] for v in db.execute("PRAGMA table_info(source_roots)")}

    def forbidden(*args, **kwargs):
        raise AssertionError("升级不得探测旧根目录")

    monkeypatch.setattr(roots, "_git_identity", forbidden)
    with closing(Store(root)) as store:
        assert store.raw(1) == raw and roots.listing(store, project)["roots"][0][
            "git_metadata"
        ] == {"status": "not_recorded"}
        assert tuple(store.db.execute("SELECT * FROM source_roots").fetchone())[:-1] == old_root
    with closing(Store(root)) as store:
        assert store.raw(1) == raw and roots.listing(store, project)["total"] == 1


def test_backup_keeps_registration_when_original_repositories_disappear(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    roots.register(store, project, linked)
    export_registry(store)
    before = roots.listing(store, project)
    target = tmp_path / "backup"
    backup(store, target)
    shutil.rmtree(repo)
    shutil.rmtree(linked)
    with closing(Store(target, readonly=True)) as restored:
        assert roots.listing(restored, project) == before
        assert load_registry(target) == load_registry(store.root)


def test_new_root_metadata_is_cleared_and_cannot_recreate_cleared_source(tmp_path):
    root = tmp_path / "store"
    work = tmp_path / "workspace"
    work.mkdir()
    (work / "protected.txt").write_text("保留研究工作区")
    with closing(Store(root)) as store:
        project = store.project("清除合成项目", [])
        roots.register(store, project, work, "data")
    preview = clear.preview(root, project)
    assert preview["blockers"] == []
    clear.execute(root, project, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        assert store.db.execute("SELECT count(*) FROM source_roots").fetchone()[0] == 0
        other = store.project("新项目", [])
        with pytest.raises(PermissionError, match="已整项目清除"):
            roots.register(store, other, work, "data")
        with pytest.raises(PermissionError, match="已整项目清除"):
            store.project("不能重建来源", [work])
    assert (work / "protected.txt").read_text() == "保留研究工作区"


def test_actual_cli_add_append_and_readonly_missing_db_behavior(tmp_path):
    root = tmp_path / "db"
    work = tmp_path / "work"
    work.mkdir()
    created = cli(root, "project", "add", "CLI合成", "--root", str(work))
    assert created.returncode == 0, created.stderr
    project = json.loads(created.stdout)["project_id"]
    path = tmp_path / "data"
    path.mkdir()
    args = ["project", "root-add", "--project", project, "--path", str(path), "--kind", "data"]
    added = cli(root, *args)
    assert added.returncode == 0 and json.loads(added.stdout)["created"]
    repeated = cli(root, *args)
    assert repeated.returncode == 0 and not json.loads(repeated.stdout)["created"]
    viewed = cli(root, "project", "roots", "--project", project, "--limit", "1")
    assert viewed.returncode == 0 and json.loads(viewed.stdout)["total"] == 2
    assert len(load_registry(root)) == 1
    missing = tmp_path / "must-not-create"
    rejected = cli(missing, "project", "roots", "--project", project)
    assert rejected.returncode != 0 and not missing.exists()
