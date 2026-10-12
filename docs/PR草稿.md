本地 Claude Code／Codex 会话现在可以形成带原文、双时间和审核记录的研究决定史。持续采集、离线钩子、影子快照、持久提取队列、研究图、检索、问答、历史导出及六个 MCP 工具已接通；模型只写候选，科研确认仍受人工或独立原话规则约束。

项目可以一次开启未来记录自动归属。迁移35只处理此后新导入且未人工指定项目的记录，按已登记本机目录归属；后续登记根和已授权工作树发现根沿用许可。未知格式、目录冲突、隐私断点及未完成旧库观测进入收件箱；唯一工具结果沿请求归属。人工归属优先，原始事件、旧归属和科研状态不回填。健康页、严格鉴权 HTTP 与 CLI 共用修订和完整登记摘要核对，保存回执与实际新记录归属分开显示。

已有科研记录的物理片段支持明确迁移。预检涵盖原件、模型工作集、上下文、人工记录、原生运行与编辑、文件快照的完整依赖；当前查询使用有效归属，历史查询恢复当时归属。迁移33采用追加账本，迁移34保留原始别名与全部 L0 字段，同时允许具有完整解析和身份证明、当前已失效的别名分别清除。整项目清除保留其他项目仍使用的证据，共享影子 Git 按确切引用回收，中断后可继续。

完整研究图在同一读取快照内批量核对历史归属；2033条合成记录固定双截止下逐字一致，SQL 从33467降至2346。图在读取或布局未完成时暂禁选择与折叠，完成后恢复。普通及历史详情读取失败明确显示原因并提供主动 GET 重读；关闭、切换项目或读取条件后，迟到响应不能覆盖新详情或撤销新项目鉴权。目标场景为本机Windows／WSL／Linux采集与桌面浏览器查看。

## 验收

完整后端、仓库外 Windows 交付安装、前端单元与生产 API 浏览器验收均对应当前冻结文件实际通过。

```text
uv run pytest -q                         1689 passed in618.11s，退出0
uv run ruff check rg tests               All checks passed!，退出0
uv run pyright                          0 errors, 0 warnings, 0 informations，退出0
Windows3.12.3 -I 仓库外安装十八组          405 passed, 1 skipped in110.33s，退出0
cd web && pnpm test                     232 passed／24文件／1.85s，退出0
cd web && pnpm exec tsc --noEmit         退出0
cd web && pnpm build                    259模块／1.60s，退出0
cd web && pnpm test:browser              217 passed(5.9m)，退出0
```

交付 wheel 的168成员／163运行资源与源码及 Windows 安装逐字相同，CP936／utf8_mode0保持；真实密钥与私有文件零匹配。新25项自动归属固定案例覆盖原子回滚、旧库观测缺口、人工优先、工具归属冲突、分页过期、备份与共享清除。前端新增6项自动归属和6项详情失败专项使用真实生产 API；超时、零重试及原测试断言保留。云端单独固定案例、两代 MCP／两种客户端及同提交双 CI 由[检查页](https://github.com/FangWHao/ResearchGraph/pull/1/checks)核对。

完整浏览器在后端与原生安装验收成功后串行运行；详情修复后的217项全部通过，包括原笔记用例。当前策略9文件和详情2文件已分别独立恢复逐字核对；完整提交另做全历史恢复与秘密检查。CLI真实策略保存及未来归属实际退出0、模型调用0。

## 保留的边界

本地合成工程验收不代替真实科研质量。Atlas 现有48条候选参考尚无正式人工验收回执；实际参考与问题核对、完整客户端采集、真实 I/O、钩子性能、质量与成本、规模和两周使用仍待验收。跨设备、HPC 和运行包装依 §3 留在 M5 以后。完整 M0–M4 未宣布完成；真实资料、凭据、数据库、缓存与截图不提交，草稿保持未合并。

证据见[未来记录自动归属验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/未来记录自动归属验收_20261012.md)、[科研依赖迁移验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/科研依赖明确迁移验收_20261012.md)、[失效原生别名清除验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/失效原生别名清除验收_20261012.md)和[需求落实及剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。
