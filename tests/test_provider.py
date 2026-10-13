from __future__ import annotations

import json

import httpx
import pytest

from rg.extract.provider import Provider
from rg.slim.tokens import CountingUnavailable


def test_deepseek_count_and_generate_share_input():
    requests = []

    def handle(request: httpx.Request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": "submit_claims", "input": {"ok": True}}],
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 10,
                },
                "stop_reason": "tool_use",
            },
        )

    provider = Provider(
        "https://api.deepseek.com", "deepseek-flash", "fake", httpx.MockTransport(handle)
    )
    try:
        body = provider.request("合成指令", "合成正文", {"type": "object"})
        assert provider.count_request(body) == 100
        generated = provider.generate(body)
        assert json.loads(generated.text) == {"ok": True}
        assert generated.input_tokens == 145
        assert requests[0][1]["messages"] == requests[1][1]["messages"]
        assert requests[0][1]["system"] == requests[1][1]["system"]
        assert requests[0][1]["tools"] == requests[1][1]["tools"]
        assert "max_tokens" not in requests[0][1]
    finally:
        provider.close()


def test_compatible_service_missing_count_fails_closed():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": "unsupported"})

    provider = Provider(
        "https://example.invalid/v1", "configured-model", "fake", httpx.MockTransport(handle)
    )
    with pytest.raises(CountingUnavailable):
        provider.count_request(provider.request("指令", "合成正文", {"type": "object"}))
    assert paths == ["/v1/responses/input_tokens"]
    provider.close()


def test_count_masks_credentials_before_remote_request():
    bodies = []

    def handle(request):
        bodies.append(request.content)
        return httpx.Response(200, json={"input_tokens": 10})

    provider = Provider(
        "https://api.deepseek.com", "deepseek-flash", "fake", httpx.MockTransport(handle)
    )
    provider.count_text("key=sk-syntheticsecret123; email=alice@example.org")
    assert b"sk-syntheticsecret123" not in bodies[0]
    assert b"alice@example.org" not in bodies[0]
    provider.close()
