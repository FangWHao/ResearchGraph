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

## 2026-10-14 · 提交并替换本机服务

- 任务：用户同意提交，并把 8788 上的服务换成新版，换完后自己试用。
- 提交：`458e1e8 按手绘草图重写为研究时间线`（分支 rewrite-timeline，未推送）。本机没有 git 身份，按用户以往在 GitHub 上的身份单次署名，没有改全局配置。
- 更正：上一轮说“旧数据库里没有项目”是照着旧进度记录说的，实际 `~/.researchgraph/rg.db`（约 209 MB）里有 1 个项目、297 个会话、126,149 条原始事件（只读查询计数）。新版只用 `timeline.db`，`rg.db` 原样保留；新界面看不到旧项目，需要重新建。
- 部署：`systemctl --user stop researchgraph.service` → `uv pip install --python ~/.local/share/researchgraph/venv/bin/python --reinstall-package researchgraph .cache/rewrite-20261014/researchgraph-0.2.0-py3-none-any.whl`（0.1.0 → 0.2.0，旧模块全部移除）→ `systemctl --user start`。systemd 配置没改，命令行参数与新版兼容。最终 wheel 的 SHA256 是 015af7d33d0223dd3e3cc5280753356101af05098444c065c8af1f87521ed86b。
- 验收：ActiveState=active / SubState=running / ExecMainStatus=0；`ss` 显示只监听 127.0.0.1:8788；`/` 200 text/html，`/api/state` 200（sources 空，发现 claude 38、codex 326、codex 31 个文件，has_key false，项目 0）；Host 为 evil.example 时返回 403。浏览器打开首次引导页没有报错，修正了“高级设置”链接居中的样式。
- 回滚：`uv pip install --python ~/.local/share/researchgraph/venv/bin/python --reinstall-package researchgraph .cache/main-merge-20261013/wheel/researchgraph-0.1.0-py3-none-any.whl` 后重启服务，旧版仍读 `rg.db`。
- 下一步：用户在引导页填写 API Key，选一个真实项目试整理，反馈效果。

## 2026-10-14 · 一键检测 API Key，Key 无效时只报一次

- 起因：用户第一次填了一个 DeepSeek 不认的 Key（46 位，DeepSeek 返回 401），每个会话各报一条“提取失败：HTTP 401”，状态栏被刷满。用户随后换好 Key，整理出 25 个会话、151 个事件。
- 改动：
  - `rg/model.py`：401/403 抛 `AuthError`，不重试，也不带接口返回的原文（有的接口会回显部分 Key）。`Model.check()` 只发一条 `ping`（max_tokens 1），确认 Key、接口地址和模型名都能用。
  - `rg/server.py`：`POST /api/settings/check` 用填写中或已保存的 Key 检测，不保存设置。App 加了可注入的 `transport`，供测试使用。
  - `rg/organize.py`：遇到 AuthError，所有提取线程停下，只记一条错误；已整理的进度不前进也不丢失，改好 Key 后点“重试”接着做。
  - `rg/parse.py`：斜杠命令去掉 `<command-name>` 等标签，自动标题跳过 `/clear` 这类命令。
  - 前端：设置页 Key 输入框旁边加“检测”按钮；出错的状态栏增加“设置”按钮。
  - 测试：新增 `tests/test_model.py`，并在 organize / server / parse 测试里补了对应用例。
- 验收：`uv run pytest -q` → 40 passed in 6.31s；ruff All checks passed!，格式检查 18 files already formatted；pyright 0 errors；`pnpm test` 8 passed，`pnpm build` 成功。重装本机服务（wheel SHA256 df5eb2db64a9e106c14955dd94fe9f02b39f68c2ea80e03946538c0f124adf98）前确认没有整理任务在跑；重启后 active/running，项目数据完好（1 个项目、25 个会话、151 个事件）。线上 `/api/settings/check` 用已保存的 Key 返回 `可以使用（deepseek-flash）`，浏览器里点“检测”显示 ✓，页面无报错。
- 下一步：用户看真实项目的整理效果并反馈。
