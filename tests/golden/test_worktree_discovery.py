from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.ingest.watch import cycle
from rg.snapshot import config
from rg.snapshot.git import Git
from rg.store import clear, migrations, roots, worktrees
from rg.store.database import ConflictError, Store
from rg.store.objects import digest
from tests.directory_aliases import directory_alias
from tests.golden.test_ingestion import lines, record
from tests.golden.test_project_roots import git, repository, scene
from tests.test_migrations import legacy_store


def enable(store: Store, project: str, value: bool = True):
    return worktrees.configure(store, project, value, store.revision())


def root_paths(store: Store, project: str):
    return {
        r[0]
        for r in store.db.execute("SELECT path FROM source_roots WHERE project_id=?", (project,))
    }


def cli(root: Path, *arguments: str):
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(root),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_default_disabled_does_not_probe_or_register(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    before = list(store.db.iterdump())
    monkeypatch.setattr(Git, "run", lambda *a, **k: pytest.fail("未开启不应探测 Git"))
    assert worktrees.discover(store) == {}
    assert worktrees.listing(store, project)["follow_enabled"] is False
    assert root_paths(store, project) == {str(repo)}
    assert str(linked) not in root_paths(store, project)
    assert list(store.db.iterdump()) == before


def test_cycle_discovers_identity_without_changing_history_or_research(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    transcript = tmp_path / "public.jsonl"
    lines(transcript, [record("公开合成未归属会话")])
    scan_file(store, transcript, "claude")
    original = store.raw(1)
    sessions = [tuple(r) for r in store.db.execute("SELECT * FROM sessions")]
    first = dict(store.db.execute("SELECT * FROM source_roots").fetchone())
    index = (repo / ".git" / "index").read_bytes()
    settings = (repo / ".git" / "config").read_bytes()
    enable(store, project)
    assert cycle(store)["worktrees_created"] == 1
    values = worktrees.listing(store, project)
    assert values["total"] == 2 and values["follow_enabled"]
    new = next(r for r in values["roots"] if r["path"] == str(linked))
    assert new["kind"] == "worktree" and new["root_id"] != first["root_id"]
    assert new["git_common_dir"] == first["git_common_dir"]
    assert new["git_metadata"]["discovered_from"] == first["root_id"]
    assert new["git_metadata"]["discovery_basis"] == "unique_git_common_dir"
    assert config.choose(store.root, str(linked / "child"))["root_id"] == new["root_id"]
    assert worktrees.discover(store, project).get("worktrees_created", 0) == 0
    assert store.raw(1) == original
    assert [tuple(r) for r in store.db.execute("SELECT * FROM sessions")] == sessions
    assert (
        dict(
            store.db.execute(
                "SELECT * FROM source_roots WHERE root_id=?", (first["root_id"],)
            ).fetchone()
        )
        == first
    )
    assert store.db.execute("SELECT count(*) FROM claims").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
    assert (repo / ".git" / "index").read_bytes() == index
    assert (repo / ".git" / "config").read_bytes() == settings
    assert (linked / "text.txt").read_bytes().replace(b"\r\n", b"\n") == b"original\n"


def test_disable_preserves_registered_history_and_reenable_finds_new(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    worktrees.discover(store, project)
    enable(store, project, False)
    newer = tmp_path / "later"
    git(repo, "worktree", "add", "--detach", str(newer))
    before = list(store.db.iterdump())
    assert worktrees.discover(store, project) == {}
    assert list(store.db.iterdump()) == before
    assert root_paths(store, project) == {str(repo), str(linked)}
    enable(store, project)
    assert worktrees.discover(store, project)["worktrees_created"] == 1
    assert root_paths(store, project) == {str(repo), str(linked), str(newer)}


def test_shared_common_dir_across_projects_is_held_without_reassignment(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    other = store.project("另一明确项目", [linked])
    enable(store, project)
    enable(store, other)
    before = [tuple(r) for r in store.db.execute("SELECT * FROM source_roots")]
    assert worktrees.discover(store)["worktrees_held"] == 2
    assert [tuple(r) for r in store.db.execute("SELECT * FROM source_roots")] == before
    for owner in (project, other):
        view = worktrees.listing(store, owner)
        assert view["roots"][0]["discovery"]["status"] == "ambiguous"
        assert (other if owner == project else project) not in json.dumps(view)


def test_same_remote_does_not_join_distinct_common_directories(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    separate = tmp_path / "separate"
    separate.mkdir()
    foreign = repository(separate)
    url = "https://example.invalid/synthetic.git"
    git(repo, "remote", "add", "origin", url)
    git(foreign, "remote", "add", "origin", url)
    other = store.project("独立共同目录", [foreign])
    enable(store, project)
    assert worktrees.discover(store, project)["worktrees_created"] == 1
    assert root_paths(store, other) == {str(foreign)}
    assert root_paths(store, project) == {str(repo), str(linked)}


def test_invalid_or_foreign_git_list_cannot_register_a_root(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    separate = tmp_path / "other"
    separate.mkdir()
    foreign = repository(separate)
    enable(store, project)
    original = Git.run
    foreign_list = git(foreign, "worktree", "list", "--porcelain", "-z")

    def replacement(self, *args, **kwargs):
        return foreign_list if args[:2] == ("worktree", "list") else original(self, *args, **kwargs)

    monkeypatch.setattr(Git, "run", replacement)
    assert worktrees.discover(store, project).get("worktrees_created", 0) == 0
    assert root_paths(store, project) == {str(repo)}
    detail = worktrees.listing(store, project)["roots"][0]["discovery"]["details"]
    assert detail["changed_identity"] == 1


@pytest.mark.parametrize(
    "suffix", [b"", b"\0", b"\0\0unexpected x\0\0", b"\0branch refs/heads/a\0branch duplicate\0\0"]
)
def test_incomplete_unknown_or_duplicate_list_is_not_a_partial_success(tmp_path, suffix):
    raw = b"worktree " + str(tmp_path).encode() + b"\0HEAD " + b"a" * 40 + suffix
    with pytest.raises(ValueError):
        worktrees.parse_porcelain(raw)


def test_oversize_or_duplicate_list_is_rejected_whole(tmp_path, monkeypatch):
    raw = b"worktree " + str(tmp_path).encode() + b"\0HEAD " + b"a" * 40 + b"\0\0"
    with pytest.raises(ValueError, match="重复"):
        worktrees.parse_porcelain(raw + raw)
    monkeypatch.setattr(worktrees, "MAX_LIST_BYTES", len(raw) - 1)
    with pytest.raises(ValueError, match="超限"):
        worktrees.parse_porcelain(raw)


def test_invalid_list_observation_contains_digest_without_git_payload(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    raw = b"worktree /private-synthetic-marker\0HEAD truncated\0\0"
    original = Git.run

    def replacement(self, *args, **kwargs):
        return raw if args[:2] == ("worktree", "list") else original(self, *args, **kwargs)

    monkeypatch.setattr(Git, "run", replacement)
    assert worktrees.discover(store, project)["worktrees_held"] == 1
    observation = worktrees.listing(store, project)["roots"][0]["discovery"]
    assert observation["status"] == "invalid_or_oversized_list"
    assert observation["input_sha256"] == digest(raw)
    assert "private-synthetic-marker" not in "\n".join(store.db.iterdump())


@pytest.mark.skipif(os.name == "nt", reason="Windows 文件名不允许换行及部分符号")
def test_nul_listing_preserves_newlines_and_never_executes_path_text(store, tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "executed"
    linked = tmp_path / "工作树\n$(touch executed)`quoted`"
    git(repo, "worktree", "add", "--detach", str(linked))
    project = store.project("NUL 路径", [repo])
    enable(store, project)
    assert worktrees.discover(store, project)["worktrees_created"] == 1
    assert str(linked) in root_paths(store, project) and not marker.exists()


def test_nested_same_project_chooses_deepest_root_but_cross_project_remains_ambiguous(
    store, tmp_path
):
    repo = repository(tmp_path)
    nested = repo / "nested"
    git(repo, "worktree", "add", "--detach", str(nested))
    project = store.project("嵌套工作树", [repo])
    enable(store, project)
    assert worktrees.discover(store, project)["worktrees_created"] == 1
    assert config.choose(store.root, str(nested / "child"))["path"] == str(nested)
    other = store.project("其它嵌套数据", [])
    (nested / "child").mkdir(exist_ok=True)
    roots.register(store, other, nested / "child", "alias")
    config.export_registry(store)
    with pytest.raises(ValueError, match="歧义"):
        config.choose(store.root, str(nested / "child"))


def test_cross_project_overlap_is_checked_again_at_commit(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    other = store.project("并发显式数据根", [])
    enable(store, project)
    original = worktrees._prepare

    def interleaved(*args, **kwargs):
        state, value = original(*args, **kwargs)
        if value:
            child = linked / "child"
            child.mkdir()
            roots.register(store, other, child, "data")
        return state, value

    monkeypatch.setattr(worktrees, "_prepare", interleaved)
    assert worktrees.discover(store, project).get("worktrees_created", 0) == 0
    assert root_paths(store, project) == {str(repo)}
    assert worktrees.listing(store, project)["roots"][0]["discovery"]["details"]["conflict"] == 1


def test_disabling_during_git_probe_prevents_registration(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    original = worktrees._prepare

    def interleaved(*args, **kwargs):
        state, value = original(*args, **kwargs)
        if value:
            enable(store, project, False)
        return state, value

    monkeypatch.setattr(worktrees, "_prepare", interleaved)
    assert worktrees.discover(store, project)["worktrees_held"] == 1
    assert root_paths(store, project) == {str(repo)}


def test_new_shared_identity_during_probe_is_held(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    other = store.project("并发明确工作树", [])
    enable(store, project)
    original = worktrees._prepare

    def interleaved(*args, **kwargs):
        state, value = original(*args, **kwargs)
        if value:
            roots.register(store, other, linked)
        return state, value

    monkeypatch.setattr(worktrees, "_prepare", interleaved)
    assert worktrees.discover(store, project)["worktrees_held"] == 1
    assert root_paths(store, project) == {str(repo)}
    assert root_paths(store, other) == {str(linked)}
    assert (
        worktrees.listing(store, project)["roots"][0]["discovery"]["status"]
        == "policy_or_identity_changed"
    )


def test_noncanonical_member_is_rejected_before_git_probe(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    alias = tmp_path / "directory-alias"
    raw = (
        b"worktree "
        + str(alias).encode()
        + b"\0HEAD "
        + git(repo, "rev-parse", "HEAD").strip()
        + b"\0\0"
    )
    original = Git.run

    def replacement(self, *args, **kwargs):
        assert self.worktree != alias
        return raw if args[:2] == ("worktree", "list") else original(self, *args, **kwargs)

    monkeypatch.setattr(Git, "run", replacement)
    with directory_alias(alias, linked):
        assert worktrees.discover(store, project).get("worktrees_created", 0) == 0
    assert root_paths(store, project) == {str(repo)}
    assert worktrees.listing(store, project)["roots"][0]["discovery"]["details"]["unavailable"] == 1


def test_paged_membership_survives_restart_and_head_changes(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    linked = []
    for number in range(5):
        path = tmp_path / f"member-{number}"
        git(repo, "worktree", "add", "--detach", str(path))
        linked.append(path)
    root = tmp_path / "data"
    monkeypatch.setattr(worktrees, "PAGE_SIZE", 2)
    with closing(Store(root)) as store:
        project = store.project("分页工作树", [repo])
        enable(store, project)
        worktrees.discover(store, project, limit=1)
        first = dict(store.db.execute("SELECT * FROM worktree_discovery_progress").fetchone())
        assert first["after_path"]
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "--allow-empty",
        "-m",
        "新 HEAD",
    )
    with closing(Store(root)) as store:
        worktrees.discover(store, project, limit=1)
        after = dict(
            store.db.execute(
                "SELECT * FROM worktree_discovery_progress WHERE root_id=?", (first["root_id"],)
            ).fetchone()
        )
        assert after["list_sha256"] == first["list_sha256"]
        assert len(root_paths(store, project)) >= 2
        for _ in range(12):
            worktrees.discover(store, project, limit=1)
        assert root_paths(store, project) == {str(repo), *(str(p) for p in linked)}


def test_missing_first_anchor_does_not_starve_later_repositories(store, tmp_path):
    project = store.project("轮转项目", [])
    expected = set()
    repos = []
    for number in range(5):
        parent = tmp_path / str(number)
        parent.mkdir()
        repo = repository(parent)
        linked = parent / "linked"
        git(repo, "worktree", "add", "--detach", str(linked))
        roots.register(store, project, repo)
        repos.append(repo)
        expected.update((str(repo), str(linked)))
    enable(store, project)
    missing = repos[0].with_name("renamed")
    repos[0].rename(missing)
    for _ in range(8):
        worktrees.discover(store, project, limit=1)
    assert expected - {str(tmp_path / "0" / "linked")} <= root_paths(store, project)
    assert worktrees.listing(store, project)["total"] == 9


def test_registry_publication_failure_is_repaired_next_cycle(store, tmp_path, monkeypatch):
    project, repo, linked = scene(store, tmp_path)
    config.export_registry(store)
    enable(store, project)
    original = config.export_registry
    with monkeypatch.context() as patch:
        patch.setattr(
            config, "export_registry", lambda *_: (_ for _ in ()).throw(OSError("合成中断"))
        )
        assert cycle(store)["worktrees_errors"] == 1
    assert str(linked) in root_paths(store, project)
    assert not any(r["path"] == str(linked) for r in config.load_registry(store.root))
    cycle(store)
    assert any(r["path"] == str(linked) for r in config.load_registry(store.root))
    assert config.export_registry is original


def test_project_clear_removes_policy_and_progress_without_reviving_cleared_worktree(tmp_path):
    root = tmp_path / "data"
    with closing(Store(root)) as store:
        project, repo, linked = scene(store, tmp_path)
        enable(store, project)
        worktrees.discover(store)
        other_repo_parent = tmp_path / "retained"
        other_repo_parent.mkdir()
        other_repo = repository(other_repo_parent)
        other = store.project("保留自动策略", [other_repo])
        enable(store, other)
    preview = clear.preview(root, project)
    assert preview["blockers"] == []
    clear.execute(root, project, str(uuid4()), preview["preview_sha256"])
    with closing(Store(root)) as store:
        for table in (
            "worktree_follow_actions",
            "worktree_discovery_progress",
            "worktree_discovery_observations",
            "worktree_discovery_cursors",
        ):
            assert (
                store.db.execute(
                    f"SELECT count(*) FROM {table} WHERE project_id=?", (project,)
                ).fetchone()[0]
                == 0
            )
        assert worktrees.enabled(store, other)
        fresh = tmp_path / "fresh"
        git(repo, "worktree", "add", "--detach", str(fresh))
        successor = store.project("同仓库新明确根", [fresh])
        enable(store, successor)
        worktrees.discover(store, successor)
        assert root_paths(store, successor) == {str(fresh)}
        details = worktrees.listing(store, successor)["roots"][0]["discovery"]["details"]
        assert details["privacy_blocked"] == 2
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None
    assert (repo / "text.txt").exists() and (linked / "text.txt").exists()


def test_migration29_rolls_back_and_preserves_original_events(tmp_path, monkeypatch):
    root, raw = legacy_store(tmp_path, monkeypatch, version=28)
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 29, (*migrations.MIGRATIONS[29], "INVALID SQL"))
        with pytest.raises(sqlite3.OperationalError):
            Store(root)
    with closing(sqlite3.connect(root / "rg.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 28
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name LIKE 'worktree_%'"
            ).fetchone()[0]
            == 0
        )
    with closing(Store(root)) as store:
        assert store.raw(1) == raw
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None
        assert store.db.execute("SELECT count(*) FROM worktree_follow_actions").fetchone()[0] == 0


@pytest.mark.parametrize("table", ["worktree_follow_actions", "worktree_discovery_observations"])
@pytest.mark.parametrize("operation", ["DELETE", "UPDATE"])
def test_policy_and_discovery_history_are_append_only(store, tmp_path, table, operation):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    worktrees.discover(store, project)
    statement = (
        f"DELETE FROM {table}"
        if operation == "DELETE"
        else f"UPDATE {table} SET recorded_at='changed'"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(statement)


def test_readonly_listing_pagination_and_stale_revision_require_no_git(
    store, tmp_path, monkeypatch
):
    project, repo, linked = scene(store, tmp_path)
    enable(store, project)
    worktrees.discover(store, project)
    before = list(store.db.iterdump())
    monkeypatch.setattr(Git, "run", lambda *a, **k: pytest.fail("读取不可探测 Git"))
    first = worktrees.listing(store, project, limit=1)
    second = worktrees.listing(
        store, project, limit=1, offset=first["next_offset"], expected_revision=first["revision"]
    )
    assert second["next_offset"] is None and second["roots"][0] != first["roots"][0]
    assert list(store.db.iterdump()) == before
    enable(store, project, False)
    with pytest.raises(ConflictError):
        worktrees.listing(store, project, expected_revision=first["revision"])


@pytest.mark.parametrize("value", [True, -1, 0, 201, "1"])
def test_invalid_paging_never_silently_widens_scope(store, value):
    project = store.project("分页参数", [])
    with pytest.raises(ValueError):
        worktrees.listing(store, project, limit=value)


def test_cli_initial_optin_policy_conflict_and_discovery(tmp_path):
    repo = repository(tmp_path)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--detach", str(linked))
    root = tmp_path / "db"
    result = cli(
        root, "project", "add", "命令行公开合成", "--root", str(repo), "--follow-worktrees"
    )
    assert result.returncode == 0, result.stderr
    project = json.loads(result.stdout)["project_id"]
    result = cli(root, "project", "worktrees", "--project", project)
    assert result.returncode == 0, result.stderr
    before = json.loads(result.stdout)
    assert before["follow_enabled"] and before["total"] == 1
    result = cli(root, "project", "discover-worktrees", "--project", project)
    assert result.returncode == 0 and json.loads(result.stdout)["worktrees_created"] == 1
    result = cli(
        root,
        "project",
        "follow-worktrees",
        "--project",
        project,
        "--disable",
        "--expected-revision",
        str(before["revision"]),
    )
    assert result.returncode != 0
    result = cli(root, "project", "worktrees", "--project", project)
    latest = json.loads(result.stdout)
    result = cli(
        root,
        "project",
        "follow-worktrees",
        "--project",
        project,
        "--disable",
        "--expected-revision",
        str(latest["revision"]),
    )
    assert result.returncode == 0 and not json.loads(result.stdout)["follow_enabled"]


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_cli_json_preserves_unicode_paths_on_limited_output_encoding(store, tmp_path, encoding):
    project, repo, linked = scene(store, tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.stdout.reconfigure(encoding={encoding!r}); "
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "project",
            "worktrees",
            "--project",
            project,
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, repr(result.stderr)
    value = json.loads(result.stdout)
    assert value == worktrees.listing(store, project)
    assert value["roots"][0]["path"] == str(repo)
    assert result.stdout.decode(encoding).encode(encoding) == result.stdout


def test_http_auth_strict_config_readonly_and_conflict(store, tmp_path):
    project, repo, linked = scene(store, tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("公开合成", encoding="utf-8")
    server = LocalServer(store.root, web, port=0, token="public-worktree-test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {"project_id": project, "follow_enabled": True, "expected_revision": store.revision()}
    try:
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
            assert client.get("/api/worktrees", params={"project": project}).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            assert (
                client.post(
                    "/api/worktrees/follow",
                    json=body,
                    headers={"Origin": "https://external.invalid"},
                ).status_code
                == 403
            )
            for invalid in (
                body | {"extra": 1},
                body | {"follow_enabled": 1},
                body | {"expected_revision": True},
                body | {"project_id": []},
            ):
                assert client.post("/api/worktrees/follow", json=invalid).status_code == 400
            duplicate = json.dumps(body)[:-1] + ', "follow_enabled": false}'
            assert (
                client.post(
                    "/api/worktrees/follow",
                    content=duplicate,
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 400
            )
            assert client.post("/api/worktrees/follow", json=body).status_code == 200
            assert root_paths(store, project) == {str(repo)}
            assert client.post("/api/worktrees/follow", json=body).status_code == 409
            before = list(store.db.iterdump())
            assert client.get("/api/worktrees", params={"project": project}).json()[
                "follow_enabled"
            ]
            assert (
                client.get("/api/worktrees", params={"project": project, "extra": 1}).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/worktrees",
                    params={"project": project, "expected_revision": body["expected_revision"]},
                ).status_code
                == 409
            )
            assert list(store.db.iterdump()) == before
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
