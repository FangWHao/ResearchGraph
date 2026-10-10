from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.api.views import NotFound
from rg.artifacts.worker import Worker
from rg.query import version_diff
from rg.store.backup import backup
from rg.store.database import ConflictError, Store, dumps
from tests.golden.test_artifacts import snapshot
from tests.golden.test_l1 import ingest
from tests.golden.test_read_tools import T1, T2, T3, T4
from tests.golden.test_snapshots_hooks import project


def owner(store, tmp_path):
    work = tmp_path / "synthetic-diff"
    work.mkdir(exist_ok=True)
    identity = store.project("合成物理版本差异", [work])
    root = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (identity,)
    ).fetchone()[0]
    return identity, root, work


def saved(
    store,
    identity,
    root,
    work,
    content=b"before\n",
    *,
    mode="100644",
    source="shadow_snapshot",
    representation="physical_file_bytes",
    occurred=T1,
    recorded=T2,
    copied=True,
    skipped=None,
    commit_matches=True,
    path="source.txt",
    event=None,
):
    version = "synthetic:" + str(uuid4())
    blob = hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False
    ).hexdigest()
    sha = store.objects.put(content)
    store.db.execute(
        "INSERT INTO artifact_versions(version_id,project_id,path,algo,digest,size,source,"
        "observed_at,content_sha256,phase,root_id,basis,claim_state,representation,"
        "evidence_event_id) "
        "VALUES (?,?,?,'git-sha1',?,?,?, ?,?,'observed',?,'direct_record','candidate',?,?)",
        (
            version,
            identity,
            str(work / path),
            blob,
            len(content),
            source,
            T4,
            sha,
            root,
            representation,
            event,
        ),
    )
    snap = store.db.execute(
        "INSERT INTO workspace_snapshots(project_id,root_id,trigger,shadow_commit,skipped,"
        "taken_at,recorded_at,metadata) VALUES (?,?,'synthetic',?,?,?,?,'{}')",
        (identity, root, "a" * 40, skipped, occurred, recorded),
    ).lastrowid
    job = store.db.execute(
        "INSERT INTO artifact_jobs(job_key,project_id,root_id,snapshot_id,kind,input_json,"
        "state,created_at,updated_at) VALUES (?,?,?,?,'snapshot_blob','{}','done',?,?)",
        (version, identity, root, snap, recorded, recorded),
    ).lastrowid
    store.db.execute(
        "INSERT INTO artifact_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            version,
            version,
            job,
            snap if source == "shadow_snapshot" else None,
            snap,
            mode,
            None,
            occurred,
            occurred,
            0,
            None,
            dumps(
                {
                    "complete": True,
                    "content_copied": copied,
                    "shadow_commit": "a" * 40 if commit_matches else "b" * 40,
                }
            ),
            recorded,
        ),
    )
    return version


def pair(store, tmp_path, left=b"before\n", right=b"after\n", **right_options):
    identity, root, work = owner(store, tmp_path)
    before = saved(store, identity, root, work, left)
    after = saved(store, identity, root, work, right, **right_options)
    return identity, before, after


def compare(store, identity, before, after, **values):
    return version_diff.query(
        store, identity, {"before_version_id": before, "after_version_id": after} | values
    )


