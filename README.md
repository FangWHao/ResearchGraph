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

## 清除管理目录中的整个项目

本地界面的“项目清除”先显示数据库记录、对象、快照和概览的清除数量与归属阻碍，再由用户核对项目名称执行。清除完成后页面重新载入，未保存的草稿会丢失。其他项目仍引用的相同字节对象会保留；跨项目记录依赖、未登记对象、无法确定归属的提示或输出会阻止执行。

命令行使用相同预览摘要和幂等请求 UUID：

```bash
uv run rg --data-dir /tmp/rg-demo project clear-preview PROJECT_ID
uv run rg --data-dir /tmp/rg-demo project clear PROJECT_ID --request-id REQUEST_UUID --preview-sha256 PREVIEW_SHA256
uv run rg --data-dir /tmp/rg-demo project clear-status --request-id REQUEST_UUID
uv run rg --data-dir /tmp/rg-demo project clear-resume REQUEST_UUID
```

预览不删除资料；执行时会重新核对目标记录、文件清单和修订，变化后需重新预览。清除期间要求整个数据目录没有存活的读取、写入、钩子或备份连接，占用时返回 409。中断后保留不含原文的恢复记录，普通入口暂停访问；界面和 `clear-resume` 可以继续完成数据库、全文索引、WAL、对象、spool、影子快照、概览和根登记清理。

范围是当前数据目录管理的资料。Claude/Codex 原始日志、研究工作区以及用户保存或分享的备份/导出副本保留；本次不会自动寻找或删除这些外部副本。管理的数据清除后只保留无原文记录和来源摘要屏障，阻止扫描、钩子或快照重新导入已清除来源；后续备份也携带该屏障。原生 Windows 清除和大型项目仍待验收，当前恢复清单有 4 MiB 上限，超出时在删除前拒绝。详见[整项目清除验收](docs/acceptance/管理目录整项目清除验收_20261010.md)。

## Agent 只读查询与状态卡

```bash
uv run rg --data-dir /tmp/rg-demo context --project PROJECT_ID --budget 2000
uv run rg --data-dir /tmp/rg-demo context --project PROJECT_ID --budget 6000 --scope data=v2 step=cnv
uv run rg --data-dir /tmp/rg-demo mcp --project PROJECT_ID
```

`mcp` 使用标准输入输出，不打开端口。启动参数固定项目；提供 `research.search`、`research.node`、`research.evidence`、`research.history`、`research.context` 五个只读工具，不注册人工确认入口。指定数据库必须已经存在且完成当前版本升级，查询不会建库、迁移、写计数缓存或修改对象库。`context` 直接输出带 `<rg-context>` 标记的状态卡；再次导入时按注入内容排除。

2026-10-10 用户明确取消 §7.11 的固定 1500 token 返回上限。普通查询按条目分页，默认 20、最多 100 条；原文默认 4000 UTF8 字节、最多 24000 字节。状态卡仍按调用方的 `budget` 实测，默认 2000，可调高；优先保留当前采用及拒绝或暂缓，再装填问题、待核对、反对证据和文件观察，超出预算的详情变成引用或进入下一页。这个预算计整个标记文本，不包含 MCP 的协议封装。

本地默认编码为 `cl100k_base`，启动时可用 `--encoding o200k_base`。两份公开词表随包提供并按正式 SHA256 验证，查询期间不联网；返回注明编码，MCP `_meta` 另给标记正文的实测 token 数。它们不是 Claude 或 DeepSeek 的计数标准，提取模型的完整请求仍必须使用提供方实测。

工具参数 `occurred_until` 和 `known_until` 分别限定发生时间与已记录时间，均需带时区；未指定时使用本次读取时间。历史审核、人工修改和原文引用也受已知时间约束。分页续读应复用返回的两个截止时间，可带 `expected_revision`，图变化时重新开始；候选、时间未知、同刻冲突和未知范围不会推断为当前采用。对象状态从整个符合截止时间的历史计算，再分页展示，不受问题／时间线简版视图的 2000 条限制。

`search` 默认检索 claims，`source=events` 可检索原文正文的字面量预览。原始事件没有统一分析范围，带 scope 的检索使用 claims。引用前缀 `C`、`S`、`E`、`V:`、`R:` 分别表示记录、片段、事件、文件观察和运行；`evidence` 的参数对应 `claim_id`、`span_id`、`event_id`、`version_id`、`run_id`，一次指定一种。原文窗口带实际字节位置、窗口摘要和下一字节偏移；源文件删除后仍读已复制原件。文件观察保留原算法及候选状态，不读取当前文件补写旧版本。

支持新版 MCP 2026-07-28 的逐请求元数据和旧版初始化协议；详情与官方客户端复现命令见 [只读 MCP 与状态卡验收](docs/acceptance/M4只读MCP与状态卡验收_20261010.md)。本轮没有修改个人客户端设置；候选写工具 `propose_note` 仍等待 note 类型的明确约定。

`research.evidence(version_id)` 同样支持后台物理版本，`observations` 按 `limit/offset` 分页。快照版本使用保存快照的发生时间，当前摘要使用实际完整读取结束时间；入库截止另核对观察和必要的快照记录。缓存继续沿用原读取窗口，发现快照只是线索。摘要时间和条数只计算双截止内的观察，缺失读取窗口显示未知，不采用版本表首次插入时间补造历史。状态卡按同一规则装填文件版本；指定分析范围时仍不猜文件归属。新增版本、观察或快照会改变 `revision`，续页可提交 `expected_revision`，冲突后重新开始。这里返回登记元数据，不打开当前文件或推定运行输入输出。见[物理版本 MCP 验收](docs/acceptance/M4物理版本MCP双时间验收_20261010.md)。

## Codex 与 Claude 项目接入包

```bash
uv run rg --data-dir /合成数据目录 client-pack --project 项目ID --output /新目录/接入包
uv run rg --data-dir /合成数据目录 client-pack --project 项目ID --output /另一个新目录/接入包 --encoding o200k_base
```

生成两套原生目录：Codex 的 `.agents/skills` 和 `.codex/config.toml`，Claude 的 `.claude/skills` 和 `.mcp.json`。每套包含 research-context、research-question、research-decide。数据库只读，不调用模型；输出已有时拒绝覆盖，不修改个人设置或安装钩子。包内 README 说明如何把技能目录和 MCP 条目合并到已登记项目的工作区，保留已有配置。包包含本机绝对路径，移动环境或数据目录后需要重新生成，不宜直接上传。

