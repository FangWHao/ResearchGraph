本地 Claude Code／Codex 会话可形成带原文、双时间和审核记录的研究决定史。流式持续采集、离线钩子、影子快照、持久提取队列、研究图、检索、问答、历史导出及 MCP 已接通；模型只写候选，确认来自人工或严格独立原话规则。目标场景是本机 Windows／WSL／Linux 与桌面浏览器。

桌面默认只显示研究问题、研究图、研究问答，原文搜索位于顶部，八个低频工具收进更多工具；待复核提示仍可见，图未完整加载时明确只统计已载入记录，工具深链自动展开。问答隐私摘要碰巧命中手机号或十八位数字形状时，预览与实发保持一致，正文和范围继续遮盖。

已登记项目可用 hook-install 安装项目本地配置，保留原设置和其它钩子，检查模式不写客户端文件，重复调用不重写。配置损坏、链接、同名定义冲突或检测到并发编辑时拒绝覆盖。固定解释器隔离工作区导入，入口异常也静默退出0；明确范围外事件不采集，未知与超限输入保留原件。安装与生效分别报告，不改个人配置或持久信任，不启用 ResearchGraph 开发钩子。原生 Windows Codex 的命令 shell 尚未验收，明确拒绝安装，WSL 可用；Claude 使用执行文件及参数数组。

真实原生对照发现 Codex 退出时取消异步 Stop，导致回执丢失。Stop／SessionEnd 改为两秒预算同步落盘，后续扫描仍在后台，不联网、不读会话、不调模型；执行规格、示例和安装定义一致更新。两个 WSL 原生客户端已在隔离合成项目中实际保存开始、提示、回复完成、会话结束四类事件，Git index 和研究数据库未改变。该测试不修改个人信任，不能冒充个人客户端已启用。

项目可一次开启未来记录自动归属；旧日志归属与科研状态不回填。具有完整依赖的物理片段支持明确迁移，当前查询按有效归属、历史查询恢复当时归属；整项目清除保留其它项目仍使用的对象和确切影子引用，中断后可继续。完整研究图批量核对历史归属，合成2033记录固定双截止下逐字一致，SQL 从33467降至2346。普通及历史详情失败明确提示并支持主动GET重读，迟到响应按项目、记录和读取条件隔离。

## 验收

当前后端及仓库外Windows交付安装均对应最终冻结文件实际通过。前端本轮未修改，源码与c3baf07逐字相同，沿用该版本已通过的实际输出。

```text
uv run pytest -q                         1719 passed in627.85s，退出0
uv run pytest 两组钩子／快照专项           50 passed in15.59s，退出0
uv run ruff check .                      All checks passed!，退出0
uv run pyright                           0 errors, 0 warnings, 0 informations，退出0
uv run python scripts/check_native_hooks.py  两个原生客户端四回调齐全，退出0，远程模型0
rg控制台 init／登记／检查／安装／重复       五次退出0，八类定义，重复字节不变
Windows3.12.3 -I 仓库外安装二十一组        463 passed, 3 skipped in137.37s，退出0
c3baf07：pnpm test                        232 passed／24文件，退出0
c3baf07：pnpm exec tsc --noEmit            退出0
c3baf07：pnpm build                       259模块，退出0
c3baf07：pnpm test:browser                 220 passed，退出0
```

最终165资源与170成员的wheel及Windows安装逐字相同、真实密钥及私有文件零匹配；CP936／utf8_mode0保持。三跳过包含新增两项建立符号链接权限不足与原有一项，不算通过。新增27项黄金案例覆盖配置丢失、重复定义、并发编辑、跨项目采集、未知原件及导入失败；Windows CI 增加该组，原超时与全部既有步骤保留。完整后端包含1672项固定案例，单独固定案例命令与当前提交双CI以[检查页](https://github.com/FangWHao/ResearchGraph/pull/1/checks)实际终态为准，不借用c3baf07绿灯。

异步Stop原生红灯、专项首轮两项失败和脚本入口／空服务表错误均保留，修正后才记成功。前端与运行代码的冻结、交付包和完整Git历史独立恢复按实际回执核对。

## 保留的边界

本机原生会话独立归档已验收，WSL843／Windows31文件完整SHA256恢复一致；WSL保留期3650天已核对，活跃源后续追加不属于捕获版本。归档、真实文件、逐文件清单不提交。

工程合成验收不代替科研效果。Atlas48条候选参考尚无正式人工验收回执；个人真实钩子启用、全八类事件、真实I/O、提示快照p95、强杀、研究质量与成本、规模和两周使用仍待验收。跨设备、HPC 和运行包装依§3留在M5以后。完整M0–M4未宣布完成，资料／凭据／数据库／缓存不提交，草稿保持未合并。

证据见[项目钩子接入验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/项目钩子接入验收_20261013.md)、[桌面入口精简验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/桌面入口精简验收_20261013.md)、[未来记录自动归属验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/未来记录自动归属验收_20261012.md)和[需求落实及剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。
