#!/usr/bin/env python3
"""从本地 Atlas 会话生成私有人工作业表；只输出统计数字，不将原文带入开发上下文。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rg.extract.redact import redact
from rg.extract.worker import CUES
from rg.ingest.common import parse
from rg.ingest.spans import event_span
from rg.store.database import dumps
from rg.store.objects import atomic_write, digest


def prepare(manifest: Path, output: Path) -> dict[str, int]:
    if output.exists():
        raise ValueError("输出目录已存在，不能覆盖人工标注；请指定新目录")
    repo = Path(__file__).resolve().parents[1]
    if output.resolve().is_relative_to(repo):
        raise ValueError("真实会话作业表必须在代码仓库之外")
    selected = []
    sources = sorted(json.loads(manifest.read_text()), key=lambda x: x["bytes"])
    for source in sources:
        path = Path(source["path"])
        if not path.exists():
            continue
        candidates = []
        position = 0
        with path.open("rb") as stream:
            for raw in stream:
                offset = position
                position += len(raw)
                try:
                    record = json.loads(raw)
                    events = parse(source["tool"], record)
                except (ValueError, UnicodeError, TypeError, AttributeError):
                    continue
                for index, event in enumerate(events):
                    if event.kind != "user_msg" or event.excluded or not CUES.search(event.text):
                        continue
                    bounds = event_span(raw, source["tool"], index)
                    if not bounds:
                        continue
                    masked = redact(event.text.encode()).data.decode()
                    # 提供有限预览；完整原文仍由原始文件与行位置定位，预览不作模型输入。
                    candidates.append(
                        {
                            "source_path": str(path),
                            "tool": source["tool"],
                            "line_byte_start": offset,
                            "line_byte_end": position,
                            "record_index": index,
                            "line_sha256": digest(raw),
                            "text_byte_start": bounds[0],
                            "text_byte_end": bounds[1],
                            "preview": masked[:800],
                            "claim_state": "candidate",
                            "human_reference": False,
                            "action": None,
                            "scope": {},
                            "target": None,
                            "reason": None,
                        }
                    )
                if len(candidates) >= 5:
                    break
        if len(candidates) >= 3:
            selected.append(candidates[:5])
        if len(selected) == 10:
            break
    output.mkdir(parents=True)
    flat = [x for group in selected for x in group]
    atomic_write(output / "references.json", dumps(flat).encode())
    lines = [
        "# Atlas 试点人工参考作业表",
        "",
        "以下均为规则定位的候选，尚未成为人工参考记录。预览可能不完整，必须回到原文核对。",
        "",
        "请填写 target、action、scope、reason，并将核对后的 human_reference 改为 true。",
        "",
        "人工无法判断时保留未确认，不可据此计算找回率。",
        "",
    ]
    for index, candidate in enumerate(flat, 1):
        lines.extend(
            [
                f"## 候选 {index}",
                "",
                f"来源工具：{candidate['tool']}；文件：`{candidate['source_path']}`。",
                "",
                f"行字节位置：{candidate['line_byte_start']}–{candidate['line_byte_end']}。",
                "",
                *["> " + line for line in candidate["preview"].splitlines()],
                "",
                "- 人工目标对象：未填写",
                "- 采用状态：未填写",
                "- 适用范围：未填写",
                "- 理由：未填写",
                "- 原材料无法判断：待核对",
                "",
            ]
        )
    atomic_write(output / "人工参考作业表.md", "\n".join(lines).encode())
    questions = [
        "# Atlas 试点问题草案",
        "",
        "以下问题待用户确认，不计入正式验收问题。",
        "",
        "1. 当时为什么放弃或暂缓某个分析方案，它后来是否再次采用？",
        "2. 哪次代码回退没有改变当时采用的方法？",
        "3. 哪个阴性结果在方案暂停后仍被保留，适用范围是什么？",
        "4. 哪项研究决定仅在特定数据版本或队列条件下成立？",
        "5. 哪个产物对应的运行没有明确结束记录，当时有哪些可查证证据？",
        "",
    ]
    atomic_write(output / "试点问题草案.md", "\n".join(questions).encode())
    return {
        "selected_sessions": len(selected),
        "candidate_references": len(flat),
        "human_confirmed_references": 0,
        "confirmed_questions": 0,
    }


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--manifest", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(prepare(args.manifest, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
