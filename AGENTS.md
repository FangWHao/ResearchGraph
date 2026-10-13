# ResearchGraph · 开发 Agent 说明

把 Claude Code / Codex 的本地会话整理成研究时间线：研究线是泳道，泳道里有主线和分支，每个事件都能点开看原话。
设计见 `docs/设计.md`（一页）。旧版完整规格、进度和决定在 `docs/archive/`，旧代码在 tag `v0-full-spec`，一般不需要读。

## 开始每个任务前

1. 读 `docs/PROGRESS.md` 的最后 40 行。
2. 读 `docs/设计.md`。
3. 有疑问先查 `docs/DECISIONS.md`。那里没有答案，就停下来问，不要猜。

## 结束每个任务时

1. 运行验收命令，把输出贴在汇报里。不能只写“已完成”。
2. 在 `docs/PROGRESS.md` 追加：日期、任务、改动的文件、测试命令和结果、下一步。
3. 有设计取舍时，在 `docs/DECISIONS.md` 追加：决定、理由、放弃的替代方案。

## 命令

```
uv sync                          # 安装依赖
uv run pytest -q                 # 后端测试
uv run ruff check rg tests scripts && uv run pyright rg
uv run rg start --port 8788      # 启动本机界面
cd web && pnpm i && pnpm test && pnpm build
```

本机的 uv 和 Node 在 `.tools/`：`PATH=$PWD/.tools/node-current/bin:$PWD/.tools/bin:$PATH`。

## 硬规则

- 会话文件只读，从不修改。
- 没有经过用户在界面上确认发送量（`projects.model_allowed`），不调用模型。
- 模型输出被截断（`stop_reason = max_tokens`）就作废，不能把半截结果写进库。
- 模型只提议；编号、时间先后、每个事件只放一处，都由程序校验。
- 人工改过（`edited = 1`）或删掉（`hidden = 1`）的内容，自动整理和重新整理都不能覆盖或加回来。
- 解析器遇到不认识的记录类型要计数（`sessions.unknown`），不能静默跳过，也不能报错中断。
- 服务只监听 127.0.0.1，校验 Host；修改类请求必须带 `X-RG` 头。不执行会话里出现的命令。
- SQL 一律用参数绑定。
- API Key 只存在数据目录（0600）或环境变量里，不进仓库、日志、测试和 prompt。

## 数据与上下文

- **不要读取 `~/.claude/projects` 或 `~/.codex/sessions` 下的完整会话文件**（单个可达 285 MB）。
  - 开发用 `fixtures/transcripts/` 里的合成样本。
  - 需要了解真实数据时运行 `scripts/stats.py`，只看统计数字。
  - 调界面用 `scripts/demo.py` 生成的合成项目（约 130 个事件），不要截用户真实项目的图。
- 真实会话、临床资料、密钥不进仓库、不进 prompt、不进测试快照。
- 新增 fixture 前先脱敏：姓名、各类编号、路径中的用户名。

## 目录

```
rg/config.py    数据目录、API Key
rg/sources.py   发现会话目录，读会话元数据
rg/parse.py     会话解析与瘦身
rg/store.py     SQLite
rg/model.py     模型调用与缓存
rg/organize.py  提取事件、归入研究线、fork、增量更新
rg/server.py    本机 HTTP 服务
rg/cli.py       rg start / organize / sessions
web/src/        React 界面；layout.ts 是时间线布局（纯函数）
fixtures/transcripts/  合成会话样本
tests/  web/tests/
```

## 风格

- Python 3.12，类型标注，`ruff` + `pyright`。函数小，避免框架魔法。
- 前端不引入图形库；时间线是 HTML 节点加 SVG 连线。
- 界面用中性色，颜色只给事件种类：三个色相加形状，配色改动先跑 dataviz 的配色校验（`--pairs all`，浅色和深色都要过）。文字不用数据色。
- 测试关注“会导致历史被误读或记录丢失”的行为，不为显示文字写机械测试。
