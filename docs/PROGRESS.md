# 开发进度（只追加）

## 2026-10-09 · 初始核对

- 目录原有执行规格、Agent 说明、设计书和体量统计脚本；无实现、测试、Git 仓库或历史进度记录。
- 本次范围：先建立后端基础和 M1 提取流水线的可测试实现；遵守 §12，真实提取效果通过之前不开发界面。
- 用户指定 Atlas 为试点、OpenAI 兼容接口为模型接口。已验证试点路径 `/mnt/d/Documents/Atlas` 存在；该项目文件只读。
- 人工参考决定、参考问题、真实试点模型计数和效果指标尚未完成，不能宣称 M0/M1 验收通过。

## 2026-10-09 · 后端基础与有界提取流水线的阶段交付

- 任务：从规格起步建立可运行、可验证的后端基础；使用合成会话验收真实 DeepSeek 接口，并为用户指定的 Atlas 试点准备本地人工核对材料。完整 M0–M4 目标尚未完成。
- 改动的工程文件：`.gitignore`、`CLAUDE.md`、`pyproject.toml`、`uv.lock`；新建 Git 仓库、Python 3.12 环境，独立 uv 位于被忽略的 `.tools/bin/uv`。
- 改动的存储文件：`rg/store/schema.sql`、`database.py`、`objects.py`、`backup.py`；L0/候选/复核追加约束、事务建库、zstd 原文对象、FTS 字面量检索、范围状态保护、SQLite backup API。
- 改动的导入文件：`rg/ingest/common.py`、`claude.py`、`codex.py`、`scanner.py`、`spans.py`；多内容块、游标与残片、源实例轮换、去重、压缩与注入排除、原文 UTF-8 位置。
- 改动的提取文件：`rg/slim/tokens.py`、`slimmer.py`；`rg/extract/provider.py`、`redact.py`、`schemas.py`、`segmenter.py`、`working_set.py`、`validate.py`、`worker.py`、`report.py`、`prompts/pass1.txt`、`prompts/pass2.txt`；`rg/cli/main.py` 及各包入口。
- 改动的样本与测试：`fixtures/transcripts/claude/2.1-synthetic.jsonl`、`fixtures/transcripts/codex/2026-synthetic.jsonl`、`tests/conftest.py`、`tests/golden/` 与 provider/worker/组件预算/范围/原子建库/试点统计测试。样本全部合成，不含真实会话或真实密钥。
- 改动的脚本：新增 `scripts/measure_atlas.py`、`prepare_atlas_pilot.py`、`smoke_deepseek.py`。原有 `scripts/measure_transcripts.py` 未改动。
- 改动的文档：`README.md`、`docs/ResearchGraph_执行版_v2.md`（与原始规格字节一致的规范入口副本）、`REQUIREMENTS_STATUS.md`、`DECISIONS.md`、`PROGRESS.md`、`docs/acceptance/`。原始规格、设计书、AGENTS.md 与 Atlas 项目文件未修改。
- 验收环境：将 `.tools/bin` 加入 PATH，`UV_CACHE_DIR` 指向项目 `.cache/uv`。
- `uv sync`：`Resolved 23 packages in 12ms`；`Checked 22 packages in 42ms`。
- `uv run pytest -q`：`47 passed in 6.84s`。
- `uv run pytest tests/golden -q`：`36 passed in 5.85s`；仅代表当前已实现的固定案例，完整 §15 覆盖尚未完成。
- `uv run ruff check rg tests scripts/measure_atlas.py scripts/prepare_atlas_pilot.py scripts/smoke_deepseek.py`：`All checks passed!`。
- `uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help`：退出码 0，输出 init/project/import/health/search/evidence/preview/extract/review/backup 命令帮助。
- `uv run python scripts/smoke_deepseek.py --key-file api_key`：真实接口使用合成中文会话；2 个片段、5 条候选、0 个失败人工片段、0 个覆盖缺口、缓存通过，全部保持 candidate。发送前实测输入分别为 481/2886/481/2941 token；实际全输入为 461/2866/461/2921，输出为 48/361/48/482。严格引用校验通过。
- `python -I scripts/measure_atlas.py --manifest private/atlas_sessions_verified.json`：505 个文件（Claude 203、Codex 302），3,781,566,087 字节、307,061 条记录、431 个压缩点；只输出数字。修正路径前缀边界后文件集合一致。
- `uv run python scripts/prepare_atlas_pilot.py --manifest private/atlas_sessions.json --output /tmp/researchgraph-atlas-pilot-20261009`：选出 10 段，48 条候选，人工确认参考仍为 0、正式问题仍为 0。
- 密钥验证：API 凭据可用、指定模型可用；待纳入仓库文件的实际密钥匹配数 0，凭据与私有清单已被忽略。未发送任何真实 Atlas 会话到模型接口。
- 完整命令输出入口：`docs/acceptance/后端阶段验收_20261009.md`；需求与未完成项入口：`docs/REQUIREMENTS_STATUS.md`。
- 保存位置限制：复制人工核对材料到 `/mnt/d/ResearchGraphPilot/20261009` 被自动审批拒绝，理由是预览可能含敏感信息、具体目的地及转移未获明确授权。已向用户请求该目的地确认；未绕过，三份材料仍在 `/tmp/researchgraph-atlas-pilot-20261009`。
- 下一步：新会话先读本文件末尾、§2/§12/§13/§15 与决定记录；确认真实材料保存位置，完成至少 25 条人工参考和 5 个正式问题，补齐 M0 的保留设置/独立备份和 M1 尚缺的工作集/重叠逻辑，再进行真实效果验收。M1 达标前不进入界面开发，未达标按 §16 决定降级路线。HTTP/MCP、钩子、spool、快照、完整图等仍未完成；Web 验收尚不适用。

