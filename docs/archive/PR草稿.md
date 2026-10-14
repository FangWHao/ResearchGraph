本机 Codex／Claude Code 的会话可以持续采集为带原文引用、审核状态和双时间依据的研究决定史。Windows 路径及旧编辑记录可以按已存请求和报告追加核验，历史读取保留原记录及后来补齐时间；模型解释始终与人工确认分开。

## 实现

- 项目创建、显式来源、历史和增量导入、父子会话、压缩与注入排除；钩子只写本地 spool 和快照。提供项目技能及 MCP 安装，统一入口管理桌面服务与后台采集。
- 有界提取、关联、概览及恢复队列；128k 默认预算可调，模型调用前实测并遮盖，需项目明确外发许可。模型不可用时仍可离线导入、检索和查证。
- 问题、研究图、问答、检索、证据回溯、审核、版本差异、历史导出、备份及整项目清除。桌面保留三个主要入口，完整安装包内含界面，服务仅监听 127.0.0.1。
- Windows／UNC／POSIX 路径、逐记录目录、L1 文件运行图、旧编辑追加复核、根身份双时间账本及共享依赖清除。运行报告和真实执行事实分别展示，未知 I/O 保留未知。
- 修复旧升级测试的基线修订计数及导出中的摘要误遮盖。程序生成摘要只在固定位置保留，用户同名字段仍遮盖，自定义规则冲突时拒绝发布。

## 验收

当前代码提交 `55de1c336b5c5ab1af74c1ed7de12c7e6b1e4e45` 的 push 与 PR 检查均已通过：

```text
全部离线测试       1838 passed
固定案例           1791 passed
Windows            590 passed, 1 skipped
前端语义测试       238 passed
浏览器及本地 API    224 passed
ruff               All checks passed!
pyright            0 errors, 0 warnings, 0 informations
```

前端构建、完整 wheel 构建及仓库外安装也通过。[完整 CI](https://github.com/FangWHao/ResearchGraph/actions/runs/38405168917)、[PR CI](https://github.com/FangWHao/ResearchGraph/actions/runs/38405177991)。PR 的首次 Windows 检查触及 10 分钟任务时限，单独重跑后通过；已通过的后端没有重跑。

## 实际使用边界

按用户明确授权合入 main，供实际试用。试点项目 正式参考、找回率／解释质量／成本、个人客户端全事件、完整研究 I/O、规模及长期使用仍待真实验收；合并不表示完整 M0–M4 已验收。真实资料、密钥、数据库及生成缓存不提交。

详见[使用说明](https://github.com/FangWHao/ResearchGraph/blob/main/README.md)、[需求落实情况](https://github.com/FangWHao/ResearchGraph/blob/main/docs/REQUIREMENTS_STATUS.md)和[开发进度](https://github.com/FangWHao/ResearchGraph/blob/main/docs/PROGRESS.md)。
