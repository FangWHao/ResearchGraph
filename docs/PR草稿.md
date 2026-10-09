# 建立后端试验版、有界提取与可续跑链接

主分支目前仅有许可证，无法安装或运行 ResearchGraph。本次接入经过本地验收的 Python 后端、中文执行规格与进度记录、合成样本和离线 CI，沿用 Apache-2.0。

后端支持流式导入 Codex/Claude 会话、保存原文对象、字面量检索与引用查看，默认以 128000 token 输入预算实测计数后提取候选。程序登记精确字节窗口；跨会话关系逐对判断，总预算 4000，持久进度支持跨页续跑、暂停恢复与显式失败重试。比如 66 对候选首次处理 50 对，重跑跳过已完成项并处理余下 16 对；校验失败不会阻塞后续页。概览直接读取 claims，并标注模型摘要和防回流标记。

模型结果保持 candidate，人工审核追加动作。旧数据库事务升级到版本 3，保留 L0 和既有定位。程序标识符只在规定的元数据位置免除遮盖，避免摘要数字误匹配手机号而使引用失配；原文与自由文本仍照常遮盖。无关系结果必须给出 null 端点，错误结果不入库。

## 验证

```text
uv sync --locked                  # 23 个包解析，22 个包检查
uv run pytest -q                  # 111 passed in 22.96s
uv run pytest tests/golden -q     # 88 passed in 21.88s
uv run ruff check rg tests scripts # All checks passed!
uv run pyright rg                 # 0 errors, 0 warnings, 0 informations
uv run rg --help                  # 退出码 0；link 帮助含 --retry-failed
uv build --wheel                  # 成功；42 个文件，必要入口无缺失，实际密钥匹配 0
```

此前 DeepSeek 合成提取测试得到 5 条候选、0 个覆盖缺口。最新合成链接测试返回 none，概览生成 1 页，缓存通过；原进程退出码未在中断后取回，已独立验证落盘结果与输出 schema。模型曾为 none 填端点的失败记录同样保留。仅使用合成输入；凭据、真实材料、环境和本地 Word 归档未纳入提交。

完整结果见 [链接调度验收](acceptance/M1链接调度验收_20261009.md)与[定位验收](acceptance/M1定位链接与仓库验收_20261009.md)。这是后端试验版，尚未通过 Atlas 真实 M1 效果门槛；HTTP/MCP、采集正式化、界面和完整图仍未完成。

## 推送状态

已推送分支 `codex/m1-backend-20261009`，并建立[草稿 PR #1](https://github.com/FangWHao/ResearchGraph/pull/1)，目标为 main。此前连接器写入的 403 未复测；本次使用用户恢复的本机 Git/gh 认证完成维护。

首次推送与 PR 的远端 CI 均通过，验证提交为 `3966eac`；PR 验收输出 111 项测试、88 项固定案例、ruff 和 pyright 全部通过，详情见[远端运行](https://github.com/FangWHao/ResearchGraph/actions/runs/37890556219)。主分支尚未合并；后续文档提交的检查以 PR 上的当前结果为准。

```bash
git push -u origin codex/m1-backend-20261009
# 在其他目录恢复完整代码：
git clone /mnt/d/researchgraph/.cache/researchgraph-backend-20261009.bundle ResearchGraph
```