## 2026-10-09 · 保存 Atlas 人工核对材料

- 任务：按用户明确授权，将三份试点材料持久保存到 `/mnt/d/ResearchGraphPilot/20261009`。
- 改动的文件：仓库外三份材料；`README.md`、`docs/DECISIONS.md`、`docs/PROGRESS.md` 的入口与授权记录。
- 验收：本地复制脚本逐份读取并比对 SHA-256；已有同名文件内容不同时拒绝覆盖。退出码 0，输出 `{"destination":"/mnt/d/ResearchGraphPilot/20261009","files_verified":3,"candidate_references":48,"human_reference_count":0}`。真实原话未输出到开发上下文，未外发。
- 下一步：继续完成 M1 后端缺项，并由人工核对至少 25 条参考决定及 5 个问题。持久目录已获授权，先前保存阻塞已解除。

## 2026-10-09 · M1 可调预算、重叠与工作集

- 任务：按用户明确选择将完整输入预算改为默认 128000 token，允许调高；实现相邻回合/延续窗口重叠、证据归属、工作集优先级与显式范围校验。
- 改动的文件：新增 `rg/extract/budgets.py`、`paths.py`；修改 `provider.py`、`segmenter.py`、`worker.py`、`working_set.py`、`validate.py`、`prompts/pass1.txt`、`prompts/pass2.txt`、`rg/cli/main.py`、`scripts/smoke_deepseek.py`。新增 `tests/golden/test_overlap.py`、`test_working_selection.py`、`tests/test_budget_configuration.py`；调整 worker 与预算测试。更新 `README.md`、`docs/DECISIONS.md`、`REQUIREMENTS_STATUS.md` 及本文件；新增 `docs/acceptance/M1预算与重叠验收_20261009.md` 和 `deepseek_128k_smoke` 的 JSON、中文报告与合成模型输出。
- 预算：用户确认覆盖原规格 §7.2 的 24k 数值。输入默认 128000；内容分配 120000，工作集与指令/schema 各 3000，封装余量 2000，输出仍 4000。DeepSeek `/models` 实测窗口 1048576；预算加输出不得超窗。元数据缺失或计数失败不生成。
- 重叠：普通片段携带完整上一回合；超长回合内的延续/重试窗口保留最近不可拆组并记录遗漏位置。重叠不算新覆盖，每条候选必须有当前窗口证据。完整上一回合放不下时不静默截断，失败转人工并保留 pending。
- 工作集：直接 ID、字面量 FTS、同文件路径、本会话前序候选按序装填；长正文同一事件内按字节边界区分先前与后续。`--scope 字段=值` 可重复，完整范围相等筛选，模型增删字段时拒绝；未指定范围仍只能当带范围的检索线索，不能声称同范围选择已完成。
- `uv sync`：`Resolved 23 packages in 12ms`；`Checked 22 packages in 40ms`。
- `uv run pytest -q`：`66 passed in 25.25s`。
- `uv run pytest tests/golden -q`：`47 passed in 7.57s`；完整 §15 尚未覆盖。
- `uv run ruff check rg tests scripts/measure_atlas.py scripts/prepare_atlas_pilot.py scripts/smoke_deepseek.py`：`All checks passed!`。
- `uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help` 与 `uv run rg extract --help`：退出码 0；新增 `--input-budget`（128000 默认）与重复 `--scope`。
- `uv run python scripts/smoke_deepseek.py --key-file api_key --output docs/acceptance/deepseek_128k_smoke.json`：合成会话 2 片段、5 候选、0 人工失败、0 覆盖缺口，scope 一致、全部 candidate、缓存通过。前置实测输入 512/3069/556/3210；实际全输入 492/3049/536/3190，输出 48/338/48/557。本次没有测满 128k 的提取质量，未发送 Atlas 真实材料。
- 完整输出见 `docs/acceptance/M1预算与重叠验收_20261009.md`。原规格与入口副本仍字节一致，凭据和私有材料继续被忽略。
- 本地真实凭据检查：当时 70 个待纳入文件中实际密钥匹配为 0，仅输出数字。
- 下一步：下个任务先读本文件末尾、§2/§7.5/§7.7/§12/§13/§15 与决定记录；补齐 pass1 精确字节候选定位、范围选择、跨会话链接等 M1 后端缺项。人工核对持久目录的至少 25 条参考决定和 5 个问题后，再进行真实效果验收；真实 M1 达标前不开发界面。完整目标仍未完成。

## 2026-10-09 · M1 候选定位、链接、概览与 GitHub 初始维护

