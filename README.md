# ResearchGraph

把 Codex / Claude Code 的本地会话整理成可查证的研究决定史。

当前入口是本文件。完整规格见 [执行规格](docs/ResearchGraph_执行版_v2.md)，当前开发范围见 [需求落实情况](docs/REQUIREMENTS_STATUS.md)，接续工作先读 [开发进度](docs/PROGRESS.md) 最后 40 行与 [开发决定](docs/DECISIONS.md)。

当前交付包含研究记录后端与本地界面：流式导入、持久来源轮询与 spool 消费、只写原件的钩子、影子 Git 快照、原文对象库、检索、前置实测 token、有界提取、精确字节定位、跨会话链接、结构化概览、独立原话规则确认、人工复核与修改，以及问题、时间线、证据、健康和研究图页面。**尚未完成整个 M0–M4，也尚未通过 Atlas 的真实效果验收。** 2026-10-09 用户授权前端子 agent 提前开发界面；真实质量门槛继续保留。

2026-10-09 用户确认将完整请求的输入上限改为 **128,000 token，允许调高**。这是对原规格 §7.2 的 24k 上限的明确调整，理由和边界见 [开发决定](docs/DECISIONS.md)。源规格保留原文。

## 环境与验收

使用 Python 3.12。依赖固定在 `uv.lock`；先安装 uv。GitHub 的“开发验收”工作流在推送与 PR 时运行后端、前端和真实本地 API 的合成验收，不需要 API 密钥。

```bash
uv sync --locked --python 3.12
uv run pytest -q
uv run pytest tests/golden -q
uv run ruff check rg tests scripts
uv run pyright rg
uv run rg --help
```

前端使用 Node 24.21.0、pnpm 12.10.1，依赖固定在 `web/pnpm-lock.yaml`：

```bash
cd web
pnpm install --frozen-lockfile
pnpm test
pnpm build
pnpm exec playwright install --with-deps --only-shell chromium
pnpm test:browser
cd ..
```

浏览器测试临时创建合成库，不读取真实会话与密钥。当前命令输出和可验收范围见 [前端与本地 API 验收](docs/acceptance/前端与本地API验收_20261009.md)和[前端续开发验收](docs/acceptance/前端续开发验收_20261009.md)。

本机可用 `.tools/bin/uv`；该工具及 `.venv`、缓存、凭据和私有材料均不进入仓库。执行规格保留在 Markdown 中，本地 Word 设计书不纳入公开提交。仓库沿用 [Apache-2.0 许可证](LICENSE)。

## 打开本地界面

从仓库根目录完成上面的 `pnpm build`，然后启动：

```bash
uv run rg --data-dir /tmp/rg-demo serve --port 8787 --open
# 使用同一个数据目录，也可以直接打开复核页。
uv run rg --data-dir /tmp/rg-demo review --open --port 8787
```

服务只监听 `127.0.0.1`。终端返回带本次随机令牌的浏览器链接；页面取得令牌后清除地址中的片段，后续请求同源 API。界面读取指定数据目录，不会自动导入 Atlas。问题页、队列、时间线、健康、搜索与图均可打开记录的原文窗口；复核支持确认、驳回、按片段批量确认，以及保留旧记录的人工修改。

采集健康页分别显示全库来源与提示队列、当前项目的快照缺口，以及模型阶段的用量观测。未完成提示不代表进程仍在运行；快照遗漏、异步时点和元数据缺失可能重叠，不能由这些统计推算完整版本或成功数量。缺少记录时显示未知，真实零值仍为零。

前端构建目录默认是当前工作目录下的 `web/dist`。Python wheel 包含后端和 API，静态资源另行构建；安装 wheel 后，使用 `serve --web-dir /绝对路径/web/dist` 指定它。Vite 开发服务器用于界面开发，实际数据复核使用上面的同源本地服务。

## 本地导入与检索

