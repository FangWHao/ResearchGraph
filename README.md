# ResearchGraph

把本机 Claude Code / Codex 的会话整理成一张**研究时间线**：每条研究线一条泳道，泳道里有主线和分支，分支可以汇回；每个事件都能点开看原话。

```
去批次比较（已完成） ┃ [试 Harmony ✓] → [分数 0.82] → ▌[采用 Harmony]
                    ┃        ╲                       ↗
                    ┃         [scVI 对照 ✗] ─────────┘
细胞注释（进行中）   ┃ [参考图谱注释 ✗] → [T 细胞单独聚类 ✓] → ▌[找到耗竭样亚群]
```

事件只记三类：**尝试**（成功 ✓ / 失败 ✗）、**决定**、**结果**。▌ 是里程碑。

## 三步上手

1. **启动**：`rg start --open`。第一次打开会自动找到 `~/.claude/projects` 和 `~/.codex/sessions`（WSL 下也找 Windows 用户目录里的），也可以手动添加目录；再填一个 DeepSeek API Key。
2. **新建项目**：选一个工作目录，里面的会话自动加入，以后的新会话也会自动加入；或者逐个勾选会话、上传 `.jsonl` 文件。
3. **整理**：界面会先告诉你要发送多少字给模型，确认后自动整理成时间线。之后只处理新增内容：打开着的项目会在会话安静几分钟后自动更新，没打开的项目下次打开时问你。

默认只看里程碑，顶栏“详略”可以切到“关键”或“全部”；点左上角“概览”看每条研究线的状态、进展和里程碑；顶栏搜索能找事件和原话。时间线可以切换“按研究线”和“按会话”两种看法。事件、研究线和分支都能手动改；改过的内容不会被自动整理覆盖。fork 出来的会话会作为一条新分支，从原会话的分出点接出去。

## 安装与启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
(cd web && pnpm install && pnpm build)   # 构建界面，只需一次
uv run rg start --port 8788 --open
```

只监听 `127.0.0.1`。在别的电脑上看，用 SSH 转发：`ssh -L 8788:127.0.0.1:8788 用户@主机`，然后打开 `http://127.0.0.1:8788/`。

其他命令：`rg sessions`（列出发现的会话目录和工作目录）、`rg organize <项目编号> [--full] [--yes]`（在命令行整理）。数据目录默认是 `~/.researchgraph`，可用 `--data-dir` 指定。

打包含界面的 wheel：`(cd web && pnpm build) && RG_PACKAGE_WEB=1 uv build --wheel`。

## 数据与隐私

- 会话文件只读，从不修改。读入的文字存在本机 SQLite（`~/.researchgraph/timeline.db`），原文件被客户端清理后时间线仍然保留。
- 只有在你确认发送量之后，才会把会话文字发送给模型。工具调用只发送一行摘要。
- API Key 保存在数据目录里（权限 0600），也可以用环境变量 `RG_API_KEY` 或 `RG_API_KEY_FILE`。

## 开发

```bash
uv run pytest -q
uv run ruff check rg tests scripts && uv run pyright rg
cd web && pnpm test && pnpm build
```

设计见 [docs/设计.md](docs/设计.md)，开发约定见 [AGENTS.md](AGENTS.md)。2026-10-13 之前的完整版（双时间账本、复核队列、钩子、MCP 等）保存在 tag `v0-full-spec`，相关文档在 [docs/archive/](docs/archive/)。
