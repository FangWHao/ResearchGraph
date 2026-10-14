from __future__ import annotations

import json

import httpx
import pytest

from rg.model import AuthError, Model, ModelError, Truncated, Usage, cached


def model(handler, retries: int = 2) -> tuple[Model, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Model(
        "https://api.example.com/anthropic", "m1", "sk-test", httpx.MockTransport(record), retries
    ), seen


def test_check_reports_model_and_sends_no_session_content():
    client, seen = model(lambda r: httpx.Response(200, json={"model": "m1-2026", "content": []}))
    assert client.check() == "m1-2026"
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/anthropic/v1/messages"
    assert body["max_tokens"] == 1 and body["messages"] == [{"role": "user", "content": "ping"}]


def test_check_hides_provider_text_on_auth_failure():
    reply = {"error": {"message": "Incorrect API key provided: sk-te****"}}
    client, _ = model(lambda r: httpx.Response(401, json=reply))
    with pytest.raises(AuthError) as error:
        client.check()
    assert "401" in str(error.value) and "sk-" not in str(error.value)


def test_check_explains_other_failures():
    client, _ = model(lambda r: httpx.Response(400, json={"error": {"message": "Model Not Exist"}}))
    with pytest.raises(ModelError, match="HTTP 400：Model Not Exist"):
        client.check()
    client, _ = model(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(ModelError, match="接口地址"):
        client.check()


def test_auth_failure_is_not_retried(monkeypatch):
    monkeypatch.setattr("rg.model.time.sleep", lambda s: None)
    client, seen = model(lambda r: httpx.Response(401, json={}))
    with pytest.raises(AuthError):
        client("s", "u", {"type": "object"}, 10)
    assert len(seen) == 1


def test_truncated_reply_still_reports_tokens():
    reply = {
        "model": "m1-2026",
        "stop_reason": "max_tokens",
        "content": [],
        "usage": {"input_tokens": 1200, "output_tokens": 4000, "cache_read_input_tokens": 300},
    }
    client, _ = model(lambda r: httpx.Response(200, json=reply))
    seen: list[Usage | None] = []
    with pytest.raises(Truncated):
        client("s", "u", {"type": "object"}, 4000, seen.append)
    assert seen == [Usage("m1-2026", 1200, 4000, 300, 0)]


def test_empty_tool_input_is_retried_and_never_cached(monkeypatch, store):
    monkeypatch.setattr("rg.model.time.sleep", lambda s: None)
    replies = [{}, {}, {"events": []}]

    def handler(request: httpx.Request) -> httpx.Response:
        tool = {"type": "tool_use", "name": "submit", "input": replies.pop(0)}
        return httpx.Response(200, json={"content": [tool], "stop_reason": "tool_use"})

    client, seen = model(handler)
    schema = {"type": "object", "required": ["events"]}
    assert cached(store, client, "t")("s", "u", schema, 10) == {"events": []}  # 第三次才对
    assert len(seen) == 3

    replies[:] = [{}, {}, {}]
    with pytest.raises(ModelError):
        cached(store, client, "t")("s", "other", schema, 10)
    # 空结果不会被缓存，下次还会真的去问
    assert store.one("SELECT COUNT(*) AS n FROM model_cache")["n"] == 1


def test_thinking_lets_the_model_call_the_tool_itself():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        tool = {"type": "tool_use", "name": "submit", "input": {"events": []}}
        return httpx.Response(
            200, json={"content": [{"type": "thinking"}, tool], "stop_reason": "tool_use"}
        )

    client = Model(
        "https://api.example.com/anthropic",
        "m1",
        "sk-test",
        httpx.MockTransport(handler),
        thinking="high",
    )
    assert client("s", "u", {"type": "object", "required": ["events"]}, 1000) == {"events": []}
    body = seen[0]
    # 思考和强制调用工具不能同时用；思考的 token 另外预留
    assert body["tool_choice"] == {"type": "auto"}
    assert body["thinking"]["type"] == "enabled" and body["max_tokens"] > 1000