- 任务：落实 §7.5 精确字节位置、§7.8 逐对跨会话链接、§7.9 claims 概览；按用户提供的 GitHub 仓库开始维护。
- 改动的文件：新增 `rg/extract/locator.py`、`linker.py`、`overview.py`、链接/概览提示、`rg/store/migrations.py`、`tests/golden/test_locations.py`、`test_links_overview.py`、`tests/test_migrations.py`、`scripts/smoke_derived.py` 和 `.github/workflows/ci.yml`。修改 JSON 位置映射、瘦身、遮盖、schema、worker、数据库入口、CLI、试点定位脚本、worker 模拟测试；格式化统计脚本。更新 README、需求落实、决定与本文件；新增本轮验收文档及合成接口记录。
- 备份：工作区允许清单备份到仓库外临时 tar.gz，逐文件摘要比对，77 个文件一致；未复制凭据或真实试点材料。
- 定位：程序给出源 JSON 的字节范围，模型不能发明偏移；规则和模型来源分别持久化。处理重复块、转义、工具输出尾部和遮盖区域。版本 1 数据库事务升级到 2，失败回滚，L0 保留。
- 链接：跨会话、同完整范围，加标签 FTS 或同文件版本作为检索线索；每对只有两张卡片和一侧原文。输入 ≤3000、输出 ≤1000，总量 ≤4000。合法结果为 candidate；独立 attempt 合并和非法方向拒绝；不确定可返回 none。
- 概览：直接从 claims 分页，不读 L0 或旧摘要。记录输入与引用 ID，防回流标记保护，全部成功后原子发布。人工复核改变状态后重新计算缓存。
- `uv sync --locked`：`Resolved 23 packages in 10ms`；`Checked 22 packages in 26ms`。
- `uv run pytest -q`：`94 passed in 13.42s`。
- `uv run pytest tests/golden -q`：`72 passed in 12.14s`；仍不代表完整 §15 通过。
- `uv run ruff check rg tests scripts`：`All checks passed!`。
- `uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help`、`rg link --help`、`rg overview --help`：退出码均为 0，link/overview CLI 可用。
- `uv run python scripts/smoke_deepseek.py --key-file api_key --output docs/acceptance/deepseek_locations_smoke.json`：2 片段、5 候选、0 人工失败、0 覆盖缺口、全部 candidate、scope 与缓存通过。实测输入 598/3071/675/3266，实际全输入 578/3051/655/3246，输出 58/354/82/530；仅合成会话。
- `uv run python scripts/smoke_derived.py --key-file api_key`：直接构造 2 条合成候选；1 对链接返回 none、0 失败、缓存通过；概览 1 页。链接实测输入 748、实际 728、输出 238；概览实测 695、实际 675、输出 353。不是实际提取效果验收。
- GitHub：远端初始仅 LICENSE，已连接 origin 并从初始提交建立 `codex/m1-backend-20261009`。代码、公开中文文档、合成 fixtures 和离线 CI 准备提交；远端 PR 及验收结果完成后追加记录。
- 完整输出：`docs/acceptance/M1定位链接与仓库验收_20261009.md`。
- 打包与入库检查：`uv build --wheel` 成功；wheel 41 个文件，6 个必要的 schema/迁移/提示入口均存在，实际密钥匹配 0。88 个待提交文件实际密钥匹配 0，原规格副本字节一致；凭据、私有清单、工具和 Word 归档均被忽略。
- 下一步：完成本轮远端提交与 CI 检查后结束任务。下个任务读本文件末尾、§2 与相关章节，继续范围选择/链接调度等 M1 缺项；由人工核对至少 25 条参考和 5 个问题后进行真实效果验收，达标前不开发界面。原始规格保持不变，真实材料和凭据不外发、不入库。

## 2026-10-09 · GitHub 写入受限与本地交付

- 任务：完成可审阅的维护提交；记录真实远端写入结果。
- 改动的文件：新增 `docs/PR草稿.md`，补充本轮验收与本文件；维护成果保存于本地开发分支，公开代码 bundle 存放在忽略的 `.cache/researchgraph-backend-20261009.bundle`。
- 远端结果：GitHub 连接器创建 tree 与分支均返回 `403 Resource not accessible by integration`；本机 HTTPS 无可用登录，SSH 返回 `Permission denied (publickey)`。远端仍只有原始 LICENSE，未创建 PR；GitHub CI 未执行。full access 不提供 GitHub 服务的写入认证。
- 本地结果：代码与必要的中文文档已可审阅，94 个测试、72 个固定案例及 ruff/pyright 通过；以上命令输出见本轮验收。后续提交和 bundle 完整性验证追加在交付结果中。
- 下一步：恢复 GitHub 连接器写入权限或本机 Git 认证后，推送 `codex/m1-backend-20261009` 并创建草稿 PR，再检查远端 CI。下个开发任务读本文件末尾及 §2/相关章节，继续 M1 缺项；真实效果门槛仍待至少 25 条人工参考和 5 个正式问题。
- 交付检查：实现提交 `d982ceb`，89 个新增文件实际密钥匹配 0；`git diff --cached --check` 无输出、退出码 0。`git bundle verify` 通过，独立临时目录恢复 bundle 后，90 个已跟踪文件与工作区逐份字节一致，实际密钥匹配 0；包含原有 LICENSE。bundle 同时保留 HEAD 与开发分支引用，默认克隆可恢复。取消该开发分支对 origin/main 的临时跟踪，避免后续推送目标含糊。

## 2026-10-09 · M1 链接调度、额度暂停与标识符遮盖

