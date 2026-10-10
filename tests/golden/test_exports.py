from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import zipfile
from contextlib import closing
from uuid import uuid4

import httpx
import pytest

from rg.api.server import LocalServer
from rg.derive.worker import derive
from rg.export.package import archive, verify, write
from rg.record.manifest import record
from rg.store.database import ConflictError, Store, dumps
from rg.store.migrations import LATEST_VERSION
from rg.store.objects import digest
from tests.golden.test_l1 import claude_call, claude_result, ingest, runs
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, T4, append, decision, original, review
from tests.golden.test_run_manifests import body
from tests.golden.test_version_queries import captured, observed, scene


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return {n: json.loads(package.read(n)) for n in package.namelist() if n.endswith(".json")}


def options(project, **values):
    return {"project_id": project, "occurred_until": T4, "known_until": T4} | values


def evidence(store, owner, claim, text, *, occurred=T1, recorded=T1):
    session = store.db.execute(
        "INSERT INTO sessions(tool,native_session_id,project_id,last_at,cwd) "
        "VALUES ('claude',?,?,'2099-01-01','/home/private-user/future-workspace')",
        (str(uuid4()), owner),
    ).lastrowid
    file_id = store.db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,parser,parser_version,first_seen,"
        "last_read,status) VALUES (?,'/home/private-user/future-source','a','claude','test',?,?,"
        "'deleted_at_source')",
        (session, recorded, "2099-01-01"),
    ).lastrowid
    raw = dumps({"message": {"content": text}}).encode()
    sha = store.objects.put(raw)
    event = store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,object_sha256,"
        "seq,kind,role,occurred_at,recorded_at,line_sha256) "
        "VALUES (?,?,500,?,?,0,'user_text','user',?,?,?)",
        (session, file_id, 500 + len(raw), sha, occurred, recorded, sha),
    ).lastrowid
    encoded = dumps(text)[1:-1].encode()
    start = raw.index(encoded)
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,?,?,?)",
        (event, start, start + len(encoded), digest(encoded)),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim, span))
    return event, span, raw


def unchanged(store):
    return "\n".join(store.db.iterdump())


def test_default_export_is_complete_readonly_and_has_no_raw_binary_or_future_metadata(
    store, monkeypatch
):
    project = store.project("合成导出", [])
    entity, claim = original(store, project)
    _, _, raw = evidence(store, project, claim, "仅这一段合成证据")
    for i in range(105):
        append(store, entity, "entity_version", {"label": f"版本{i}"})
        evidence(store, project, claim, f"另一段证据{i}")
    before = unchanged(store)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("不能联网"))
    manifest, zipped = archive(store, options(project))
    value = unpack(zipped)
    assert manifest["counts"]["claims"] == 106
    assert len(value["evidence.json"]) == 106
    assert len(value["claims.json"][0]["evidence"]) == 106
    assert len(value["sources.json"]) == 106
    assert all(s["verification"] == "not_read" and "text" not in s for s in value["evidence.json"])
    assert all(
        "path" not in r
        and "cwd" not in r
        and "last_at" not in r
        and "last_read" not in r
        and "status" not in r
        for r in value["sources.json"]
    )
    with zipfile.ZipFile(io.BytesIO(zipped)) as package:
        joined = b"\n".join(package.read(n) for n in package.namelist())
        assert raw not in joined and b"private-user" not in joined
        assert b"future-workspace" not in joined and b"deleted_at_source" not in joined
    assert manifest["conditions"]["revision"] == store.revision()
    assert manifest["schema_version"] == LATEST_VERSION
    assert verify(io.BytesIO(zipped))["verified_files"] == 10
    assert unchanged(store) == before


def test_review_history_and_two_state_axes_do_not_use_current_or_candidate_status(store):
    project = store.project("合成双时间", [])
    entity, first = original(store, project)
    adopted = decision(store, entity, occurred=T1, recorded=T1)
    contested = append(
        store,
        entity,
        "evidence_event",
        {"target": entity, "state": "contested"},
        state="confirmed",
        occurred=T1,
        recorded=T1,
    )
    decision(store, entity, "rejected", state="candidate", occurred=T3, recorded=T3)
    review(store, adopted, "confirm", T3)
    review(store, first, "dismiss", T4)
    _, early = archive(store, options(project, known_until=T2))
    node = unpack(early)["graph.json"]["nodes"][0]
    assert node["states"][0]["adoption"]["state"] == "unknown"
    assert node["states"][0]["evidence_state"]["state"] == "contested"
    assert node["states"][0]["evidence_state"]["claim_ids"] == [contested]
    _, late = archive(store, options(project, occurred_until=T2, known_until=T3))
    value = unpack(late)
    assert len(value["reviews.json"]) == 1
    assert value["graph.json"]["nodes"][0]["states"][0]["adoption"]["state"] == "accepted"
    rows = {r["claim_id"]: r for r in value["claims.json"]}
    assert rows[adopted]["claim_state"] == "candidate"
    assert rows[adopted]["effective_state"] == "confirmed"
    assert rows[first]["effective_state"] == "candidate"


