"""折叠验收的独立合成项目；不改变既有默认项目与真实资料。"""

from uuid import uuid4

from rg.store.database import Store, dumps
from rg.store.objects import digest
from tests.golden.test_read_tools import SCOPE, T1, T2, append


def evidence(store: Store, project: str, claim: int, text: str) -> None:
    session = store.db.execute(
        "INSERT INTO sessions(tool,native_session_id,project_id,project_basis) "
        "VALUES ('rg',?,?,'manual')",
        (str(uuid4()), project),
    ).lastrowid
    file = store.db.execute(
        "INSERT INTO source_files(session_pk,path,prefix_sha256,parser,parser_version,"
        "status,first_seen,last_read) VALUES (?,?,?,'synthetic_fixture','1','virtual',?,?)",
        (session, "synthetic-fixture://" + project, "a", T1, T1),
    ).lastrowid
    raw = dumps({"content": text}).encode()
    sha = store.objects.put(raw)
    event = store.db.execute(
        "INSERT INTO raw_events(session_pk,file_instance_id,byte_start,byte_end,object_sha256,"
        "seq,kind,role,occurred_at,recorded_at,line_sha256) "
        "VALUES (?,?,0,?,?,0,'user_text','user',?,?,?)",
        (session, file, len(raw), sha, T1, T1, sha),
    ).lastrowid
    encoded = dumps(text)[1:-1].encode()
    start = raw.index(encoded)
    span = store.db.execute(
        "INSERT INTO evidence_spans(event_id,byte_start,byte_end,quote_sha256) VALUES (?,?,?,?)",
        (event, start, start + len(encoded), digest(encoded)),
    ).lastrowid
    store.db.execute("INSERT INTO claim_evidence VALUES (?,?,'support')", (claim, span))


def seed_graph(store: Store) -> None:
    project = store.project("验收语义图项目", [])

    def claim(entity, kind, payload, *, scope=SCOPE, state="confirmed", occurred=T1):
        payload = {"claim_type": kind} | payload
        if kind == "entity_version":
            payload["temp_id"] = entity
        elif kind == "decision_event":
            payload |= {"speaker": "user", "explicitness": "explicit", "referent_unique": True}
        identity = append(store, entity, kind, payload, scope=scope, state=state, occurred=occurred)
        evidence(store, project, identity, "合成语义来源：" + str(payload))
        return identity

    def entity(kind, label):
        identity = str(uuid4())
        store.db.execute(
            "INSERT INTO entities VALUES (?,?,?,NULL,?)", (identity, project, kind, T1)
        )
        claim(identity, "entity_version", {"kind": kind, "label": label, "content": label})
        return identity

    a, b, c, d = [
        entity("finding", label)
        for label in ("原始合成观察", "需保留撤回的中间发现", "阴性实验记录", "合成后续结论")
    ]
    for source, target in ((a, b), (b, c), (c, d)):
        claim(source, "relation", {"source": source, "target": target, "relation": "supports"})
    claim(b, "decision_event", {"target": b, "action": "withdrawn", "reason": "明确撤回"})
    claim(c, "evidence_event", {"target": c, "state": "refuted", "reason": "合成阴性证据"})
    claim(
        c,
        "evidence_event",
        {"target": c, "state": "needs_review", "reason": "尚未审核"},
        state="candidate",
    )
    claim(
        b,
        "entity_version",
        {"kind": "finding", "label": "中间发现的另一内容版本", "content": "不同内容仍保留"},
        occurred=T2,
    )
    claim(
        b,
        "entity_version",
        {"kind": "finding", "label": "其它范围的发现", "content": "不同范围不可合并"},
        scope={"data": "synthetic_v2", "step": "测试"},
    )
    claim(a, "relation", {"source": a, "target": c, "relation": "same_topic"})
    first, second = entity("approach", "合成比较方案 B"), entity("approach", "合成比较方案 C")
    for semantics, label in (
        ("compare_then_select", "比较后选择 B"),
        ("all_required", "共同输入汇合"),
        ("evidence_synthesis", "综合两项证据"),
    ):
        target = entity("join", label)
        claim(
            target,
            "join_ports",
            {
                "target": target,
                "semantics": semantics,
                "inputs": [{"port": "方案B", "ref": first}, {"port": "方案C", "ref": second}],
                "selected": first if semantics == "compare_then_select" else None,
            },
        )
    missing = store.project("验收语义图缺口项目", [])
    identity = str(uuid4())
    store.db.execute(
        "INSERT INTO entities VALUES (?,?,?,NULL,?)", (identity, missing, "finding", T1)
    )
    created = append(
        store,
        identity,
        "entity_version",
        {"kind": "finding", "label": "缺口合成发现"},
        state="confirmed",
    )
    evidence(store, missing, created, "缺口项目的合成记录")
    dangling = append(
        store,
        identity,
        "relation",
        {"source": identity, "target": "synthetic-missing", "relation": "supports"},
        state="confirmed",
    )
    evidence(store, missing, dangling, "没有同范围端点的合成支持关系")