- 任务：补齐 §7.8 跨页链接调度，并落实 §7.10 每日上限的暂停恢复。开始时读取本文件末尾、§2、§7.7–§7.10 与决定记录。
- 改动的文件：`rg/extract/linker.py`、`worker.py`、`redact.py`、`prompts/link.txt`、`rg/slim/tokens.py`、`rg/store/migrations.py`、`rg/cli/main.py`；新增 `rg/store/locking.py`、`tests/golden/test_link_scheduling.py`、`test_input_identifiers.py`，更新链接与迁移测试。更新 README、需求落实、决定、PR 草稿和本文件；新增本轮验收与合成接口成功/失败记录。
- 链接：版本 3 进度表记录 pending/done/failed；首次 50 对后可继续余下候选，已完成项不计数/生成。失败默认跳过，显式重试复用人工任务。系统进程锁限制同项目并发，导入仍可写库；进程终止释放锁。成功关系提交后的中断可以恢复进度，不重复候选。
- 暂停：每日额度不足保留当前项与覆盖缺口，不拆片或新增人工失败；接口不可用保留链接 pending。成功重试清除运行和暂停通知的旧错误。
- 遮盖：修复摘要数字被手机号规则误遮盖而使 pair ID 失配；只在规定的程序标识符路径保留原值，自由文本保持遮盖。none 输出端点必须为 JSON null；模型的错误端点结果作废，失败材料保留。
- `uv sync --locked`：`Resolved 23 packages in 12ms`；`Checked 22 packages in 111ms`。
- `uv run pytest -q`：`111 passed in 22.96s`。
- `uv run pytest tests/golden -q`：`88 passed in 21.88s`；仍不代表完整 §15 通过。
- `uv run ruff check rg tests scripts`：`All checks passed!`。
- `uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help` 和 `uv run rg link --help`：退出码 0；新增 `--retry-failed`，`--limit` 说明为新任务/重试上限。
- `uv build --wheel`：成功；打开 wheel 检查 `wheel_files=42 required_missing=0 actual_secret_matches=0`。原规格副本字节一致；已批准的试点目录三份材料仍存在，仅输出数字。
- 中断前的 DeepSeek 合成链接/概览测试已落盘：1 对返回 none、0 失败、缓存通过、概览 1 页、全部 candidate。链接发送前实测 911、实际全输入 891、输出 214；概览实测 695、实际 675、输出 406。原句柄中断后丢失且未发现活进程，没有重新调用以补造退出码；独立核验输出 schema 和结果，`synthetic_artifact_check=passed model_outputs=2 invalid_links=0 link_cache_ok=true`。
- GitHub 状态仍未改变：之前连接器写入 403，本机 Git 缺可用认证；未推送、无 PR、远端 CI 未执行。继续完成本地维护和公开代码恢复包，不重复尝试未改变认证条件的写入。
- 完整输出：`docs/acceptance/M1链接调度验收_20261009.md`。所有接口材料仅为合成输入，未发送 Atlas 真实材料。
- 提交前检查：`git diff --cached --check` 无输出、退出码 0。100 个已跟踪文件、24 个本轮暂存文件，实际密钥匹配 0，禁止入库的私有/环境文件 0。
- 下一步：结束本任务，下个会话读取本文件末尾、§2 与相关章节，继续 M1 的未指定范围工作集选择或监控缺项；真实效果仍待至少 25 条人工参考和 5 个正式问题，达标前不开发界面。GitHub 认证恢复后再推送本地分支并检查远端 CI；完整开发目标保持进行中。


## 2026-10-09 · GitHub 认证只读诊断

- 任务：检查 GitHub 认证与连接情况；未实际推送、创建远端对象或修改认证配置。
- 改动的文件：仅在 `docs/PROGRESS.md` 追加本次诊断；已有代码和文档改动保留。
- 验收命令与结果：公开 GitHub API 和 ResearchGraph 元数据均返回 `HTTP 200`；`git ls-remote origin HEAD` 退出码 0，返回 `2e6feaa3781e1a894005275acd56fd481d878d19 HEAD`。
- 本机写入预检：设置 `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=Never` 后运行 `git push --dry-run origin HEAD:refs/heads/codex/m1-backend-20261009`，退出码 128，输出 `fatal: could not read Username for 'https://github.com': terminal prompts disabled`。`gh` 未安装，`GH_TOKEN` 和 `GITHUB_TOKEN` 均未设置；存在 credential helper 配置，但本次未能提供可用凭据。
- 连接器验收：`get_profile` 成功识别 `FangWHao`；`get_repo` 成功读取仓库，并返回账号 `admin=true`、`push=true`；`list_installed_accounts` 返回 `accounts=[]`。账号权限不能证明集成拥有写入权限；本次没有复测连接器写操作，此前记录的 403 不能写成已恢复。
- 结论：网络与连接器账号认证有效；本机 HTTPS 推送缺少凭据，连接器 App 安装/仓库授权需要检查。
- 下一步：配置本机 GitHub 凭据或恢复连接器仓库写入授权，再按既有维护授权推送并检查 CI。本次诊断结束，不继续开发。


## 2026-10-09 · 准备本机 GitHub 浏览器登录

- 任务：按用户要求处理认证问题，准备本机 Git 登录。
- 改动的文件：安装官方 GitHub CLI 到仓库外 `/home/fanghao/.local/bin/gh`；追加本文件和决定记录。未修改项目代码、推送提交或写入远端。
- 验收：官方 GitHub CLI 发布资产 SHA256 与官方 API 提供的摘要一致；`/home/fanghao/.local/bin/gh --version` 输出 `gh version 2.102.0 (2026-09-30)`，退出码 0。
- 登录：已运行 `gh auth login --hostname github.com --git-protocol https --web --scopes workflow`，选择为 Git 使用同一凭据。生成浏览器设备授权码，等待账号持有人在 GitHub 完成授权；未将授权码或凭据写入文档。
- 下一步：用户完成浏览器授权后，检查账号为 `FangWHao`、配置 Git helper 并重复推送 dry-run。当前不能声称认证已恢复；等待用户完成必要的账号授权。


