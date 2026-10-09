# 建立研究记录后端、有界提取与并发保护

主分支目前仅有许可证，无法安装或运行 ResearchGraph。本次接入经过本地验收的 Python 后端、中文执行规格与进度记录、合成样本和离线 CI，沿用 Apache-2.0。

后端支持流式导入 Codex/Claude 会话、保存原文对象、字面量检索与引用查看，默认以 128000 token 输入预算实测计数后提取候选。程序登记精确字节窗口；跨会话关系逐对判断，总预算 4000，持久进度支持跨页续跑、暂停恢复与显式失败重试。比如 66 对候选首次处理 50 对，重跑跳过已完成项并处理余下 16 对；校验失败不会阻塞后续页。概览直接读取 claims，并标注模型摘要和防回流标记。

模型结果保持 candidate，人工审核追加动作。旧数据库事务升级到版本 4，保留 L0 和既有定位；每次调用尝试保存预算、实测与实际用量、失败和结束状态，重试不抹掉旧失败。health 支持 UTC 用量、未结算预留、利用率与校验拒绝告警、覆盖缺口分页；同片段的重试不放大片段占比。程序标识符只在规定的元数据位置免除遮盖，避免摘要数字误匹配手机号而使引用失配；原文与自由文本仍照常遮盖。无关系结果必须给出 null 端点，错误结果不入库。

同会话提取、同模型任务和同项目概览使用系统进程锁。例如两个 worker 同时处理一个输入，后到者在计数前返回忙，完成的模型响应可在重启后复用，再校验原文并入库一次；不同会话提取和采集仍可进行。成功运行的错误后续请求不会改写原结果。终止进程后系统释放锁，已发送但用量未知的尝试继续保留预留；全队列守护调度与自动接管尚未实现。

## 验证

```text
uv sync --locked                  # 23 个包解析，22 个包检查
uv run pytest -q                  # 136 passed in 21.71s
uv run pytest tests/golden -q     # 112 passed in 20.17s
uv run ruff check rg tests scripts # All checks passed!
uv run pyright rg                 # 0 errors, 0 warnings, 0 informations
uv run rg --help                  # 退出码 0；link 帮助含 --retry-failed
uv build --wheel                  # 成功；43 个文件，必要资源无缺失，实际密钥匹配 0
```

此前 DeepSeek 合成提取测试得到 5 条候选、0 个覆盖缺口。最新合成链接/概览测试退出码 0，1 对返回 none，概览 1 页，缓存通过。模型曾为 none 填端点的失败记录同样保留；错误结果未入库。仅使用合成输入，凭据、真实材料、环境和本地 Word 归档未纳入提交。

最新合成监控实测仅有 2 次发送，输入 1566、输出 556，总量 2122，与每日计数一致；缓存通过。6 个种子事件阶段覆盖缺口如实保留，不把派生成功视为全部事件已提取。

完整结果见 [并发与缓存恢复验收](acceptance/M1并发与缓存恢复验收_20261009.md)、[提取监控验收](acceptance/M1提取监控验收_20261009.md)、[链接调度验收](acceptance/M1链接调度验收_20261009.md)与[定位验收](acceptance/M1定位链接与仓库验收_20261009.md)。这是后端试验版，尚未通过 Atlas 真实 M1 效果门槛；HTTP/MCP、采集正式化、界面和完整图仍未完成。

## 推送状态

已推送分支 `codex/m1-backend-20261009`，并建立[草稿 PR #1](https://github.com/FangWHao/ResearchGraph/pull/1)，目标为 main。此前连接器写入的 403 未复测；本次使用用户恢复的本机 Git/gh 认证完成维护。

本轮开始时最新提交 `03b0012` 的 PR 和推送检查均通过；PR 日志为 129 项测试、105 项固定案例、ruff 和 pyright 全部通过，详情见[远端运行](https://github.com/FangWHao/ResearchGraph/actions/runs/37894266046)。当前新增并发保护的本地结果见上文；推送后的检查以 PR 对应提交为准。主分支尚未合并。

```bash
git push -u origin codex/m1-backend-20261009
# 在其他目录恢复完整代码：
git clone /mnt/d/researchgraph/.cache/researchgraph-backend-20261009.bundle ResearchGraph
```
