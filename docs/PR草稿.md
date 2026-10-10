显式登记的Codex／Claude会话原先缺少可定位的决定、范围和历史版本入口，来源清理与fork／resume副本还可能丢失父线程依据。本次增加Python后端与React本地工作区，把增量日志整理成保留原文引用、完整范围和双时间的研究决定史；正文去重与来源父声明分别保存，缺口和冲突明确显示。

扫描器、离线钩子、影子快照、候选提取、跨会话关联及概览由持久队列接续。每次完整模型请求先实测token，默认128000可调，项目默认禁止外发；模型输出始终candidate，截断、超预算或执行器压缩结果作废。规则原话确认和可选人工复核独立处理；MCP沿用户选择取消固定1500token返回上限，保留分页、原文字节窗口和本地精确计数。

Codex以session_meta直接父线程声明核对，根session_id及forked_from_id不替代直接父；Claude逐物理记录保存uuid、parentUuid和严格布尔isSidechain，多块共用记录锚点，副本保留各自声明。父迟到自动恢复，冲突、歧义、循环与缺父不猜。Claude同来源先核对，再回退同项目唯一等价组；来源上下文和64步上限明确停止，20个父引用与完整计数分开。跨项目不继承归属、不落父外键、不公开父编号或原文引用。

迁移22与23仅增加元数据表／核对游标，不读原日志。两工具每来源每批从已存L0核对至多256条；源文件消失、旧轮换实例和旧备份恢复后仍可自动补记。批内观察与游标一起提交，损坏对象留缺口计错并继续其它来源，新尾不跳旧缺口。Codex已有头不重读，本会话或既有全库身份核对未齐时不发布唯一关联；已经观察的非法声明和冲突保留。原文页显示核对状态，旧响应覆盖未知时停父导航。查询仅使用同一SQLite读取快照，不触发补记或模型调用，不改变原L0及时间。

工作区提供原文检索、问题／问答／决定、复核、双时间线、研究图、物理版本／运行图、运行证据和历史ZIP。四类状态独立，UTC微秒保留冲突和未知时间／范围，图折叠及显示版本不改事实；手机项目切换、草稿和迟到响应隔离。来源类型／工具版本、钩子错误和失败队列有持久账本，健康页不把未观测补零。整项目清除覆盖当前管理目录，提供只读预览、独占执行、中断恢复、无原文回执和重导入屏障；共享字节及其他项目保留，未知归属拒绝清除。服务只监听127.0.0.1并检查令牌／Host／同源，五个只读MCP和两种客户端接入包不改个人设置，不执行会话中的命令。

## 当前验收

```text
uv sync --locked                       Resolved28/1ms；Checked27/47ms
uv run pytest -q                       1123 passed in489.71s(0:08:09)
uv run pytest tests/golden -q           1076 passed in474.66s(0:07:54)
uv run ruff check rg tests scripts      All checks passed!
uv run pyright                         0 errors,0 warnings,0 informations
uv run rg --help及session-parent --help  均退出0
cd web && pnpm test                    147 passed/18files/905ms
cd web && pnpm build                   241modules/1.49s
cd web && pnpm exec tsc --noEmit        退出0
原父线程及自动补记八项浏览器专项           8 passed(21.5s)
隔离Python3.12.3安装的父线程／补记案例      84 passed in36.87s
cd web && pnpm test:browser             133 passed(2.1m)
```

两组后端全量并行，不作为性能基准；两组和浏览器专项结束后，父任务串行完整133项浏览器，原129项保留，2.1m全部通过。新增29项固定案例验证18/20/21/22旧库与源消失恢复、末尾冲突／晚身份、连续游标、锁占用、对象缺失／摘要不符／压缩损坏、批内回滚／实际进程退出79、旧备份与轮换、CLI／令牌HTTP只读快照以及临时跨项目清除。135包成员、130运行资源与源码及隔离安装逐字一致，实际目录密钥匹配0、私有文件0。

界面新增四项语义回归与四项真实浏览器，原父线程四例、取消后的迟到200／401及其余已有案例保留。真实扫描在合成原日志删除后两批核对776／12条，不以部分资料宣称唯一；原文UTF8摘要与原L0时间保持。手机整卡／标题／声明按钮的严格位置检查通过，父实看桌面及390七图；43项源码／证据冻结全部核对，九文件恢复包独立解包逐字一致。最初失败与复验详情保留在中文验收，超时／重试未调整。

证据见[Codex自动补记验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/Codex已存父线程自动补记验收_20261011.md)、[补记界面验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/父线程自动补记界面验收_20261011.md)、[Claude父链验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/Claude事件父链验收_20261011.md)、[管理目录整项目清除验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/管理目录整项目清除验收_20261010.md)及[需求落实与剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。

## 保留边界

草稿待评审，完整M0–M4尚未完成。整项目清除覆盖当前管理目录；外部原日志、研究工作区和用户保存／分享副本沿既有保留边界。全部真实工具版本、嵌套L1完整映射、跨项目片段优先级、自动worktree发现／跨设备路径、钩子无法写日志时的漏报、完整实际I/O、影响传播／note合同、真实客户端／实际规模／原生Windows及Atlas正式人工参考仍待落实。

本阶段没有删除真实项目、读取整份真实会话或调用模型；公开源码、中文说明和合成案例，凭据、真实会话、数据库、截图及缓存不入仓库。提交后维护既有草稿PR #1，核对当前HEAD的推送／PR两种CI与独立恢复包，不预报这些结果。