上下文技能允许自动选用，用户也可输入 `$research-context` 或 `/research-context`。明确分析范围时使用只读 MCP 的完整 scope；未指定时读取固定项目的默认状态卡。人工命令只允许用户明确调用 `$research-question`、`$research-decide` 或对应的 `/research-question`、`/research-decide`。Agent 自己的判断、旧会话和 MCP 结果不能变成人工确认。

记录脚本接收 UTF8 JSON 文件，通过固定解释器和参数数组调用同一 CLI，原话不插入 shell。包装入口也可直接使用：

```bash
uv run rg --data-dir /合成数据目录 client-record question --project 项目ID --input /临时目录/请求.json
uv run rg --data-dir /合成数据目录 client-record decide --project 项目ID --input -
```

question 请求包含 `text` 和标准 UUID `request_id`；decide 包含 `selector`、`action`、`why` 和 `request_id`。可选完整 `scope`、带时区 `occurred_at`、非负整数 `expected_revision`；不接受项目、actor 或其他操作覆盖。重试复用 UUID 和原意图，沿用现有版本冲突和对象歧义合同。请求文件用后删除，回执供重试；包不自动保管临时请求。

人工入口的身份规则依赖可信本机和客户端技能调用，不构成操作系统认证。MCP 仍仅开放五个只读工具，note/propose_note 未定义的部分继续保留。格式、脚本、官方客户端及独立 wheel 验证见[客户端接入包验收](docs/acceptance/M2客户端接入包验收_20261010.md)，不替代真实 Agent 会话效果或钩子 p95 验收。

## 有界问答

```bash
uv run rg --data-dir /tmp/rg-demo ask "为什么当时放弃 ref_v1？" --project PROJECT_ID --retrieve-only
uv run rg --data-dir /tmp/rg-demo ask "为什么当时放弃 ref_v1？" --project PROJECT_ID --k 12
uv run rg --data-dir /tmp/rg-demo ask "为什么当时放弃 ref_v1？" --project PROJECT_ID --scope data=v2 --scope step=cnv --known-until 2026-10-01T00:00:00Z
```

生成模式使用下文配置的 `RG_BASE_URL`、`RG_MODEL` 与 `RG_API_KEY`，也可传 `--key-file`。远程项目仍须完成样例预览和明确外发授权。`--retrieve-only` 不需要凭据，以只读库运行，模型不可用时也可查看证据。

先按字面量检索对象名称、记录内容和相关决定，再取经过摘要、字节范围及项目校验的原文窗口。默认最多 12 条，可用 `--k` 调为 1–100；每条默认 4000 UTF8 字节，可调为 4–24000。完整范围与发生/已知双截止沿用只读查询；没有结构化记录时可搜原件，但指定范围后不猜测原件的范围。检索最多检查 200 条匹配记录、每记录 20 个引用，原件后备最多检查 200 条匹配事件；达到上限、窗口不全或引用损坏都会明确报告，有限检索不证明其他材料不存在。

完整请求逐次由提供方实测，默认输入上限 128000、输出 4000，共用日额度。超预算、计数不可用、截断、污染、未知引用或引文不在本次窗口内时不发布回答，仍返回本地检索结果。没有有效证据时不调用模型，返回无法判断。相同资料和问题可复用已校验结果；人工审核、替换或检索材料变化会改变输入，回答期间记录变化另行标明。

回答以 `<rg-context>` 包装，标为模型解释，保留逐条引文、来源窗口和不确定事项；执行记录写入既有 `qa` 账本。**引用有效不证明解释正确**，回答不写入研究 claims，也不进入其他提取阶段。合成实测与行为验收见[有界问答验收](docs/acceptance/M4有界问答验收_20261010.md)。

本地界面的“研究问答”先检索来源；可选择“仅本地检索”，也可在服务配置模型后生成解释。问题、完整范围、发生与获知截止、来源条数和窗口大小均可填写。来源卡片保留独立审核与采用/证据状态，点击“打开此处原文”定位同一校验字节窗口。首次外发先展示遮盖后的问题、记录与原文样例，再由用户主动开启项目许可；候选不必先经过人工确认。

```bash
uv run rg --data-dir /tmp/rg-demo serve --open \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file /绝对路径/凭据文件
```

服务也读取 `RG_BASE_URL`、`RG_MODEL` 和 `RG_API_KEY`；默认输入上限 128000，可用 `serve --input-budget` 调高，生成仍按提供方实测并核验模型窗口。未配置模型的服务继续支持本地检索。地址和密钥只由本机服务配置，浏览器不能指定地址、密钥或模型。项目许可涵盖远程计数、自动提取、问答及后续新增资料，可以在问答页撤回；已发送的请求可能仍在处理。页面“停止等待”仅停止浏览器等待。问答接口和界面验收见[问答接口与外发许可验收](docs/acceptance/M4问答接口与外发许可验收_20261010.md)和[前端问答验收](docs/acceptance/前端有界问答验收_20261010.md)。

## 历史导出

```bash
uv run rg --data-dir /tmp/rg-demo export --project PROJECT_ID \
  --output /tmp/history.zip --until 2026-10-01T00:00:00+08:00 \
  --known-until 2026-10-02T00:00:00+08:00 --scope data=v2 --scope step=validation
uv run rg verify-export /tmp/history.zip
```

`--until` 是有效时间截止，与 `--occurred-until` 同义；`--known-until` 限定当时已经入库的记录。未指定时取同一次读取开始的当前时间。`--scope` 按完整字段对象相等筛选，省略时包含该项目所有可见范围；包中同时记录这两个截止、范围和图修订号。可用 `--expected-revision` 拒绝变化后的导出。

本地界面的“历史导出”可选择项目、双截止、所有/完整相等/明确未知范围，主动下载 ZIP；正文默认不加入，可选遮盖片段及本次临床正则。修订变化后先主动刷新，再下载；取消等待、项目或条件变化后的迟到响应不会自动下载。手机也可切换项目。详见[历史导出验收](docs/acceptance/M4双时间历史导出验收_20261010.md)和[前端导出验收](docs/acceptance/前端历史导出验收_20261010.md)。