def test_scope_is_exact_and_source_clocks_are_independent_of_claim_clocks(store):
    project = store.project("合成范围", [])
    other = store.project("另一个私有项目", [])
    entity, claim = original(store, project)
    original(store, project, scope={"data": "other"})
    original(store, other)
    evidence(store, project, claim, "晚到证据不能回流", recorded=T3)
    evidence(store, project, claim, "未来发生证据不能回流", occurred=T3)
    append(store, entity, "entity_version", {"content": "未记录时间"}, recorded="bad")
    _, data = archive(store, options(project, scope=SCOPE, occurred_until=T2, known_until=T2))
    value = unpack(data)
    assert len(value["claims.json"]) == 1
    assert value["evidence.json"] == value["sources.json"] == []
    assert value["manifest.json"]["conditions"]["unknown_recorded_time_excluded"] == 1
    assert value["manifest.json"]["conditions"]["scope"] == SCOPE
    assert other.encode() not in data


def test_join_ports_and_relation_cycles_preserve_basis_states_and_unresolved_references(store):
    project = store.project("合成语义图", [])
    one, _ = original(store, project)
    two, _ = original(store, project, kind="join")
    for source, target in ((one, two), (two, one)):
        append(
            store, source, "relation", {"source": source, "target": target, "relation": "supports"}
        )
    append(
        store,
        two,
        "join_ports",
        {
            "target": two,
            "semantics": "all_required",
            "inputs": [{"port": "a", "ref": one}, {"port": "b", "ref": "not-visible"}],
            "selected": None,
        },
    )
    _, data = archive(store, options(project))
    graph = unpack(data)["graph.json"]
    assert len(graph["edges"]) == 2 and len(graph["nodes"]) == 2
    assert all(
        r["basis"] == "manual" and r["effective_state"] == "candidate" for r in graph["edges"]
    )
    assert graph["join_ports"][0]["inputs"][1]["ref"] == "not-visible"
    assert graph["unresolved_entity_ids"] == ["not-visible"]
    assert not graph["projection"] and not graph["same_topic_propagates"]


def test_explicit_excerpt_is_redacted_with_original_offsets_and_separate_digests(store):
    project = store.project("合成隐私", [])
    _, claim = original(store, project)
    text = "住院号 ZY12345 邮箱 synthetic@example.test 密钥 sk-syntheticabc123"
    _, span, raw = evidence(store, project, claim, text)
    _, data = archive(store, options(project, include_evidence=True, redact_patterns=[r"ZY\d+"]))
    ref = unpack(data)["evidence.json"][0]
    assert ref["span_id"] == span and ref["verification"] == "original_sha256_verified"
    assert len(ref["text"].encode()) == len(text.encode())
    assert "ZY12345" not in ref["text"] and "example.test" not in ref["text"]
    assert ref["quote_sha256"] == digest(text.encode())
    assert ref["exported_text_sha256"] == digest(ref["text"].encode())
    assert ref["byte_mapping"] == "identity_relative_to_original_event"
    assert not ref["exported_text_is_original"]
    assert all(
        ref["byte_start"] <= a < b <= ref["byte_end"] for a, b in ref["redacted_byte_ranges"]
    )
    assert raw[ref["byte_start"] : ref["byte_end"]] == text.encode()