## 2026-10-09 · 本机 GitHub 认证恢复并验收

- 任务：用户完成浏览器授权后，验证本机登录与仓库推送权限。
- 改动的文件：仓库外 GitHub CLI 登录配置和 Git credential helper；凭据文件权限设置为 0600。仓库内仅追加 `docs/PROGRESS.md`；未实际推送或创建 PR。
- 备份：修改 Git helper 前备份 `/home/fanghao/.gitconfig` 到 `/tmp/researchgraph-git-auth-backup-gi0kqtcl/gitconfig`，逐字节校验通过，备份权限 0600。
- 验收：登录进程退出码 0，输出 `Authentication complete`、`Logged in as FangWHao`；`gh auth status --hostname github.com` 退出码 0，活动账号为 FangWHao，HTTPS 协议，授权范围包含 repo 和 workflow。
- `gh auth setup-git --hostname github.com`：退出码 0，Git 已使用 GitHub CLI 凭据。
- `gh api user --jq .login`：退出码 0，输出 `FangWHao`。
- `gh repo view FangWHao/ResearchGraph --json nameWithOwner,viewerPermission`：退出码 0，输出 `{"nameWithOwner":"FangWHao/ResearchGraph","viewerPermission":"ADMIN"}`。
- 非交互验收 `git push --dry-run origin HEAD:refs/heads/codex/m1-backend-20261009`：退出码 0，输出 `To https://github.com/FangWHao/ResearchGraph.git` 和 `* [new branch] HEAD -> codex/m1-backend-20261009`。仅预检，远端分支未创建；真实推送的服务器校验与 CI 尚未验证。
- 限制：当前环境没有可用系统密钥环，GitHub CLI 使用本机配置文件保存凭据，已限制文件访问权限；未读取或输出凭据明文。连接器写入权限未复测，本机认证恢复不能代表连接器 403 已修复。
- 下一步：本机 GitHub 认证任务结束；后续仓库维护可通过本机 Git/gh 完成，推送后再验证远端 CI。

## 2026-10-09 · 接续 GitHub 维护与本机认证复核

- 任务：链接调度实现已提交 `525b956` 后，恢复包比对发现另一项认证任务刚追加了本文件；保留协作记录，按原仓库维护授权接续发布。
- 证据：临时恢复的 100 个文件中只有本文件与工作区不同，恢复版与已提交 Git 对象一致；实际密钥匹配 0。差异为认证任务追加记录，没有覆盖或回退它，旧已验证 bundle 仍保留，待最终提交后刷新。
- 本轮只读复核：`gh --version` 为 2.102.0；`gh api user --jq .login` 输出 FangWHao；仓库 `viewerPermission=ADMIN`，origin 为 `https://github.com/FangWHao/ResearchGraph.git`。`git ls-remote --heads origin` 仅返回初始 main，`gh pr list --head codex/m1-backend-20261009` 返回空列表；以上命令退出码均为 0。
- 改动的文件：保留本文件新增的认证记录，更新 PR 草稿和链接调度验收中的当前认证状态。实现与测试结果不变。
- 下一步：提交文档，推送维护分支，建立草稿 PR，验证远端 CI；完成后刷新并独立恢复 bundle。完整开发目标仍未完成，主分支由评审合并。

## 2026-10-09 · GitHub 草稿 PR 与首次远端验收

- 任务：按已有维护授权完成推送、建立草稿 PR，检查真实远端 CI；保持完整开发目标进行中。
- 改动的文件：本轮发布记录更新 `docs/PR草稿.md`、链接调度验收和本文件，实现仍为 `525b956`，认证协作记录提交为 `3966eac`。
- `git push -u origin codex/m1-backend-20261009`：退出码 0，输出 `[new branch] codex/m1-backend-20261009 -> codex/m1-backend-20261009`，已设置同名上游；没有修改 main。
- `gh pr create --base main --head codex/m1-backend-20261009 --draft --body-file .cache/pr-body.md`：退出码 0，返回 `https://github.com/FangWHao/ResearchGraph/pull/1`。复核为 OPEN、isDraft=true，base=main，head 为维护分支。临时正文为中文，未包含推送准备段和凭据。
- 首次 push 运行 `37890513400` 与 PR 运行 `37890556219` 均 completed/success，验证 HEAD 为 `3966eac`。PR 远端日志：`111 passed in 4.97s`、`88 passed in 4.32s`、`All checks passed!`、`0 errors, 0 warnings, 0 informations`。
- `gh run watch 37890556219 --exit-status --interval 10`：退出码 0，`✓ backend in 21s`，全部步骤通过。查看进程/运行均按实际句柄与状态完成，没有因观察超时重启工作。
- 下一步：提交并推送这些发布记录，检查新 HEAD 的远端结果，刷新本地 bundle 并独立恢复比对后结束本任务。下个会话继续 M1 缺项；真实效果门槛仍需人工材料，M0–M4 未完成，草稿 PR 也不能替代完整产品验收。

## 2026-10-09 · M1 提取监控与逐次用量历史