ZIP 包含图、全部可见断言版本、决定/证据状态事件、当时已知的审核动作、可定位的证据引用、来源实例与字节位置、算法和对象摘要，以及可见的文件观察和候选运行报告。它遍历全部分页，不受问题／时间线简版视图的 2000 条上限影响；关系和共同输入保留候选、依据及证据，范围外端点只列未解析 ID。`graph.json` 的 `semantic` 和 `l1` 分别使用完整 L2 语义图与下述文件运行图引擎；实际运行输入输出完整性与影响传播仍待验收。

默认不包含引用正文、完整会话、二进制数据或环境副本。确需正文时显式加 `--include-evidence`，仅加入已选择断言的引用片段；先校验原文摘要和 UTF8 边界，再等长字节遮盖。常见密钥、身份证、手机、邮箱和用户名被遮盖，凭据字段另行清空。`--redact-pattern 'ZY\d+'` 可重复指定本次导出的额外临床编号规则；项目已保存规则始终同时应用。分享副本中的范围可能遮盖，选择仍按原范围执行；规则影响结构 ID 或造成字段碰撞时拒绝发布。

导出只读证据库，不联网、不调用模型或读取当前工作区文件。文件必须使用已有父目录中的新名称，拒绝父目录符号链接、证据库内目标和覆盖；成功后原子发布。CLI 请求 0600 权限并报告实际模式；Windows 挂载目录可能不体现该模式，需按实际 ACL 核对，不能据创建参数认定文件已经私有化。默认引用包在原文对象缺失时仍可生成并标注未读取，显式正文则拒绝缺失或损坏的原件。`verify-export` 不需要数据库，只校验包内成员摘要，不能证明研究结论或清单真实性。本地同源令牌接口 `POST /api/exports` 接受同样阅读条件并直接返回 ZIP，不保存服务端副本。

## 文件运行图

```bash
uv run rg --data-dir /tmp/rg-demo l1-graph --project PROJECT_ID --collection nodes
uv run rg --data-dir /tmp/rg-demo l1-graph --project PROJECT_ID --collection edges \
  --known-until 2026-10-02T00:00:00+08:00 --occurred-until 2026-10-01T00:00:00+08:00 \
  --scope data=v2 --scope step=validation --limit 100
```

`l1-graph` 与令牌接口 `GET /api/l1-graph?project=PROJECT_ID` 共用已登记 L1 记录的完整图。五种集合为 `nodes`、`edges`、`observations`、`evidence`、`unresolved`；每页 1–100 项。后续页固定首个响应的双截止与 `expected_revision`，修订变化返回冲突。HTTP 的 `scope` 是完整相等的 JSON 对象；`null` 表示明确未知范围。没有范围的文件或原生运行不会因路径相同自动归属，按范围查看仅使用明确运行清单引用，标注报告关联。

图保留文件版本、原生执行、候选清单、候选编辑、工作区快照与同范围尝试的全部可见内容版本。重复输入端口各有序号；未知 `null` 与明确空列表 `[]` 分别显示。报告的 `consumes` / `produces` 只连接报告声明，原生请求、开始、退出与报告退出码独立；报告冲突不覆盖实际观察。缺失端点、编辑缺失前像、快照超时与不明确调用关联保持缺口。当前文件的发现快照不证明文件曾包含在旧快照中。

界面的“文件运行图”独立于 L2 研究图，完整载入五种集合后显示。原文通过 `GET /api/l1-evidence` 在固定项目、双截止与修订下按 UTF-8 字节续读，不混入后来的派生上下文。图查询不读正文对象、当前文件或影子仓库；原文只在用户打开证据时读取。历史 ZIP 的 `graph.json.l1` 保存同源集合，默认仍不含完整原文。

`l1_graph_complete=true` 仅表示已登记图记录完整返回；`l1_dependencies_complete=false`、`actual_io_completeness=unknown` 继续说明实际运行输入输出未知。没有从任意命令推断完整 I/O，也不执行会话中的命令。验收见[后端文件运行图验收](docs/acceptance/M4已登记L1文件运行图验收_20261010.md)和[前端文件运行图验收](docs/acceptance/前端L1文件运行图验收_20261010.md)。

## 语义图与过程组

```bash
uv run rg --data-dir /tmp/rg-demo graph --project PROJECT_ID --collection nodes
uv run rg --data-dir /tmp/rg-demo graph --project PROJECT_ID --collection joins \
  --known-until 2026-10-02T00:00:00+08:00 --occurred-until 2026-10-01T00:00:00+08:00 \
  --scope data=v2 --scope step=validation --limit 100
```

`graph` 与令牌接口 `GET /api/semantic-graph?project=PROJECT_ID` 共用完整 L2 图。`collection` 可选 `nodes`、`edges`、`joins`、`merges`、`state_events`、`unresolved`、`claims`，每页 1–100 项，按 `next_offset` 续读。固定首个响应的双截止并传 `expected_revision`，图改变时返回冲突。HTTP 的 `scope` 是完整 JSON 对象；明确未知范围用 JSON `null`。查询只读已存记录，不打开原文或当前文件，不联网调用模型。

节点按对象身份和完整范围区分。所有内容版本与历史更正保留，多个有效版本不按较大 ID 推定当前内容；审核、采用、证据与运行维度独立。关系保留原方向、依据、原文定位与循环；缺少同范围版本的端点明确列为未解析，不借用其它项目或范围。汇合保留命名端口：共同输入全部必需、比较区分被选与未选、证据综合不推断统计独立。已有方向或汇合错误显式标记，人工合并不删除对象或自动确认它们。

研究图使用 `collection=claims` 完整分页，首响应固定项目、双截止及修订，读齐后才开放折叠；问题／时间线保留2000条简版投影及部分提示。部分图、缺端点、方向／汇合错误或隐藏候选时拒绝折叠。完整投影的过程组保留成员的撤回、阴性结果、范围历史、候选审核、所有内部关系及每条边界的原类型/端点/端口/证据；未选比较仅显示为比较。展开恢复同一修订的原节点、关系和位置。版本选择、组名、折叠都只改显示。详见[后端语义图验收](docs/acceptance/M4完整L2语义图验收_20261010.md)与[前端语义图验收](docs/acceptance/前端语义图验收_20261010.md)。