def test_payload_credentials_and_custom_scope_are_masked_without_changing_selection(store):
    project = store.project("合成隐私", [])
    original(store, project, scope={"cohort": "MR12345"})
    record(
        store,
        project,
        body(
            parameters={
                "api_key": "syntheticbarecredential",
                "nested": {"password": "more private"},
            },
            scope={"cohort": "MR12345"},
            seed=123456789012345678,
        ),
    )
    _, data = archive(
        store,
        {"project_id": project, "scope": {"cohort": "MR12345"}, "redact_patterns": [r"MR\d+"]},
    )
    value = unpack(data)
    report = value["runs.json"][0]["manifests"][0]["reported"]
    assert report["parameters"]["api_key"]["redacted"]
    assert report["parameters"]["nested"]["password"]["redacted"]
    assert report["seed"] == "123456789012345678"
    assert value["manifest.json"]["conditions"]["scope"] == {"cohort": "*******"}
    assert value["manifest.json"]["counts"]["claims"] == 1
    joined = dumps(value)
    assert "syntheticbarecredential" not in joined and "more private" not in joined
    assert "MR12345" not in joined


def test_escaped_json_credentials_in_explicit_excerpt_keep_byte_mapping(store):
    project = store.project("合成转义密钥", [])
    _, claim = original(store, project)
    text = '{"api_key":"synthetic bare credential", "内容":"中文"}'
    _, _, raw = evidence(store, project, claim, text)
    _, data = archive(store, options(project, include_evidence=True))
    span = unpack(data)["evidence.json"][0]
    assert "synthetic bare credential" not in span["text"]
    assert span["quote_sha256"] == digest(raw[span["byte_start"] : span["byte_end"]])
    assert span["exported_text_sha256"] == digest(span["text"].encode())
    assert len(span["text"].encode()) == span["byte_end"] - span["byte_start"]


def test_unknown_occurred_time_and_timezone_comparison_are_preserved(store):
    project = store.project("合成时间未知", [])
    entity, _ = original(store, project)
    decision(store, entity, state="confirmed", occurred=None)
    earlier = append(
        store,
        entity,
        "entity_version",
        {"label": "同一时刻"},
        occurred="2026-01-02T08:00:00+08:00",
        recorded=T2,
    )
    _, data = archive(store, options(project, occurred_until=T2))
    value = unpack(data)
    assert earlier in [r["claim_id"] for r in value["claims.json"]]
    assert value["graph.json"]["nodes"][0]["states"][0]["adoption"]["state"] == "time_unknown"


@pytest.mark.parametrize("broken", ["utf8", "sha"])
def test_corrupt_evidence_cannot_publish_text(store, tmp_path, broken):
    project = store.project("合成损坏证据", [])
    _, claim = original(store, project)
    event, _, raw = evidence(store, project, claim, "中文证据")
    start = raw.index("中文".encode()) + (1 if broken == "utf8" else 0)
    end = start + 2 if broken == "utf8" else start + len("中文".encode())
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,?,?,?)",
        (event, start, end, digest(raw[start:end]) if broken == "utf8" else "b" * 64),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'context')", (claim, span))
    with pytest.raises((ValueError, UnicodeError)):
        write(store, options(project, include_evidence=True), tmp_path / "broken.zip")
    assert not (tmp_path / "broken.zip").exists()


def test_all_report_pages_and_io_entries_are_exported_even_without_native_run(store, monkeypatch):
    monkeypatch.setattr("rg.record.manifest.now", lambda: T2)
    project = store.project("合成全部清单", [])
    first = record(store, project, body(outputs=["unknown-version"] * 256, scope=SCOPE))
    for _ in range(101):
        record(store, project, body(inputs=[], scope=SCOPE))
    _, data = archive(store, options(project, scope=SCOPE))
    run = unpack(data)["runs.json"][0]
    assert run["native"] is None and run["scope_basis"] == "reported_only"
    assert len(run["manifests"]) == 102
    report = next(r for r in run["manifests"] if r["request_id"] == first["request_id"])
    assert report["claim_state"] == "candidate"
    assert report["io"]["total"] == len(report["io"]["items"]) == 256
    assert report["io"]["items"][-1]["ordinal"] == 255
    assert not report["io"]["partial"] and run["actual_io_completeness"] == "unknown"


def test_native_state_does_not_borrow_late_exit_and_scoped_export_needs_report(
    store, tmp_path, monkeypatch
):
    _, project = ingest(
        store,
        tmp_path,
        [claude_call(timestamp=T1), claude_result(metadata={"exit_code": 0}, timestamp=T3)],
    )
    derive(store)
    identity = runs(store)[0]["run_id"]
    _, data = archive(store, {"project_id": project, "occurred_until": T2})
    run = unpack(data)["runs.json"][0]["native"]
    assert run["state"] == "requested" and run["exit_code"] is None
    assert not run["observations_partial"]
    _, filtered = archive(store, {"project_id": project, "scope": SCOPE})
    assert unpack(filtered)["runs.json"] == []
    monkeypatch.setattr("rg.record.manifest.now", lambda: T2)
    record(store, project, body(identity, scope=SCOPE, exit_code=2))
    _, linked = archive(store, {"project_id": project, "scope": SCOPE})
    run = unpack(linked)["runs.json"][0]
    assert run["native"]["state"] == "exited" and run["native"]["exit_code"] == 0
    assert run["manifests"][0]["reported_exit_conflicts_with_native"]


