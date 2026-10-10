from pathlib import Path

from rg.extract.validate import persist
from rg.extract.working_set import working_set
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_extraction import new_run, setup


def test_decision_in_other_scope_does_not_change_this_scope(store: Store, tmp_path: Path):
    project, output, run = setup(store, tmp_path)
    ids = persist(store, output, project, run, set(), {1}, "test-segment")
    store.review(ids[1], "confirm", "human:tester", store.revision())
    current = working_set(store, project, "方法甲", FakeProvider())[0]
    other = output["claims"][1]
    other["target"] = current["id"]
    other["action"] = "rejected"
    other["scope"] = {"dataset_version": "different_data"}
    output["claims"] = [other]
    run = new_run(store, output, "other-scope")
    new = persist(store, output, project, run, {current["id"]}, {1}, "test-segment")
    store.review(new[0], "confirm", "human:tester", store.revision())
    assert working_set(store, project, "方法甲", FakeProvider())[0]["state"] == "accepted"
