# PR 草稿

建议标题：构建本地研究决定史、保留原文父链与整项目清除

原始Codex／Claude会话缺少可定位的决定、范围和历史版本入口，fork／resume正文副本还可能携带不同的父链声明。本次增加Python后端与React本地工作区，把显式登记的增量日志整理成保留原文引用、完整范围和双时间的研究决定史；正文去重与来源父链分别保存，缺口和冲突明确显示。

扫描器、离线钩子、影子快照、候选提取、跨会话关联及概览由持久队列接续。每次模型完整请求先实测token，默认128000可调，项目默认禁止外发；模型输出保持candidate，截断、超预算或执行器压缩结果作废。规则原话确认与可选人工复核分别处理；MCP沿用户选择取消固定1500token返回上限，保留条目分页、原文字节窗口与本地精确计数。

原生父链现在保留两种工具各自的依据：Codex以session_meta直接父线程声明核对，根session_id及forked_from_id不替代直接父；Claude逐物理记录保存uuid、parentUuid及严格布尔isSidechain，多块共用记录锚点，fork／resume别名保留各自声明。同来源先核对，再回退同项目唯一等价组；父迟到自动恢复，歧义、来源矛盾、循环和缺父不猜。Claude祖先按来源实例及UUID组合判循环，多个来源上下文或64步上限停止；至多20个父引用与完整计数分开。跨项目不继承归属、不落父外键或返回父原文引用。

迁移22只建表，不读原日志。Claude旧元数据每来源每批从已存L0补记至多256条，原件消失仍可恢复；批内观测与游标原子提交，损坏对象留缺口计错，压缩解码异常规范到扫描循环既有错误处理，继续其它来源，新尾不跳旧缺口。补记未齐时不判唯一父或无循环。只读CLI／令牌接口及原文页共用SQLite读取快照，查询不补记、不读取原日志、不调用模型。新表纳入备份及管理目录整项目清除，原L0和时间不改。

工作区还包含原文检索与字节证据、问题／问答／决定、复核、双时间线、研究图、文件版本／运行图、运行证据与历史ZIP。文件比较区分实际字节和工具候选全文；UTC微秒的采用／证据状态保留冲突、未知时间及未知范围。采集类型／工具版本、钩子错误和失败队列有持久账本，健康页不把未观测补成零或把报告数当完整失败总数。已有项目可追加仓库、worktree、数据及别名根目录，相同远端不合并项目；390宽项目切换和迟到响应保持隔离。

管理目录整项目清除提供只读预览、独占执行、中断恢复、无原文回执和重导入屏障，共享字节及其他项目保留，未知归属拒绝清除。服务仅监听127.0.0.1，检查令牌、Host及同源；五个只读MCP和两种客户端接入包不修改个人设置。读取和恢复都不执行历史会话中的命令。

## 当前验收

```text
uv sync --locked                       Resolved28/1ms；Checked27/58ms
uv run pytest -q                       1094 passed in469.39s
uv run pytest tests/golden -q           1047 passed in454.13s
uv run ruff check rg tests scripts      All checks passed!
uv run pyright                         0 errors,0 warnings,0 informations
uv run rg --help及event-chain --help     均退出0
cd web && pnpm test                    143 passed/18files/978ms
cd web && pnpm build                   241modules/1.51s
cd web && pnpm exec tsc --noEmit        退出0
Claude父链4项真实浏览器专项               4 passed (15.0s)
隔离Python3.12.3安装的新父链案例           65 passed in32.14s
cd web && pnpm test:browser             129 passed (2.0m)
```

后端两组全量并行，不作为性能基准；此前两组结束后才串行浏览器。终检再补真实循环的损坏压缩对象回归，后端65项／全量／隔离安装重新验收；前端合同及33项冻结不变，保留已完成界面验收，新HEAD两种CI还会检查完整浏览器集成。安装包135成员、130运行资源与源码及隔离安装逐字一致，实际目录密钥与私有文件匹配0。新增65项固定案例覆盖来源变体／正文冲突、迟到父、多块、严格旁支、同源与跨源循环、部分引用、64步、事务／实际退出79、升级回滚、有界补记／源消失／对象损坏／真实循环继续其它来源、旧备份、只读CLI／HTTP并发快照、跨项目隔离及临时整项目清除；既有父线程、钩子与采集案例保留。

新增8项前端语义测试及4项真实专项首次全部通过，无超时或重试变动。真实扫描验证260条旧记录两轮补记、积压不判唯一父、fork别名仍有独立源声明、迟到父和21等价副本；导航只传事件ID，并核对原文UTF8 SHA。父在后端和专项结束后串行完整129项浏览器，原125项完整保留，2.0m全部通过；33项源码／证据冻结及九文件恢复包逐字一致。390取消后的真实迟到200／401不覆盖新证据；错误身份只在测试路由注入。父任务实看桌面关联／积压／副本和390关联／引用卡／跨项目六图，引用卡完整位于固定标题下，页面与抽屉无横向溢出。

证据见[Claude父链验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/Claude事件父链验收_20261011.md)、[Claude父链界面验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/Claude事件父链界面验收_20261011.md)、[Codex父线程验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/Codex父线程关联验收_20261011.md)、[管理目录整项目清除验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/管理目录整项目清除验收_20261010.md)及[需求落实与剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。

## 保留边界

草稿仍待评审，完整M0–M4尚未完成。整项目清除覆盖当前管理目录；外部原日志、研究工作区和用户保存／分享的副本沿既有保留边界。全部真实工具版本、嵌套L1完整映射、跨项目片段优先级、自动worktree发现／跨设备路径、无法写错误日志时的钩子漏报、完整实际I/O、影响传播／note合同、真实客户端／实际规模／原生Windows及Atlas正式人工参考仍待落实。

本阶段没有删除真实项目或调用模型；提交公开源码、中文说明和合成案例，凭据、真实会话、数据库、截图及缓存不入仓库。维护既有草稿PR #1，提交后核对当前HEAD的推送／PR两种CI与独立恢复包，不预报这些结果。