def test_artifacts_use_visible_observations_and_scoped_report_ids_without_opening_files(
    store, tmp_path, monkeypatch
):
    owner, root, version = scene(store, tmp_path)
    snap = captured(store, owner, root)
    observed(store, owner, root, version, snap, recorded=T3, finished=T2)
    observed(store, owner, root, version, snap, "future", recorded=T4, finished=T4)
    _, old = archive(store, options(owner, known_until=T2))
    assert unpack(old)["artifacts.json"] == []
    _, current = archive(store, options(owner, known_until=T3, occurred_until=T3))
    card = unpack(current)["artifacts.json"][0]
    assert len(card["observations"]) == 1
    assert card["version"]["observed_at"] == T2
    _, scoped = archive(store, options(owner, scope=SCOPE))
    assert unpack(scoped)["artifacts.json"] == []
    monkeypatch.setattr("rg.record.manifest.now", lambda: T3)
    record(store, owner, body(inputs=[version], scope=SCOPE))
    _, linked = archive(store, options(owner, scope=SCOPE, known_until=T3, occurred_until=T3))
    assert unpack(linked)["artifacts.json"][0]["scope_basis"] == "reported_only"
    assert not (tmp_path / "synthetic-work" / "file.bin").exists()


@pytest.mark.parametrize(
    "extra",
    [
        {"project_id": "missing"},
        {"scope": {}},
        {"known_until": "2026-01-01"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"include_evidence": 1},
        {"output": "/private"},
        {"redact_patterns": "bad"},
        {"redact_patterns": ["["]},
    ],
)
def test_invalid_export_is_readonly_and_unpublished(store, tmp_path, extra):
    project = store.project("合成错误", [])
    before = unchanged(store)
    target = tmp_path / "invalid.zip"
    with pytest.raises(ValueError):
        write(store, options(project) | extra, target)
    assert not target.exists() and not list(tmp_path.glob(".rg-export-*"))
    assert unchanged(store) == before


def test_revision_and_one_sqlite_snapshot_remain_consistent_with_concurrent_writer(
    store, tmp_path, monkeypatch
):
    project = store.project("合成一致性", [])
    entity, _ = original(store, project)
    revision = store.revision()
    from rg.export import build as module

    actual = module.graph

    def concurrent(reader, claims):
        with closing(Store(store.root)) as writer:
            append(writer, entity, "entity_version", {"label": "后来插入"})
        return actual(reader, claims)

    monkeypatch.setattr(module, "graph", concurrent)
    with closing(Store(store.root, readonly=True)) as reader_store:
        manifest, _ = archive(reader_store, {"project_id": project, "expected_revision": revision})
    assert manifest["conditions"]["revision"] == revision
    assert manifest["counts"]["claims"] == 1
    assert store.revision() > revision
    with pytest.raises(ConflictError):
        write(store, {"project_id": project, "expected_revision": revision}, tmp_path / "stale.zip")


def test_missing_or_corrupt_original_only_blocks_explicit_text_not_locator_export(store, tmp_path):
    project = store.project("合成缺原件", [])
    _, claim = original(store, project)
    event, _, _ = evidence(store, project, claim, "合成正文")
    sha = store.db.execute(
        "SELECT object_sha256 FROM raw_events WHERE event_id=?", (event,)
    ).fetchone()[0]
    store.objects.path(sha).unlink()
    write(store, options(project), tmp_path / "references.zip")
    with pytest.raises(FileNotFoundError):
        write(store, options(project, include_evidence=True), tmp_path / "text.zip")
    assert not (tmp_path / "text.zip").exists()


def test_structural_id_and_redacted_key_collisions_fail_closed(store, tmp_path):
    project = store.project("合成遮盖错误", [])
    entity, _ = original(store, project)
    append(store, entity, "entity_version", {"a@example.test": 1, "b@example.test": 2})
    with pytest.raises(ValueError, match="碰撞"):
        write(store, options(project), tmp_path / "collision.zip")
    other = store.project("另一合成项目", [])
    original(store, other)
    with pytest.raises(ValueError, match="结构标识符"):
        write(store, options(other, redact_patterns=[other]), tmp_path / "id.zip")


