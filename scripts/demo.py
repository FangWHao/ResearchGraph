#!/usr/bin/env python3
"""生成一个合成演示项目，不调用模型、不读真实会话，用于开发和查看界面。

uv run python scripts/demo.py --data-dir /tmp/rg-demo
uv run rg --data-dir /tmp/rg-demo start --port 8790
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from rg.config import DB_NAME, data_dir
from rg.store import Store

START = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)  # 北京时间 9 月 21 日 9:00

# 研究线：(标题, 说明, 起始天, 分支列表)。分支：(标题, 会话, 步骤, 分出点, 汇入点)。
# 步骤：(种类, 标题, 摘要)。分出点和汇入点写成 "线号.分支号.步骤号"。
STORY: list[dict[str, Any]] = [
    {
        "title": "数据质控",
        "summary": "过滤低质量细胞和双细胞，确定质控阈值",
        "day": 0,
        "branches": [
            {
                "title": "",
                "session": 1,
                "steps": [
                    (
                        "question",
                        "批次之间差异有多大",
                        "八个样本来自两个批次，先看测序深度和线粒体比例的分布",
                    ),
                    ("attempt", "按样本过滤低质细胞", "按每个样本的基因数和 UMI 中位数设置上下限"),
                    ("finding", "两个批次线粒体偏高", "B 批次线粒体比例中位数 11%，A 批次 6%"),
                    (
                        "attempt",
                        "比较 10%/15%/20% 阈值",
                        "三个阈值下保留的细胞数分别为 7.1 万、8.4 万、8.9 万",
                    ),
                    (
                        "decision",
                        "线粒体阈值定为 15%",
                        "15% 保留了大部分上皮细胞，同时去掉明显的破损细胞",
                    ),
                    ("output", "质控报告 v1", "生成 qc_report_v1.html，包含每个样本的过滤前后对比"),
                ],
            },
            {
                "title": "双细胞检测",
                "session": 1,
                "from": "0.0.1",
                "into": "0.0.4",
                "steps": [
                    ("attempt", "试 scDblFinder", "每个样本单独跑，期望双细胞率按上样量估计"),
                    ("finding", "双细胞约 6%", "集中在两个大样本，主要是 T 细胞和髓系细胞混合"),
                ],
            },
        ],
    },
    {
        "title": "去批次方法比较",
        "summary": "比较 Harmony、scVI 和 BBKNN，最终采用 Harmony",
        "day": 3,
        "branches": [
            {
                "title": "",
                "session": 2,
                "steps": [
                    (
                        "question",
                        "哪种方法保住生物差异",
                        "既要混合批次，又不能把肿瘤和正常上皮混在一起",
                    ),
                    ("attempt", "Harmony 默认参数", "theta=2，在前 30 个主成分上运行"),
                    ("finding", "批次混合分数 0.82", "kBET 接受率 0.82，上皮亚群仍然分开"),
                    (
                        "attempt",
                        "调 theta=1/3/4",
                        "theta 越大混合越好，但 theta=4 时肿瘤亚群开始合并",
                    ),
                    ("finding", "theta=2 最平衡", "theta=2 在混合分数和亚群分离度之间最好"),
                    ("decision", "采用 Harmony theta=2", "后续分析都基于 Harmony theta=2 的结果"),
                ],
            },
            {
                "title": "scVI 对照",
                "session": 3,
                "from": "1.0.1",
                "into": "1.0.5",
                "steps": [
                    ("attempt", "scVI 训练 200 轮", "两层编码器，潜空间 30 维"),
                    (
                        "finding",
                        "分数 0.79，耗时三倍",
                        "效果和 Harmony 接近，但 GPU 上也要 40 分钟",
                    ),
                    ("attempt", "scVI 加批次协变量", "把测序深度作为连续协变量"),
                    ("finding", "提升不明显", "混合分数 0.80，不值得额外复杂度"),
                ],
            },
            {
                "title": "BBKNN",
                "session": 2,
                "from": "1.0.0",
                "steps": [
                    ("attempt", "试 BBKNN", "每个批次取 3 个近邻"),
                    ("decision", "放弃 BBKNN", "图结构不适合后面的轨迹分析"),
                ],
            },
        ],
    },
    {
        "title": "细胞注释",
        "summary": "参考图谱自动注释，T 细胞单独细分",
        "day": 7,
        "from": "1.0.5",
        "branches": [
            {
                "title": "",
                "session": 4,
                "steps": [
                    ("attempt", "参考图谱自动注释", "用 atlas_v2 做标签迁移，得到 14 个大类"),
                    ("finding", "T 细胞亚群分不开", "CD8 效应和耗竭样细胞混在一起"),
                    ("attempt", "T 细胞单独再聚类", "只取 T 细胞重新找高变基因，分辨率 0.8"),
                    ("finding", "找到耗竭样亚群", "PDCD1、HAVCR2、TOX 共同高表达，占 CD8 的 18%"),
                    ("attempt", "髓系细胞再聚类", "巨噬细胞分成 SPP1+ 和 C1QC+ 两群"),
                    ("decision", "按标志基因手动命名", "自动标签只作参考，最终名称按标志基因确定"),
                    ("output", "注释表 v2", "cell_annotation_v2.csv，共 23 个亚群"),
                ],
            },
            {
                "title": "换参考图谱",
                "session": 5,
                "from": "2.0.1",
                "into": "2.0.5",
                "steps": [
                    ("attempt", "改用 atlas_v3", "新版图谱多了肿瘤相关成纤维细胞的细分"),
                    ("finding", "一致性提高到 0.90", "和手工标注的一致性从 0.81 提高到 0.90"),
                ],
            },
        ],
    },
    {
        "title": "生存分析",
        "summary": "耗竭样 T 细胞比例与预后",
        "day": 11,
        "from": "2.0.3",
        "branches": [
            {
                "title": "",
                "session": 6,
                "steps": [
                    (
                        "question",
                        "亚群比例和预后有关吗",
                        "用公开队列的 bulk 数据做反卷积，估计各亚群比例",
                    ),
                    ("attempt", "反卷积估计比例", "CIBERSORTx，签名矩阵来自本项目的注释表 v2"),
                    ("attempt", "Cox 回归", "以耗竭样 T 细胞比例的中位数分组"),
                    ("finding", "HR 1.8，p=0.03", "高比例组总生存更差"),
                    ("question", "是否被分期混杂", "晚期患者里耗竭样比例本来就更高"),
                    ("attempt", "按分期分层", "分层 Cox 模型，同时校正年龄和性别"),
                    ("finding", "效应减弱仍显著", "HR 1.5，p=0.04"),
                    ("decision", "主文用分层模型", "未分层的结果放补充材料"),
                    ("output", "图 4 生存曲线", "fig4_survival.pdf，含风险表"),
                ],
            },
            {
                "title": "换队列验证",
                "session": 7,
                "from": "3.0.3",
                "into": "3.0.7",
                "steps": [
                    ("attempt", "第二个队列复现", "另一个公开队列，样本量 180"),
                    ("finding", "方向一致", "HR 1.6，但 p=0.08，样本量偏小"),
                ],
            },
        ],
    },
    {
        "title": "论文图",
        "summary": "统一配色和排版，完成主图和补充图",
        "day": 14,
        "branches": [
            {
                "title": "",
                "session": 8,
                "steps": [
                    ("attempt", "统一亚群配色", "23 个亚群固定颜色，所有图共用"),
                    ("output", "图 1 研究设计", "流程示意和样本信息"),
                    ("output", "图 2 细胞图谱", "UMAP 和亚群比例"),
                    ("decision", "图 3 拆成两张", "T 细胞和髓系分开，信息太多放不下"),
                    ("output", "图 3a / 3b", "T 细胞耗竭轨迹和髓系亚群"),
                    ("output", "补充图 S1–S4", "质控、去批次比较、注释一致性、验证队列"),
                ],
            },
        ],
    },
]

TOPICS = ["配体受体", "转录因子", "拷贝数", "通路富集", "空间邻域", "轨迹", "克隆型", "代谢"]
KINDS = ["attempt", "finding", "attempt", "finding", "decision", "attempt", "question"]


def exploration(rng: random.Random) -> dict[str, Any]:
    """一条很密的探索性研究线：主线加四个分支，模拟真实项目的事件数量。"""
    branches: list[dict[str, Any]] = [{"title": "", "session": 9, "steps": []}]
    for i in range(40):
        kind = KINDS[i % len(KINDS)]
        topic = TOPICS[i % len(TOPICS)]
        branches[0]["steps"].append(
            (
                kind,
                f"{topic}分析 第{i // len(TOPICS) + 1}轮",
                f"{topic}方向的第 {i + 1} 步，记录参数和结果",
            )
        )
    for b in range(6):
        start = 2 + b * 6
        branches.append(
            {
                "title": f"{TOPICS[b]}细查",
                "session": 10 + b % 2,
                "from": f"5.0.{start}",
                "into": f"5.0.{start + 5}" if b % 2 == 0 else None,
                "steps": [
                    (
                        KINDS[(b + k) % len(KINDS)],
                        f"{TOPICS[b]}子问题 {k + 1}",
                        "换一种做法再看一次",
                    )
                    for k in range(rng.randint(5, 9))
                ],
            }
        )
    return {"title": "探索性分析", "summary": "多个方向的快速尝试", "day": 4, "branches": branches}


SESSIONS = {
    1: ("claude", "质控和双细胞"),
    2: ("codex", "去批次方法比较"),
    3: ("claude", "scVI 对照实验"),
    4: ("claude", "细胞注释"),
    5: ("claude", "换参考图谱试试"),
    6: ("codex", "生存分析"),
    7: ("codex", "验证队列"),
    8: ("claude", "论文图排版"),
    9: ("codex", "探索性分析"),
    10: ("claude", "细查一"),
    11: ("codex", "细查二"),
}
FORKS = {5: 4}  # 会话 5 从会话 4 fork 出来


def build(store: Store, name: str) -> int:
    rng = random.Random(7)
    story = [*STORY, exploration(rng)]
    with store.tx() as db:
        project_id = db.execute(
            "INSERT INTO projects(name, workspace, follow, model_allowed, created_at, organized_at) "
            "VALUES (?, '/workspace/demo-atlas', 0, 1, ?, ?)",
            (name, START.isoformat(), datetime.now(UTC).isoformat()),
        ).lastrowid
        assert project_id is not None
        session_ids: dict[int, int] = {}
        messages: dict[int, list[tuple[str, str, str]]] = {}
        for key, (tool, title) in SESSIONS.items():
            session_ids[key] = int(
                db.execute(
                    "INSERT INTO sessions(project_id, tool, path, title, cwd) VALUES (?, ?, ?, ?, ?)",
                    (
                        project_id,
                        tool,
                        f"/workspace/demo-atlas/.sessions/{key}.jsonl",
                        title,
                        "/workspace/demo-atlas",
                    ),
                ).lastrowid
                or 0
            )
            messages[key] = []
        ids: dict[str, int] = {}
        pending_links: list[tuple[int, str | None, str | None]] = []
        for t_index, thread in enumerate(story):
            thread_id = db.execute(
                "INSERT INTO threads(project_id, title, summary) VALUES (?, ?, ?)",
                (project_id, thread["title"], thread["summary"]),
            ).lastrowid
            clock = START + timedelta(days=thread["day"])
            for b_index, branch in enumerate(thread["branches"]):
                branch_id = int(
                    db.execute(
                        "INSERT INTO branches(thread_id, title, position) VALUES (?, ?, ?)",
                        (thread_id, branch["title"], b_index),
                    ).lastrowid
                    or 0
                )
                source = branch.get("from") or (thread.get("from") if b_index == 0 else None)
                pending_links.append((branch_id, source, branch.get("into")))
                if source and source in ids:
                    # 分支从分出点开始计时，和主线并行
                    origin = db.execute(
                        "SELECT ts FROM events WHERE id = ?", (ids[source],)
                    ).fetchone()[0]
                    clock = datetime.fromisoformat(origin) + timedelta(minutes=20)
                session = branch["session"]
                for s_index, (kind, title, summary) in enumerate(branch["steps"]):
                    step = [25, 40, 70, 110, 180, 300] if b_index == 0 else [15, 20, 30, 45]
                    clock += timedelta(minutes=rng.choice(step))
                    if clock.hour >= 15:  # 北京时间 23 点后收工，第二天 9 点继续
                        clock = (clock + timedelta(days=1)).replace(
                            hour=1, minute=rng.randint(0, 50)
                        )
                    log = messages[session]
                    first = len(log)
                    log.append(("user", f"{title}。{summary.split('，')[0]}。", clock.isoformat()))
                    log.append(
                        (
                            "tool",
                            f"运行 python analysis/{t_index}_{s_index}.py → 完成",
                            clock.isoformat(),
                        )
                    )
                    log.append(("assistant", summary + "。", clock.isoformat()))
                    event_id = int(
                        db.execute(
                            "INSERT INTO events(project_id, session_id, branch_id, kind, title, summary, "
                            "ts, anchor, refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                project_id,
                                session_ids[session],
                                branch_id,
                                kind,
                                title,
                                summary,
                                clock.isoformat(),
                                first,
                                json.dumps([first + 2, first]),
                            ),
                        ).lastrowid
                        or 0
                    )
                    ids[f"{t_index}.{b_index}.{s_index}"] = event_id
        for branch_id, source, target in pending_links:
            if target and target in ids:
                # 汇入点必须晚于分支的最后一步，否则不连
                last = db.execute(
                    "SELECT MAX(ts) FROM events WHERE branch_id = ?", (branch_id,)
                ).fetchone()[0]
                into_ts = db.execute(
                    "SELECT ts FROM events WHERE id = ?", (ids[target],)
                ).fetchone()[0]
                target = target if into_ts > last else None
            db.execute(
                "UPDATE branches SET from_event = ?, into_event = ? WHERE id = ?",
                (ids.get(source or ""), ids.get(target or ""), branch_id),
            )
        for key, log in messages.items():
            sid = session_ids[key]
            db.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                [(sid, i, role, ts, text) for i, (role, text, ts) in enumerate(log)],
            )
            times = [ts for _, _, ts in log]
            fork_of = session_ids.get(FORKS.get(key, 0))
            # fork 的分出点：原会话里“换参考图谱”分支起点之后的第一条消息
            copied = 0
            if fork_of:
                anchor = db.execute(
                    "SELECT anchor FROM events WHERE id = ?", (ids["2.0.1"],)
                ).fetchone()[0]
                copied = anchor + 1
            db.execute(
                "UPDATE sessions SET started_at = ?, ended_at = ?, message_count = ?, "
                "extracted_upto = ?, parsed_size = 0, fork_of = ?, copied = ? WHERE id = ?",
                (
                    min(times, default=None),
                    max(times, default=None),
                    len(log),
                    len(log),
                    fork_of,
                    copied,
                    sid,
                ),
            )
    return int(project_id)


def usage(store: Store, project_id: int) -> None:
    """合成的模型用量：近三周断断续续地整理，给设置页的“用量”看。"""
    rng = random.Random(11)
    now = datetime.now(UTC)
    rows = []
    for days_ago in range(21):
        if rng.random() < 0.3:
            continue
        for _ in range(rng.randint(1, 10)):
            at = now - timedelta(days=days_ago, minutes=rng.randint(0, 600))
            if days_ago == 0:
                at = now - timedelta(minutes=rng.randint(1, 120))
            stage = "threads" if rng.random() < 0.2 else "extract"
            cached = rng.random() < 0.1
            prompt = (
                rng.randint(8_000, 30_000) if stage == "threads" else rng.randint(15_000, 60_000)
            )
            hit = rng.randint(0, prompt // 3)
            rows.append(
                (
                    at.isoformat(timespec="seconds"),
                    project_id,
                    stage,
                    "deepseek-flash",
                    0 if cached else prompt - hit,
                    0 if cached else rng.randint(300, 6_000),
                    0 if cached else hit,
                    1 if cached else 0,
                )
            )
    with store.tx() as db:
        db.executemany(
            "INSERT INTO usage(at, project_id, stage, model, input_tokens, output_tokens, "
            "cache_read_tokens, cached) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            sorted(rows),
        )


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", required=True)
    cli.add_argument("--name", default="演示：肿瘤单细胞图谱")
    args = cli.parse_args()
    root = data_dir(args.data_dir)
    store = Store(root / DB_NAME)
    if not store.setting("sources"):
        store.set_setting("sources", [str(root)])  # 跳过首次引导页
    project_id = build(store, args.name)
    usage(store, project_id)
    count = store.one("SELECT COUNT(*) AS n FROM events WHERE project_id = ?", (project_id,))
    print(f"项目 {project_id}：{count['n'] if count else 0} 个事件")


if __name__ == "__main__":
    main()
