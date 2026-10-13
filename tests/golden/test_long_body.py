from pathlib import Path

from rg.extract.segmenter import segment
from rg.ingest.scanner import scan_file
from rg.slim.slimmer import slim_session
from rg.store.database import Store
from tests.conftest import FakeProvider
from tests.golden.test_ingestion import lines, record


def test_single_500kb_body_is_split_without_dropping_tail(store: Store, tmp_path: Path):
    path = tmp_path / "long-body.jsonl"
    content = "正文" * 85000 + "最后撤回方法甲"
    lines(path, [record(content)])
    scan_file(store, path, "claude")
    events = slim_session(store, 1, FakeProvider())
    items = segment(events, FakeProvider())
    pieces = [event for item in items for event in item.events]
    assert "".join(x["text"] for x in pieces) == content
    assert all(FakeProvider().count_text(x["raw_text"]) <= 2000 for x in pieces)
    assert all(a["raw_end"] == b["raw_start"] for a, b in zip(pieces, pieces[1:], strict=False))
    assert "撤回方法甲" in pieces[-1]["text"]