def test_atomic_publish_never_overwrites_or_follows_parent_links_and_cleans_failure(
    store, tmp_path, monkeypatch
):
    project = store.project("合成文件安全", [])
    target = tmp_path / "already.zip"
    target.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="已存在"):
        write(store, options(project), target)
    assert target.read_bytes() == b"preserve"
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        write(store, options(project), link / "escape.zip")
    with pytest.raises(ValueError, match="证据库"):
        write(store, options(project), store.root / "export.zip")
    original_link = os.link

    def race(source, dest, **kwargs):
        (tmp_path / dest).write_bytes(b"concurrent owner")
        return original_link(source, dest, **kwargs)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(FileExistsError):
        write(store, options(project), tmp_path / "race.zip")
    assert (tmp_path / "race.zip").read_bytes() == b"concurrent owner"
    assert not list(tmp_path.glob(".rg-export-*"))


@pytest.mark.parametrize("change", ["tamper", "extra", "missing", "duplicate", "path", "symlink"])
def test_archive_verifier_rejects_tamper_and_unsafe_member_sets(store, change):
    project = store.project("合成校验", [])
    _, data = archive(store, options(project))
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    if change == "tamper":
        files["claims.json"] = b"x" * len(files["claims.json"])
    elif change == "missing":
        del files["claims.json"]
    elif change in {"extra", "path"}:
        files["../../outside" if change == "path" else "extra.json"] = b"private"
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as target:
        for name, value in files.items():
            if change == "symlink" and name == "claims.json":
                member = zipfile.ZipInfo(name)
                member.external_attr = 0o120777 << 16
                target.writestr(member, value)
            else:
                target.writestr(name, value)
        if change == "duplicate":
            with pytest.warns(UserWarning):
                target.writestr("claims.json", files["claims.json"])
    with pytest.raises(ValueError):
        verify(io.BytesIO(result.getvalue()))


def test_cli_export_and_verifier_work_readonly_outside_workspace_and_without_database(
    store, tmp_path
):
    project = store.project("合成命令", [])
    original(store, project)
    target = tmp_path / "cli.zip"
    command = [
        sys.executable,
        "-c",
        "from rg.cli.main import main; main()",
        "--data-dir",
        str(store.root),
        "export",
        "--project",
        project,
        "--output",
        str(target),
        "--until",
        T3,
        "--known-until",
        T4,
        "--scope",
        "data=synthetic_v1",
        "--scope",
        "step=测试",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["counts"]["claims"] == 1
    assert json.loads(result.stdout)["permissions"]["posix_private"]
    assert target.stat().st_mode & 0o777 == 0o600
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(tmp_path / "nonexistent"),
            "verify-export",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["verified_files"] == 10
    assert not (tmp_path / "nonexistent").exists()


def test_http_export_same_origin_authenticated_zip_and_readonly_conflict(store, tmp_path):
    project = store.project("合成 HTTP 导出", [])
    original(store, project)
    before = unchanged(store)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>合成</html>")
    server = LocalServer(store.root, web, port=0, token="synthetic-export-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=server.origin,
            headers={"Authorization": f"Bearer {server.token}"},
            trust_env=False,
        ) as client:
            value = options(project, expected_revision=store.revision())
            assert (
                client.post("/api/exports", json=value, headers={"Authorization": ""}).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/exports", json=value, headers={"Origin": "https://example.invalid"}
                ).status_code
                == 403
            )
            assert (
                client.post("/api/exports", json=value | {"expected_revision": 0}).status_code
                == 409
            )
            assert (
                client.post("/api/exports", json=value | {"output": "/private"}).status_code == 400
            )
            response = client.post("/api/exports", json=value)
            assert (
                client.post(
                    "/api/exports",
                    content='{"project_id":"one","project_id":"two"}',
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 400
            )
            assert (
                response.status_code == 200
                and response.headers["Content-Type"] == "application/zip"
            )
            assert response.headers["Cache-Control"] == "no-store"
            assert verify(io.BytesIO(response.content))["counts"]["claims"] == 1
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert not thread.is_alive()
    assert unchanged(store) == before
    assert not (store.root / "exports").exists()
