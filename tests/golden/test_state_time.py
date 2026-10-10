"""状态时间不能降精度、借入库时间补齐或将未知当作科学结论。"""

import threading
from datetime import UTC, datetime

import httpx
import pytest

from rg.api import views
from rg.api.server import LocalServer
from rg.query.reader import Reader, instant
from tests.state_time_browser import CASES, seed


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_canonical_cards_and_exact_states_preserve_originals(store, case):
    project, identities = seed(store)
    key, _, _, scope, adoption, state = case
    before = list(store.db.iterdump())
    revision = store.revision()
    reader = Reader(store, project, {})
    assert reader.states(identities[key]["finding"], scope)["adoption"]["state"] == adoption
    assert reader.states(identities[key]["finding"], scope)["evidence_state"]["state"] == state
    legacy = {c["claim_id"]: c for c in views.graph(store, project)["claims"]}
    for row in reader.claims():
        card = reader.card(row)
        for field in ("occurred_at", "recorded_at"):
            raw = row[field]
            parsed = instant(raw)
            expected = parsed.isoformat(timespec="microseconds") if parsed else None
            assert card[field + "_utc"] == legacy[row["claim_id"]][field + "_utc"] == expected
            assert card[field] == legacy[row["claim_id"]][field] == raw
    assert list(store.db.iterdump()) == before
    assert store.revision() == revision
    assert store.db.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 0


def test_microsecond_dual_cutoff_changes_only_visible_events(store):
    project, identities = seed(store)
    entity = identities["micro"]["finding"]
    early = Reader(store, project, {"occurred_until": "2026-01-02T00:00:00.000001Z"})
    late = Reader(store, project, {"occurred_until": "2026-01-02T00:00:00.000002Z"})
    assert early.states(entity, CASES[0][3])["adoption"]["state"] == "withdrawn"
    assert early.states(entity, CASES[0][3])["evidence_state"]["state"] == "time_unknown"
    # 未被较晚替换记录遮蔽的未知原记录不能偷偷借用入库时间。
    assert late.states(entity, CASES[0][3])["adoption"]["state"] == "accepted"
    assert late.states(entity, CASES[0][3])["evidence_state"]["state"] == "refuted"
    unknown = Reader(store, project, {"known_until": "2025-12-31T23:59:59Z"})
    assert unknown.states(entity, CASES[0][3])["adoption"]["state"] == "unknown"


@pytest.mark.parametrize(
    "raw",
    [
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59-14:00",
        None,
        "2026-02-30T00:00:00Z",
        "2026-01-02T00:00:00",
    ],
)
def test_unusable_time_is_unknown_instead_of_server_error(raw):
    assert instant(raw) is None


def test_python_supported_iso_forms_keep_microseconds():
    expected = datetime(2026, 1, 2, 0, 0, 0, 123456, tzinfo=UTC)
    for raw in (
        "20260102T000000.123456Z",
        "2026-W01-5T00:00:00.123456Z",
        "2026-01-02T00:00:00,123456Z",
        "2026-01-02T00:00:00.123456789Z",
    ):
        assert instant(raw) == expected


def test_real_http_canonical_claims_match_semantic_states_and_preserve_store(store, tmp_path):
    project, identities = seed(store)
    (tmp_path / "index.html").write_text("合成界面", encoding="utf-8")
    server = LocalServer(store.root, tmp_path, port=0, token="synthetic-state-time-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before, revision = list(store.db.iterdump()), store.revision()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            params = {"project": project}
            assert client.get("/api/graph", params=params).status_code == 401
            client.headers["Authorization"] = "Bearer " + server.token
            response = client.get("/api/graph", params=params)
            assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
            legacy = {c["claim_id"]: c for c in response.json()["claims"]}
            nodes = client.get("/api/semantic-graph", params=params | {"limit": "100"})
            assert nodes.status_code == 200
            mapped = {node["entity_id"]: node for node in nodes.json()["items"]}
            for key, _, _, _, adoption, state in CASES:
                node = mapped[identities[key]["finding"]]
                assert node["states"]["adoption"]["state"] == adoption
                assert node["states"]["evidence_state"]["state"] == state
                for version in node["versions"]:
                    for field in (
                        "occurred_at",
                        "recorded_at",
                        "occurred_at_utc",
                        "recorded_at_utc",
                    ):
                        assert version[field] == legacy[version["claim_id"]][field]
            target = next(c for c in legacy.values() if c["claim_type"] == "evidence_event")
            detail = client.get("/api/claims/" + str(target["claim_id"]))
            assert detail.status_code == 200
            assert detail.json()["claim"]["occurred_at_utc"] == target["occurred_at_utc"]
            span = target["evidence"][0]
            source = client.get(
                "/api/evidence/" + str(span["event_id"]),
                params={
                    "start": span["byte_start"],
                    "end": span["byte_end"],
                },
            )
            assert source.status_code == 200
            assert source.json()["event"]["quote_sha256"] == span["quote_sha256"]
            assert list(store.db.iterdump()) == before and store.revision() == revision
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
