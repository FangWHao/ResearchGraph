from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from contextlib import closing

import httpx
import pytest

from rg.api.views import NotFound
from rg.query.graph import query
from rg.query.graph_records import detail
from rg.query.graph_records import evidence as historical_evidence
from rg.query.reader import Reader
from rg.store.database import ConflictError, Store, dumps
from tests.golden.test_exports import evidence, unchanged
from tests.golden.test_read_tools import SCOPE, T1, T2, T3, T4, append, original, review
from tests.golden.test_semantic_graph import relation


def options(**values):
    return {"known_until": T4, "occurred_until": T4} | values


def records(store, project, **values):
    return query(store, project, options(collection="claims", **values))


def test_all_records_after_2000_and_all_reference_pages_are_preserved(store):
    project = store.project("合成完整图记录", [])
    one, first = original(store, project, kind="finding")
    with store.transaction():
        for index in range(2003):
            append(store, one, "entity_version", {"label": f"历史版本{index}"})
        two, _ = original(store, project, kind="finding")
        last = relation(store, one, two, "challenges", state="confirmed")
        for index in range(105):
            evidence(store, project, first, f"合成原始引用{index}")
    initial = records(store, project, limit=100)
    assert initial["claims_total"] == initial["total"] == 2006
    assert len(initial["items"][0]["evidence"]) == 105
    assert initial["items"][0]["groups_basis"] == "visible_source_sessions"
    assert len(initial["items"][0]["groups"]) == 105
    assert all(g["segment_id"] is None for g in initial["items"][0]["groups"])
    ids = [item["claim_id"] for item in initial["items"]]
    offset = initial["next_offset"]
    while offset is not None:
        found = records(
            store, project, limit=100, offset=offset, expected_revision=initial["revision"]
        )
        ids.extend(item["claim_id"] for item in found["items"])
        offset = found["next_offset"]
    assert len(ids) == len(set(ids)) == 2006 and ids[-1] == last
    assert records(store, project, offset=len(ids))["items"] == []
    assert initial["semantic_graph_complete"] and not initial["projection"]
    assert not initial["scope_filter"] and initial["scope"] is None


def test_historical_review_and_replacement_do_not_use_current_claim_state(store, monkeypatch):
    project = store.project("合成历史记录状态", [])
    entity, first = original(store, project)
    review(store, first, "confirm", T2)
    review(store, first, "dismiss", T3)
    replacement = append(
        store,
        entity,
        "entity_version",
        {"label": "迟到人工更正"},
        state="confirmed",
        recorded=T3,
        replaces=first,
    )
    monkeypatch.setattr(Store, "claim_state", lambda *a: pytest.fail("不能读取今天的审核"))
    old = records(store, project, known_until=T2)["items"]
    assert len(old) == 1 and old[0]["effective_state"] == "confirmed"
    assert old[0]["replacement_ids"] == [] and not old[0]["replaced"]
    assert old[0]["confirmation_source"] == "human"
    before = detail(store, project, first, options(known_until=T2))["claim"]
    assert [r["action"] for r in before["review_history"]] == ["confirm"]
    current = records(store, project)["items"]
    assert current[0]["effective_state"] == "dismissed"
    assert current[0]["replacement_ids"] == [replacement] and current[0]["replaced"]
    corrected = detail(store, project, replacement, options())["claim"]
    assert corrected["replaces"]["claim_id"] == first
    assert corrected["replaces"]["payload"] == old[0]["payload"]