```bash
uv run rg --data-dir /tmp/rg-demo init
uv run rg --data-dir /tmp/rg-demo project add 合成项目 --root /workspace/demo
# 以下 PROJECT_ID 替换成上一条返回的 project_id。
uv run rg --data-dir /tmp/rg-demo import fixtures/transcripts/claude --tool claude --project PROJECT_ID
uv run rg --data-dir /tmp/rg-demo search 暂缓
uv run rg --data-dir /tmp/rg-demo evidence 1
uv run rg --data-dir /tmp/rg-demo health
uv run rg --data-dir /tmp/rg-demo backup /tmp/rg-demo-backup
```

导入不调用模型。全文和两字以内的检索也不依赖模型。数据库存放的是原文对象的摘要与位置，源文件删除后仍可打开已复制的证据。重复导入已提交的源文件不会增加事件。


## 持续扫描与 spool

```bash
# 显式登记日志来源；不是项目代码目录，不从 cwd 猜项目。
uv run rg --data-dir /tmp/rg-demo scan --path fixtures/transcripts/claude --tool claude
# 以后复用登记的来源和已显式导入的文件，每两秒扫描一次。
uv run rg --data-dir /tmp/rg-demo scan --watch --interval 2
# 只在需要时显式重试失败提示。
uv run rg --data-dir /tmp/rg-demo scan --retry-failed
```

需要项目归属时，首次登记加 `--project 项目ID`；没有归属就进入收件箱，不按路径名称或钩子里的 cwd 推断。目录递归发现新 `.jsonl`，文件与目录来源持久保存；既有 import 文件也可继续扫描。不同解析器或项目的重叠来源不任意选择，符号链接不能借目录授权读取外部文件。每个来源文件和每次扫描各有进程锁，同数据目录只允许一个持续轮询进程。

扫描器独立补齐日志，不依赖钩子提示，也不联网或调用模型。spool 写入原始字节后先复制到对象库并登记不可变回执，再消费提示；日志游标提交后才确认。未归属、未出现的日志和忙碌来源保持等待；未知事件或坏 JSON 保留原件并记为失败。每次消费最多 100 条，以最近更新时间轮转，等待项不长期阻塞后续已就绪提示。失败默认不自动重试；`scan --retry-failed` 显式重试。

临时文件不消费；符号链接和超过 4 MiB 的提示原件保留，单次扫描输出 `spool_held_files`，不会标成成功。原件与回执已经提交后，即使队列文件缺失，恢复或 backup 的副本仍能继续消费；完成后只清理内容匹配的队列文件。`health` 的 `ingest` 提供登记来源、已知路径、回执、未完成和失败提示数；未提交消费记录不等于进程仍在运行。

扫描与消费验收见 [扫描与 spool 验收](docs/acceptance/M2扫描与spool验收_20261009.md)。工具运行映射与跨 worktree 自动归属仍待实现。

## 影子快照与钩子示例

```bash
# 使用已经登记的项目和根目录；不会操作用户仓库的 index。
uv run rg --data-dir /tmp/rg-demo snapshot --project PROJECT_ID --root /workspace/demo
# 实测快照引擎 p95；超过 300 ms 或任一次失败，后续钩子改用异步快照。
uv run rg --data-dir /tmp/rg-demo snapshot --project PROJECT_ID --root /workspace/demo --benchmark 20
# 将独立待登记快照复制入对象库并追加入库。
uv run rg --data-dir /tmp/rg-demo scan
# 输出可检查的 Claude/Codex 配置；目录必须尚不存在。
uv run rg --data-dir /tmp/rg-demo hook-config --output /tmp/rg-hook-examples
```

