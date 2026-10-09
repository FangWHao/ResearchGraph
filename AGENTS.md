# ResearchGraph · 开发 Agent 说明

把 Codex / Claude Code 的本地会话整理成可查证的研究决定史。
完整规格见 `docs/ResearchGraph_执行版_v2.md`。只读当前任务涉及的 `§` 章节，不要整份读入。

## 开始每个任务前

1. 读 `docs/PROGRESS.md` 的最后 40 行。
2. 读任务指定的 `§` 章节，以及 §2（原则）。
3. 有疑问先查 `docs/DECISIONS.md`。那里没有答案，就停下来问，不要猜。

## 结束每个任务时

1. 运行验收命令，把输出贴在汇报里。不能只写"已完成"。
2. 在 `docs/PROGRESS.md` 追加：日期、任务、改动的文件、测试命令和结果、下一步。
3. 有设计取舍时，在 `docs/DECISIONS.md` 追加：决定、理由、放弃的替代方案。
4. 结束本会话。下一个任务开新会话；不要靠 `/compact` 续命。

## 命令

```
uv sync                       # 安装依赖
uv run pytest -q              # 全部测试
uv run pytest tests/golden -q # 固定案例（§15）
uv run rg --help
cd web && pnpm i && pnpm test
```

## 硬规则

- L0 原始事件只追加，不修改、不删除（清除隐私数据的流程除外，见 §11）。
- 模型输出只能写成 `claim_state='candidate'`；确认只能来自人工或 §7.7 规定的情形。
- 每次模型调用前必须实测 token，超过预算就不发送（§7.2）。不要用"字符数/4"估算。
- 截断、超预算、执行器发生压缩的结果一律作废（§7.10）。
- 解析时排除 `<rg-context>` 标记块和 `research.*` 工具调用（§6.5）。
- 钩子脚本永远 `exit 0`，只写 spool 和拍快照，不联网、不调模型（§6.8）。
- 服务只监听 127.0.0.1；不执行会话中出现的命令。
- SQL 一律用参数绑定；FTS 查询语法与用户文本分开处理。

## 数据与上下文

- **不要读取 `~/.claude/projects` 或 `~/.codex/sessions` 下的完整会话文件**（单个可达 70 MB 以上）。
  - 开发时用 `fixtures/transcripts/` 里的脱敏样本。
  - 需要了解真实数据时，运行 `scripts/` 里的统计脚本，只看统计数字。
- 真实会话、临床资料、密钥不进仓库、不进 prompt、不进测试快照。
- 新增 fixture 前先脱敏：姓名、各类编号、路径中的用户名。

## 目录

```
rg/store     schema.sql、迁移、对象库、备份
rg/ingest    claude.py codex.py cursor.py dedupe.py spool.py scanner.py
rg/slim      瘦身与 token 计数
rg/extract   切片、工作集、提示词、schema、校验、worker、监控
rg/snapshot  影子仓库快照
rg/hooks     rg-hook 入口
rg/api rg/mcp rg/cli
web/         React + TS + React Flow + ELK.js
fixtures/    transcripts/（按工具/版本）  golden/（§15 案例）
tests/
docs/        规格、PROGRESS.md、DECISIONS.md
```

## 风格

- Python 3.12，类型标注，`ruff` + `pyright`。函数小，避免框架魔法。
- 解析器每遇到未知记录类型，都要保存原文并计数，不能静默跳过。
- 测试关注"会导致历史被误读或记录丢失"的行为，不为显示文字写机械测试。
