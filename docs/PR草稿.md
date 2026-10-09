# 建立可追溯研究记录与本地复核工作区

主分支目前只有许可证，无法安装或运行 ResearchGraph。本次接入 Python 研究记录后端、React 本地工作区、中文执行规格、合成样本和自动化验收，沿用 Apache-2.0。

工作区提供问题与方案、复核队列、完整决定时间线、健康、字面量检索、原文字节证据和 React Flow/ELK 研究图。比如一个片段有 211 条候选，界面取齐 200 条 API 页后原子确认全部记录；另一窗口先改动时返回 409，刷新后重新复核。人工修改追加 confirmed 替换版并保留原记录和原文；新模型候选展示同一身份与范围的已有人工确认差异。节点和关系严格分 scope，发生时间未知或同刻冲突不任意推断当前采用；部分图和缺失端点不能折叠。

提取先实测完整请求 token，默认 128000 输入预算可调，引用程序登记的原始字节窗口。模型原行始终 candidate；§7.7 的确认由独立程序重读完整用户声明，核对对象、范围、理由和证据后追加可审计的 rule 动作，人工确认优先。截断、污染、超预算或校验失败结果作废。同会话/任务进程互斥，完整响应可恢复并入库一次，自身规则确认不使已完成会话缓存失效。

跨会话链接逐对处理并保留进度，概览分页读取结构化记录并加防回流标记；监控保留每次尝试、UTC 用量、未结算预留、利用率/校验拒绝告警与覆盖缺口。旧库事务升级至版本 4，保留 L0。HTTP 只绑定 127.0.0.1，使用随机令牌、同源与静态目录校验，不执行会话命令。Python wheel 与前端静态资源分开部署。

## 验证

```text
uv sync --locked                  # 23 个包解析，22 个包检查
uv run pytest -q                  # 189 passed in 37.04s
uv run pytest tests/golden -q     # 149 passed in 23.16s
uv run ruff check rg tests scripts # All checks passed!
uv run pyright rg                 # 0 errors, 0 warnings, 0 informations
uv run rg --help                  # 退出码 0；serve/review --open 入口有效
uv build --wheel                  # 48 个文件，必要资源/源码匹配/密钥检查通过
cd web
pnpm install --frozen-lockfile    # 锁文件无需更新
pnpm test                        # Tests 11 passed (11)
pnpm build                       # TypeScript 与生产构建通过
pnpm test:browser                # 4 passed (6.8s)；真实合成库 HTTP + Chromium
```

前端固定 Node 24.21.0、pnpm 12.10.1 和依赖锁；CI 同时运行后端、前端、构建和真实浏览器。16 项新增 HTTP/CLI 用例覆盖批次原子性、409、人工替换、边界和引用；37 项规则固定案例核对实际原话与模型声明不一致的情形；11 项前端语义用例验证范围、状态、冲突和证据集合。浏览器实际验证 211 条跨两种分页、另一窗口冲突、人工修改、图原文引用、搜索及 390 像素布局。凭据、真实材料、静态产物和截图未入仓库，本轮未新增远程模型调用。

完整证据见 [前端与本地 API 验收](acceptance/前端与本地API验收_20261009.md)、[独立规则确认验收](acceptance/M1独立规则确认验收_20261009.md)和[并发恢复验收](acceptance/M1并发与缓存恢复验收_20261009.md)。Atlas 正式人工参考与问题尚未核对，真实 M1 效果和完整 M0–M4 未验收；采集正式化、影子快照、运行映射、完整图传播、MCP 等仍需后续开发。前端提前实施来自用户明确授权，合成测试不替代真实质量门槛。

## 推送状态

维护分支为 codex/m1-backend-20261009，继续更新[草稿 PR #1](https://github.com/FangWHao/ResearchGraph/pull/1)，目标为 main。以上是本地验收；本轮远端检查以该 PR 对应提交的实际结果为准，不把此前提交的成功当作当前实现已通过。

本地恢复包只包含公开跟踪代码，完成推送后刷新并独立恢复核对：

```bash
git clone /mnt/d/researchgraph/.cache/researchgraph-backend-20261009.bundle ResearchGraph
```