`uv sync` 提供 `rg-hook` 命令。`init` 在数据目录生成示例，`project add` 更新只读根目录清单；两者都不会修改个人工具设置或启用钩子。当前仍遵守 §14.8，M2 正式验收前不把钩子接入开发本项目的会话。以后启用时，Codex 需在 `/hooks` 信任定义，修改后重新信任；官方协议见 [Codex 钩子](https://learn.chatgpt.com/docs/hooks)和 [Claude Code 钩子](https://code.claude.com/docs/en/hooks)。Codex 的 SessionEnd 即使设为 async 也按客户端同步执行，入口仍只快速写入原件。

钩子原始字节先原子写入 spool，任何异常都退出 0，不输出会话上下文、不联网、不调模型、不读 transcript。UserPromptSubmit 仅拍明确登记根目录的快照；未登记或归属歧义保存原件并记录错误类名。异步执行的是隔离 Python 下的本软件入口，输入里的命令和项目里的同名模块都不会执行。快照单独进入待登记队列，因此原始提示先被确认也不会丢掉晚到的快照记录。

影子仓库位于数据目录的 `snapshots/项目ID.git`，使用独立 index，遵守 `.gitignore`、`.rgignore`。保留原始文件字节、可执行位及链接目标文字，链接不跟随。大于 5,000,000 字节的文件只记录路径、大小、修改时间；嵌套仓库也明确记录未捕获，后台摘要和运行映射仍待实现。超时、忙或读取期间变化没有有效 shadow_commit；遗漏、竞态和采集起止保存在追加元数据中。无法安全测定 dirty 时显示缺失，不运行过滤器或通过 status 重新读取未捕获文件。

本机公开源码的 20 次手工引擎基准 p95 为 1426 ms，全部成功；模式仍为异步。157 个路径的影子对象检查未发现实际密钥或私有目录，用户 index 一致。这是引擎基准，尚未验收真实 Claude/Codex 客户端整条钩子耗时。`health.snapshots` 区分 skipped、partial、async_race 和旧元数据缺失；日志错误类名没有被当成完整失败账本。备份避开影子仓库写入，保留队列链接而不读取其目标。完整证据见 [快照与钩子验收](docs/acceptance/M2快照与钩子验收_20261009.md)。

## 远程模型

用户指定的接口为 `https://api.deepseek.com`，模型为 `deepseek-flash`。密钥从 `RG_API_KEY` 或 `--key-file` 读取，不进入代码、日志或测试。

DeepSeek 的 OpenAI 输入计数入口实测返回 404。同一服务的 Anthropic 兼容计数接口可用，当前专用适配器让计数和生成使用同一消息协议，包含正文、指令和 schema 工具定义。通用 OpenAI Responses 适配器要求服务支持计数端点，计数失败就停止生成。

接口协议参考：[DeepSeek 的兼容接口](https://api-docs.deepseek.com/guides/anthropic_api/)、[OpenAI 输入计数](https://developers.openai.com/api/docs/guides/token-counting)。接口是否可用以本机实测为准。

真实项目默认 `remote_model_allowed=0`。计数本身也会发送文本，因此未授权时连计数也不发送。启用前先生成遮盖后的字段与样例预览：

```bash
uv run rg --data-dir /tmp/rg-demo preview 1 --output /tmp/rg-preview.json
# 人工查看预览，确认数据可外发后，使用返回的摘要：
uv run rg --data-dir /tmp/rg-demo project allow-remote PROJECT_ID --ack-preview PREVIEW_SHA256
uv run rg --data-dir /tmp/rg-demo extract --session 1 --estimate \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file api_key
uv run rg --data-dir /tmp/rg-demo extract --session 1 \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file api_key \
  --input-budget 128000 --scope dataset_version=data_v2 --scope analysis_step=cnv \
  --report /tmp/rg-候选报告.md
```

`--input-budget` 控制正文、重叠、工作集、指令和 schema 的完整输入总量，默认 128000；例如 `--input-budget 256000` 可以调高。默认片段内容分配为 120000，工作集与指令/schema 各 3000，另留 2000 封装余量；输出仍限 4000。每次生成前实测完整请求；DeepSeek 还通过 `/models` 核验输入预算加输出预算是否超过当前模型窗口。窗口信息缺失时停止生成。

完整上一用户回合优先作为重叠上下文。一个超长回合内部的延续片段保留最近工具调用与结果，或最近正文窗口，并记录未携带的上下文位置；不修改原文。重叠不计作新覆盖，每条新候选必须引用当前片段的字节窗口。完整上一回合超过所设预算时，保留它并拆片重试；仍放不下则进人工队列，不能靠静默截断标为完成。

`--scope` 可重复指定完整范围字段，工作集只选完全相同的范围，模型输出必须原样使用该范围。原话明确属于其他范围时进入待澄清项。不指定范围时保留各对象已有的 scope 作为检索线索，但不能视为已完成同范围筛选；程序不会从目录名猜测范围。工作集优先级为直接 ID、标签字面量 FTS、相同文件路径、本会话较早片段的候选，不用无关对象填满预算。

pass1 的字节范围由程序按原始 JSON 路径和 UTF-8 位置给出，模型只能选取已登记窗口。重复内容块、JSON 转义和工具输出尾部不会通过相同文字的首个匹配猜位置。程序同时记录规则与模型定位，pass2 只取回候选正文和有限工具原文。当前数据库版本为 6：版本 2 新增定位表，版本 3 新增链接进度表，版本 4 新增调用尝试账本，版本 5 新增提取片段计划，版本 6 新增扫描来源及不可变 spool 回执；升级在事务中执行，保留原始事件、定位和尝试历史。

追加会话内容后，重复运行 `extract` 只处理尚未登记的新事件和未完成片段。已完成回合仅作重叠上下文；同一回合晚到的助手回复和工具结果不会再次提取旧用户决定。排除记录与镜像不充当新的回合边界。片段的事件所有权、字节窗口和原始上下文保存在本地库中，重启沿用原计划；已完成子片段不重建，后续窗口仍留在覆盖账本。候选或无候选结果与片段完成状态同事务提交，进程在更新覆盖视图前退出也能离线恢复。

失败并进入人工队列的片段默认不自动重发；新增内容仍可继续处理，但旧缺口保留。显式加入 `--retry-failed` 可重试当前配置的人工失败片段，成功后关闭对应通知，失败历史不删除。调高输入预算、换模型、修改完整范围或人工审核会形成新配置并重新核对候选，已有人工确认不会被覆盖。旧版本缺少片段计划时，只有旧缓存与当前配置、完整原文前缀和项目尝试证明精确匹配，才继承完成状态；证明不足则重新提取候选。详见 [增量与断点验收](docs/acceptance/M1增量与断点验收_20261009.md)。

同一会话的提取按顺序执行，不同会话可以并行；同一项目的概览和链接分别互斥。每个模型任务也有独立的进程锁，重复请求在计数前返回“正在运行”，不会重复调用模型或占用额度。远程调用期间不持有数据库写事务，采集仍可运行。锁由系统在进程退出后释放，保留的锁文件不表示进程仍活着。

完整响应已经通过 schema 校验但尚未入库时，重启可复用该响应，再核对原文引用、范围和对象。事务内检查成功运行，避免两个写入者重复生成候选；不同输出必须使用不同运行记录。成功缓存不重新计数或查询模型窗口，服务暂不可用时仍能读取缓存。截断、污染、超预算和校验失败结果不作为成功缓存；进程中断且用量未知的发送尝试继续保留历史与预留。自动化范围见 [并发与缓存恢复验收](docs/acceptance/M1并发与缓存恢复验收_20261009.md)。

本版本的遮盖涵盖身份证、手机号、邮箱、常见密钥、路径用户名和 rg 注入块。程序生成的候选对、片段和对象标识符只在规定的元数据位置保持原值，避免摘要中碰巧出现手机号形状的数字导致引用失配；原文、标签、理由和范围仍照常遮盖。尚未实现完整临床编号配置和隐私清除流程，因此 Atlas 的真实会话目前只做本地统计和人工参考准备，不发送给远程模型。

仅发送内置合成会话的真实接口验收命令：

```bash
uv run python scripts/smoke_deepseek.py --key-file api_key
```

结果在 [接口验收记录](docs/acceptance/deepseek_smoke.json)。合成测试不能替代真实提取找回率和人工耗时。

128k 默认配置、回合重叠和显式范围的当前实测见 [新接口验收记录](docs/acceptance/deepseek_128k_smoke.json)。该测试实际输入约 0.5k–3.2k token，验证预算配置与流水线；不代表已测试 128k 满窗口的提取质量。

## 跨会话链接与概览

```bash
# 本地预览候选对；不联网，也不读两侧完整会话。
uv run rg --data-dir /tmp/rg-demo link --project PROJECT_ID --estimate
uv run rg --data-dir /tmp/rg-demo link --project PROJECT_ID --limit 50 \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file api_key \
  --scope dataset_version=data_v2 --scope analysis_step=cnv
uv run rg --data-dir /tmp/rg-demo overview --project PROJECT_ID \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file api_key \
  --output /tmp/rg-项目概览.md
# 加 --session SESSION_ID 生成指定会话概览。
```

候选对必须来自不同会话、完整范围相等，且标签 FTS 命中或存在相同路径、算法和摘要的文件版本；未知范围和未知文件版本不参与。检索线索不能证明两个对象相同。链接每次只输入两张结构化卡片与一侧有限原文，实测输入最多 3000、输出最多 1000，总量不超过 4000 token。模型可以判断没有充分关系，此时两个端点必须为 JSON `null`。合法关系只写成 candidate。独立尝试不能自动合并，非法端点、关系方向和失败结果不会入库。

`--limit` 限制本次新任务或显式重试的数量；已完成的候选对不占用额度，也不再计数或生成。重复执行同一条命令会继续后续候选对，新增对象也会进入调度。返回的 `processed` 表示本次尝试数，`cached` 表示跳过的已完成对，`has_more=1` 表示仍有待处理对。`has_more=0` 仍须结合 `skipped_failed` 和人工队列查看，不能把跳过的失败对视为成功。需要重试校验失败的对时加 `--retry-failed`，原人工任务会更新，避免重复创建。

每日额度不足或链接提供方不可用时返回 `paused=1`，当前对保留 pending；额度恢复或接口恢复后重复执行命令即可继续。同项目链接进程互斥，进程退出后系统释放锁，期间导入仍可写库。提取达到每日额度时也保留覆盖缺口和排队通知，不拆片或计成人工校验失败。模型、提示、schema、审核状态和实际输出上限变化会重新计算相应链接任务。

概览直接从 claims 分页生成，保留审核状态、范围与引用的记录 ID，不读取 L0 原文，不把前一页摘要交给下一页。文件标注“模型摘要”，包在 `<rg-context>` 中，重新导入时排除；不作证据，也不进入其他提取阶段。人工复核改变输入状态后，概览与链接缓存会重新计算。

合成接口测试：`uv run python scripts/smoke_derived.py --key-file api_key`。该脚本直接构造两条合成候选，测试链接与概览协议，不代表提取效果。最新结果见 [链接调度接口验收](docs/acceptance/deepseek_scheduler_smoke.json)，本轮完整记录见 [链接调度验收](docs/acceptance/M1链接调度验收_20261009.md)。上一轮的定位与概览验收另见 [定位、链接与仓库验收](docs/acceptance/M1定位链接与仓库验收_20261009.md)。

## 提取健康与每日用量

```bash
uv run rg --data-dir /tmp/rg-demo health --project PROJECT_ID \
  --day 2026-10-09 --daily-budget 50000 --limit 20 --offset 0
```

健康检查不调用模型。`extraction` 部分按项目和 UTC 日期统计调用尝试；原有导入总数仍为全库统计。每日用量也保持全库统计，`--daily-budget` 应与 worker 的配置相同，它是本次用于对照的上限。日期筛选只作用于模型用量，覆盖缺口始终反映当前状态；使用返回的 `next_offset` 读取下一页，缺口带事件 ID、会话 ID 和阶段，不包含原文。

实测利用率按实际完整输入计算；实际用量未知时使用发送前的实测输入。pass1/pass2 中超过 80% 预算的片段占比严格大于 10% 时报警，同一片段的补查和重试不重复计算片段数。校验拒绝率严格大于 5% 时报警，并分别列出引用失败数与整体校验拒绝数；这些统计不等于真实找回率或引用成功率。

尝试账本保留重试前的失败、预算、实际用量和结束原因，缓存命中不新增尝试。每日额度暂停与输入超预算分别显示；已尝试发送却拿不到实际用量时标为未结算，保守预留仍可能占用额度。缺少最终验收记录不代表进程仍在运行；旧数据库没有尝试历史时明确标为缺失，不补造用量或失败率。这里的费用按 §7.10 使用 token 额度，不推算货币金额。

本轮命令与接口实测见 [监控验收](docs/acceptance/M1提取监控验收_20261009.md)。

## 复核

```bash
uv run rg --data-dir /tmp/rg-demo review
# 使用当前 revision；actor 必须是人工身份。
uv run rg --data-dir /tmp/rg-demo review --claim CLAIM_ID --action confirm \
  --actor human:reviewer --expected-revision REVISION
```

人工复核追加动作，不修改原候选或原文。过期版本会拒绝写入。重新提取只能生成新候选，不能覆盖已确认内容。不同范围的决定状态分别计算。

§7.7 规则另行核对完整用户原话、原始字节和同范围对象身份。限定形式的明确采用、撤回、拒绝、refuted 与 all_required 可以追加 `rule:explicit-user-v1` 确认；模型原始记录仍为 candidate，不能凭模型填写的 user/explicit/true 自行确认。理由须来自原话或可核实的动作描述，同名对象、未知范围、条件句、局部引文和自编理由均保留候选。证明记录包含事件、字节范围、摘要和规则版本，见 [独立规则确认验收](docs/acceptance/M1独立规则确认验收_20261009.md)。

新模型候选若对应同一对象、类型与范围的人工确认，队列展示已有记录和字段差异；不会自动覆盖人工决定。人工修改追加 confirmed 的替换记录和审核动作，原记录与证据保留。发生时间缺失或同一时刻存在互相冲突的动作时，界面明确显示待核对，不用入库顺序推断研究决定。

## Atlas 试点

统计脚本只输出数字。历史会话不会被读进开发 prompt：

```bash
python -I scripts/measure_atlas.py --manifest private/atlas_sessions.json
uv run python scripts/prepare_atlas_pilot.py \
  --manifest private/atlas_sessions.json \
  --output /tmp/researchgraph-atlas-pilot-新日期
```

2026-10-09 的统计找到 505 个 Atlas 相关会话文件，3,781,566,087 字节、307,061 条记录、431 个压缩点。已从中挑选 10 段会话，生成 48 条规则候选。经用户明确批准，三份材料已保存到 `/mnt/d/ResearchGraphPilot/20261009`，源副本与持久副本逐份 SHA-256 一致。入口是 [人工参考作业表](/mnt/d/ResearchGraphPilot/20261009/人工参考作业表.md)，另有 `references.json` 和 `试点问题草案.md`。**人工参考记录仍为 0 条**；不得拿这些候选计算找回率。

下一步需人工核对至少 25 条参考决定的对象、状态、范围、理由和原文位置，并确认 5 个问题，再进行真实 M1 验收。每次完成一个任务后更新进度并开新会话，遵守 `AGENTS.md` 的接续规则。