- 任务：落实 §7.10 的利用率、校验拒绝、覆盖缺口和每日用量监控。开始读取本文件末尾、§2、§7.6–§7.10、§10 与决定记录；前任务已推送并通过远端验收，恢复包已独立核对 100 份文件。
- 改动的文件：新增 `rg/extract/monitor.py`、`tests/golden/test_monitor.py`；修改 worker、validator、linker、overview、数据库入口、版本 4 迁移、CLI、迁移测试和合成接口脚本。更新 README、需求落实、决定、PR 草稿和本文件；新增监控验收及初次/最终合成接口记录。
- 行为：每次尝试保留实测、实际用量、结束原因和失败；缓存不新增尝试。显式尝试 ID 避免两个 worker 交错时状态串线。片段占比按项目与片段去重，重试不放大占比；校验拒绝和引用失败分开。日额度暂停不冒充输入超长；未知实际用量保留预留。跨午夜按 UTC 预留日归属，旧历史不补造，缺口按事件阶段分页并附可查证的事件 ID。
- `uv run pytest -q`：`129 passed in 29.92s`。
- `uv run pytest tests/golden -q`：`105 passed in 28.19s`；完整 §15 仍未通过。
- `uv run ruff check rg tests scripts`：`All checks passed!`。
- `uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help` 与 `uv run rg health --help`：退出码 0。独立合成临时库实际运行 health CLI：`health_cli=passed project_filter=true daily_budget=50000 empty_gaps=0`。
- `uv run python scripts/smoke_derived.py --key-file api_key --output docs/acceptance/deepseek_monitor_final_smoke.json`：退出码 0，1 对链接返回 none、0 失败、缓存通过、概览 1 页、全部 candidate。两次尝试均 ok；输入 1566、输出 556、总量 2122，与每日计数一致，未结算为 0。6 个种子阶段缺口保留，因为脚本没有执行 slim/pass1/pass2；没有发送真实 Atlas 材料。
- 原规格副本仍字节一致。完整输出见 `docs/acceptance/M1提取监控验收_20261009.md`；打包、暂存密钥与恢复包检查在交付收尾运行。本轮提交继续追加到现有草稿 PR #1；远端最新检查以相应提交为准。
- `uv build --wheel`：成功；打开 wheel：`wheel_files=43 required_missing=0 actual_secret_matches=0 source_mismatches=0`，其中源码比对覆盖监控、worker 和迁移三个入口。恢复与提交检查只处理公开代码，不包含凭据或真实试点。
- 提交前检查：`git diff --cached --check` 无输出，退出码 0；`tracked_files=109 staged_files=23 actual_secret_matches=0 forbidden_tracked_files=0`。未把 formatter 对无关 provider 的纯排版变化纳入本轮。
- 下一步：完成本轮推送、远端检查和恢复包收尾后结束任务；下个会话继续 M1 的范围选择、规则确认或真实评估准备。M0–M4 的完整目标保持进行中，实际试点仍需人工参考与正式问题，达标前不开发界面。

## 2026-10-09 · 同会话并发保护与响应缓存恢复

- 任务：落实 §7.6 同会话顺序和 §7.10 重复处理；开始核对进度末尾、§2、§7.6–§7.10 与现有系统进程锁/尝试账本决定。前一监控提交 `03b0012` 已推送，PR 检查 37894266046 与推送检查均 success；恢复包仍保留前一已验证版本。
- 改动的文件：worker、validator、overview 和通用锁；新增 `tests/golden/test_concurrency.py`，调整提取、监控和范围复核测试；更新 README、需求落实、决定、PR 草稿、本文件，新增中文并发验收记录。
- 行为：同会话、同模型任务和同项目概览非阻塞互斥；项目进入任务缓存键。完整响应与尝试状态原子保存，重启可复用，仍严格验证原文；入库事务防重复，成功记录不被后续错误请求改成 invalid。忙不算失败，不拆片，不进人工失败队列，不关闭覆盖缺口。采集及不同会话可并行；服务离线仍能使用成功缓存。
- `uv run pytest tests/golden/test_concurrency.py tests/test_review_scope.py tests/golden/test_extraction.py -q`：`29 passed in 5.70s`。实际线程和子进程验证：相同模型任务只生成 1 次，相同响应仅入库 1 批；终止后可取锁恢复，未知用量保留原预留，没有根据文件存在推断进程存活。
- `uv run pytest -q`：`136 passed in 21.71s`。
- `uv run pytest tests/golden -q`：`112 passed in 20.17s`；完整 §15 仍未通过。
- `uv run ruff check rg tests scripts`：`All checks passed!`；`uv run pyright rg`：`0 errors, 0 warnings, 0 informations`。
- `uv run rg --help`：退出码 0；`uv build --wheel`：成功。打开 wheel 输出 `wheel_files=43 required_missing=0 actual_secret_matches=0 source_mismatches=0`；原规格副本字节一致。
- 首次完整回归为 127 项通过、2 项失败，原因是旧复核用例把不同结果写在同一个运行里；改为独立运行后通过。未放宽重复判定以绕过失败。并发测试使用合成提供方，没有新增远程模型调用。
- 提交允许清单检查：`git diff --cached --check` 无输出、退出码 0；`tracked_files=111 staged_files=14 actual_secret_matches=0 forbidden_tracked_files=0`。未纳入凭据、真实材料、数据库、环境、缓存与本地 Word 归档；草稿 PR 正文为中文，由当前文件生成。
- 下一步：完成允许清单提交、现有草稿 PR 推送与对应 HEAD 的远端验收，刷新恢复包并独立恢复比对后结束本任务。下个会话继续 M1 未指定范围的选择、自动确认规则或真实评估准备；人工参考门槛仍待满足，整个 M0–M4 未完成。

## 2026-10-09 · 独立原话确认与前端本地复核工作区