def test_real_snapshots_bytes_survive_workspace_git_and_original_store_removal(store, tmp_path):
    identity, root, work = project(store, tmp_path)
    path = work / "source.txt"
    path.write_bytes("甲\r\n原版本\u2028正文".encode())
    snapshot(store, identity, root, work)
    Worker(store).run()
    before = store.db.execute(
        "SELECT version_id FROM artifact_versions WHERE path=?", (str(path),)
    ).fetchone()[0]
    path.write_bytes("甲\n新版本\u2028正文\n".encode())
    snapshot(store, identity, root, work)
    Worker(store).run()
    after = store.db.execute(
        "SELECT version_id FROM artifact_versions WHERE path=? AND version_id!=?",
        (str(path), before),
    ).fetchone()[0]
    raw = [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")]
    revision = store.revision()
    destination = tmp_path / "backup"
    backup(store, destination)
    shutil.rmtree(work)
    shutil.rmtree(store.root / "snapshots")
    result = compare(store, identity, before, after, expected_revision=revision)
    assert result["diff"]["available"] and result["diff"]["complete"]
    assert result["byte_identity"] == "different" and result["diff"]["before_lines"] == 2
    assert "-原版本\u2028正文\n\\ No newline" in result["diff"]["text"]
    assert result["diff"]["before_newlines"] == {"lf": 0, "crlf": 1, "final_newline": False}
    assert (
        store.revision() == revision
        and [tuple(r) for r in store.db.execute("SELECT * FROM raw_events")] == raw
    )
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
    store.close()
    shutil.rmtree(store.root)
    shutil.rmtree(destination / "snapshots")
    with closing(Store(destination, readonly=True)) as restored:
        repeated = compare(
            restored,
            identity,
            before,
            after,
            occurred_until=result["occurred_until"],
            known_until=result["known_until"],
        )
        assert repeated == result


def test_direction_candidates_and_mode_only_change_are_preserved(store, tmp_path):
    identity, before, after = pair(store, tmp_path, b"same\n", b"same\n", mode="100755")
    result = compare(store, identity, before, after)
    assert result["before"]["version_id"] == before and result["after"]["version_id"] == after
    assert result["byte_identity"] == "same" and result["mode_changed"]
    assert result["diff"]["available"] and result["diff"]["text"] == ""
    assert result["before"]["modes"] == ["100644"] and result["after"]["modes"] == ["100755"]
    assert result["before"]["claim_state"] == result["after"]["claim_state"] == "candidate"
    result = compare(store, identity, after, before)
    assert result["before"]["modes"] == ["100755"]


@pytest.mark.parametrize("cutoff,value", [("known_until", T1), ("occurred_until", T1)])
def test_dual_cutoffs_do_not_load_later_objects(store, tmp_path, monkeypatch, cutoff, value):
    identity, before, after = pair(store, tmp_path, occurred=T3, recorded=T3)
    monkeypatch.setattr(version_diff, "_bytes", lambda *a: pytest.fail("hidden bytes were loaded"))
    with pytest.raises(NotFound):
        compare(store, identity, before, after, **{cutoff: value})


def test_version_first_time_does_not_replace_visible_snapshot_observation_time(store, tmp_path):
    identity, before, after = pair(store, tmp_path)
    assert compare(store, identity, before, after, occurred_until=T2, known_until=T2)["diff"][
        "available"
    ]


@pytest.mark.parametrize(
    "options",
    [
        {"copied": False},
        {"skipped": "synthetic skipped"},
        {"commit_matches": False},
        {"mode": "120000"},
        {"representation": "unknown"},
    ],
)
def test_inconsistent_snapshot_proof_cannot_become_physical_diff(store, tmp_path, options):
    identity, before, after = pair(store, tmp_path, **options)
    result = compare(store, identity, before, after)
    assert not result["diff"]["available"] and result["diff"]["reason"] == "invalid_provenance"
    assert result["byte_identity"] == "unverified"


def test_current_observation_with_content_still_does_not_prove_saved_snapshot(store, tmp_path):
    identity, before, after = pair(store, tmp_path, source="current_file")
    result = compare(store, identity, before, after)
    assert (
        result["diff"]["reason"] == "unsupported_source" and result["byte_identity"] == "unverified"
    )
    assert result["mode_changed"] is None


def test_tool_reported_text_does_not_become_physical_diff(store, tmp_path):
    identity, root, work = owner(store, tmp_path)
    ingest(
        store,
        tmp_path,
        [
            {
                "type": "user",
                "uuid": "synthetic-user",
                "sessionId": "synthetic",
                "message": {"content": "合成工具来源"},
            }
        ],
        project=identity,
    )
    event = store.db.execute("SELECT event_id FROM raw_events").fetchone()[0]
    before = saved(store, identity, root, work)
    after = saved(
        store,
        identity,
        root,
        work,
        source="agent_edit",
        representation="tool_reported_utf8",
        event=event,
    )
    assert compare(store, identity, before, after)["diff"]["reason"] == "unsupported_source"


def test_later_contradictory_mode_does_not_leak_into_earlier_comparison(store, tmp_path):
    identity, before, after = pair(store, tmp_path)
    original = store.db.execute(
        "SELECT * FROM artifact_observations WHERE version_id=?", (after,)
    ).fetchone()
    job = store.db.execute(
        "INSERT INTO artifact_jobs(job_key,project_id,root_id,snapshot_id,kind,input_json,"
        "state,created_at,updated_at) "
        "SELECT ?,project_id,root_id,snapshot_id,kind,input_json,'done',?,? "
        "FROM artifact_jobs WHERE job_id=?",
        (str(uuid4()), T4, T4, original["job_id"]),
    ).lastrowid
    store.db.execute(
        "INSERT INTO artifact_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(uuid4()),
            after,
            job,
            original["snapshot_id"],
            original["discovery_snapshot_id"],
            "100755",
            None,
            None,
            None,
            0,
            None,
            original["details"],
            T4,
        ),
    )
    historical = compare(store, identity, before, after, known_until=T2)
    assert historical["diff"]["available"] and historical["after"]["modes"] == ["100644"]
    current = compare(store, identity, before, after)
    assert current["diff"]["reason"] == "invalid_provenance"
    assert current["byte_identity"] == "unverified" and current["mode_changed"] is None


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("missing", "object_unavailable"),
        ("corrupt", "object_unavailable"),
        ("wrong_bytes", "object_integrity"),
        ("symlink", "object_unavailable"),
    ],
)
def test_missing_corrupt_and_linked_objects_are_not_reported_as_complete(
    store, tmp_path, kind, reason
):
    identity, before, after = pair(store, tmp_path)
    sha = store.db.execute(
        "SELECT content_sha256 FROM artifact_versions WHERE version_id=?", (after,)
    ).fetchone()[0]
    path = store.objects.path(sha)
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"invalid compressed object")
    elif kind == "wrong_bytes":
        import zstandard

        path.write_bytes(zstandard.ZstdCompressor().compress(b"wrong\n"))
    else:
        outside = tmp_path / "outside-object"
        shutil.copyfile(path, outside)
        path.unlink()
        path.symlink_to(outside)
    result = compare(store, identity, before, after)
    assert result["diff"]["reason"] == reason and result["diff"]["text"] is None
    assert result["byte_identity"] == "unverified" and not result["after"]["content_verified"]


