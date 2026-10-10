from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rg.cli.main import parser
from rg.extract.budgets import Budgets
from rg.extract.provider import ModelResult, Provider
from rg.extract.segmenter import Segment
from rg.extract.worker import Worker
from rg.slim.tokens import BudgetExceeded
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup


class MeasuredProvider(FakeProvider):
    def count_request(self, request):
        return 100 if request["input"] == "" else 128001


def test_default_budget_and_larger_override_both_measure_full_request(
    store: Store,
    tmp_path: Path,
):
    project, _, _ = setup(store, tmp_path)
    provider = MeasuredProvider([ModelResult('{"candidates":[]}', 128001, 10, "stop")])
    item = Segment("default", [{"event_id": 1}])
    initial_runs = store.db.execute("SELECT count(*) FROM extraction_runs").fetchone()[0]
    with pytest.raises(BudgetExceeded):
        Worker(store, provider).invoke("pass1", item, project, "短正文", {"type": "object"}, [])
    assert provider.calls == 0
    Worker(store, provider, input_budget=256000).invoke(
        "pass1",
        item,
        project,
        "短正文",
        {"type": "object"},
        [],
    )
    assert provider.calls == 1
    assert (
        store.db.execute("SELECT count(*) FROM extraction_runs").fetchone()[0] == initial_runs + 2
    )
    assert Budgets().content_tokens == 120000
    assert parser().parse_args(["extract", "--session", "1"]).input_budget == 128000


def test_model_catalog_limits_include_output_and_cache_metadata():
    calls = []

    def handle(request):
        calls.append(request.method)
        assert request.url.path == "/models"
        assert not request.content
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "deepseek-flash",
                        "context_window": 1048576,
                        "max_output_tokens": 393216,
                    }
                ]
            },
        )

    provider = Provider(
        "https://api.deepseek.com", "deepseek-flash", "fake", httpx.MockTransport(handle)
    )
    try:
        provider.validate_budget(128000, 4000)
        provider.validate_budget(1000000, 4000)
        with pytest.raises(ValueError, match="超过模型窗口"):
            provider.validate_budget(1048576, 4000)
        assert calls == ["GET"]
    finally:
        provider.close()


def test_private_project_does_not_even_fetch_model_catalog(store: Store, tmp_path: Path):
    setup(store, tmp_path)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(500)

    provider = Provider(
        "https://api.deepseek.com", "deepseek-flash", "fake", httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(PermissionError):
            Worker(store, provider).process(1)
        assert not requests
    finally:
        provider.close()


@pytest.mark.parametrize("catalog", [None, [], {}, {"data": [{"id": "deepseek-flash"}]}])
def test_missing_model_window_fails_before_generation(catalog):
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json=catalog)

    provider = Provider(
        "https://api.deepseek.com", "deepseek-flash", "fake", httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(RuntimeError, match="窗口"):
            provider.validate_budget(128000, 4000)
        assert calls == ["GET"]
    finally:
        provider.close()


def test_full_model_content_is_redacted_before_generation(store: Store, tmp_path: Path):
    project, _, _ = setup(store, tmp_path)

    class Recording(FakeProvider):
        def generate(self, request):
            assert "sk-syntheticprivate123" not in json.dumps(request)
            return ModelResult('{"candidates":[]}', 10, 10, "stop")

    Worker(store, Recording(count=100)).invoke(
        "pass1",
        Segment("redacted", [{"event_id": 1}]),
        project,
        '{"scope":{"key":"sk-syntheticprivate123"}}',
        {"type": "object"},
        [],
    )
