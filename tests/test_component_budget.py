from pathlib import Path

import pytest

from rg.extract.segmenter import Segment
from rg.extract.worker import Worker
from rg.slim.tokens import BudgetExceeded
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_extraction import setup


class LargeHeaderProvider(FakeProvider):
    def count_request(self, request):
        return 3001 if request["input"] == "" else 100


def test_schema_and_instructions_budget_checked_before_generation(store: Store, tmp_path: Path):
    project, _, _ = setup(store, tmp_path)
    provider = LargeHeaderProvider()
    with pytest.raises(BudgetExceeded):
        Worker(store, provider).invoke(
            "pass1", Segment("header", [{"event_id": 1}]), project, "short", {"type": "object"}, []
        )
    assert provider.calls == 0
    assert (
        store.db.execute("SELECT status FROM extraction_runs WHERE stage='pass1'").fetchone()[0]
        == "over_budget"
    )


def test_segment_budget_is_independent_of_full_request_limit(store: Store, tmp_path: Path):
    project, _, _ = setup(store, tmp_path)
    provider = FakeProvider(count=100)
    with pytest.raises(BudgetExceeded):
        Worker(store, provider).invoke(
            "pass1",
            Segment("segment", [{"event_id": 1}]),
            project,
            "x" * 120001,
            {"type": "object"},
            [],
        )
    assert provider.calls == 0