研究图可指定带时区的发生截止与已知截止；详情和原文沿用相同条件与修订，只读历史审核、更正和可见引用。原文前后上下文先按双时间筛选，再读取已存对象；历史响应明确不加载当前父链、运行及文件版本派生。完整记录页的来源分组只含可见会话，片段未知，不能把晚到提取计划补成旧图已知内容。CLI 也可使用 `graph --collection claims --limit 100`；历史详情和原文接口以 `project`、`occurred_until`、`known_until`、`expected_revision` 选择历史分支，无参数时保留当前入口。见[完整记录与历史原文验收](docs/acceptance/完整研究图记录与历史原文验收_20261011.md)及[完整研究图界面验收](docs/acceptance/完整研究图界面验收_20261011.md)。

完整性标志限定为 `layer=L2`；不代表实际运行 I/O、科研结论成立或真实规模性能已验收。完整 M0–M4 与 Atlas 正式参考门槛继续保留。

## 人工记录研究问题

问题页提供“新增研究问题”，也可以使用同一后端的人工命令：

```bash
uv run rg --data-dir /tmp/rg-demo question '当时为什么选择方法甲？' --project PROJECT_ID
uv run rg --data-dir /tmp/rg-demo question '这个队列的验证目标是什么？' --project PROJECT_ID --actor 'human:研究者' --scope data=v2 step=validation
```

文字原样保存，人工问题以 `basis=manual`、人工身份直接确认，能够从详情回到原文、在图中查看和按字面量检索。没有指定范围时保留 `null`；确认问题不产生方案采用、证据成立或运行成功。模型不可用时仍可记录，模型提取的严格范围规则继续执行。

`--request-id` 接受 UUID：网络或进程重试同一意图时复用该编号，返回既有记录；相同编号用于不同文字、项目、身份或范围时拒绝写入。省略编号每次新建，内容相同也可以是不同记录。`--expected-revision` 指定读取时的图版本，冲突后刷新再人工提交；`--occurred-at` 可回填带时区的发生时间，入库时间仍单独保留。问题最多 16000 UTF8 字节，完整记录最多 65536 字节，引用按最多 8000 字节原文窗口保存。

命令与本机令牌 HTTP 是可信人工入口，Agent 的候选写入口仍须单独实现。开发验证只写合成项目；`note` 存储类型尚待约定，question/decide 的两种客户端包装见上文。详见 [人工问题验收](docs/acceptance/M2人工问题验收_20261009.md)。人工记录供补记与纠错使用，日常采集和提取走自动链路，不要求先手工录入问题或决定。

## 人工补记决定与选择对象

时间线可选择「记录人工决定」，CLI 示例：

```bash
uv run rg --data-dir /合成数据目录 decide accept "对象的完整名称或ID" \
  --why "保留的理由" --project 项目ID --scope data=v2 step=cnv
uv run rg --data-dir /合成数据目录 decision-targets --project 项目ID --query "对象"
uv run rg --data-dir /合成数据目录 resolve-decision 待复核决定ID \
  --target 对象ID --expected-revision 当前版本
```

支持 accept、defer、reject、withdraw。对象按整个项目的有效版本查找，名称需完全相等；明确范围时所有字段和值需完全相等。唯一对象直接形成 confirmed 的人工决定，仍不确认目标对象或发现证据。未指定范围保留 null，含未知值的范围也不计入当前采用。

同名或未知对象保留 target=null 的 candidate，在队列选择现有对象。不会新造一个方案，也不能通过普通、批量确认或通用修改跳过歧义。选择仅改变目标，原动作、理由、完整范围和发生时间保留；追加人工替换版、复核动作及选择原文，旧记录始终可查。对象搜索支持字面量与分页，不依赖图的 2000 条返回上限。

人工问题、决定与对象选择的 UUID 共用重试保护。重放不撤销后续驳回；不同操作或意图不可复用编号。表单草稿仅在当前页面内存，401/400/409 或迟到响应不自动丢弃新草稿。详见 [人工决定验收](docs/acceptance/M2人工决定验收_20261010.md)和[前端人工决定验收](docs/acceptance/前端人工决定验收_20261010.md)。

采集健康页分别显示全库来源与提示队列、当前项目的快照缺口，以及模型阶段的用量观测。未完成提示不代表进程仍在运行；快照遗漏、异步时点和元数据缺失可能重叠，不能由这些统计推算完整版本或成功数量。缺少记录时显示未知，真实零值仍为零。

导入后会从已保存的 L0 原件派生运行与编辑记录；原始文件删除后仍可继续。记录缺少项目、调用或执行器句柄归属时保留等待项，人工失败不会自动重试。补处理入口为：

```bash
uv run rg --data-dir /tmp/rg-demo derive --limit 100
# 明确重试失败的派生项；每次仍最多处理 100 条。
uv run rg --data-dir /tmp/rg-demo derive --limit 100 --retry-failed
```

运行只读取原生执行元数据或执行器头，不从 stdout 中搜索退出码；请求缺少结果时保留“仅有请求”，退出码缺失时显示未知。关联只依赖同一会话的调用 ID，执行器句柄只在同一会话内唯一时关联，不按命令或时间接近猜测。命令仅展示，不执行；运行的输入、输出版本仍未记录。

Claude 报告的完整编辑前文本与可核对补丁可生成前后候选文本版本。它们的摘要标签为 `sha256:tool-utf8`，不代表物理文件的编码、BOM 或字节已验证。Codex 的更新和删除补丁没有完整原文时，前后版本保持未知；成功新增可保留报告的新增文本，原文件仍可能被覆盖，不能补造空白前版本。不读取当前文件修复旧历史。原文检索页支持按全库事件编号打开工具事件，编号跨项目；证据页逐条显示运行观察、候选版本和仅补丁差异，超限明确不可展示；完整边界见 [运行与编辑验收](docs/acceptance/M2运行与编辑验收_20261009.md)。

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

扫描与消费验收见 [扫描与 spool 验收](docs/acceptance/M2扫描与spool验收_20261009.md)。已有同会话调用关联的运行与编辑派生；全部工具版本、完整实际输入输出及跨 worktree 自动归属仍待落实。

