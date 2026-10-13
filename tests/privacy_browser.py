"""项目遮盖配置的独立合成库；编号和文本全部为固定虚构数据。"""

from uuid import uuid4

from rg.store.database import Store
from tests.golden.test_read_tools import T1, append
from tests.graph_browser import evidence


def seed_privacy(store: Store) -> None:
    for suffix in ("", "二"):
        project = store.project("验收遮盖项目" + suffix, [])
        entity = str(uuid4())
        store.db.execute(
            "INSERT INTO entities VALUES (?,?,?,NULL,?)", (entity, project, "finding", T1)
        )
        text = (
            "遮盖问答合成资料：HOSP-246810，CASE-135790，SAMPLE-ABC123。"
            "临床编号完全虚构，仅用于程序验收。privacy-fixture@example.invalid。"
            "<script>window.privacyInjected=true</script>"
        )
        claim = append(
            store,
            entity,
            "entity_version",
            {
                "claim_type": "entity_version",
                "temp_id": entity,
                "kind": "finding",
                "label": "遮盖问答发现" + suffix,
                "content": text,
            },
            scope={"data": "synthetic_privacy_v1", "step": "遮盖验收"},
            state="confirmed",
        )
        evidence(store, project, claim, text)
