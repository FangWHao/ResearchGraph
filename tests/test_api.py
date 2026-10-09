from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from rg.api import views
from rg.api.server import LocalServer
from rg.extract.validate import persist
from rg.ingest.scanner import scan_file
from rg.store.database import Store, dumps, now
from tests.golden.test_extraction import setup
from tests.golden.test_ingestion import lines, record


@pytest.fixture
def browser_api(store: Store, tmp_path: Path):
    project, output, run = setup(store, tmp_path)
    ids = persist(store, output, project, run, set(), {1}, "test-segment")
    store.db.execute(
        "UPDATE coverage SET segment_id='test-segment' WHERE event_id=1 AND stage='pass2'"
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>合成界面</html>")
    (web / "asset.js").write_text("window.synthetic=true")
    server = LocalServer(store.root, web, port=0, token="synthetic-browser-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(
        base_url=server.origin,
        headers={"Authorization": f"Bearer {server.token}"},
        timeout=5,
        trust_env=False,
    ) as client:
        yield server, client, project, ids, output
    server.shutdown()
    thread.join(5)
    server.server_close()
    assert not thread.is_alive()


def test_local_binding_authentication_and_cross_origin(browser_api):
    server, client, _, _, _ = browser_api
    assert server.server_address[0] == "127.0.0.1"
    assert client.get("/").status_code == 200
    assert client.get("/api/projects", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/api/projects", headers={"Origin": "https://external.invalid"}).status_code
        == 403
    )
    assert client.get("/api/projects", headers={"Host": "external.invalid"}).status_code == 403
    assert client.get("/api/projects", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    response = client.options("/api/review")
    assert response.status_code == 403
    assert "Access-Control-Allow-Origin" not in response.headers
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert server.token not in response.text


def test_static_path_traversal_and_symlink_escape(browser_api, tmp_path: Path):
    server, client, _, _, _ = browser_api
    outside = tmp_path / "outside.txt"
    outside.write_text("不要向浏览器提供这个文件")
    (server.web_dir / "escape.txt").symlink_to(outside)
    assert client.get("/%2e%2e/outside.txt").status_code == 403
    assert client.get("/escape.txt").status_code == 403
    assert client.get("/asset.js").status_code == 200
    assert client.get("/api/projects?project=x&project=y").status_code == 400


def test_batch_review_conflict_and_atomic_missing_record(browser_api, store: Store):
    _, client, _, ids, _ = browser_api
    before = store.revision()
    body = {
        "claim_ids": [ids[0], 999],
        "action": "confirm",
        "actor": "human:测试者",
        "expected_revision": before,
    }
    assert client.post("/api/review", json=body).status_code == 404
    assert store.revision() == before
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0
    body["claim_ids"] = ids
    result = client.post("/api/review", json=body)
    assert result.status_code == 200
    assert result.json()["revision"] == before + 2
    assert client.post("/api/review", json=body).status_code == 409
    assert store.revision() == before + 2
    assert [store.claim_state(item) for item in ids] == ["confirmed", "confirmed"]
    assert [item[0] for item in store.db.execute("SELECT claim_state FROM claims")] == [
        "candidate",
        "candidate",
    ]


def test_review_cannot_impersonate_rule_or_model(browser_api, store: Store):
    _, client, _, ids, _ = browser_api
    for actor in ["rule:pretend", "model:pretend", "human:", "human:   "]:
        result = client.post(
            "/api/review",
            json={
                "claim_ids": ids,
                "action": "confirm",
                "actor": actor,
                "expected_revision": store.revision(),
            },
        )
        assert result.status_code == 400
    result = client.post(
        "/api/review",
        json={
            "claim_ids": ids,
            "action": "confirm",
            "actor": "human:tester",
            "expected_revision": True,
        },
    )
    assert result.status_code == 400
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0


def test_append_only_edit_preserves_raw_and_exposes_difference(browser_api, store: Store):
    _, client, project, ids, _ = browser_api
    before_raw = store.raw(1)
    before = client.get(f"/api/claims/{ids[0]}").json()
    payload = before["claim"]["payload"] | {
        "label": "人工澄清的方法甲",
        "content": "只适用于 data_v2",
    }
    body = {
        "payload": payload,
        "scope": {"dataset_version": "data_v2"},
        "actor": "human:人工",
        "expected_revision": before["revision"],
    }
    result = client.post(f"/api/claims/{ids[0]}/edit", json=body)
    assert result.status_code == 200, result.text
    new_id = result.json()["claim_id"]
    detail = client.get(f"/api/claims/{new_id}").json()["claim"]
    assert detail["claim_state"] == detail["effective_state"] == "confirmed"
    assert detail["confirmation_source"] == "human"
    assert detail["basis"] == "manual"
    assert detail["replaces"]["payload"]["label"] == "方法甲"
    assert detail["evidence"] == before["claim"]["evidence"]
    assert store.raw(1) == before_raw
    assert store.claim_state(ids[0]) == "dismissed"
    assert store.db.execute("SELECT payload FROM claims WHERE claim_id=?", (ids[0],)).fetchone()[
        0
    ] == dumps(before["claim"]["payload"])
    assert client.post(f"/api/claims/{ids[0]}/edit", json=body).status_code == 409
    body["expected_revision"] = store.revision()
    assert client.post(f"/api/claims/{ids[0]}/edit", json=body).status_code == 400
    candidates = client.get("/api/claims", params={"project": project, "state": "candidate"}).json()
    assert ids[0] not in [item["claim_id"] for item in candidates["claims"]]


def test_edit_rejects_retargeting_invalid_scope_and_invalid_payload(browser_api, store: Store):
    _, client, _, ids, _ = browser_api
    original = client.get(f"/api/claims/{ids[1]}").json()["claim"]
    for payload, scope in [
        (original["payload"] | {"target": "different-entity"}, original["scope"]),
        (original["payload"], {}),
        (original["payload"] | {"action": "invented"}, original["scope"]),
    ]:
        before = store.revision()
        result = client.post(
            f"/api/claims/{ids[1]}/edit",
            json={
                "payload": payload,
                "scope": scope,
                "actor": "human:tester",
                "expected_revision": before,
            },
        )
        assert result.status_code == 400
        assert store.revision() == before


def test_rule_confirmation_origin_is_distinct_from_record_actor(browser_api, store: Store):
    _, client, _, ids, _ = browser_api
    store.db.execute(
        "INSERT INTO review_actions (claim_id,action,actor,expected_revision,recorded_at,reason) "
        "VALUES (?,'confirm','rule:explicit-user-v1',?,?,?)",
        (
            ids[1],
            store.revision(),
            now(),
            json.dumps({"basis": "direct_record", "rule_version": "explicit-user-v1"}),
        ),
    )
    detail = client.get(f"/api/claims/{ids[1]}").json()["claim"]
    assert detail["confirmation_source"] == "rule"
    assert detail["actor"].startswith("model:")
    assert detail["claim_state"] == "candidate"
    assert detail["effective_state"] == "confirmed"
    assert detail["review"]["actor"] == "rule:explicit-user-v1"


def test_evidence_utf8_offsets_context_and_local_survival(
    browser_api, store: Store, tmp_path: Path
):
    _, client, project, ids, _ = browser_api
    path = tmp_path / "next.jsonl"
    lines(path, [record("后续原文上下文")])
    scan_file(store, path, "claude", project)
    spans = client.get(f"/api/claims/{ids[0]}").json()["claim"]["evidence"]
    span = spans[0]
    result = client.get(
        f"/api/evidence/{span['event_id']}",
        params={
            "start": span["byte_start"],
            "end": span["byte_end"],
            "context": 2,
        },
    )
    assert result.status_code == 200
    event = result.json()["event"]
    assert event["quote"] == "采用方法甲"
    assert event["quote_sha256"] == span["quote_sha256"]
    assert event["source_byte_start"] == span["source_byte_start"]
    source = Path(event["path"])
    source.unlink()
    assert client.get(f"/api/evidence/{span['event_id']}").status_code == 200
    assert (
        client.get(
            f"/api/evidence/{span['event_id']}",
            params={
                "start": span["byte_start"] + 1,
                "end": span["byte_end"],
            },
        ).status_code
        == 400
    )
    assert client.get(f"/api/evidence/{span['event_id']}?context=6").status_code == 400


def test_project_filter_literal_search_and_queue_groups(browser_api, store: Store, tmp_path: Path):
    _, client, project, ids, _ = browser_api
    other = store.project("其他合成项目", [tmp_path / "other"])
    path = tmp_path / "other.jsonl"
    lines(path, [record('采用方法甲 OR " SELECT * FROM claims', sessionId="other-demo")])
    scan_file(store, path, "claude", other)
    response = client.get("/api/search", params={"q": "采用方法甲", "project": project})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    assert (
        client.get("/api/search", params={"q": 'OR " SELECT', "project": other}).json()["total"]
        == 1
    )
    assert (
        client.get("/api/search", params={"q": 'OR " SELECT', "project": project}).json()["total"]
        == 0
    )
    group = client.get(
        "/api/claims", params={"project": project, "session": 1, "segment": "test-segment"}
    ).json()
    assert [item["claim_id"] for item in group["claims"]] == ids
    assert client.get("/api/claims", params={"project": other}).json()["claims"] == []
    assert client.get("/api/graph", params={"project": other}).json()["claims"] == []
    assert client.get("/api/graph?project=absent").status_code == 404


def test_health_missing_metrics_and_budget_not_fabricated(browser_api, store: Store):
    _, client, project, _, _ = browser_api
    response = client.get("/api/health", params={"project": project})
    assert response.status_code == 200
    result = response.json()
    assert result["hook_failures"] is None
    assert result["global_counts"] is True
    assert result["extraction"]["daily_usage"]["scope"] == "all_projects"
    assert result["extraction"]["daily_usage"]["budget_tokens"] == 500000
    assert result["sources"][0]["cursor_lag_bytes"] == 0
    path = Path(result["sources"][0]["path"])
    path.unlink()
    health = client.get("/api/health", params={"project": project}).json()
    assert health["sources"][0]["cursor_lag_bytes"] is None
    assert health["sources"][0]["cleanup_risk"] == "source_missing"


def test_request_body_limit_and_no_command_execution(browser_api, store: Store, tmp_path: Path):
    _, client, _, _, _ = browser_api
    sentinel = tmp_path / "must-not-exist"
    result = client.post(
        "/api/review", content=b"x" * 65537, headers={"Content-Type": "application/json"}
    )
    assert result.status_code == 413
    assert client.post("/api/execute", json={"command": f"touch {sentinel}"}).status_code == 404
    assert not sentinel.exists()
    assert client.post("/api/review", json=[1, 2]).status_code == 400
    assert (
        client.post(
            "/api/review", content=b"{", headers={"Content-Type": "application/json"}
        ).status_code
        == 400
    )
    assert store.db.execute("SELECT count(*) FROM review_actions").fetchone()[0] == 0


def test_large_evidence_is_returned_as_bounded_window(browser_api, store: Store, tmp_path: Path):
    _, client, project, _, _ = browser_api
    path = tmp_path / "large.jsonl"
    text = "很长的合成上下文。" * 5000 + "重点引用"
    lines(path, [record(text)])
    scan_file(store, path, "claude", project)
    event_id = store.db.execute("SELECT max(event_id) FROM raw_events").fetchone()[0]
    raw = store.raw(event_id)
    start = raw.index("重点引用".encode())
    result = client.get(
        f"/api/evidence/{event_id}", params={"start": start, "end": start + 12, "context": 0}
    ).json()["event"]
    assert result["quote"] == "重点引用"
    assert result["window_truncated"]
    assert (
        len((result["before"] + result["quote"] + result["after"]).encode())
        <= views.MAX_WINDOW_BYTES
    )


def test_candidate_comparison_uses_exact_human_confirmed_object_and_scope(
    browser_api, store: Store
):
    _, client, project, ids, _ = browser_api
    store.review(ids[1], "confirm", "human:先前复核", store.revision())
    original = client.get(f"/api/claims/{ids[1]}").json()["claim"]
    payload = original["payload"] | {"action": "withdrawn", "reason": "新的模型推测"}
    cursor = store.db.execute(
        "INSERT INTO claims (claim_type,entity_id,payload,scope,basis,actor,"
        "claim_state,recorded_at) "
        "VALUES ('decision_event',?,?,?,'model_inference','model:synthetic','candidate',?)",
        (original["entity_id"], dumps(payload), dumps(original["scope"]), now()),
    )
    new_id = cursor.lastrowid
    comparisons = client.get(f"/api/claims/{new_id}").json()["claim"]["human_comparisons"]
    assert comparisons[0]["claim_id"] == ids[1]
    assert "action" in comparisons[0]["differing_fields"]
    assert (
        client.get("/api/claims", params={"project": project}).json()["claims"][-1][
            "human_comparisons"
        ]
        == comparisons
    )
    store.review(ids[1], "dismiss", "human:先前复核", store.revision())
    assert client.get(f"/api/claims/{new_id}").json()["claim"]["human_comparisons"] == []


def test_batch_over_two_hundred_is_complete_and_oversize_is_rejected(browser_api, store: Store):
    _, client, _, ids, _ = browser_api
    original = store.db.execute("SELECT * FROM claims WHERE claim_id=?", (ids[0],)).fetchone()
    batch_ids = []
    for _ in range(201):
        cursor = store.db.execute(
            "INSERT INTO claims (claim_type,entity_id,payload,scope,basis,actor,recorded_at) "
            "VALUES (?,?,?,?,'model_inference','model:batch',?)",
            (
                original["claim_type"],
                original["entity_id"],
                original["payload"],
                original["scope"],
                now(),
            ),
        )
        batch_ids.append(cursor.lastrowid)
    before = store.revision()
    result = client.post(
        "/api/review",
        json={
            "claim_ids": batch_ids,
            "action": "confirm",
            "actor": "human:批量",
            "expected_revision": before,
        },
    )
    assert result.status_code == 200
    assert result.json()["revision"] == before + 201
    assert result.json()["claim_ids"] == batch_ids
    assert [store.claim_state(claim_id) for claim_id in batch_ids] == ["confirmed"] * 201
    result = client.post(
        "/api/review",
        json={
            "claim_ids": list(range(1, 2002)),
            "action": "dismiss",
            "actor": "human:批量",
            "expected_revision": store.revision(),
        },
    )
    assert result.status_code == 400
    assert store.revision() == before + 201


def test_graph_bound_is_explicit_not_complete_subgraph(browser_api, monkeypatch):
    _, client, project, _, _ = browser_api
    monkeypatch.setattr(views, "MAX_GRAPH_CLAIMS", 1)
    result = client.get("/api/graph", params={"project": project}).json()
    assert result["partial"] is True
    assert result["limit"] == 1
    assert len(result["claims"]) == 1


def test_review_open_passes_review_entry_to_server(store: Store, monkeypatch):
    from rg.cli.main import parser, run

    calls = []
    monkeypatch.setattr("rg.api.server.serve", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = run(parser().parse_args(["review", "--open"]), store)
    assert result == {"server": "stopped"}
    assert calls[0][1]["initial_view"] == "review"
    run(parser().parse_args(["serve"]), store)
    assert calls[1][1]["initial_view"] == "questions"