@pytest.mark.parametrize(
    "left,right,reason",
    [
        (b"A\0", b"A\0", "binary"),
        (b"\xff", b"x", "invalid_utf8"),
        (b"\n" * 2001, b"\n", "line_limit"),
        (b"A" * 64000, b"B", "input_byte_limit"),
        ((b"A" * 31 + b"\n") * 1000, (b"B" * 31 + b"\n") * 1000, "diff_byte_limit"),
    ],
    ids=["binary", "invalid_utf8", "line_limit", "input_byte_limit", "diff_byte_limit"],
)
def test_binary_invalid_encoding_and_all_budgets_have_no_partial_text(
    store, tmp_path, left, right, reason
):
    identity, before, after = pair(store, tmp_path, left, right)
    result = compare(store, identity, before, after)
    assert result["diff"]["reason"] == reason and not result["diff"]["available"]
    assert result["diff"]["text"] is None and not result["diff"]["complete"]
    assert result["byte_identity"] == (
        "unverified" if reason == "input_byte_limit" else "same" if left == right else "different"
    )


def test_link_target_bytes_are_compared_without_following_targets(store, tmp_path):
    identity, root, work = owner(store, tmp_path)
    before = saved(
        store,
        identity,
        root,
        work,
        b"/synthetic/missing-a",
        mode="120000",
        representation="symlink_target_bytes",
    )
    after = saved(
        store,
        identity,
        root,
        work,
        b"/synthetic/missing-b",
        mode="120000",
        representation="symlink_target_bytes",
    )
    result = compare(store, identity, before, after)
    assert result["diff"]["available"] and "+/synthetic/missing-b" in result["diff"]["text"]
    normal = saved(store, identity, root, work, b"normal\n")
    result = compare(store, identity, before, normal)
    assert result["diff"]["reason"] == "representation_changed" and result["mode_changed"]


