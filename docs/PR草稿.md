# 建立后端试验版、候选定位与离线验收

远端目前仅有许可证，无法安装或运行 ResearchGraph。本次接入经过本地验收的 Python 后端、中文执行规格与进度记录、合成样本和离线 CI，沿用 Apache-2.0。

后端支持流式导入 Codex/Claude 会话、保存原文对象、字面量检索与引用查看，默认以 128000 token 输入预算实测计数后提取候选。程序登记精确字节窗口；跨会话关系逐对判断，总预算 4000；概览直接读取 claims，并标注模型摘要和防回流标记。模型结果保持 candidate，人工审核追加动作。旧数据库事务升级到版本 2，不改写 L0。

## 验证

```text
uv sync --locked                  # 23 个包解析，22 个包检查
uv run pytest -q                  # 94 passed in 13.42s
uv run pytest tests/golden -q     # 72 passed in 12.14s
uv run ruff check rg tests scripts # All checks passed!
uv run pyright rg                 # 0 errors, 0 warnings, 0 informations
uv run rg --help                  # 退出码 0
uv build --wheel                  # 成功；必要 schema、迁移、提示均包含
```

DeepSeek 合成提取测试得到 5 条候选、0 个覆盖缺口；合成链接测试返回 none，概览生成 1 页，缓存通过。仅使用合成输入。凭据、真实材料、环境和本地 Word 归档未纳入提交。

完整结果见 [本轮验收](acceptance/M1定位链接与仓库验收_20261009.md)。这是后端试验版，尚未通过 Atlas 真实 M1 效果门槛；HTTP/MCP、采集正式化、界面和完整图仍未完成。

## 推送状态

本地分支为 `codex/m1-backend-20261009`。GitHub 连接器创建 tree 和分支均返回 `403 Resource not accessible by integration`；本机 HTTPS 缺少可用登录，SSH 返回 `Permission denied (publickey)`。尚未推送、未创建 PR，GitHub CI 未执行；CI 配置只通过本地对应命令检查。

恢复具备仓库写入权限的 GitHub 连接或本机 Git 认证后，推送该分支并用上面的内容建立草稿 PR。
