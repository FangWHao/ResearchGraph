显式登记的Codex／Claude会话可以整理成带原文引用、完整分析范围及发生／已知双时间的研究决定史。本地研究图、时间线、检索、问答和历史导出保留候选、撤回、阴性结果和人工更正；模型输出始终为候选，确认来自人工或独立原话规则。

扫描、离线钩子、影子快照、提取、跨会话关联及概览沿持久队列接续。完整模型请求发送前实测token，默认128000可调；项目默认禁止外发，截断、超预算和执行器压缩结果作废。保存原件的注入来源与模型记录来源资格在自动输入和状态计算前核对，旧记录和人工审核只追加保留。五个只读MCP工具、两个客户端接入包共用证据合同。

完整研究图按固定项目、双截止和修订读齐分页后才允许折叠；过程组保留原节点、方向、端点、端口、历史及证据，展开恢复同修订原位置。整项目清除具备预览、独占执行、中断恢复和重导入屏障，保留其他项目与共享字节。普通NTFS存储、快照、完整摘要和独占ZIP通过原生父目录句柄检查；目录替换或读取变化时作废，失败仅清理本次文件。摘要缓存使用真实ChangeTime；POSIX导出同样核对祖先变化。

默认工作集按完整范围保留最新合格卡片，并在入库前核对所有已有对象引用端点；卡片和分组共同受3000token预算约束，补查最多两轮。不会从路径、唯一旧范围或模型声明推断整会话范围，中文提示词明确UTF-8读取。

本批进一步修复独立原话确认依赖版本写入顺序的问题。规则使用每个完整范围最新的有效版本，被替换／驳回／来源不合格的版本不能提供标签。同名范围歧义、同ID多范围均保持候选；明确完整范围、唯一范围ID和独立人工定义仍可确认。explicit-user-v2进入持久配置与pass2调用身份，避免旧ok调用绕过新判断，pass1缓存保留。原模型、原件与人工审核不改写；旧v1证明的历史资格复核仍待实现。

## 当前验收

```text
uv run pytest -q                         1395 passed in 437.96s (0:07:17)，退出0
uv run pytest 关联六组 -q                151 passed in 45.10s，退出0
uv run ruff check rg tests scripts        All checks passed!，退出0
uv run pyright rg                        0 errors, 0 warnings, 0 informations，退出0
uv run rg --help                         退出0
uv sync --locked                         Resolved28/13ms，Checked27/53ms，退出0
仓库外Windows3.12.3 -I实际安装           100 passed in 21.24s，退出0，CP936、utf8_mode0
```

最终wheel146成员／141运行资源，源码、包与原生Windows独立安装逐字一致；实际凭据匹配0、私有文件0。Windows持续验收包含存储／清除／快照／导出61项、范围25项和规则14项，未增加重试、放宽超时或全局切换编码。旧原生安装包14个固定公开案例为7 failed／7 passed，涵盖顺序依赖、多范围ID、无来源标签、已替换版本及旧调用缓存；原始失败日志保留。公开fixture固定实体ID，生产UUID及脱敏不改。

前批d482e5f推送与PR双CI实际成功：完整后端1381、golden1334、前端182、浏览器166、构建245模块、Windows86及静态和官方协议检查。本批前端不改，沿320项冻结及八文件独立恢复。最终HEAD的推送／PR持续验收分别执行完整后端、golden、前端、浏览器、构建、静态、两代SDK、两客户端配置、物理MCP、运行报告MCP及100项Windows案例；结果见PR检查，草稿保持未合并。

证据见[独立原话确认验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/独立原话确认版本与范围验收_20261011.md)、[默认范围工作集验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/默认范围工作集与引用验收_20261011.md)、[原生快照摘要与导出验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/原生Windows快照摘要与导出验收_20261011.md)、[模型记录来源资格验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/模型记录来源资格验收_20261011.md)及[需求落实与剩余项](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。

完整M0–M4尚未验收，旧v1规则证明的历史资格复核仍待实现。Atlas正式人工参考、真实范围语义／提取质量／找答案时间、全部工具和采集版本、完整实际运行I/O、真实钩子与规模、两周使用仍待落实。普通剩余设计按用户自主授权继续开发。原生末级符号链接权限不足，该分支、所有文件系统和断电持久性未完成物理验收。整项目清除覆盖管理目录，外部原日志、工作区及用户保存／分享的副本保留。

本批仅使用公开合成资料，没有读取完整真实会话、调用远程模型、删除真实项目或安装日常钩子。真实资料、数据库、截图、缓存和密钥不入仓库；固定输出不证明真实质量达标。
