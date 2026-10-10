from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from rg.api.qa import QAConfig
from rg.api.server import LocalServer
from rg.ingest.scanner import scan_file
from rg.store.database import dumps
from tests.golden.test_ingestion import lines, record
from tests.golden.test_links_overview import add_object
from tests.golden.test_qa import semantic, unpack


class Protocol:
    def __init__(self):
        self.counts = 0
        self.calls = 0
        self.inputs = []
        self.broken = False

    def handle(self, request):
        data = json.loads(request.content)
        if request.url.path == "/responses/input_tokens":
            self.counts += 1
            return httpx.Response(200, json={"input_tokens": 10})
        assert request.url.path == "/responses"
        self.calls += 1
        content = json.loads(data["input"])
        self.inputs.append(content)
        source = content["sources"][0]
        answer = {
            "status": "answered",
            "statements": [
                {
                    "text": "合成回答，保留候选状态。",
                    "citations": [
                        {
                            "id": "E99999" if self.broken else source["citation_id"],
                            "quote": source["text"][:50],
                        }
                    ],
                }
            ],
            "caveats": ["仅验证合成协议；引用有效不证明解释正确。"],
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": dumps(answer)}]}
                ],
            },
        )


@pytest.fixture
def qa_api(store, tmp_path):
    project = store.project("合成 API 问答", [])
    claim, _ = add_object(store, tmp_path, project, "api-qa", label="ref_v1")
    other = store.project("合成其他 API 项目", [])
    protocol = Protocol()
    config = QAConfig(
        "https://synthetic-model.invalid",
        "synthetic-model",
        "synthetic-secret-only",
        transport=httpx.MockTransport(protocol.handle),
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("合成静态页面")
    server = LocalServer(store.root, web, port=0, token="synthetic-qa-browser", qa_config=config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(
        base_url=server.origin,
        headers={"Authorization": f"Bearer {server.token}"},
        timeout=10,
        trust_env=False,
    ) as client:
        yield server, client, project, claim, other, protocol
    server.shutdown()
    thread.join(5)
    server.server_close()
    assert not thread.is_alive()


def body(project, store, question="为什么放弃 ref_v1"):
    return {"project_id": project, "question": question, "expected_revision": store.revision()}


def payload(response):
    assert response.status_code == 200, response.text
    return unpack(response.json()["context_text"])


def grant(client, project, store, question="为什么放弃 ref_v1"):
    core = body(project, store, question)
    preview = payload(client.post("/api/qa/preview", json=core))
    result = client.post(
        "/api/qa/allow-remote",
        json=core
        | {
            "allow": True,
            "preview_sha": preview["preview_sha"],
        },
    )
    assert result.status_code == 200, result.text
    return core, preview


def test_readonly_retrieve_preview_options_and_no_credentials_exposed(qa_api, store):
    _, client, project, _, _, protocol = qa_api
    before = list(store.db.iterdump())
    options = client.get("/api/qa/options", params={"project": project}).json()
    assert options["configured"] and options["remote"] and not options["remote_allowed"]
    retrieved = payload(client.post("/api/qa/retrieve", json=body(project, store)))
    preview = payload(client.post("/api/qa/preview", json=body(project, store)))
    assert retrieved["sources"] and preview["source_count"] == 1
    assert preview["input"]["sources"][0]["text_redacted"] is False
    assert list(store.db.iterdump()) == before and protocol.counts == protocol.calls == 0
    assert "synthetic-secret-only" not in dumps([retrieved, preview, options])
    assert "synthetic-model.invalid" not in dumps([retrieved, preview, options])


def test_permission_blocks_counter_then_ack_allows_and_revoke_blocks_again(qa_api, store):
    _, client, project, _, _, protocol = qa_api
    denied = client.post("/api/qa/answer", json=body(project, store))
    assert denied.status_code == 403 and protocol.counts == protocol.calls == 0
    before = semantic(store)
    core, preview = grant(client, project, store)
    assert semantic(store) == before and protocol.counts == protocol.calls == 0
    assert store.db.execute("SELECT count(*) FROM remote_previews").fetchone()[0] == 1
    answer = payload(client.post("/api/qa/answer", json=core))
    assert answer["status"] == "answered" and protocol.calls == 1
    assert "provider" not in answer and "synthetic-model.invalid" not in dumps(answer)
    assert protocol.inputs[0] == preview["input"] and semantic(store) == before
    repeated = payload(client.post("/api/qa/answer", json=core))
    assert repeated["run_id"] == answer["run_id"] and protocol.calls == 1
    response = client.post(
        "/api/qa/disable-remote",
        json={
            "project_id": project,
            "expected_revision": store.revision(),
        },
    )
    assert response.status_code == 200 and not response.json()["remote_allowed"]
    # 已有完整缓存也不能绕过撤回的项目许可。
    assert client.post("/api/qa/answer", json=core).status_code == 403
    assert protocol.calls == 1


@pytest.mark.parametrize("change", ["question", "k", "scope", "model", "records", "allow", "sha"])
def test_wrong_or_stale_preview_cannot_enable_remote(qa_api, store, change):
    server, client, project, claim, _, protocol = qa_api
    core = body(project, store)
    preview = payload(client.post("/api/qa/preview", json=core))
    attempt = core | {"allow": True, "preview_sha": preview["preview_sha"]}
    if change == "question":
        attempt["question"] = "ref_v1 新问题"
    elif change == "k":
        attempt["k"] = 13
    elif change == "scope":
        attempt["scope"] = {"dataset_version": "data_v2"}
    elif change == "model":
        server.qa_config = QAConfig("https://synthetic-model.invalid", "other-model", "test-only")
    elif change == "records":
        store.review(claim, "confirm", "human:合成", store.revision())
    elif change == "allow":
        attempt["allow"] = 1
    elif change == "sha":
        attempt["preview_sha"] = "0" * 64
    response = client.post("/api/qa/allow-remote", json=attempt)
    assert response.status_code in {400, 409}
    assert not store.db.execute(
        "SELECT remote_model_allowed FROM projects WHERE project_id=?", (project,)
    ).fetchone()[0]
    assert store.db.execute("SELECT count(*) FROM remote_previews").fetchone()[0] == 0
    assert protocol.counts == protocol.calls == 0


@pytest.mark.parametrize(
    "path", ["retrieve", "preview", "answer", "allow-remote", "disable-remote"]
)
def test_all_qa_operations_require_host_origin_and_token(qa_api, store, path):
    _, client, project, _, _, protocol = qa_api
    for headers, status in [
        ({"Authorization": ""}, 401),
        ({"Origin": "https://outside.invalid"}, 403),
        ({"Host": "outside.invalid"}, 403),
    ]:
        response = client.post("/api/qa/" + path, json=body(project, store), headers=headers)
        assert response.status_code == status
    assert protocol.counts == protocol.calls == 0


@pytest.mark.parametrize(
    "headers,status,size",
    [
        ({"Authorization": ""}, 401, 32768),
        ({"Origin": "https://outside.invalid"}, 403, 32768),
        ({}, 413, 65537),
    ],
)
def test_early_rejections_deliver_status_with_request_body_still_arriving(
    qa_api, store, headers, status, size
):
    _, client, _, _, _, protocol = qa_api
    before = list(store.db.iterdump())

    def chunks():
        yield b"x" * 1024
        time.sleep(0.03)
        yield b"x" * (size - 1024)

    response = client.post(
        "/api/qa/disable-remote",
        content=chunks(),
        headers={"Content-Type": "application/json", "Content-Length": str(size)} | headers,
    )
    assert response.status_code == status and response.json()["error"]
    assert list(store.db.iterdump()) == before
    assert protocol.counts == protocol.calls == 0


def test_wrong_project_no_model_empty_evidence_and_rejected_browser_overrides(qa_api, store):
    server, client, project, _, other, protocol = qa_api
    core = body(project, store)
    assert not payload(client.post("/api/qa/retrieve", json=body(other, store)))["sources"]
    assert (
        client.post(
            "/api/qa/allow-remote",
            json=body(other, store)
            | {
                "allow": True,
                "preview_sha": "0" * 64,
            },
        ).status_code
        == 400
    )
    for override in (
        {"base_url": "https://outside.invalid"},
        {"key": "test-only"},
        {"input_budget": 999999},
        {"model": "other"},
        {"k": True},
    ):
        assert client.post("/api/qa/answer", json=core | override).status_code == 400
    server.qa_config = None
    assert (
        payload(client.post("/api/qa/answer", json=body(project, store, "unmatched_unique")))[
            "status"
        ]
        == "insufficient"
    )
    assert client.post("/api/qa/answer", json=core).status_code == 503
    assert payload(client.post("/api/qa/retrieve", json=core))["sources"]
    assert protocol.calls == protocol.counts == 0


def test_bad_model_and_daily_budget_keep_evidence_and_research_facts(qa_api, store):
    server, client, project, _, _, protocol = qa_api
    core, _ = grant(client, project, store)
    before = semantic(store)
    server.daily_budget = 1
    result = payload(client.post("/api/qa/answer", json=core))
    assert result["status"] == "unavailable" and result["sources"] and protocol.calls == 0
    server.daily_budget = 500000
    protocol.broken = True
    result = payload(client.post("/api/qa/answer", json=core))
    assert result["status"] == "unavailable" and result["statements"] == [] and result["sources"]
    assert semantic(store) == before and protocol.calls == 1


def test_preview_masks_actual_question_and_source_then_generation_uses_identical_input(
    qa_api, store, tmp_path
):
    _, client, project, _, _, protocol = qa_api
    path = tmp_path / "privacy-qa.jsonl"
    lines(
        path, [record("隐私样例 qa-fixture@example.invalid", "qa-private", sessionId="qa-private")]
    )
    scan_file(store, path, "claude", project)
    core, preview = grant(client, project, store, "隐私样例 qa-fixture@example.invalid")
    assert preview["input"]["question_redacted"]
    assert any(source["text_redacted"] for source in preview["input"]["sources"])
    assert "qa-fixture@example.invalid" not in dumps(preview["input"])
    local = payload(client.post("/api/qa/retrieve", json=core))
    assert "qa-fixture@example.invalid" in dumps(local)
    before = semantic(store)
    answer = payload(client.post("/api/qa/answer", json=core))
    assert answer["status"] == "answered" and protocol.inputs[0] == preview["input"]
    assert semantic(store) == before


def test_new_raw_source_invalidates_preview_even_without_claim_revision_change(
    qa_api, store, tmp_path
):
    _, client, project, _, _, protocol = qa_api
    core = body(project, store)
    preview = payload(client.post("/api/qa/preview", json=core))
    path = tmp_path / "late-raw.jsonl"
    lines(path, [record("新原文 ref_v1，尚无结构化记录。", "qa-late", sessionId="qa-late")])
    scan_file(store, path, "claude", project)
    assert store.revision() == core["expected_revision"]
    response = client.post(
        "/api/qa/allow-remote",
        json=core | {"allow": True, "preview_sha": preview["preview_sha"]},
    )
    assert response.status_code == 409
    assert not client.get("/api/qa/options", params={"project": project}).json()["remote_allowed"]
    assert protocol.counts == protocol.calls == 0


def test_local_model_requires_no_remote_permission_and_reuses_answer(qa_api, store):
    server, client, project, _, _, protocol = qa_api
    server.qa_config = QAConfig(
        "http://127.0.0.1:9000",
        "synthetic-local-model",
        "synthetic-key-only",
        transport=httpx.MockTransport(protocol.handle),
    )
    core = body(project, store)
    before = semantic(store)
    answer = payload(client.post("/api/qa/answer", json=core))
    again = payload(client.post("/api/qa/answer", json=core))
    assert "provider" not in answer and "http://127.0.0.1:9000" not in dumps(answer)
    assert answer["status"] == "answered" and again["run_id"] == answer["run_id"]
    assert protocol.calls == 1 and semantic(store) == before
    assert store.db.execute("SELECT count(*) FROM remote_previews").fetchone()[0] == 0
    assert not client.get("/api/qa/options", params={"project": project}).json()["remote"]


@pytest.mark.parametrize(
    "base",
    [
        "http://remote.invalid",
        "https://user:pass@remote.invalid",
        "https://remote.invalid?key=value",
        "https://remote.invalid#value",
    ],
)
def test_config_is_private_and_rejects_unsafe_addresses(base):
    with pytest.raises(ValueError):
        QAConfig(base, "model", "synthetic-secret-only")
    config = QAConfig("https://synthetic-model.invalid", "model", "synthetic-secret-only")
    assert "synthetic-secret-only" not in repr(config)
