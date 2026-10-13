from __future__ import annotations

import json

import httpx
import pytest

from rg.model import AuthError, Model, ModelError


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