def test_scope_and_both_cutoffs_filter_versions_sources_and_missing_times(store):
    project = store.project("合成完整范围与时间", [])
    entity, first = original(store, project)
    other = append(store, entity, "entity_version", {"label": "另一个条件"}, scope={"data": "v2"})
    unknown = append(store, entity, "entity_version", {"label": "未知范围"}, scope=None)
    future = append(store, entity, "entity_version", {"label": "未来发生"}, occurred=T3)
    late = append(store, entity, "entity_version", {"label": "旧事迟到"}, recorded=T3)
    missing = append(store, entity, "entity_version", {"label": "缺发生时间"}, occurred=None)
    append(store, entity, "entity_version", {"label": "缺已知时间"}, recorded="unknown")
    early_event, early_span, _ = evidence(store, project, first, "早期合成引用")
    evidence(store, project, first, "后来发生引用", occurred=T3)
    evidence(store, project, first, "旧事迟到引用", recorded=T3)
    evidence(store, project, first, "缺入库时间引用", recorded="unknown")
    foreign = store.project("合成其它项目", [])
    evidence(store, foreign, first, "外项目错绑的历史引用")
    found = records(store, project, known_until=T2, occurred_until=T2)
    assert [r["claim_id"] for r in found["items"]] == [first, other, unknown, missing]
    assert found["unknown_recorded_time_excluded"] == 2
    assert [s["span_id"] for s in found["items"][0]["evidence"]] == [early_span]
    assert found["items"][0]["evidence"][0]["event_id"] == early_event
    assert found["items"][-1]["occurred_time_unknown"]
    assert [r["claim_id"] for r in records(store, project, scope=None)["items"]] == [unknown]
    assert [r["claim_id"] for r in records(store, project, scope=SCOPE)["items"]] == [
        first,
        future,
        late,
        missing,
    ]
    with pytest.raises(NotFound):
        detail(store, project, other, options(scope=SCOPE))


def test_replacement_detail_does_not_reveal_cutoff_excluded_original(store):
    project = store.project("合成缺更正原件", [])
    entity, first = original(store, project)
    replacement = append(
        store,
        entity,
        "entity_version",
        {"label": "另范围人工版"},
        scope={"data": "v2"},
        replaces=first,
    )
    found = detail(store, project, replacement, options(scope={"data": "v2"}))["claim"]
    assert not found["replacement_original_visible"] and "replaces" not in found
    with pytest.raises(NotFound):
        detail(store, store.project("合成隔离详情", []), first, options())


