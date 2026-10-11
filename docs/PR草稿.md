显式登记的Codex／Claude会话可整理为带原文引用、完整范围及发生／已知双时间的研究决定史。本地研究图、时间线、检索、问答和历史导出保留候选、撤回、阴性结果与人工更正；模型始终写candidate，确认只来自人工或独立原话规则。

本批补上历史规则证明的独立资格。旧确认仍保留，自动采用／证据状态和模型输入先核对原确认当时已知的定义、完整用户原话与引用字节。没有保存范围筛选且存在范围歧义，或当时工作集缺失时，明确显示无法核对。新explicit-user-v3保存实际筛选、工作集ID和定义版本ID。来源与规则分别诊断；较新未能核对动作阻止退回旧采用，独立人工当前复核不改变历史截止视图。后台持久轮转自动补查和重查，查询只读匹配元数据；资格策略更新提取／关联／概览身份，pass1缓存保留。

前端保留确认徽章、原话与审核历史，独立显示规则待核对／无法核对／无效／不可用。七个公开合成项目通过实际本地API验证当前状态、证据、人工覆盖和390宽布局，数据库只读摘要相同。新增派生表纳入整项目清除；只有严格单字段非负数字全局游标可保留，未知任务继续阻断。关联及持久流水线中文提示词明确UTF-8读，原生CP936与utf8_mode0不变。

原有流式导入、离线钩子、影子快照、持久队列、有界提取、五个只读MCP工具、两客户端接入包及完整研究图继续验收。完整模型请求发送前实测token，默认128000可调，项目默认禁止外发；截断、超预算和执行器压缩结果作废。折叠保留原节点、方向、端口及证据，展开恢复同修订位置。清除具备预览、独占执行、中断恢复和重导入屏障，保留其他项目／共享字节及外部工作区／原日志／用户副本。

## 当前已退出验收

```text
uv run pytest -q                          1430 passed in450.60s(0:07:30)，退出0
uv run pytest 十二组关联 -q --tb=short    298 passed in84.13s(0:01:24)，退出0
uv run pytest 规则与清除 -q               55 passed in19.96s，退出0
uv run ruff check rg tests scripts         All checks passed!，退出0
uv run pyright rg                         0 errors, 0 warnings, 0 informations，退出0
uv run rg --help                          退出0
uv sync --locked                          Resolved28/1ms，Checked27/41ms，退出0
仓库外Windows3.12.3 -I实际安装            135 passed in25.83s，退出0，CP936／utf8_mode0
cd web && pnpm test                       198 passed／20文件／1.07s，退出0
cd web && pnpm exec tsc --noEmit           退出0
cd web && pnpm build                      245模块／1.13s，退出0
cd web && pnpm test:browser 规则专项       4 passed(21.0s)，退出0
cd web && pnpm test:browser               170 passed(4.6m)，退出0，实际进程281.008s
```

148包成员／143运行资源与源码及实际安装逐字一致，实际凭据／私有文件0，wheel SHA bd24e2aee90deff2251184da0277fd2dfc3477b8ca91200b7fedbccd7b31e8dd。前端153项稳定证据冻结及七文件独立恢复已由父核对，六图已原尺寸查看。原生首轮1 failed／134 passed和前端两轮语义红灯保留，未扩大超时、增加重试或全局切换编码。完整后端1430项与完整浏览器170项实际通过，完整浏览器在后端退出后串行运行。首轮浏览器缺本机libasound.so.2，实际中断退出130，失败证据保留；只修执行环境，不调产品、超时或重试。

基线e47134b推送／PR双CI均实际成功：完整后端1395、golden1348、前端182、浏览器166、构建245模块、Windows100，各30步骤／14类检查通过；494文件全历史独立恢复相同，凭据及私有路径0。本批精确草稿、独立恢复及两类持续验收按对应提交核对，机器结果保存在本机缓存；草稿保持未合并，不把基线结果冒充本批终态。

证据见[历史规则证明验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/历史规则证明资格验收_20261011.md)、[独立原话确认验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/独立原话确认版本与范围验收_20261011.md)及[需求落实与剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。

旧证明自动资格已实现，不能补造旧日志缺失材料。Atlas正式人工参考、真实范围语义／提取质量／找答案时间与成本、完整采集版本／实际运行I/O、实际钩子／规模及两周使用仍待落实，完整M0–M4尚未验收。原生末级符号链接权限分支、其他文件系统和断电持久性仍保留物理验收边界。上述合成通过不证明真实研究质量。

本批仅使用公开合成资料，未读取完整真实会话、调用远程模型、删除真实项目或安装日常钩子。真实资料、数据库、截图、缓存及密钥不提交。
