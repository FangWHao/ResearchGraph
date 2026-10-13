本机 Codex／Claude Code 的会话现在可从项目接入、持续采集、提取到桌面查询连起来使用。`client-install` 合并项目技能与 MCP，保留其它配置并拒绝覆盖冲突；`start` 一次管理界面及扫描、spool、派生、文件处理，明确加 `--extract` 后串接现有提取／关联／概览。默认离线，模型调用继续执行项目许可、真实 token 计数、128k 可调预算和每日额度。

新增 `impact` CLI 与认证 `GET /api/impact`，按对象或文件版本查询下游核对项和关系路径。项目、完整范围、发生／获知截止及修订固定，确认关系与候选／报告依赖分开；比较汇合仅沿选中输入，不沿 same_topic 等检索关系传播。两集合独立分页，深度或数量达到上限明确不完整，不改变任何科研状态、不推定真实 I/O 完整性。桌面仍默认研究问题／研究图／研究问答，影响入口只放在已有详情折叠区，无新增主导航。

原生 Windows Codex 钩子已补齐固定系统 PowerShell 编码参数执行；含中文、空格、引号及 shell 符号的隔离工作区实测四类回调齐全。Windows 高频状态读取阻止原子替换的问题已修复，使用共享删除读取、辅助回执失败隔离及非缓冲控制管道；工作进程空闲释放数据库，监督器接续实际退出的进程，父管道断开后退出。配置存在与客户端实际生效分别报告，不改个人设置或信任。

## 验证

按用户要求只运行本地相关检查，完整回归交现有 CI；没有删旧用例、放宽超时或增加失败跳过。

```text
最终新功能三组 pytest                   28 passed in 8.87s，退出0
阶段七组相关 pytest                     112 passed in 44.62s，退出0
ruff check rg tests scripts             All checks passed!，退出0
pyright                                0 errors, 0 warnings, 0 informations，退出0
start／client-install／impact --help     退出0
Windows3.12.3 -I 仓库外安装四组          52 passed, 3 skipped in 12.27s，退出0
原生Windows Codex0.162.0                 5.656秒，四回调齐全，退出0，远程模型0
真实CLI／HTTP合成日常启动                事件1到2，模型0，Ctrl+C退出0
pnpm test                              235 passed／25文件／1.58s，退出0
pnpm exec tsc --noEmit                  退出0
pnpm build                             262模块／1.41s，退出0
影响专项浏览器                          2 passed in 12.2s，退出0
```

Windows三跳过包含原有平台条件及符号链接权限不足，不算通过。浏览器专项通过实际本机 HTTP 验证报告依赖分级、固定双截止分页、GET-only／图逐字不变及迟到401隔离。Windows首次两失败、诊断阶段进程退出、浏览器缺依赖的首次失败均保留；最终修复后才计通过。此前84a5220同HEAD的PR38341459762与push38341455018已实际成功，但不能代替当前提交；当前完整后端／固定案例／浏览器以[检查页](https://github.com/FangWHao/ResearchGraph/pull/1/checks)的实际终态为准。

## 实际验收边界

工程合成结果不代替科研效果。Atlas参考候选未取得正式人工参考回执；真实找回率、模型解释质量与成本、个人接入、全八类钩子、真实I/O、提示时刻p95、规模和两周使用仍待实际验收。跨worktree身份及完整物理历史diff保留限制；跨设备、HPC及运行包装留在M5以后。资料、密钥、数据库及缓存不提交，草稿保持未合并，未宣布完整M0–M4验收完成。

说明见[日常使用与影响查询验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/日常使用与影响查询验收_20261013.md)、[使用说明](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/README.md)及[需求落实情况](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。