- 任务：§2、§5.3、§7.6–§7.7 原话规则确认，以及用户明确授权前端子 agent 后的 §9–§11 界面/本地 API。开始核对本文件末尾、相应章节与决定；基线 c91d3ea 已推送、远端验收和恢复包均通过。前端授权改变开发顺序，不改变真实 M1 门槛。
- 改动的文件：新增 rg/extract/rules.py、differences.py、rg/api/{server,views,__init__}.py、tests/test_api.py、tests/frontend_server.py、tests/golden/test_rule_confirmation.py；修改 validator、worker、working_set、report、CLI、schema 注释。新增 web 的 React/TS 页面、图、样式、API 客户端、类型、语义/浏览器测试及 package/锁文件/Vite/Playwright 配置；更新 CI、README、需求落实、决定、PR 草稿、本文件，新增两个中文验收记录。
- 原话规则：模型仍只插入 candidate；独立重读完整 L0 用户声明，核对身份、完整 scope、动作、理由、端口与字节，追加 rule:explicit-user-v1 审核证明。同名、未知范围、条件语句、工具/assistant、部分引用、自编理由均不自动确认；已有人工确认优先。规则与候选同事务回滚，自身规则动作不使完成缓存失效。
- 人工复核：同对象/类型/范围的有效人工确认差异可查。批量复核先校验全部成员与 revision，原子追加；人工修改另存 confirmed 替换版，原行和证据保留。API/报告区分原提取来源与实际审核来源，人工修改版不会因没有第二次审核被标成未复核。
- 前端：问题、方案、队列、完整事件时间线、健康、原文字面量检索和证据抽屉。严格分范围，同瞬间冲突和缺少发生时间显示待核对。图使用 React Flow/ELK；同范围汇合语义，多条记录不任意选择，候选过滤一致；partial/缺失端点/隐藏候选时拒绝折叠，保留组内部和边界证据。缺少传播、运行映射和产物 diff 如实显示。
- 服务：127.0.0.1 同源服务，每次随机令牌，页面清除 hash；Host/Origin/Fetch-Site、静态目录/符号链接、请求体上限、人工身份和 409 均经真实 HTTP 验证。serve 默认首页，review --open 直接打开队列。wheel 不含静态资源，独立部署用 --web-dir。
- uv sync --locked：Resolved 23 packages，Checked 22 packages；pnpm install --frozen-lockfile：Lockfile is up to date, resolution step is skipped，pnpm 12.10.1，退出码 0。
- uv run pytest -q：189 passed in 37.04s。包含新增 16 项 HTTP/CLI 和 37 项规则用例。
- uv run pytest tests/golden -q：149 passed in 23.16s；完整 §15 仍未通过。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations。
- uv run rg --help、serve --help、review --help：退出码均为 0，包含服务构建目录与直接复核入口。
- cd web && pnpm test：Tests 11 passed (11)；pnpm build：tsc --noEmit 和 Vite 构建通过。首页约 272 kB、图约 184 kB、ELK 约 1431 kB 懒加载；不把调高警告阈值当作缩小包体积。
- pnpm test:browser：4 passed (6.8s)；211 条合成候选跨界面 50 条页和 API 200 条页全部确认，检查所有原行仍为 candidate、有效状态 confirmed、来源 human。另验 hash 清除、图原文、完整时间线、健康未知值、项目/筛选切换、另一窗口 409、刷新、人工修改和检索。桌面/手机截图已人工查看，390 像素无横向溢出；本机仅解压临时浏览器运行库，CI 安装正式浏览器依赖。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl。打开比对：wheel_files=48 required_missing=0 actual_secret_matches=0 source_mismatches=0 private_or_static_files=0。原规格 Git 差异为 0。
- 验收修复：真实浏览器发现切项目时卸载队列会重置筛选，已保持独立加载；审查发现问题/图/汇合的跨范围回退与半行裁切，均已修正。新增汇合测试最初漏填 payload 必填类型字段，补齐后重新通过构建；没有放宽校验或用旧构建声称新源码通过。当前无新增真实会话读取或远程模型调用。
- 维护入口：现有草稿 PR #1；远端成功必须按当前提交另行验证。提交只选公开源码、中文文档与合成测试；凭据、真实材料、环境、构建、缓存和截图保持忽略。恢复包在提交后更新并独立核对。
- 提交前允许清单验证：git diff --cached --check 无输出、退出码 0；tracked_files=139 staged_files=40 actual_secret_matches=0 forbidden_tracked_files=0。wheel 对全部 43 份源码/SQL/提示资源核对，required_missing=0；中文 PR 正文从当前草稿生成，不含推送状态段和凭据。
- 下一步：下个任务继续 M1 未指定范围的工作集选择、真实人工参考准备与追加事件的增量提取，再推进 M2 采集/钩子/快照、MCP 和完整 M4 图语义。正式人工参考与问题仍缺失，完整 M0–M4 目标保持进行中，不以本轮 UI 或固定案例代替质量验收。

## 2026-10-09 · 追加事件增量提取与持久断点计划

