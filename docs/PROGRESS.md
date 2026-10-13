# 开发进度

2026-10-13 之前的记录见 `archive/PROGRESS.md`。

## 2026-10-14 · 按草图重写为研究时间线（分支 rewrite-timeline）

- 任务：用户认为旧版跑偏，要求按手绘草图大修，并且容易上手。草图里的三步：首次打开自动或手动定位 CC / Codex 目录 → 新建项目，自动或手动导入会话 → 自动整理成多泳道时间线（研究线一行，分支从事件分出、汇回后面的事件）。用户选定：两层视图、模型总结关键步骤、直接显示可手改、新分支重写。中途补充：fork 应看作新开一条线。
- 保底：当前 main（83c4bd4）打 tag `v0-full-spec`；旧规格、进度、决定、验收移到 `docs/archive/`；旧 `rg/`、`tests/`、`scripts/`、`web/src`、`web/tests`、`fixtures/golden` 删除（都在 tag 里）。
- 新文件：`docs/设计.md`；`rg/{config,sources,parse,store,model,organize,server,cli}.py`（约 1,900 行，旧版约 29,000 行）；`web/src/{App,Setup,NewProject,ProjectView,Timeline,Panel}.tsx` 和 `layout.ts` / `api.ts` / `types.ts` / `format.ts` / `styles.css`；`tests/` 4 个文件、`web/tests/layout.test.ts`；`scripts/stats.py`；合成样本 `fixtures/transcripts/{claude,codex}/research-synthetic.jsonl`、`codex/{subagent,legacy}-synthetic.jsonl`。改写 AGENTS.md、README.md、CI、pyproject（依赖只剩 httpx）、web/package.json（去掉 React Flow、ELK、Playwright）。
- 本机数据（只看统计）：Codex 326 个文件中 214 个是子 Agent、14 个 fork（原会话都在本机）；Claude 38 个主会话，没有 fork 标记。最近 60 个文件完整解析，未识别记录 0。最大的会话 285 MB，瘦身后约 260 万字、33 次调用。
- 验收：
  - `uv run pytest -q` → 33 passed in 5.86s
  - `uv run ruff check rg tests scripts` → All checks passed!；`uv run pyright rg` → 0 errors, 0 warnings, 0 informations
  - `cd web && pnpm test` → 8 passed；`pnpm build` → built in 331ms
  - `RG_PACKAGE_WEB=1 uv build --wheel` → 17 个文件，含 `rg/_web`；仓库外隔离安装后 `web_dir()` 指向 site-packages
  - 真实模型冒烟（仅仓库合成样本，DeepSeek deepseek-flash，独立数据目录与 8790 端口）：两个会话提取出 8 个事件；第一次归类时模型在 events 里抄回了整行文字，全部落入“未归类”，已改为只取开头编号并加测试；重新整理后得到“去批次方法比较”（主线 + scVI 对照分支，从“试跑 Harmony”分出、汇入“采用 Harmony”）和“细胞注释与亚群细分”（从“采用 Harmony”跨研究线分出），与草图一致。
  - 浏览器截图（浅色、深色、390px 窄屏、事件详情、新建项目）：页面无报错；修正了时间线撑宽页面导致详情面板被挤出屏幕、事件框高度不够两个问题。
- 未做：Windows CI 只写进工作流，没有在本机原生 Windows 上跑；没有用真实会话整理（需要用户确认发送）；8788 端口上的 systemd 服务仍是旧版。
- 下一步：用户试用真实项目，根据效果调整提示词和瘦身力度；确认后合并到 main 并更新本机服务。