Codex 原生 `patch_apply_begin/end` 也可派生候选编辑；只按同会话唯一调用 ID 关联，优先保留已有补丁请求。外层执行器回执独立，失败、矛盾、缺少开始及重复 ID 保留缺口。新增后的全文和删除前的全文按原样显示为工具报告候选；更新、移动及缺失全文不读取当前文件补齐。单侧全文不是完整前后比较，删除后未知不变成空文件；单项最多 64000 UTF-8 字节、2000 行，整事件正文合计最多 64000 字节，超限明确不展示。详见[原生补丁验收](docs/acceptance/M2原生补丁与单侧候选全文验收_20261010.md)。

## 影子快照与钩子示例

已有项目追加根目录时，使用 `project root-add --project PROJECT_ID --path /workspace/another-worktree`；可明确指定 `--kind worktree|data|alias|repo`。`project roots --project PROJECT_ID` 只读列出登记路径、类型和登记时的 Git 身份，支持分页与修订核对。新登记保留 Git 共同目录及远端 URL 摘要，不保存原始 URL；相同摘要不合并项目，追加根目录不改变已有会话归属。显式 data 不探测 Git 或加入钩子快照清单，旧登记的未记录元数据不自动补造。详见[根目录登记验收](docs/acceptance/项目根目录与Git身份登记验收_20261010.md)。

手机页面已恢复当前项目选择入口，可直接切换项目；各项目页面内草稿和迟到响应沿用原隔离规则。详见[移动端验收](docs/acceptance/移动端项目切换验收_20261010.md)。

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

影子仓库位于数据目录的 `snapshots/项目ID.git`，使用独立 index，遵守 `.gitignore`、`.rgignore`。保留原始文件字节、可执行位及链接目标文字，链接不跟随。大于 5,000,000 字节的文件在钩子中只记录路径、大小、修改时间，后台再顺序读取完整 SHA256；嵌套仓库仍明确记录未捕获，完整运行输入输出映射尚未生成。超时、忙或读取期间变化没有有效 shadow_commit；遗漏、竞态和采集起止保存在追加元数据中。无法安全测定 dirty 时显示缺失，不运行过滤器或通过 status 重新读取未捕获文件。

文件版本可用本地页面或以下命令查看；不会读取当前文件正文。持续 scan/extract 在有快照记录后启动离线摘要子进程，停止或父进程断开时子进程退出。单次 scan 不等待慢文件，需要自行运行 hash-files；没有安装系统服务。

```bash
uv run rg --data-dir /tmp/rg-demo hash-files --project PROJECT_ID --limit 20
uv run rg --data-dir /tmp/rg-demo hash-files --watch
uv run rg --data-dir /tmp/rg-demo versions --project PROJECT_ID --limit 25 --offset 0
uv run rg --data-dir /tmp/rg-demo version-diff BEFORE_VERSION_ID AFTER_VERSION_ID \
  --project PROJECT_ID --expected-revision REVISION
```

快照版本保留 git-sha1、原始字节、权限和链接目标文字；不跟随链接。后台大文件版本为 current_file/sha256，仅证明实际读取窗口中的当前普通文件，snapshot_id 保持空；发现它的快照只提供线索，不证明当时字节一致或实际用于运行。复用缓存时保留原完整读取窗口与观察来源，设备、inode、模式、大小、mtime、ctime 是变化提示，不是内容摘要。内容变化后重新读取；读取中发生变化则作废并等待五秒，文件缺失或非普通文件留下失败状态，可用 --retry-failed 明确重试。健康统计中的 running 不证明进程存活，当前版本、工具报告与旧运行 I/O 不互相补写。物理版本可通过 versions CLI/HTTP 及 MCP 双时间入口查看；显式运行清单可报告与尝试、文件版本的关联，实际输入输出的完整追踪仍待落实。完整证据见[后台摘要验收](docs/acceptance/M2物理文件版本与后台摘要验收_20261010.md)与[版本界面验收](docs/acceptance/前端物理文件版本验收_20261010.md)。

比较两个明确版本时，`version-diff` 与本地文件版本页、`GET /api/version-diff` 共用只读查询。两侧必须属于同项目、同登记根目录与路径；可用 `--occurred-until` 和 `--known-until` 固定双截止，`expected_revision` 核对列表读取后是否有新增记录。比较先验证保存对象的长度、SHA256 和 Git blob 摘要，并显示实际选用的捕获观察；不读取当前文件或运行 Git，也不把工具报告文本、大文件当前哈希当成旧快照字节。

已保存的 UTF-8 文本返回完整统一差异，权限变化与字节变化分别显示；链接只比较目标文字，不读取目标。两侧输入合计最多 64000 字节、每侧最多 2000 行、差异输出最多 64000 字节；二进制、无效编码、来源不明、缺失或损坏对象、超限都明确不可展示，不截断为完整结果。缺模式证明时显示未知。文件版本仍保持候选状态，不证明运行实际使用或研究结论成立。

## 离线运行清单

已登记运行或明确的待到运行 ID，可由本地程序用 `record-run` 追加清单；不需要人工确认。入口不执行命令、不打开声明文件、不联网，也不覆盖原生请求或执行器结果。清单原件、候选报告、I/O 行和 UUID 回执同事务提交；重试同一 UUID 和意图返回原回执，换内容或复用人工操作 UUID 会冲突。

```bash
uv run rg --data-dir /tmp/rg-demo record-run --project PROJECT_ID --input /tmp/run-manifest.json
uv run rg --data-dir /tmp/rg-demo run-evidence --project PROJECT_ID --run RUN_ID
uv run rg --data-dir /tmp/rg-demo run-evidence --project PROJECT_ID --run RUN_ID \
  --manifest-id REQUEST_UUID --io-offset 100 --expected-revision REVISION
```

清单为有限 UTF8 JSON，文件最多 65536 字节，包含来源元数据的完整原件也受此上限约束。必填 `request_id` 标准 UUID 与 `run_id`；可选 `attempt_id`、`snapshot_id`、`scope`、`parameters`、`seed`、`started_at`、`ended_at`、`exit_code`、`occurred_at`、`expected_revision`。`inputs`、`scripts`、`patches`、`environment`、`outputs` 均为明确版本 ID 列表，合计最多 256 项，保留重复和顺序。省略或 `null` 表示未报告，`[]` 表示明确报告空列表；实际 I/O 完整性仍为未知。种子保存为十进制字符串，避免浏览器丢失整数精度。时间需带时区，未知发生时间保持空。

