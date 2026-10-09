#!/usr/bin/env python3
"""统计本机 Claude Code / Codex 会话的体量构成（只输出统计数字，不输出任何会话内容）。

用于 ResearchGraph 执行版 v2 的 §1 与 M0：确定瘦身层占比、每会话正文规模、压缩次数，
从而设定提取流水线的单次调用预算。

用法：
    python3 -I scripts/measure_transcripts.py
    python3 -I scripts/measure_transcripts.py --json
    # 可用 --claude 和 --codex 指定会话目录；脚本只输出统计。
"""

import argparse
import glob
import json
import os
from collections import Counter

BYTES_PER_TOKEN = (3.0, 4.0)  # 中英混合的粗估区间；M0 用提供方计数接口实测后替换


def nbytes(x):
    if isinstance(x, str):
        return len(x.encode("utf-8"))
    return len(json.dumps(x, ensure_ascii=False).encode("utf-8"))


def scan_claude(root):
    total, per_session = Counter(), []
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        is_sub = f"{os.sep}subagents{os.sep}" in path
        total["files_subagent" if is_sub else "files_main"] += 1
        s = Counter()
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                n = len(line.encode("utf-8"))
                total["bytes"] += n
                s["bytes"] += n
                try:
                    o = json.loads(line)
                except ValueError:
                    total["bad_lines"] += 1
                    continue
                if o.get("type") == "system" and o.get("subtype") == "compact_boundary":
                    total["compactions"] += 1
                    pre = (o.get("compactMetadata") or {}).get("preTokens") or 0
                    total["max_pre_compact_tokens"] = max(total["max_pre_compact_tokens"], pre)
                    continue
                msg = o.get("message") or {}
                if o.get("isCompactSummary"):
                    total["compact_summary"] += nbytes(msg.get("content", ""))
                    continue
                content = msg.get("content")
                if o.get("type") == "user":
                    if isinstance(content, str):
                        s["user_text"] += nbytes(content)
                    elif isinstance(content, list):
                        for it in content:
                            if it.get("type") == "text":
                                s["user_text"] += nbytes(it.get("text", ""))
                            elif it.get("type") == "tool_result":
                                s["tool_output"] += nbytes(it.get("content", ""))
                elif o.get("type") == "assistant" and isinstance(content, list):
                    for it in content:
                        t = it.get("type")
                        if t == "text":
                            s["asst_text"] += nbytes(it.get("text", ""))
                        elif t == "tool_use":
                            s["tool_input"] += nbytes(it.get("input", {}))
                        elif t == "thinking":
                            s["thinking"] += nbytes(it.get("thinking", ""))
        total["max_file_bytes"] = max(total["max_file_bytes"], os.path.getsize(path))
        for k in ("user_text", "asst_text", "tool_input", "tool_output", "thinking"):
            total[k] += s[k]
        if not is_sub:
            per_session.append(s["user_text"] + s["asst_text"])
    return total, per_session


def scan_codex(root):
    total, per_session = Counter(), []
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        total["files_main"] += 1
        s = Counter()
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                n = len(line.encode("utf-8"))
                total["bytes"] += n
                try:
                    o = json.loads(line)
                except ValueError:
                    total["bad_lines"] += 1
                    continue
                t, p = o.get("type"), o.get("payload") or {}
                if t == "compacted":
                    total["compactions"] += 1
                    total["compaction_replay"] += n
                    if not p.get("message"):
                        total["compactions_opaque"] += 1
                    continue
                if t != "response_item":
                    continue
                pt = p.get("type")
                if pt == "message":
                    txt = "".join(
                        (x.get("text") or "")
                        for x in (p.get("content") or [])
                        if isinstance(x, dict)
                    )
                    key = {"user": "user_text", "assistant": "asst_text"}.get(
                        p.get("role"), "developer_text"
                    )
                    s[key] += nbytes(txt)
                elif pt in ("function_call_output", "custom_tool_call_output"):
                    s["tool_output"] += nbytes(p.get("output", ""))
                elif pt in ("function_call", "custom_tool_call"):
                    s["tool_input"] += nbytes(p.get("arguments") or p.get("input") or "")
                elif pt == "reasoning":
                    s["reasoning"] += n
        total["max_file_bytes"] = max(total["max_file_bytes"], os.path.getsize(path))
        for k in (
            "user_text",
            "asst_text",
            "developer_text",
            "tool_input",
            "tool_output",
            "reasoning",
        ):
            total[k] += s[k]
        per_session.append(s["user_text"] + s["asst_text"])
    return total, per_session


def quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


def summarize(name, total, per_session):
    b = total["bytes"] or 1
    lo, hi = BYTES_PER_TOKEN
    text = total["user_text"] + total["asst_text"]
    out = {
        "tool": name,
        "files_main": total["files_main"],
        "files_subagent": total["files_subagent"],
        "total_mb": round(b / 1e6, 1),
        "share_pct": {
            k: round(100 * total[k] / b, 1)
            for k in (
                "user_text",
                "asst_text",
                "tool_input",
                "tool_output",
                "thinking",
                "developer_text",
                "reasoning",
                "compaction_replay",
            )
            if total[k]
        },
        "per_session_text_kb": {
            q: round(quantile(per_session, v) / 1e3)
            for q, v in (("median", 0.5), ("p90", 0.9), ("max", 1.0))
        },
        "all_text_tokens_est": [int(text / hi), int(text / lo)],
        "compactions": total["compactions"],
        "compactions_opaque": total["compactions_opaque"],
        "max_pre_compact_tokens": total["max_pre_compact_tokens"],
        "max_file_mb": round(total["max_file_bytes"] / 1e6, 1),
        "bad_lines": total["bad_lines"],
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--codex", default=os.path.expanduser("~/.codex/sessions"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    results = []
    if os.path.isdir(a.claude):
        results.append(summarize("claude", *scan_claude(a.claude)))
    if os.path.isdir(a.codex):
        results.append(summarize("codex", *scan_codex(a.codex)))
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    for r in results:
        print(
            f"== {r['tool']}: 主会话 {r['files_main']}，子 Agent {r['files_subagent']}，"
            f"共 {r['total_mb']} MB，最大单文件 {r['max_file_mb']} MB"
        )
        for k, v in r["share_pct"].items():
            print(f"   {k:18s} {v:5.1f}%")
        p = r["per_session_text_kb"]
        print(f"   每会话正文 KB：中位 {p['median']} / P90 {p['p90']} / 最大 {p['max']}")
        lo, hi = r["all_text_tokens_est"]
        print(f"   全部正文约 {lo / 1e6:.1f}–{hi / 1e6:.1f}M token（粗估）")
        print(
            f"   压缩 {r['compactions']} 次（不透明 {r['compactions_opaque']}），"
            f"压缩前最大 {r['max_pre_compact_tokens']} token；坏行 {r['bad_lines']}"
        )


if __name__ == "__main__":
    main()