def test_readonly_records_never_read_raw_files_or_connect_to_models(store, monkeypatch):
    project = store.project("合成离线完整记录", [])
    _, first = original(store, project)
    evidence(store, project, first, "原始引用元数据")
    before = unchanged(store)
    monkeypatch.setattr(Store, "raw", lambda *a: pytest.fail("分页与详情不能打开原文"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("不能联网"))
    with closing(Store(store.root, readonly=True)) as readonly:
        assert records(readonly, project)["items"][0]["evidence"]
        assert detail(readonly, project, first, options())["claim"]["evidence"]
    assert unchanged(store) == before


def test_writer_during_reference_loading_cannot_mix_revisions(store, monkeypatch):
    project = store.project("合成读取快照", [])
    entity, first = original(store, project)
    revision = store.revision()
    original_refs = Reader.references
    fired = False

    def refs(reader, *args, **kwargs):
        nonlocal fired
        if not fired:
            fired = True
            with closing(Store(store.root)) as writer:
                review(writer, first, "confirm")
                append(writer, entity, "entity_version", {"label": "并发更正"}, replaces=first)
        return original_refs(reader, *args, **kwargs)

    monkeypatch.setattr(Reader, "references", refs)
    found = records(store, project, expected_revision=revision)
    assert found["revision"] == revision and found["total"] == 1
    assert found["items"][0]["effective_state"] == "candidate"
    assert not found["items"][0]["replacement_ids"]
    with pytest.raises(ConflictError):
        records(store, project, offset=1, expected_revision=revision)
    with pytest.raises(ConflictError):
        detail(store, project, first, options(expected_revision=revision))


def sequence(store, project, claim, rows):
    first, _, _ = evidence(
        store, project, claim, rows[0][0], occurred=rows[0][1], recorded=rows[0][2]
    )
    base = store.db.execute("SELECT * FROM raw_events WHERE event_id=?", (first,)).fetchone()
    ids = [first]
    for seq, (text, occurred, recorded) in enumerate(rows[1:], 1):
        raw = dumps({"message": {"content": text}}).encode()
        sha = store.objects.put(raw)
        identity = store.db.execute(
            "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,object_sha256,"
            "seq,kind,role,occurred_at,recorded_at,line_sha256) "
            "VALUES (?,?,?,?,?,?,'user_text','user',?,?,?)",
            (
                base["session_pk"],
                base["file_instance_id"],
                seq * 1000,
                seq * 1000 + len(raw),
                sha,
                seq,
                occurred,
                recorded,
                sha,
            ),
        ).lastrowid
        ids.append(identity)
    return ids


def test_historical_raw_context_skips_future_late_unknown_events_before_reading_objects(
    store, monkeypatch
):
    project = store.project("合成历史原文", [])
    _, first = original(store, project)
    ids = sequence(
        store,
        project,
        first,
        [
            ("可见前文", T1, T1),
            ("前文未来发生", T3, T1),
            ("引用原件", T1, T1),
            ("后文迟到", T1, T3),
            ("后文缺已知时间", T1, "unknown"),
            ("可见后文", T1, T1),
        ],
    )
    raw_method = Store.raw
    read = []

    def raw(store, identity):
        assert identity in {ids[0], ids[2], ids[5]}, "不能打开不可见原件"
        read.append(identity)
        return raw_method(store, identity)

    monkeypatch.setattr(Store, "raw", raw)
    foreign = store.project("合成原文隔离", [])
    before = unchanged(store)
    found = historical_evidence(
        store, project, ids[2], options(known_until=T2, occurred_until=T2, context=1)
    )
    assert [e["event_id"] for e in found["before"]] == [ids[0]]
    assert [e["event_id"] for e in found["after"]] == [ids[5]]
    assert set(read) == {ids[0], ids[2], ids[5]}
    assert found["history_context"] and not found["derived_context_loaded"]
    assert not set(found) & {"l1", "event_chain", "session_parent", "artifact_versions"}
    for identity in ids[1], ids[3], ids[4]:
        with pytest.raises(NotFound):
            historical_evidence(
                store, project, identity, options(known_until=T2, occurred_until=T2)
            )
    with pytest.raises(NotFound):
        historical_evidence(store, foreign, ids[2], options())
    assert unchanged(store) == before


def test_quote_bytes_hash_and_revision_are_preserved_with_deleted_original(store):
    project = store.project("合成历史字节窗口", [])
    _, first = original(store, project)
    event, span, raw = evidence(store, project, first, "字节与引用不重写")
    saved = store.db.execute("SELECT * FROM evidence_spans WHERE span_id=?", (span,)).fetchone()
    before = unchanged(store)
    found = historical_evidence(
        store,
        project,
        event,
        options(
            expected_revision=store.revision(),
            start=saved["byte_start"],
            end=saved["byte_end"],
            context=0,
        ),
    )
    assert found["event"]["quote"].encode() == raw[saved["byte_start"] : saved["byte_end"]]
    assert found["event"]["quote_sha256"] == saved["quote_sha256"]
    assert unchanged(store) == before
    with pytest.raises(ConflictError):
        historical_evidence(store, project, event, options(expected_revision=0))


def test_writer_during_original_read_cannot_add_context_to_the_same_snapshot(store, monkeypatch):
    import rg.query.graph_records as graph_records

    project = store.project("合成原文并发快照", [])
    _, first = original(store, project)
    event, _, _ = evidence(store, project, first, "原件先已登记")
    revision = store.revision()
    raw_event = store.db.execute("SELECT * FROM raw_events WHERE event_id=?", (event,)).fetchone()
    initial_event = graph_records._event
    written = False

    def open_event(current, identity, start, end):
        nonlocal written
        if not written:
            written = True
            with closing(Store(store.root)) as writer:
                raw = dumps({"message": {"content": "读途中新增的原文"}}).encode()
                sha = writer.objects.put(raw)
                writer.db.execute(
                    "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,"
                    "object_sha256,seq,kind,role,occurred_at,recorded_at,line_sha256) "
                    "VALUES (?,?,1000,?,?,1,'user_text','user',?,?,?)",
                    (
                        raw_event["session_pk"],
                        raw_event["file_instance_id"],
                        1000 + len(raw),
                        sha,
                        T1,
                        T1,
                        sha,
                    ),
                )
                review(writer, first, "confirm", T2)
        return initial_event(current, identity, start, end)

    monkeypatch.setattr(graph_records, "_event", open_event)
    found = historical_evidence(
        store, project, event, options(expected_revision=revision, context=2)
    )
    assert found["revision"] == revision and found["before"] == found["after"] == []
    assert store.db.execute("SELECT count(*) FROM raw_events").fetchone()[0] == 2
    with pytest.raises(ConflictError):
        historical_evidence(store, project, event, options(expected_revision=revision))


@pytest.mark.parametrize(
    "bad",
    [
        {"context": True},
        {"context": 6},
        {"start": True, "end": 3},
        {"start": 0},
        {"start": -1, "end": 3},
        {"expected_revision": True},
        {"scope": SCOPE},
    ],
)
def test_invalid_historical_evidence_parameters_are_rejected_without_writes(store, bad):
    project = store.project("合成历史窗口拒绝", [])
    _, first = original(store, project)
    event, _, _ = evidence(store, project, first, "窗口不能猜")
    before = unchanged(store)
    with pytest.raises(ValueError):
        historical_evidence(store, project, event, options(**bad))
    assert unchanged(store) == before


@pytest.mark.parametrize(
    "bad",
    [
        {"limit": 101},
        {"offset": -1},
        {"expected_revision": True},
        {"known_until": "2026-01-01"},
        {"scope": []},
        {"unknown": "field"},
    ],
)
def test_bad_record_page_queries_are_rejected_without_mutating_the_graph(store, bad):
    project = store.project("合成分页拒绝", [])
    original(store, project)
    before = unchanged(store)
    with pytest.raises(ValueError):
        records(store, project, **bad)
    assert unchanged(store) == before


def test_cli_and_real_authorized_http_expose_records_and_historical_detail(store, tmp_path):
    from rg.api.server import LocalServer

    project = store.project("合成分页入口", [])
    _, first = original(store, project)
    event, _, _ = evidence(store, project, first, "入口验收原话")
    review(store, first, "confirm", T3)
    command = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rg.cli.main import main; main()",
            "--data-dir",
            str(store.root),
            "graph",
            "--project",
            project,
            "--collection",
            "claims",
            "--known-until",
            T2,
            "--occurred-until",
            T2,
        ],
        capture_output=True,
        text=True,
    )
    assert command.returncode == 0, command.stderr
    cli = json.loads(command.stdout)
    (tmp_path / "index.html").write_text("合成本地页")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = unchanged(store)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            values = {"project": project, "known_until": T2, "occurred_until": T2}
            assert client.get("/api/semantic-graph", params=values).status_code == 401
            client.headers["Authorization"] = "Bearer synthetic-token"
            response = client.get("/api/semantic-graph", params=values | {"collection": "claims"})
            assert response.status_code == 200 and response.json()["items"] == cli["items"]
            assert "no-store" in response.headers["cache-control"]
            historical = client.get(f"/api/claims/{first}", params=values)
            assert historical.status_code == 200
            assert historical.json()["claim"]["effective_state"] == "candidate"
            assert not historical.json()["claim"]["review_history"]
            assert (
                client.get(f"/api/claims/{first}").json()["claim"]["effective_state"] == "confirmed"
            )
            source = client.get(f"/api/evidence/{event}", params=values | {"context": 0})
            assert source.status_code == 200 and source.json()["history_context"]
            assert (
                client.get(
                    f"/api/claims/{first}", params=values | {"expected_revision": 0}
                ).status_code
                == 409
            )
            assert (
                client.get(f"/api/evidence/{event}", params=values | {"context": "-1"}).status_code
                == 400
            )
            assert (
                client.get(f"/api/evidence/{event}", params=values | {"scope": "null"}).status_code
                == 400
            )
            assert (
                client.get(
                    f"/api/claims/{first}", params=values, headers={"Host": "unknown.test"}
                ).status_code
                == 403
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert unchanged(store) == before
