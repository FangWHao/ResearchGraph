"""精确时间与未知状态验收的独立合成库；不读取真实会话或调用模型。"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from uuid import uuid4

from rg.api.server import LocalServer
from rg.store.database import Store
from tests.golden.test_read_tools import SCOPE, T1, append
from tests.graph_browser import evidence

CASES = (
    (
        "micro",
        "精确微秒",
        ("2026-01-02T00:00:00.000002Z", "2026-01-02T00:00:00.000001Z"),
        SCOPE,
        "accepted",
        "refuted",
    ),
    (
        "tie",
        "同刻时区冲突",
        ("2026-01-02T00:00:00.123456Z", "2026-01-02T08:00:00.123456+08:00"),
        SCOPE,
        "conflict",
        "conflict",
    ),
    ("missing", "缺失时间", (None,), SCOPE, "time_unknown", "time_unknown"),
    ("naive", "没有时区", ("2026-01-02T00:00:00",), SCOPE, "time_unknown", "time_unknown"),
    ("invalid", "非法日期", ("2026-02-30T00:00:00Z",), SCOPE, "time_unknown", "time_unknown"),
    ("scope", "未知范围", (T1,), None, "unknown_scope", "needs_review"),
    ("basic", "紧凑日期", ("20260102T000000.123456Z",), SCOPE, "accepted", "refuted"),
    ("week", "周日期", ("2026-W01-5T00:00:00.123456Z",), SCOPE, "accepted", "refuted"),
    ("comma", "逗号小数", ("2026-01-02T00:00:00,123456Z",), SCOPE, "accepted", "refuted"),
    ("offset", "秒级偏移", ("2026-01-02T00:00:00.123456+00:00:30",), SCOPE, "accepted", "refuted"),
    ("overflow", "UTC越界", ("0001-01-01T00:00:00+14:00",), SCOPE, "time_unknown", "time_unknown"),
)


def seed(store: Store) -> tuple[str, dict[str, dict[str, str]]]:
    project = store.project("验收精确状态项目", [])
    identities: dict[str, dict[str, str]] = {}

    def claim(entity, kind, payload, *, scope=SCOPE, occurred=T1, state="confirmed", replaces=None):
        identity = append(
            store,
            entity,
            kind,
            {"claim_type": kind} | payload,
            scope=scope,
            occurred=occurred,
            state=state,
            replaces=replaces,
        )
        evidence(store, project, identity, "精确状态合成来源：" + str(payload))
        return identity

    def entity(kind, label, scope=SCOPE):
        identity = str(uuid4())
        store.db.execute(
            "INSERT INTO entities VALUES (?,?,?,NULL,?)", (identity, project, kind, T1)
        )
        claim(
            identity,
            "entity_version",
            {"temp_id": identity, "kind": kind, "label": label, "content": label},
            scope=scope,
        )
        return identity

    for key, label, times, scope, _, _ in CASES:
        finding = entity("finding", label + "发现", scope)
        question = entity("question", label + "问题", scope)
        approach = entity("approach", label + "方案", scope)
        identities[key] = {"finding": finding, "question": question, "approach": approach}
        claim(
            approach,
            "relation",
            {"source": approach, "target": question, "relation": "part_of"},
            scope=scope,
        )
        for index, occurred in enumerate(times):
            for target in (finding, approach):
                claim(
                    target,
                    "decision_event",
                    {
                        "target": target,
                        "action": "accepted" if index == 0 else "withdrawn",
                        "reason": "合成明确决定",
                    },
                    scope=scope,
                    occurred=occurred,
                )
            claim(
                finding,
                "evidence_event",
                {
                    "target": finding,
                    "state": "refuted" if index == 0 else "supported",
                    "reason": "合成证据记录",
                },
                scope=scope,
                occurred=occurred,
            )
    # 可折叠链只作用于视图，微秒发现位于链内部。
    first, third, last = [
        entity("finding", label) for label in ("精确状态链入口", "精确状态链中间", "精确状态链出口")
    ]
    middle = identities["micro"]["finding"]
    identities["chain"] = {"first": first, "middle": middle, "third": third, "last": last}
    for source, target in ((first, middle), (middle, third), (third, last)):
        claim(source, "relation", {"source": source, "target": target, "relation": "supports"})
    # 候选、驳回和被替换记录的缺失时间均不能污染当前事实。
    for state in ("candidate", "dismissed"):
        claim(
            middle,
            "evidence_event",
            {"target": middle, "state": "supported"},
            occurred=None,
            state=state,
        )
    replaced = claim(
        middle, "evidence_event", {"target": middle, "state": "supported"}, occurred=None
    )
    claim(
        middle,
        "evidence_event",
        {"target": middle, "state": "refuted"},
        occurred="2026-01-02T00:00:00.000002Z",
        replaces=replaced,
    )
    store.project("精确状态独立空项目", [])
    return project, identities


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9794)
    parser.add_argument("--web-dir", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchgraph-state-time-") as temp:
        root = Path(temp) / "store"
        store = Store(root)
        try:
            seed(store)
        finally:
            store.close()
        server = LocalServer(root, args.web_dir, args.port, token="synthetic-state-time-token")
        print("精确状态合成服务已启动；无真实资料、模型调用或原件修改。", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