def test_other_project_other_file_stale_revision_and_scope_cannot_read_bytes(
    store, tmp_path, monkeypatch
):
    identity, root, work = owner(store, tmp_path)
    before = saved(store, identity, root, work)
    other_file = saved(store, identity, root, work, path="other.txt")
    other = store.project("另一个合成项目", [tmp_path / "other"])
    other_root = store.db.execute(
        "SELECT root_id FROM source_roots WHERE project_id=?", (other,)
    ).fetchone()[0]
    foreign = saved(store, other, other_root, work)
    monkeypatch.setattr(
        version_diff, "_bytes", lambda *a: pytest.fail("invalid request read bytes")
    )
    with pytest.raises(NotFound):
        compare(store, identity, before, foreign)
    with pytest.raises(ValueError):
        compare(store, identity, before, other_file)
    with pytest.raises(ConflictError):
        compare(store, identity, before, before, expected_revision=store.revision() - 1)
    with pytest.raises(ValueError):
        compare(store, identity, before, before, scope={"data": "synthetic"})
    with pytest.raises(NotFound):
        compare(store, identity, before, "' OR 1=1 --")


def test_real_cli_and_http_share_readonly_guarded_query(store, tmp_path):
    identity, before, after = pair(store, tmp_path)
    revision = store.revision()
    args = [
        "--data-dir",
        str(store.root),
        "version-diff",
        before,
        after,
        "--project",
        identity,
        "--occurred-until",
        T3,
        "--known-until",
        T3,
        "--expected-revision",
        str(revision),
    ]
    result = subprocess.run(
        [str(Path(sys.executable).with_name("rg")), *args], capture_output=True, check=True
    )
    cli = json.loads(result.stdout)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("合成界面")
    server = LocalServer(store.root, web, port=0, token="synthetic-diff-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    parameters = {
        "project": identity,
        "before_version_id": before,
        "after_version_id": after,
        "occurred_until": T3,
        "known_until": T3,
        "expected_revision": str(revision),
    }
    try:
        with httpx.Client(
            base_url=server.origin,
            headers={"Authorization": f"Bearer {server.token}"},
            trust_env=False,
        ) as client:
            response = client.get("/api/version-diff", params=parameters)
            assert response.status_code == 200 and response.json() == cli
            assert response.headers["Cache-Control"] == "no-store"
            assert (
                client.get(
                    "/api/version-diff", params=parameters, headers={"Authorization": ""}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/api/version-diff",
                    params=parameters,
                    headers={"Origin": "https://external.invalid"},
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/version-diff", params=parameters | {"expected_revision": "-1"}
                ).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/version-diff",
                    params=parameters | {"expected_revision": str(revision - 1)},
                ).status_code
                == 409
            )
            assert (
                client.get(
                    "/api/version-diff", params=parameters | {"after_version_id": "missing"}
                ).status_code
                == 404
            )
            assert (
                client.get("/api/version-diff", params=parameters | {"scope": "{}"}).status_code
                == 400
            )
            assert (
                client.get(
                    "/api/version-diff", params=[*parameters.items(), ("project", identity)]
                ).status_code
                == 400
            )
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert not thread.is_alive() and store.revision() == revision
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0