清单总是 `candidate/direct_record`：直接记录的是本地报告，不能据此确认实际读取、完整环境或运行成功。已存在的运行、尝试、版本和声明快照核对项目；缺失的运行和版本 ID 可先保存，后来只在读取视图中按同项目精确 ID 解析，不改原观察。参数、种子和报告退出码与原生事实分开；退出码矛盾单独显示。绑定明确运行请求也不构成 I/O 使用证明。

`research.evidence(run_id)`、`run-evidence` 和受认证的 `GET /api/run-evidence?project=PROJECT_ID&run_id=RUN_ID` 共用双时间读取：原生状态从截止内的不可变执行观察重新计算，清单与来源原件分别核对截止。`limit/offset` 对清单及原生观察分页，`manifest_id` 可限定一份清单，`io_offset` 对每份清单按 100 项续读；`scope` 只筛选报告范围，不给原生运行补造分析范围。新增原生运行、执行观察或清单会改变修订号，续页提交 `expected_revision`；HTTP 冲突返回 409。版本详情与尝试对象可反查候选报告的运行关联。

本地证据页为已有原生运行把执行事实与候选清单分开展示，保留未知版本、部分列表和声明快照缺口；续页先取得固定修订，冲突后刷新重新读取。没有原生运行的清单仍可通过回执原文、CLI、MCP 或 HTTP 读取，界面暂不创建独立运行卡。详见[运行清单验收](docs/acceptance/M2运行清单与双时间关联验收_20261010.md)。

本机公开源码的 20 次手工引擎基准 p95 为 1426 ms，全部成功；模式仍为异步。157 个路径的影子对象检查未发现实际密钥或私有目录，用户 index 一致。这是引擎基准，尚未验收真实 Claude/Codex 客户端整条钩子耗时。`health.snapshots` 区分 skipped、partial、async_race 和旧元数据缺失；日志错误类名没有被当成完整失败账本。备份避开影子仓库写入，保留队列链接而不读取其目标。完整证据见 [快照与钩子验收](docs/acceptance/M2快照与钩子验收_20261009.md)。

## 远程模型

用户指定的接口为 `https://api.deepseek.com`，模型为 `deepseek-flash`。密钥从 `RG_API_KEY` 或 `--key-file` 读取，不进入代码、日志或测试。

DeepSeek 的 OpenAI 输入计数入口实测返回 404。同一服务的 Anthropic 兼容计数接口可用，当前专用适配器让计数和生成使用同一消息协议，包含正文、指令和 schema 工具定义。通用 OpenAI Responses 适配器要求服务支持计数端点，计数失败就停止生成。

