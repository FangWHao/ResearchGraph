# 原生客户端 MCP 样本

`codex/0.162-native-mcp.jsonl` 来自本机 Codex CLI 0.162.0，`claude/2.1.280-native-mcp.jsonl` 来自 Claude Code 2.1.280。两者在新建的隔离配置目录和公开合成工作区运行，连接生成接入包中的真实 ResearchGraph stdio 服务；模型接口由只监听回环地址的固定响应服务替代。

样本保留一次状态卡调用及实际返回。Codex 的调用名由 `namespace=mcp__researchgraph` 与 `name=research_context` 两字段组成；Claude 的名称为 `mcp__researchgraph__research_context`。Codex 返回是带 `content` 与元数据的对象，不能只删除标记正文而保留其余包装作为新研究资料。

这里只保留相关记录，Codex 会话头也仅保留必要身份字段，不复制客户端自带的长系统提示。临时根目录统一替换为 `/synthetic/native-client`；问题、身份和编号均为本轮生成的合成内容，没有真实研究会话、姓名、临床编号或凭据。原始测试日志及摘要留在仓库外或忽略缓存中，未修改原件。

这些材料证明实际客户端的序列化形状，不证明真实模型的提取质量。固定响应中的 token 用量是协议占位值，不用于产品计数、成本或预算验收；合成人工记录不计入 Atlas 正式人工参考。

复现五个工具的原生接入：

```bash
uv run python scripts/check_native_clients.py
uv run pytest tests/golden/test_native_client_mcp.py -q
```

首条命令需要已经安装的 Codex 与 Claude Code，可用 `--codex-bin` 和 `--claude-bin` 指定入口。脚本不安装客户端、不安装个人钩子、不改变日常配置；打印独立临时目录，保留合成日志和验收回执供核对。客户端版本变化后应重新运行，不从旧回执推定兼容。
