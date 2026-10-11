显式登记的 Codex／Claude 会话可以增量整理成带原文引用、完整分析范围及发生／已知双时间的研究决定史。原来只能按聊天顺序查找的决定、尝试与阴性结果，现在通过本地研究图、时间线、检索、问答和历史导出查证；模型记录始终为候选，人工更正及审核独立保留。

扫描、离线钩子、影子快照、提取、跨会话关联及概览沿持久队列接续。模型完整请求发送前实测 token，默认 128000 可调，项目默认禁止外发；截断、超预算和执行器压缩结果作废。来源父链从保存原件有界恢复，缺失、歧义、冲突及跨项目不猜关联。五个只读 MCP 工具与 Codex／Claude 项目接入包共用证据合同。

完整研究图固定项目、双截止及修订，分页齐备后才允许折叠；过程组保留原节点、边界、端点、端口、历史及证据，展开恢复原位置。十一套独立合成场景核对十类语义及浏览器折叠。MultiEdit 按原顺序核对，后台缓存不改旧候选；工具报告不确认物理文件。旧漏标注入来源在问答与链接计数前重新核对，失效任务和调用缓存按处理版本重查；长混合正文与转义窗口不让注入内容回流，原件及人工审核保留。

整项目清除提供预览、独占执行、中断恢复与重导入屏障，保留其他项目及共享字节。旧安装包在原生 Windows 下保存原件后报缺少目录标志，中文 schema 被按 GBK 解码；现在明确 UTF-8 读取并使用同目录文件同步、宽字符写穿透发布。清除预览、回执和恢复路径逐级拦截 NTFS junction 等重解析点，Windows 错误日志固定目录句柄只追加异常类型。新增 Windows CI 持续验证存储与清除，不要求管理员权限或全局切换编码。

## 当前验收

```text
uv sync --locked                         Resolved28/1ms，Checked27/53ms
uv run pytest -q                         1311 passed in 578.76s (0:09:38)
uv run pytest tests/golden -q             1264 passed in 562.88s (0:09:22)
uv run ruff check rg tests scripts        All checks passed!
uv run pyright rg                        0 errors, 0 warnings, 0 informations
uv run rg --help                         退出0
Linux 存储／清除／钩子关联                64 passed in 25.35s
Linux 最终目录别名专项                   20 passed in 7.09s
原生 Windows 存储／清除／占用锁           41 passed in 10.88s，无跳过
仓库外 Windows3.12.3 -I 实际安装          41 passed in 11.98s，CP936、utf8_mode0
```

列出通过命令均实际退出 0。最终 wheel 含 143 成员／138 运行资源，与源码及原生 Windows 实际安装逐字一致，实际目录凭据匹配 0、私有文件 0。前端本批未改，既有 169 项冻结保持一致；本地 172 单测、245 模块构建及 162 浏览器属于前一批基线，当前提交另由两类 CI 核对。官方 SDK 两代协议、两客户端配置及原生五工具证据均保留原批次边界，不把这次存储验收当作再次验证全部客户端。收尾核对新 HEAD 的推送／PR 两类 CI、独立完整 bundle 恢复与精确草稿正文，草稿不合并。

证据见[原生 Windows 存储与清除验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/原生Windows存储与清除验收_20261011.md)、[链接来源与长窗口验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/链接来源与长窗口验收_20261011.md)、[十类语义与折叠验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/十类语义折叠验收_20261011.md)及[需求落实与剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。

完整 M0–M4 尚未验收。Atlas 正式人工参考、真实提取质量／找答案时间、全部工具／客户端版本、完整实际 I/O、真实钩子／规模、M2 全量采集及 M3 两周使用仍待落实。Windows 断电持久性、完整影子快照／摘要／本地 ZIP 导出未通过本批验收。note、影响传播、跨项目优先级、污染候选参与自动状态与工作集、收尾同步等普通设计按用户授权由 Agent 继续处理，不再等待普通选项答复；尚未实现的功能仍如实标注。整项目清除只覆盖当前管理目录，外部原日志、工作区及用户保存／分享的副本保留。

本批全部为公开合成资料，没有读取整份真实会话、调用远程模型、删除真实项目或安装日常钩子。真实会话、数据库、截图、缓存及密钥不入仓库，固定输出不证明真实提取质量。
