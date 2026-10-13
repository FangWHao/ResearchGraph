from __future__ import annotations

import json

import pytest

from rg.parse import NotASession, parse_file, parse_lines
from tests.conftest import FIXTURES


def texts(session):
    return [(m.role, m.text) for m in session.messages]


def test_claude_keeps_research_text_and_drops_injected_content():
    session = parse_file(FIXTURES / "claude" / "research-synthetic.jsonl")
    joined = "\n".join(m.text for m in session.messages)
    assert session.tool == "claude"
    assert session.cwd == "/workspace/demo"
    assert session.messages[0].text == "想比较两种去批次方法对细胞注释的影响，数据用 demo_v1。"
    # 注入块、isMeta、子 Agent 侧链、压缩回放、思考都不能当成会话内容。
    for absent in ("注入内容", "内部命令", "压缩回放", "内部思考", "读到的文件内容"):
        assert absent not in joined
    assert joined.count("Harmony 之后批次混合分数 0.82。") == 1
    assert ("tool", "运行 python qc.py --input demo_v1 → kept 9500/10000 cells") in texts(session)
    assert ("tool", "编辑 /workspace/demo/batch.py → ok") in texts(session)


def test_claude_title_and_unknown_records_are_counted():
    session = parse_file(FIXTURES / "claude" / "research-synthetic.jsonl")
    assert session.title == "批次与注释"  # 用户自定义标题优先于自动标题
    assert session.unknown["claude:brand-new-record"] == 1
    assert session.unknown["bad_line"] == 1  # 正在写入的半行
    assert session.started_at == "2026-10-01T01:00:00Z"
    assert session.ended_at == "2026-10-01T01:12:00Z"


def test_codex_drops_mirrors_instructions_and_environment():
    session = parse_file(FIXTURES / "codex" / "research-synthetic.jsonl")
    assert session.tool == "codex"
    assert texts(session) == [
        ("user", "用 Harmony 的结果做细胞注释。"),
        ("tool", "运行 python annotate.py --ref atlas_v2 → annotated 12 cell types"),
        ("tool", "编辑 annotate.py → Success"),
        ("assistant", "注释完成，T 细胞亚群分不开。"),
        ("user", "T 细胞单独再聚类。"),
    ]
    assert session.unknown["codex:mystery_record"] == 1
    assert not session.subagent


def test_codex_legacy_event_messages_are_kept_when_no_response_items():
    session = parse_file(FIXTURES / "codex" / "legacy-synthetic.jsonl")
    assert texts(session) == [("user", "旧版本只有 event_msg。"), ("assistant", "收到。")]
    assert session.ended_at == "2026-10-02T02:02:00Z"


def test_codex_subagent_is_flagged():
    assert parse_file(FIXTURES / "codex" / "subagent-synthetic.jsonl").subagent


def test_non_session_file_is_rejected():
    with pytest.raises(NotASession):
        parse_lines([json.dumps({"hello": i}) for i in range(30)])
    with pytest.raises(NotASession):
        parse_lines([])


def test_long_message_is_clipped_not_dropped():
    record = {
        "type": "user",
        "sessionId": "s",
        "message": {"content": "开头" + "x" * 50_000 + "结尾"},
    }
    session = parse_lines([json.dumps(record)])
    text = session.messages[0].text
    assert text.startswith("开头") and text.endswith("结尾") and len(text) < 21_000