接口协议参考：[DeepSeek 的兼容接口](https://api-docs.deepseek.com/guides/anthropic_api/)、[OpenAI 输入计数](https://developers.openai.com/api/docs/guides/token-counting)。接口是否可用以本机实测为准。

项目的住院号、病案号和样本编号可在“研究问答”或“历史导出”的“项目编号遮盖规则”中配置，每行一个正则。保存后用于本项目的计数、提取、关联、概览、问答和导出，内置个人信息与密钥规则继续生效。修改规则保留已有外发许可，但会使旧预览和旧任务输入失效；内置规则不会因清空项目规则而关闭。本地原文不改写。

命令行也可读取或保存。配置文件使用 UTF8 JSON 字符串数组，如 `["HOSP-\\d{6}", "SAMPLE-[A-Z]{3}\\d{3}"]`；编号格式为虚构示例。

```bash
uv run rg --data-dir /tmp/rg-demo project privacy PROJECT_ID
uv run rg --data-dir /tmp/rg-demo project privacy PROJECT_ID \
  --patterns-file /tmp/编号规则.json --actor human:本机用户 --expected-revision REVISION
```

读取不写库，保存需使用刚读取的修订号。规则最多 32 条、每条 1000 字符，语法由服务端校验；旧修订或正在发送时返回冲突并保留草稿。导出的临时规则追加到项目规则，导出不改变项目配置。详见[项目遮盖验收](docs/acceptance/M4项目临床编号遮盖验收_20261010.md)。

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

完成来源登记及项目外发授权后，可持续处理新内容，不必逐个指定会话：

```bash
uv run rg --data-dir /tmp/rg-demo extract --watch \
  --base-url https://api.deepseek.com --model deepseek-flash --key-file api_key \
  --input-budget 128000 --daily-budget 500000
# 只处理一个项目时加 --project PROJECT_ID；不加 --watch 则处理本轮持久队列。
# Ctrl+C 正常停止；用相同配置重新启动可接续。
```

持续模式扫描已登记来源、消费 spool、派生 L1，再依次处理提取、跨会话关联、项目与会话概览；`--project` 筛选这些模型阶段，离线扫描仍处理全库已登记来源。未归属或未允许远程调用的会话在计数前跳过；虚拟人工输入不入提取队列。默认每轮各阶段最多 20 个任务，`--limit 1–200` 可调；关联任务按项目、概览按项目或会话计数，每项目每轮默认最多尝试 50 个新关联对，可用 `--link-limit 1–1000` 调整。`--interval` 为有限正秒数。系统锁防止同数据目录重复调度，模型请求期间不占数据库写事务。提取任务固定截止事件，新内容替代旧待执行任务并保留历史；`--extract-only` 可单独诊断原提取阶段。

每日额度不足等待下个 UTC 日，新增内容和切回旧配置保留同配置的等待；关联与概览的服务故障等待 30 秒，阶段忙碌等待 2 秒。审核变化不重提取没有新增内容的全部历史，只刷新受影响的关联和概览；新内容到达时仍按原 worker 合同核对审核配置。失败缺口默认保留，显式 `--retry-failed` 在持续模式仅启动首轮生效，失败关联对在整轮跨页重试中最多再试一次。关联尚在分页或等待时不提前发布概览；部分失败则保留缺口并允许阅读已有结构化记录。

概览直接分页读取 claims，带输入摘要与生成时间，保存在数据目录 `overviews/` 下按项目和完整范围分隔的目录。生成期间记录变化时保留旧文件并重新排队；模型摘要不作证据、不作为后续输入。备份包含概览并避开写入，恢复到新位置后按当前数据目录找文件；文件缺失或被修改时重新核对记录，从有效缓存重建。健康页的提取队列与关联/概览队列分别分页、解释等待原因；任务完成不代替覆盖、事实确认或进程存活证明。省略会话的 `--estimate` 只估算首批已授权会话，不生成或建立任务。持续命令需保持运行，尚未安装系统常驻服务。详见[自动关联与概览验收](docs/acceptance/M3自动关联与概览验收_20261010.md)与[健康页验收](docs/acceptance/自动关联与概览健康页验收_20261010.md)。

`--input-budget` 控制正文、重叠、工作集、指令和 schema 的完整输入总量，默认 128000；例如 `--input-budget 256000` 可以调高。默认片段内容分配为 120000，工作集与指令/schema 各 3000，另留 2000 封装余量；输出仍限 4000。每次生成前实测完整请求；DeepSeek 还通过 `/models` 核验输入预算加输出预算是否超过当前模型窗口。窗口信息缺失时停止生成。

完整上一用户回合优先作为重叠上下文。一个超长回合内部的延续片段保留最近工具调用与结果，或最近正文窗口，并记录未携带的上下文位置；不修改原文。重叠不计作新覆盖，每条新候选必须引用当前片段的字节窗口。完整上一回合超过所设预算时，保留它并拆片重试；仍放不下则进人工队列，不能靠静默截断标为完成。

`--scope` 可重复指定完整范围字段，工作集只选完全相同的范围，模型输出必须原样使用该范围。原话明确属于其他范围时进入待澄清项。不指定范围时保留各对象已有的 scope 作为检索线索，但不能视为已完成同范围筛选；程序不会从目录名猜测范围。工作集优先级为直接 ID、标签字面量 FTS、相同文件路径、本会话较早片段的候选，不用无关对象填满预算。

pass1 的字节范围由程序按原始 JSON 路径和 UTF-8 位置给出，模型只能选取已登记窗口。重复内容块、JSON 转义和工具输出尾部不会通过相同文字的首个匹配猜位置。程序同时记录规则与模型定位，pass2 只取回候选正文和有限工具原文。当前数据库版本为 17：版本 2–6 包含定位、链接进度、调用尝试、片段计划和扫描回执；版本 7–10 包含快照、L1、人工问题与决定；版本 11 增加持久提取队列，版本 12 增加关联/概览队列及只追加历史；版本 13 增加物理文件版本观察、大文件完整摘要缓存及离线队列，版本 14 增加版本/观察/快照修订触发器，版本 15 增加候选运行清单与角色端口，版本 16 保存项目临床编号规则，版本 17 补充编辑记录修订与旧记录失效；升级在事务中执行，保留原始事件、定位和尝试历史。

追加会话内容后，重复运行 `extract` 只处理尚未登记的新事件和未完成片段。已完成回合仅作重叠上下文；同一回合晚到的助手回复和工具结果不会再次提取旧用户决定。排除记录与镜像不充当新的回合边界。片段的事件所有权、字节窗口和原始上下文保存在本地库中，重启沿用原计划；已完成子片段不重建，后续窗口仍留在覆盖账本。候选或无候选结果与片段完成状态同事务提交，进程在更新覆盖视图前退出也能离线恢复。

失败并进入人工队列的片段默认不自动重发；新增内容仍可继续处理，但旧缺口保留。显式加入 `--retry-failed` 可重试当前配置的人工失败片段，成功后关闭对应通知，失败历史不删除。调高输入预算、换模型、修改完整范围或人工审核会形成新配置并重新核对候选，已有人工确认不会被覆盖。旧版本缺少片段计划时，只有旧缓存与当前配置、完整原文前缀和项目尝试证明精确匹配，才继承完成状态；证明不足则重新提取候选。详见 [增量与断点验收](docs/acceptance/M1增量与断点验收_20261009.md)。

同一会话的提取按顺序执行，不同会话可以并行；同一项目的概览和链接分别互斥。每个模型任务也有独立的进程锁，重复请求在计数前返回“正在运行”，不会重复调用模型或占用额度。远程调用期间不持有数据库写事务，采集仍可运行。锁由系统在进程退出后释放，保留的锁文件不表示进程仍活着。

完整响应已经通过 schema 校验但尚未入库时，重启可复用该响应，再核对原文引用、范围和对象。事务内检查成功运行，避免两个写入者重复生成候选；不同输出必须使用不同运行记录。成功缓存不重新计数或查询模型窗口，服务暂不可用时仍能读取缓存。截断、污染、超预算和校验失败结果不作为成功缓存；进程中断且用量未知的发送尝试继续保留历史与预留。自动化范围见 [并发与缓存恢复验收](docs/acceptance/M1并发与缓存恢复验收_20261009.md)。

本版本的遮盖涵盖身份证、手机号、邮箱、常见密钥、路径用户名和 rg 注入块。程序生成的候选对、片段和对象标识符只在规定的元数据位置保持原值，避免摘要中碰巧出现手机号形状的数字导致引用失配；原文、标签、理由和范围仍照常遮盖。项目临床编号规则已实现；按用户选择提供管理目录的整项目清除，外部日志及用户保存的副本沿既有保留边界。Atlas 的真实会话只做本地统计和人工参考准备，不发送给远程模型。

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

解析类型与工具版本可单独只读查询，界面的采集健康页使用同一接口：

```bash
uv run rg --data-dir /tmp/rg-demo parser-health --project PROJECT_ID --limit 20
# 后续页使用第一页返回的 snapshot_id、scope_key 和 next_offset。
uv run rg --data-dir /tmp/rg-demo parser-health --project PROJECT_ID --limit 20 \
  --snapshot SNAPSHOT_ID --expected-scope-key SCOPE_KEY --offset NEXT_OFFSET
```

`GET /api/parser-health` 需要本机访问令牌，参数与命令一致。每条完整物理日志行追加一次类型观测，内容块出现次数与日志行数分开；副本仍算另一次实际遇到。类型分组保留解析器版本、工具版本、版本来源、未知标记及首次／最近原文事件，可分页查看和跳回已保存原文。已识别只表示解析器识别类型或读取其文字，不表示提取覆盖完整或研究结论确认。

Claude 工具版本只取顶层 `version`，Codex 只取 `session_meta.payload.cli_version`；缺字段只允许继承同一物理文件实例已有的有效声明，显式非法声明清空该上下文。轮换、复制文件及旧库升级均不借用邻近版本；旧记录显示未观测。迁移 19 不读旧日志、不补造统计。命令只读打开当前版本的库；旧库应先经正常导入或扫描入口升级。续页固定观测截止，后来追加不挤动页面；已登记记录的项目归属改变时返回冲突，需重新读取第一页。账本随数据库备份，纳入管理目录的整项目清除。

见[解析账本验收](docs/acceptance/解析器类型与版本账本验收_20261010.md)及[界面验收](docs/acceptance/解析器健康界面验收_20261010.md)。

钩子失败报告由 `scan` 或自动流水线在后台采集，钩子本身不访问数据库。只读查看已保存的全库报告：

```bash
uv run rg --data-dir /tmp/rg-demo hook-errors --limit 20
# 后续页固定第一页的 snapshot_id，刷新第一页才能看后来报告。
uv run rg --data-dir /tmp/rg-demo hook-errors --limit 20 --snapshot SNAPSHOT_ID --offset NEXT_OFFSET
```

`GET /api/hook-errors` 使用本机令牌，参数与命令一致；`project` 只验证项目存在，统计仍属于整个管理目录。报告只保存时间、固定阶段、白名单异常类、字节位置和摘要；未知类只保留摘要，非法行不复制正文。错误日志不进入 L0、对象库或模型。来源轮换、缩短或前缀／提交边界变化建立新实例，已保存报告保留；相同字节的不同实例分别计数，报告数不能视为实际失败总数。

迁移20不读取旧日志。没有观测到日志时失败次数未知；已观测空文件为零条报告，完整失败历史仍未知。残片、每批1000行后的积压、超过4096字节的行、读取失败和源文件缺失分别保留；超长行不截断、不推进游标。无变化轮询不重复追加观测，显示时间是最近持久观测变化，不是进程存活证明。备份保存账本而不复制原日志；整项目清除保留不含项目、会话、正文或用户路径的全库运维元数据。见[钩子失败账本验收](docs/acceptance/钩子失败账本验收_20261010.md)。

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

API 在原始双时间旁提供只读的 `occurred_at_utc`、`recorded_at_utc`，由后端统一解析为 UTC 六位微秒；时间未知为 null，不改写原始字段。界面据此区分同一毫秒内的事件先后，真正同刻的相反采用或证据显示有冲突，单条有效确认事件缺失发生时间也显示时间未知。候选、驳回、被替换与其它范围仍隔离；未知范围的证据显示需复核。时间诊断不会成为人工可填写的科学证据状态。

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

## Codex 父线程的原文依据

```bash
uv run rg --data-dir /tmp/researchgraph-demo session-parent --session 1
```

迁移21保存原生 `session_meta` 的父线程声明。支持头部 `parent_thread_id` 和旧版 `source.subagent.thread_spawn.parent_thread_id`；两处不同、重复头声明矛盾、身份无效、自引用、循环或父身份不唯一时不建立父会话投影。子线程先导入时等待父线程，父头后来导入后自动恢复；查询直接解读不可变声明，投影更新中断也不会把已知关系显示成未知。

`session_id` 是根会话字段，`forked_from_id` 是分叉来源；它们不能替代直接父线程。未声明父线程只证明头记录没有声明，不能证明这是根线程。项目归属不继承父线程；跨项目关系不落外键，查询不返回父项目会话编号或原文引用。只读接口 `/api/session-parent?session=1` 与原文证据响应使用同一实现，最近20条头观测仅作有界展示，关系判定包含全部已存声明。

迁移23只增加已存元数据核对游标，不读取原日志、不改L0。`scan/watch` 自动从已保存的首行头和解析账本可识别的头记录补记观测；源文件消失、旧实例轮换或旧备份恢复后仍可继续。每来源每批至多核对256条物理记录，批内观测与游标一起提交；新尾不会跳过旧缺口，已登记的头无需重读。记录时间是补记时刻，更老账本无法识别的非首行头仍不补造。

元数据积压时显示“父线程待补记”，没有完成身份核对前不发布唯一父关联；已观察的冲突或非法声明仍保留。`source_metadata_complete` 表示本会话已存来源核对到摄取游标，`identity_metadata_complete` 表示既有全库Codex身份核对已齐，两者都不证明源日志或历史完整。对象缺失、损坏或锁占用留缺口，继续其它来源；查询只读，恢复由后台执行。原文页展示覆盖状态，旧接口缺少覆盖字段时停用父导航；完整且同项目的关联才能打开父头。细节见[自动补记验收](docs/acceptance/Codex已存父线程自动补记验收_20261011.md)和[原生声明验收](docs/acceptance/Codex父线程关联验收_20261011.md)。

## Claude 事件父链的原文依据

```bash
uv run rg --data-dir /tmp/rg-demo event-chain --event 1
```

迁移22逐物理记录保存 `uuid`、`parentUuid` 与 `isSidechain` 声明，和原事件、解析账本及游标一起提交。正文仍按原有UUID及内容去重，fork／resume副本保留各自来源声明；多内容块共用一条记录依据。显式空父UUID、未声明与非法声明分别显示，旁支标记只有布尔值才有效，缺失不补为false。空父UUID不能证明根会话。

父UUID先在同来源文件实例核对，再按同项目的唯一等价记录组回退；未归属记录仅与未归属记录核对，不继承项目。同来源矛盾、不同副本的父声明／标记／正文冲突、缺父、跨项目及循环均明确显示。等价副本能证明父UUID时展示至多20个原文引用及完整计数；来源上下文不唯一便停止祖先追踪，不任选副本。祖先最多核对64步，达到上限仍未知；直接父身份和祖先诊断分别报告。

升级不读原日志；普通扫描每来源每批从已保存对象补记至多256条旧记录。原件已消失也能补记，损坏对象留缺口并计错，批内失败整体回滚。新尾部不能跳过旧元数据缺口；补记未完成时不判唯一父或无循环，不改L0及其时间。只读接口 `/api/event-chain?event=1` 与证据页共用读取快照，不读原日志、不调用模型。记录纳入备份及管理目录整项目清除，跨项目不返回父引用。详见[Claude父链验收](docs/acceptance/Claude事件父链验收_20261011.md)。