- 任务：§2、§7.2–§7.10 的增量处理与完整覆盖。开始读取进度末尾、相应章节及预算/重叠/缓存决定；上一提交 da69367 的后端、前端与浏览器 CI 已通过，现有恢复包已验证。前端子 agent 的基础工作区已完成，完整目标继续推进。
- 改动的文件：新增 rg/extract/progress.py、tests/golden/test_incremental.py；修改 worker、validator、slimmer、CLI、版本 5 迁移及迁移测试；更新 README、需求落实、决定、PR 草稿、本文件，新增中文增量验收。
- 行为：不可变片段计划持久化所有权、字节边界、原始上下文与状态。追加回复/工具结果只处理新事件；旧正文不重复提取，排除与镜像不影响真实回合边界。中断恢复已完成子片段和覆盖视图，按原始顺序继续；长正文任一窗口 pending 仍保留缺口。候选/无候选结果与完成计划原子提交。人工失败只在显式 extract --retry-failed 时重发，队列更新去重、成功关闭，历史尝试保留。
- 旧库：事务升级至版本 5，保留 L0、定位和尝试历史；仅继承配置、完整前缀和项目证明精确匹配的旧完成缓存。预算/模型/范围/人工审核变化会重新核对，旧配置缺口不阻止新配置完成。元数据清单仍全量读取，不声称大库性能已验收。
- uv run pytest tests/golden/test_incremental.py -q：15 passed in 6.27s。
- uv run pytest -q：205 passed in 45.04s；uv run pytest tests/golden -q：164 passed in 32.24s。完整 §15 尚未通过。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations。
- uv run rg --help、extract --help：退出码均为 0，新显式重试选项有效，输入默认仍为 128000。
- cd web && pnpm test：Tests 11 passed (11)；pnpm build：TypeScript 与 Vite 通过；pnpm test:browser：4 passed (7.4s)，合成库真实 HTTP 与 Chromium 流程通过。本轮没有修改前端源码。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl。打开归档核对：wheel_files=49 source_resources=44 required_missing=0 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0；缓存目录构建提示未形成实际打包泄露。
- 初始回归复现追加回复重复旧决定；后续修复完整窗口覆盖汇总和排除事件的重叠边界。测试自身的两次失败为错误查询列和未触发多片段的合成预算，已修正测试条件并重跑。所有新增用例为合成材料，没有读取完整真实会话或远程模型调用。
- 下一步：完成允许清单提交、现有草稿 PR 的新 HEAD 远端验收及恢复包独立还原后结束本任务。下个会话继续 M1 未指定范围的工作集选择，再推进 M2 采集与快照、MCP 和完整 M4；正式人工参考与问题仍待核对，完整 M0–M4 不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=142 staged_files=14 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。只包含公开源码、中文文档与合成测试；远端结果按本次提交核对。

## 2026-10-09 · 扫描轮询与 spool 消费基础

- 任务：§2、§6.1/§6.5/§6.6、§10 的轮询与提示消费。开始读进度末尾、相应章节、里程碑边界和已有决定；上一增量提交 302f24b 已推送，两项 CI 均成功，当前恢复包已验证。完整目标保持 M0–M4，没有把质量门槛改成代码测试。
- 改动的文件：新增 rg/ingest/{sources,spool,watch}.py、tests/golden/test_spool_watch.py；修改 scanner、CLI、backup、health、版本 6 迁移与迁移测试；更新 README、需求落实、决定、PR 草稿、本文件，新增中文扫描/spool 验收。
- 行为：显式来源持久登记，定时递归发现新日志并复扫既有 import 文件。project 不从 cwd 推断，未知进收件箱；归属歧义与越界符号链接拒绝读取。来源文件、扫描消费与持续轮询使用系统进程锁。扫描器独立补齐，采集不联网、不调模型、不执行历史命令。
- spool：原始字节原子落盘，原件复制对象库后与不可变回执和 queued 任务事务登记；游标提交后确认。未知事件/坏 JSON 保留 failed，未归属/未出现/忙保留 queued；单轮最多 100 条按更新时间轮转，失败默认不重试。临时文件不消费，超 4 MiB 与符号链接提示保留并报告。确认前或确认后清理前退出均可恢复，已提交回执可不依赖队列文件重放；backup 包含全部回执原件。health.ingest 只统计，不把 running 当存活证明。
- uv run pytest tests/golden/test_spool_watch.py tests/test_migrations.py -q：23 passed in 5.12s；uv run pytest -q：222 passed in 48.87s；uv run pytest tests/golden -q：180 passed in 35.67s。完整 §15 尚未通过。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations。uv run rg --help、scan --help 退出码均为 0。
- 实际 CLI 子进程轮询合成 fixtures、Ctrl+C 退出再复扫：actual_cli_watch_exit=0 first_cycle_files=1 first_cycle_events=8 repeat_events=0 ctrl_c_stopped=True。真实 Linux 子进程在日志提交后 SIGKILL，恢复保留原回执与全部两条事件，重扫无新增；不是本机全部真实会话的 M2 验收。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl。归档核对：wheel_files=52 source_resources=47 required_missing=0 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0。cd web && pnpm test:browser：4 passed (7.2s)；前端源码未改动，真实本地 HTTP 与 Chromium 仍通过。
- 首次针对回归的 1 个失败是测试为两个项目使用同一唯一根目录，修正合成根目录后通过；没有放宽项目/解析器歧义检查。撤回 formatter 对无关 provider 的排版变化，并重建 wheel 比对当前源码。本轮不读取真实完整会话、不调用远程模型、不安装钩子、不改用户设置。
- 下一步：完成允许清单提交、现有草稿 PR 的新 HEAD 检查和独立恢复包还原后结束本任务。下个会话继续实际钩子与影子快照、运行映射；未指定范围的工作集、MCP 和完整图语义仍需落实，真实参考与质量门槛仍待核对，整个 M0–M4 不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=147 staged_files=16 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。仅公开源码、中文文档与合成测试，凭据/数据库/队列/缓存不入库，远端以本次提交实际结果为准。
