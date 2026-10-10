# 接入自动采集、关联概览与研究问答

主分支只有许可证，缺少研究会话的采集和查询入口。本次接入 Python 后端与 React 本地工作区，把 Codex/Claude 会话整理成带原文引用的研究记录，提供有界问答及客户端技能。

登记来源后，extract --watch 自动扫描、消费 spool、派生 L1、提取、逐对关联，再从结构化 claims 生成会话和项目概览。原件只追加，未知记录和覆盖缺口保留；系统锁、任务归属和只追加历史支持强杀接续。关联分页与等待完成前不提前发布概览，失败对显式重试跨页最多再试一次。生成期间记录变化则保留旧文件；发布回执绑定本次字节，备份及目录迁移可恢复，缺失文件优先复用有效缓存。

完整模型请求每次生成前由提供方实测，默认输入 128000 可调；额度不足等 UTC 次日，关联/概览服务故障及忙碌分别等待 30/2 秒，新内容保留等待。项目默认禁止外发，首次许可预览实际遮盖内容；模型行始终 candidate，截断、超预算和污染结果作废。原话规则与人工动作另存，采用、审核、证据、运行互不替代，范围不明或对象不唯一不自动确认。

本地工作区提供问题、问答、可选人工补记、分组复核、双时间线、原文窗口、运行/编辑证据、两个独立健康队列及 React Flow/ELK 图。问答先本地检索，再按本机服务配置生成带逐条引用的解释；故障保留来源，项目/问题变化后的迟到结果隔离。HTTP 只监听 127.0.0.1，使用随机令牌及 Host/同源校验。任务状态不冒充进程存活或事实确认，概览与模型解释不作研究证据。

Agent 使用五个固定项目的只读 MCP 工具、双截止历史和离线状态卡；所有研究文本包装防回流。client-pack 生成 Codex/Claude 原生技能与 stdio 配置，上下文允许自动选用，人工 question/decide 仅明确调用。原话经有限 JSON 及固定项目包装进入已有幂等写入合同，不插入 shell；不覆盖个人配置或安装钩子。

```text
uv sync --locked                   → 28 个包解析、27 个包检查
uv run pytest -q                   → 563 passed in 202.43s (0:03:22)
uv run pytest tests/golden -q       → 516 passed in 189.67s (0:03:09)
uv run ruff check rg tests scripts  → All checks passed!
uv run pyright rg                  → 0 errors, 0 warnings, 0 informations
uv run rg --help / extract --help / health --help → 退出码均 0
cd web && pnpm test                → 47 passed，857ms
cd web && pnpm build               → 200 modules，1.03s
cd web && pnpm test:browser         → 47 passed，34.1s
uv build --wheel                   → 98 文件、93 源码资源匹配，私有文件及实际密钥匹配 0
```

19 项新增固定案例验证自动后续阶段、缓存、等待、权限、独立分页、文件与备份恢复及实际 SIGKILL。独立安装最终 wheel、从仓库外执行真实 CLI watch 连明确本机模拟接口：两会话首轮生成 8 次，新增第三会话后累计 14 次，45 次计数请求；逐次核对完整请求已计数，没有变化的轮次新增网络 0，原件不变、模型仅候选，正常停止退出 0。模拟计数不冒充提供方准确 token。本轮没有新增真实模型调用；此前合成 DeepSeek 实测单独记录，不代表 Atlas 的正式质量。

完整证据见[自动关联与概览验收](acceptance/M3自动关联与概览验收_20261010.md)、[健康页验收](acceptance/自动关联与概览健康页验收_20261010.md)、[问答与许可验收](acceptance/M4问答接口与外发许可验收_20261010.md)及[客户端接入包验收](acceptance/M2客户端接入包验收_20261010.md)。官方 MCP SDK 两种协议与两种生成配置的子进程连通验收保留在 CI。

Atlas 正式参考、真实效果和完整 M0–M4 尚未验收。后续包括完整运行 I/O、后台大文件摘要、全图语义、export、临床编号配置与隐私清除；note/propose_note 类型待约定。没有安装个人客户端钩子或系统常驻服务。凭据、真实材料、数据库、截图与私有缓存不入仓库。维护[草稿 PR #1](https://github.com/FangWHao/ResearchGraph/pull/1)，提交后按新 HEAD 核对推送/PR 两种 CI并独立恢复代码包。
