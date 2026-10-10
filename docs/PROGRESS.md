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

## 2026-10-09 · 实际钩子与影子快照基础

- 任务：§2、§6.8、§8.2、§11 的钩子、工作区快照与备份。开始读取进度末尾、相关章节和决定，上一扫描提交 8b40a68 已推送，两项 CI 成功，恢复包已验证。按 openai-docs 技能核对官方 Codex 钩子格式，保留 §14.8 开发会话禁用；完整目标仍是 M0–M4。
- 改动的文件：新增 rg/hooks、rg/snapshot、tests/golden/test_snapshots_hooks.py；修改 CLI、spool、对象写入、backup、health、版本 7 迁移与迁移测试、pyproject；更新 README、需求落实、决定、PR 草稿与本文件，新增中文快照/钩子验收。
- 行为：原始输入先原子保存，钩子异常静默 exit 0、不联网、不调模型、不读 transcript、不执行输入命令；明确根目录来自只读清单，init/hook-config 仅生成示例。独立裸仓库/index/暂存和目录句柄保存原始字节、执行位与链接目标，忽略规则有效，大文件/嵌套仓库明确遗漏，超时/忙/变化无有效提交。dirty 无法安全核定时显示未知，不由 Git status 重读遗漏文件。
- 持久性：独立快照原件与不可变回执追加入库，原提示先确认也不影响快照；提交/树/blob 全部存在才固定引用。原目录删除、提交后退出、队列缺失均可恢复一次。版本 6→7 失败整体回滚，旧快照不补造元数据。backup 与写入共用影子锁、不跟随队列链接，检查备份自身引用后才写成功清单；health 统计 skipped/partial/async_race/旧元数据未知。
- uv sync --locked：Resolved 23 packages，本项目重新安装；uv run rg --help、snapshot --help、hook-config --help 退出码均为 0。实际安装 rg-hook 子进程：installed_hook_exit=0 stdout_bytes=0 stderr_bytes=0 raw_unchanged=True sqlite_created=False。
- uv run pytest -q：246 passed in 51.75s；uv run pytest tests/golden -q：203 passed in 37.90s。新增 23 项快照/钩子固定案例及版本 7 迁移回滚；完整 §15 尚未通过。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations。
- cd web && pnpm test:browser：4 passed (7.8s)，现有本地 HTTP 与 Chromium 交互通过；前端源码未修改。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl。归档核对 wheel_files=61 source_resources=56 required_missing=0 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0，rg-hook 入口存在。
- 本机公开源码最终 20 次引擎基准：p95_ms=1426 skipped=0 valid_new_snapshots=20 captured_unique_paths=157，user_index_unchanged=True forbidden_snapshot_paths=0 actual_secret_matches=0 hooks_installed=False。按超过 300 ms 的规则保存 async 模式和请求/采集时间；不是实际客户端整条调用链的验收。本机独立备份恢复 objects=64 events=0 valid_shadow_snapshots=42 integrity=ok，只有公开源码快照和缺口元数据。
- 验收修复：第一次裸仓库初始化的 work-tree 参数冲突已修正；挂载盘暂存的 20 次超时如实记录，随后改进暂存、目录访问和元数据。坏 JSON 的异常类测试修正为 JSONDecodeError；错误参数测试已隔离 RG_DATA_DIR，并撤回本任务产生的 13 行类名日志，其他记录未删。补齐完整引用、原目录删除、备份锁与链接、大文件不重读等检查，没有放宽引用/预算校验或把失败当成成功。
- 下一步：完成允许清单提交、现有草稿 PR 新 HEAD 的远端检查和恢复包独立还原后结束本任务。下个会话继续运行/编辑/产物映射、后台大文件摘要、未指定范围的工作集、MCP 和完整图语义；真实参考、正式钩子与质量门槛仍待落实，整个 M0–M4 不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=158 staged_files=24 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。仅公开源码、中文文档与合成测试，凭据/快照/数据库/队列/缓存不入库，远端结果按新提交核对。

## 2026-10-09 · 前端健康统计续开发

- 任务：用户明确要求前端子 agent 开发，本轮落实 §2、§9、§10 中已有后端接口支持的健康展示。开始读取进度末尾、相关规格和决定，工作树干净；基线 01f60a7 的 bundle 验证可用，子 agent 另存并核对前端源码副本。上一快照提交的推送与 PR 两项远端检查均成功，完整 M0–M4 仍未验收。
- 改动的文件：前端子 agent 修改 web/src/{Views,types}，新增 HealthView、health 逻辑与样式、两份健康测试和中文续开发验收；父任务扩展 tests/frontend_server.py 的合成健康/模型账本种子，整合 README、需求落实、决定、PR 草稿和本文件。没有修改生产后端、原始规格或依赖锁。
- 行为：健康页分别展示全库来源/提示回执与任务、当前项目的重叠快照缺口和 UTC 模型阶段观测。字段缺失与真实零值分开；未完成账本不代表进程存活，异步标记不证明竞态错误，缺口计数不推算成功数。非 401 读错局部告警与重试，恢复后清除旧错误；401 沿既有登录逻辑处理。手机标题和统计范围分别成行，表格支持聚焦及方向键横向滚动。
- cd web && pnpm test：Test Files 2 passed (2)，Tests 15 passed (15)，Duration 483ms；pnpm build：TypeScript 通过，181 modules transformed，built in 925ms；pnpm test:browser：8 passed (8.2s)。包含原四项完整交互与新增四项健康统计、项目切换、旧对象缺失、503 恢复、401 及手机访问验收。
- uv run pytest -q：246 passed in 72.78s；uv run pytest tests/golden -q：203 passed in 60.71s；uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；uv run rg --help 退出码 0。全量与固定案例并行运行，耗时不作为性能基准；完整 §15 仍未通过。
- 合成 HTTP 种子实测：registered_sources=2、known_source_paths=2、spool_receipts=3、spool_unfinished=2（queued/running 各一条）、spool_failed=1；主项目快照 4/1/2/2/1，空项目 1/1/0/0/0，批量项目全部为 0。pass1 输入占比未知、未发送；pass2 真实统计为 0%、一个引用拒绝样本。模型账本和快照行明确是合成统计边界，实际远程调用为 0，不作为拍摄引擎或研究效果证明。
- 主 agent 核对源代码、接口口径、故障恢复与截图。桌面 1440 宽、小屏 390 宽的原始数据均已加载；发现标题被长范围说明挤断后由子 agent 最小修正，再次构建/浏览器验收并重拍，页面无横向溢出。截图和备份留在忽略目录，不读完整真实会话、不安装钩子、不改用户设置。
- 下一步：完成本轮允许清单提交、现有草稿 PR 新 HEAD 远端检查及恢复包独立还原后结束本任务；下个任务继续运行/编辑/产物映射、未指定范围工作集和 MCP。正式人工参考及真实 M1/M2 质量门槛仍需核对，不将界面提前实现视为整个目标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=164 staged_files=14 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。仅公开源码、中文记录与合成测试，凭据、截图、数据库、私有材料和缓存不纳入提交；远端结果按本轮新 HEAD 核对。


## 2026-10-09 · L1 运行、编辑与前端证据

- 任务：落实 §2、§5.1–5.3、§6.1–6.4、§8.1–8.3、§9 的运行与编辑记录；按用户授权由前端子 agent 接入。开始读进度末尾、相关章节与决定；基线 7e2659b 的恢复包已验证。中断后核对实际工作树和进程，旧子 agent 不在 live 列表，新的前端子 agent 接续现有代码而非重写。完整 M0–M4 仍未验收。
- 改动的文件：新增 rg/derive 的六个模块、tests/golden/test_l1.py；修改版本 8 迁移、scanner/codex/watch、slimmer、CLI、backup、health、API、迁移测试与合成浏览器种子。前端修改 App/Views/components/types、健康浏览器期望，新增 DateText、EventLocator、EvidenceRecords、l1 逻辑/样式和两份测试。更新 README、需求落实、决定、PR 草稿和本文件，新增中文运行/编辑与前端验收。
- 行为：从已复制 L0 离线派生，只按同会话唯一调用/执行器句柄关联，不按命令或时间猜测。运行观察与编辑记录只追加，工作队列与事实同事务；无结果不是失败、退出码缺失未知、冲突保留事实并降为未知。stdout 与 is_error 不补运行结果，开始时间无记录则为空。结果元数据变化不因正文相同被去重；非字符串 cwd 不会中止摄取。
- 版本：Claude 完整报告与核对补丁生成候选文本，摘要明确 sha256:tool-utf8；Codex 更新/删除只有补丁，新增前版本也未知。不读当前文件补旧 preimage，不绑定最近快照作运行 I/O。缺口、用户同时修改、换行边界、超限和请求不一致明确保存；backup 保留候选内容/补丁，原文件删除仍可恢复。版本 7→8 失败整体回滚、不捏造旧事实，旧瘦身缓存升级后撤销任意 stdout 退出码推断。
- 前端：全库事件编号入口补齐正文搜索不包含工具事件的实际入口，编号明确跨项目；原文请求/结果/观测可导航，双时间、候选、仅补丁、等待/失败/排除及部分清单可核对。长命令最多 8000 UTF8 字节，有完整字节数和正文上方预览提示；280px 键盘滚动区域保留完整原文链接。差异每份及同事件合计最多 64000 字节、每份最多 2000 行，超限不冒充完整版本。运行输入/输出版本仍明确未记录。
- uv sync --locked：Resolved 23 packages in 12ms，Checked 22 packages in 42ms；uv run rg --help、derive --help 退出码均为 0。实际已安装 CLI 删除合成源文件后三次 derive --limit 1，依次 done/remaining=1/1、1/0、0/0，最终 exited/code0，source_deleted=True、l0_unchanged=True。
- uv run pytest tests/golden/test_l1.py -q：49 passed in 5.39s；uv run pytest -q：296 passed in 88.21s；uv run pytest tests/golden -q：252 passed in 75.41s。全部/固定案例并行运行，不作性能基准；49 项新 L1 案例和版本 8 回滚不替代完整 §15/M2。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=67 source_resources=62 required_missing=0 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0，派生模块包含在包内。
- cd web && pnpm test：3 files、21 tests passed，734ms；pnpm build：TypeScript 通过、186 modules、built in 1.41s；pnpm test:browser：13 passed (13.4s)。实际 HTTP/Chromium 核对运行、候选编辑、补丁、短命令和 404；独立合成响应核对旧字段、partial、超限预览与 401。真实后端超长命令另由固定案例验证，前端边界响应不作为该采集证明。
- 验收修复：首轮前端新增三项因工具事件正文搜索未命中而超时，原八项通过；增加编号入口后最终十三项全部通过。后续补预览说明前置、键盘滚动、未知完整性和跨项目说明，并复验。父 agent 实看桌面完整编辑、小屏补丁及预览截图，页面/抽屉无横向溢出。前端冻结 13 个文件，副本、截图与构建产物均在忽略目录；不读真实完整会话、不调远程模型、不装钩子、不改个人设置。
- 下一步：完成允许清单提交、现有草稿 PR 新 HEAD 的远端检查和恢复包独立还原后结束本任务。后续继续完整运行 I/O、后台大文件摘要、未指定范围工作集、显式记录与 MCP/完整图；真实人工参考、正式客户端及完整 M1/M2 门槛仍待核对，整个目标不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=180 staged_files=37 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。仅公开源码、中文记录和合成测试；密钥、真实会话、私有材料、截图、数据库、队列及缓存不入仓库，远端结果按本轮新 HEAD 核对。


## 2026-10-09 · 人工问题入口与前端创建

- 任务：§2、§5.1/5.4、§8.1、§9、§10 的人工 question 完整入口，前端继续由用户明确要求的子 agent 实现。开始读进度末尾、相关规格及决定，基线 7556835 工作树干净，已核对恢复包相同 HEAD。note 类型缺规格及决定，按 AGENTS.md 提问后留待答复；decide 歧义复核及客户端包装留后续任务。完整 M0–M4 仍未验收。
- 改动的文件：新增 rg/record 的人工问题/schema 服务、tests/golden/test_manual_question.py；修改版本 9 迁移、CLI、API、物理来源筛选、健康统计、迁移测试及合成浏览器种子。前端修改 App/Views/types，新增 ManualQuestionDialog、manualQuestion 逻辑/样式及两份测试。更新 README、需求落实、决定、PR 草稿与本文件，新增中文后端/前端人工问题验收。
- 行为：问题文字原样留存，手工来源 human/manual 直接 confirmed，确认问题不推导采用、证据成立或运行成功。未知范围保留 null，仅人工问题的独立 schema 与追加修改允许；漏交 scope 不清空范围，模型规则继续严格。L0 保留原回填时区，发生时间 UTC、入库时间独立；原件/引用/问题/回执同一事务，失败整体回滚。
- 持久性：UUID 区分人工意图，已有内容重放返回原记录和当前图版本；不同项目/内容/身份/范围/时间冲突 409，新写入版本不一致不写入。两个连接并发只有一份原文/问题；旧重试不会撤销后续驳回。备份恢复保留原件和回执。虚拟 rg 输入不作为物理日志来源、不误报缺失、不再次提取。16000 字节正文、65536 字节原件、8000 字节引用窗口与 UTF8 边界有效。
- uv sync --locked：Resolved 23 packages in 23ms，Checked 22 packages in 70ms；uv run rg --help、question --help 退出码 0。真实已安装 CLI 接收含命令替换/反引号/分号的合成文字，原样保存，第二次 UUID 重试 replayed=true；不执行输入文字。
- uv run pytest tests/golden/test_manual_question.py tests/test_migrations.py -q：49 passed in 8.22s；uv run pytest -q：336 passed in 93.38s；uv run pytest tests/golden -q：291 passed in 80.06s。全量与固定案例并行，不作性能基准。39 项人工问题案例和版本 8→9 回滚不替代完整 M2/§15。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=70 source_resources=65 missing=0 mismatches=0 actual_secret_matches=0 private_or_static_files=0，人工记录模块包含在包内。
- cd web && pnpm test：4 files、27 tests passed，627ms；pnpm build：TypeScript 通过、189 modules、1.31s；pnpm test:browser：19 passed (17.1s)。真实 HTTP 验证人工问题、JSON 原文窗口摘要、未知范围追加修改、成功后丢响应的同意图重放、真实 409 人工刷新、401、小屏和跨项目/迟到响应草稿保护；使用两个独立合成项目，原十三项用例均保留。
- 验收修复：初次 CLI 测试选了无模块执行入口的路径，改用真实已安装 rg 后通过。前端首轮 17 项通过、1 项旧修改输入框定位超时，补稳定标签后全部通过；原文窗口按 JSON 转义字节核对，没有把解码文本冒充原文 hash。父审查发现旧请求晚到可能清掉新草稿，子 agent 修复意图核验并加真实 HTTP 延迟测试。父 agent 实看 1440 桌面与 390 小屏表单，无横向溢出；前端 9 文件冻结校验均 OK。
- 边界：本轮只写合成材料，不读完整真实会话、不调远程模型、不装钩子、不改个人客户端设置。未保存草稿仅当前页面内存；本机操作/令牌是人工入口信任边界，human 字段不是操作系统身份认证，Agent MCP 候选入口尚待实现。
- 下一步：完成允许清单提交、现有草稿 PR 新 HEAD 远端验收与恢复包独立还原后结束本任务。后续接续 decide 对象歧义复核、note 类型答复、客户端包装、MCP、完整运行 I/O、后台大文件摘要与全图语义；正式人工参考和真实 M1/M2 门槛仍待核对，整个目标不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=191 staged_files=27 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。只包含公开源码、中文文档及合成测试，前端冻结源码恒等；凭据、真实会话、私有材料、截图、数据库、缓存和构建产物不纳入提交。远端检查与独立恢复将按本轮新 HEAD 核对。


## 2026-10-10 · 人工决定与对象歧义选择

- 任务：落实 §2、§5.1–5.4、§8.1、§10 的 decide、全项目对象检索及歧义复核，前端按用户明确授权由子 agent 接续。10-09 开始、10-10 收尾；开始读进度末尾、相关章节及决定，基线 a8e2529 的恢复包 SHA256 与独立还原已核对。完整 M0–M4 仍未验收。
- 改动的文件：新增 rg/record/{events,decide,targets,resolve}.py、rg/store/scopes.py、tests/golden/test_manual_decision.py；修改版本 10 迁移、Store、question 共用原件、规则/工作集、CLI/API、迁移测试和合成浏览器种子。前端六个既有文件，新增两个弹窗、意图逻辑、hook、样式及两份测试；新增中文前后端人工决定验收，更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：四人工动作按整个项目的有效版本、完整范围解析，精确 ID 优先、完整标签相等；唯一对象直接确认决定，不确认目标候选或证据。省略范围保留 null，含未知值的范围不判断当前采用。同名或未知对象保持 null 目标 candidate，只有项目载体、没有伪造方案版本；普通、批量确认和通用修改不能绕过对象歧义。
- 选择：仅选同项目、原完整范围内的有效对象，不改动作/理由/范围；验证原文后追加人工确认替换、复核和选择原件，保留原发生时间、原记录及双原文。完整项目先判唯一再分页，图 2000 条上限外同名仍歧义；长标签 8000 UTF8 字节预览与完整搜索分开。
- 持久性：问题/决定/选择 UUID 跨操作保护；同意图并发和丢响应只有一份事实，旧重放不撤销后来驳回。L0、引用、记录、审核及回执原子提交，版本 9→10 失败整体回滚、原问题仍可重放。备份删除原对象后仍能恢复决定、选择及回执。模型 schema 不变，人工原话独立校验；旧模型决定连续人工修改保留原 speaker/explicitness。
- uv sync --locked：Resolved 23 packages in 23ms，Checked 22 packages in 65ms；uv run rg --help、decide --help、decision-targets --help、resolve-decision --help 退出码 0。真实已安装 CLI 用参数数组原样接收命令替换、反引号、分号，随后选择目标，不执行输入文字。
- uv run pytest tests/golden/test_manual_decision.py tests/test_migrations.py -q：61 passed in 28.24s；uv run pytest -q：387 passed in 128.95s；uv run pytest tests/golden -q：341 passed in 116.14s。全量与固定案例并行，不作性能基准。50 项决定固定案例和 11 项迁移检查不替代完整 M2/§15。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=75 source_resources=70 missing=0 mismatches=0 actual_secret_matches=0 private_or_static_files=0，新增模块与四份提示词按源码字节匹配。
- cd web && pnpm test：5 files、33 tests passed，751ms；pnpm build：TypeScript 通过、194 modules、1.08s；pnpm test:browser：30 passed (23.8s)，保留原 19 并新增 11 项。真实 HTTP 核对创建/选择、目标不被确认、全项目分页、空范围、400/409/401、并发点击、丢回包、跨项目实际写入及迟到响应；模拟响应仅核对预览显示边界。
- 验收修复：父审查修复旧模型决定连续人工修改误套新人工原话 schema，并补回归；前端首次小屏范围筛选横向溢出已修。定位与分页等待同步修正后最终 30 项全部通过。父 agent 实看桌面/390 小屏创建和选择四张图，核对 14 文件冻结清单全部 OK；缓存、副本和截图留忽略目录。
- 产品方向：用户提出理想日常体验零人工干预；自动采集、提取、关联和查询应为默认，人工入口是可选补记/纠错，不强迫逐条复核。候选和未知仍保留检索，现行原话规则确认要求继续执行。本轮不读完整真实会话、不调远程模型、不装钩子、不改个人客户端设置。
- 下一步：本轮允许清单提交后按新 HEAD 检查现有草稿 PR 两种远端 CI、刷新并独立恢复代码包，实际证明记录在 PR 与忽略目录，随后结束本任务。下个任务优先继续自动处理链路、客户端包装、MCP、未指定范围工作集及后台摘要；note 类型待答复，正式参考和真实 M1/M2 质量门槛仍待核对，整个目标不标完成。
- 提交前允许清单检查：git diff --cached --check 无输出、退出码 0；tracked_files=206 staged_files=37 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True。仅公开源码、中文文档和合成测试，前端冻结源码恒等；凭据、真实会话、私有材料、截图、数据库、缓存及构建产物不入提交，远端按本轮新 HEAD 核对。

## 2026-10-10 · 持久自动队列与持续提取

- 任务：落实 §2、§4、§7.2/7.6/7.7/7.10、§10/11、§12 M3 的持久队列和扫描→提取循环，响应用户日常零人工干预方向；前端按既有明确授权由子 agent 接续。开始读进度末尾、相关章节和决定，b75f62c 基线恢复包独立还原与 206 跟踪文件已核对；完整 M0–M4 仍未验收。
- 改动的文件：新增 rg/extract/{queue,queue_health,estimate}.py、rg/slim/cached.py、tests/golden/test_auto_queue.py；修改 worker、版本 11 迁移、Store、CLI、迁移和合成浏览器种子。前端新增队列面板和浏览器测试，修改健康页、类型、逻辑、样式及单测；新增两份中文持续提取验收，更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：不指定会话则自动发现已归属且允许外发的 claude/codex 会话，虚拟人工来源排除；watch 接扫描/spool/L1/提取，项目仅筛选模型阶段，离线采集仍全库。任务固定最大事件和不可变输入；晚到内容进下一任务，跨截止窗口等待完整计划，不重复旧完成候选。审核变化本身不触发无新增内容重排，worker 原审核配置合同与人工优先仍保留。
- 持久性：一个数据目录一个系统锁调度器，领取编号保护提交，网络期间不占 SQLite 写事务；取得系统互斥才接管遗留 running，并追加恢复依据。日额度等下个 UTC 日，原文追加和切回旧配置保留同配置等待；忙碌轮转，失败默认保留缺口，watch 显式重试仅首轮。完整响应先缓存后重新校验，原始分量实测缓存支持服务离线接续，新生成完整请求仍逐次实测/核验窗口。
- uv sync --locked：Resolved 23 packages in 13ms，Checked 22 packages in 123ms；uv run rg --help、extract --help 退出码 0。真实已安装 CLI 对本机明确模拟计数/生成服务持续处理两次新增，events=2、claims=2、generation_calls=4、pending=0、done 两项，Ctrl+C 正常退出 0；不冒充真实远程计数。
- uv run pytest tests/golden/test_auto_queue.py tests/test_migrations.py -q：47 passed in 14.83s；uv run pytest -q：423 passed in 144.75s；uv run pytest tests/golden -q：376 passed in 131.61s。全量与固定案例并行运行，不作性能基准；35 项新增队列案例与 12 项迁移验证实际进程强杀、未知用量、归属、额度、窗口、权限、备份和版本 10→11 回滚，不替代完整 M3/§15。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=79 source_resources=74 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0，全部模块与提示按源码字节匹配。
- cd web && pnpm test：5 files、36 tests passed，575ms；pnpm build：TypeScript 通过、195 modules、973ms；pnpm test:browser：33 passed (25.5s)，保留原 30 并新增 3 项。真实 HTTP 核对七状态、固定范围计数、53/1/0 项目与全库 54、首 50/末 3 共享分页、图版本/候选/缺口不变及无写请求，旧字段覆盖只验展示边界；父 agent 实看桌面与 390 小屏图，冻结八文件全部 OK，无横向溢出。
- 合成实测：只向用户授权的 DeepSeek 接口发送脚本内一句合成会话，生成两次、新增两个 candidate、pending=0、重跑零生成。pass1 实测输入/已用输入/输出 598/578/58，pass2 3071/3051/352，均 ok。凭据与实测证明留忽略目录，不读真实完整会话、不发送真实材料、不装钩子、不改个人设置。
- 验收修复：初次测试误设拆片调用次数，改用计数不可用验证零生成和显式恢复；子进程改用 Path。缓存恢复暴露提前窗口查询和重复分量计数，已修复并加中断位置回归；恢复取消配置不能清掉原额度等待，也加了固定案例。较早全量 422/固定 375 不作最终证明，最后源码对应 423/376。前端中途服务 503 不计作应用失败；专项通过后核对没有完整运行输出，补一次完整 33 项并保存 stdout。
- 下一步：允许清单提交后核对现有草稿 PR 新 HEAD 的两种 CI、刷新并独立还原恢复包，再结束本任务。后续继续自动关联/问答、客户端包装与 MCP、完整运行 I/O、后台大文件摘要和全图语义；系统常驻服务未安装，note 类型待答复，正式参考与真实 M1–M4 门槛仍待核对，整个目标不标完成。
- 提交前允许清单检查：tracked_files=215 staged_files=25 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True；实际暂存内容与凭据/私有路径核对通过，git diff --cached --check 退出码 0。前端锁文件安装跳过解析、245ms，原锁与八文件冻结清单保持一致；源码、中文记录和合成测试之外的材料不纳入提交，远端按新 HEAD 验收。

## 2026-10-10 · 只读 MCP 与历史状态卡

- 任务：落实 §2、§5.1/5.3/5.4、§6.5、§7.11、§10 与 M4 的只读 Agent 入口；开始读进度末尾、相关章节和决定，4f3ef5d 基线恢复包 SHA256、独立还原和 215 跟踪文件已核对。用户取消固定 1500 token MCP 上限，状态卡保留调用方预算和本地精确编码；完整 M0–M4 未验收。
- 改动的文件：新增 rg/query/{reader,context,tokenizer}.py、两个包入口、rg/mcp/{server,tools}.py、两份公开压缩词表与来源/许可证、scripts/check_mcp_client.py、tests/golden/test_read_tools.py；修改 Store/ObjectStore 只读打开、CLI、HTTP GET、依赖锁和 CI；新增中文 MCP 验收，更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：固定项目的 search/node/evidence/history/context 只读工具与 context CLI；mode=ro/query_only/对象写禁用，不建库、不迁移、不补应用缓存。发生与已知双截止、历史审核与替换，完整范围与整个历史计算采用/证据，再分页；候选、未知范围/时间及同刻冲突保持明确，超过 20 个来源 ID 标记 partial。按版本引用取观察，按真实 UTF8 字节连续取已复制原文，不读当前文件补旧证据。
- 状态卡：按优先级尝试详情，超额给引用，按实际保留项推进下一页；标记整体本地实测，默认 2000、可调高。正式 cl100k/o200k 词表随包校验，查询不联网，不冒充目标模型计数。文本及执行错误包 rg-context，源尖括号不能关闭外围标记；重新导入排除。MCP 新版逐请求元数据、discover、缓存字段与旧初始化共存，stdout 仅协议、通知无回包、坏帧恢复、EOF 退出。
- uv sync --locked：Resolved 28 packages in 6ms，Checked 27 packages in 102ms；uv run rg --help、mcp --help、context --help 退出码均 0。
- uv run pytest tests/golden/test_read_tools.py tests/test_api.py -q：66 passed in 20.23s；uv run pytest -q：473 passed in 155.39s；uv run pytest tests/golden -q：426 passed in 142.45s。全量与固定案例并行，不作性能基准；50 项新增案例与原 16 项 API 不替代真实 M4 找答案时间。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=90 source_resources=85 source_mismatches=0 actual_secret_matches=0 private_or_static_files=0，两份词表与许可证存在。独立环境安装 wheel、隔离解释器及实际 CLI 两种编码分别 669/624 token，数据库逻辑不变。
- PYTHONPATH=. uv run --no-sync --with 'mcp==2.3.0' python scripts/check_mcp_client.py：SDK 2.3.0，auto→2026-07-28、legacy→2025-11-25；各列出五个工具并完成五次查询，状态卡 636/640 token，逻辑库不变。仅临时合成项目，已加入 CI。初次官方客户端发现必需缓存字段缺失后修正；初次 wheel 测试受当前目录遮蔽，改隔离解释器核对，不计为安装包通过。
- cd web && pnpm test:browser：33 passed (28.0s)。HTTP GET 改只读后原人工写入与界面仍通过；前端八文件冻结清单全部 OK。本轮未改前端源码、不调提取模型、不读真实完整会话、不装钩子、不改个人客户端设置；公开词表与私有材料分开。
- 下一步：允许清单提交后更新现有草稿 PR，按新 HEAD 检查推送/PR 两种 CI并独立恢复代码包，再结束本任务。后续继续客户端包装、自动关联与有界问答、后台大文件摘要、完整运行 I/O 及全图语义；note/propose_note 类型待答复，大库性能/取消/吞吐仍待验收，正式参考和真实 M1–M4 门槛保留，整个目标不标完成。
- 提交前允许清单检查：tracked_files=229 staged_files=26 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True；git diff --cached --check 退出码 0。仅公开源码、正式词表/许可证、中文文档和合成测试；前端冻结源码恒等。远端结果与独立恢复按本轮新 HEAD 验收，不用旧提交成功替代。

## 2026-10-10 · 有界问答与本地检索后备

- 任务：落实 §2、§7.2/7.7/7.10/7.11、§10/11 与 M4 的 ask；开始读进度末尾、相关章节和决定，de7b0ed 基线恢复包独立还原，229 跟踪文件与 SHA256 已核对。完整 M0–M4 尚未验收。
- 改动的文件：新增 rg/query/retrieval.py、rg/extract/qa.py、rg/extract/prompts/qa.txt、tests/golden/test_qa.py、中文有界问答验收；修改 CLI、README、需求落实、决定、PR 草稿与本文件。前端源码未改。
- 行为：默认最多 12 条校验原文、每窗口 4000 UTF8 字节，K 与窗口可调；有限字面量匹配记录/对象相关决定及无结构化记录的原件后备。完整范围、发生/已知截止、历史审核和完整状态计算继续保留。范围下不猜原件归属，缺失/损坏/达到上限明确列出；没有有效证据零调用并回答无法判断。
- 生成：既有 qa 执行器实际完整请求计数、权限、128k/4k、日额度、锁和缓存；逐条来源 ID/逐字引文校验，遮盖状态由程序给出。截断、污染、超预算及坏回答作废，保留检索结果。模型解释仅缓存，不写研究 claims；标记防回流，引用有效不证明语义正确，生成中记录版本变化显式报告。
- uv sync --locked：Resolved 28 packages in 14ms、Checked 27 packages in 49ms；uv run rg --help、ask --help 退出码均 0。真实已安装 CLI 的 retrieve-only 使用只读库，含命令替换、反引号和分号的参数不会执行，逻辑库不变。
- uv run pytest tests/golden/test_qa.py tests/golden/test_read_tools.py tests/test_worker.py -q：84 passed in 15.38s；uv run pytest -q：504 passed in 161.63s；uv run pytest tests/golden -q：457 passed in 147.77s。全量与固定案例并行，不作性能基准；31 项新增案例不替代真实 M4 找答案时间。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=93 source_resources=88 missing=0 mismatches=0 actual_secret_matches=0 private_or_static_files=0，问答提示词已包含。隔离环境重新安装、真实 CLI 连明确本机模拟服务，计数 2/生成 1、重问复用、L0 不变、claims=0；不冒充提供方计数。
- 合成 DeepSeek 最终实测：只发送脚本内一条合成消息，实测输入/报告输入/输出 1336/1316/428、状态 ok；生成 1、重问新增 0、同一 run、原件不变、claims=0。本轮两次独立实测共生成 2；首次附注误述遮盖后增加程序字段与提示，最终正确说明未遮盖。实际引文可核对，自由解释仍不宣称已证明正确。密钥、私有材料及证明留忽略目录。
- 验收修复：测试 fixture 误走含隐私原文的模型入库路径、误用 Parsed/CLI 接口及未剥除输出末尾换行已改正；坏引用与压缩对象分别列缺口，不回显模型失败原件。最终输出按 84/504/457 核对，早期失败不当作通过。未读完整真实会话、不发送 Atlas 材料、不改个人设置。
- 下一步：本轮允许清单提交、更新既有草稿 PR，按新 HEAD 核对两种远端 CI并独立还原代码包后结束本任务。后续继续问答界面、客户端包装、自动关联串联、完整运行 I/O、后台摘要和全图语义；note 类型待答复，正式参考与真实 M1–M4 门槛保留，整个目标仍未标完成。
- 提交前允许清单检查：tracked_files=234 staged_files=11 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True；git diff --cached --check 退出码 0。前端八文件冻结清单全部 OK，源规格保留原文。仅公开源码、中文文档和合成测试提交；凭据、真实材料、截图、数据库、缓存和产物保留在忽略目录。


## 2026-10-10 · 问答界面与项目外发许可

- 任务：落实 §2、§7.2/7.10/7.11、§9/10/11 的问答 HTTP 与前端，响应日常零人工审核方向；按用户既有明确授权使用前端子 agent。开始核对进度末尾、相关章节和决定，d355626 基线恢复包独立还原，234 个跟踪文件字节一致；完整 M0–M4 未验收。
- 改动的文件：新增 rg/api/qa.py、tests/golden/test_qa_api.py、tests/qa_browser.py、两份中文问答接口/前端验收；修改 server、CLI、provider、qa 输入准备和提示、合成浏览器服务及固定问答案例。前端新增 QaView、qa 类型/解析、工作区控制器、样式与单测/浏览器测试，修改 App 和健康种子预期；更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：界面先本地检索，可明确只读；服务配置模型后才生成。地址、密钥和输入预算由本机服务配置，HTTP 不接受浏览器覆盖，不返回服务地址/凭据。默认 128k/4k、提供方完整请求实测、日额度、互斥、缓存和作废合同不变；无来源零调用，模型故障仍保留来源，不创建研究 claims。
- 权限：本地预览与生成共用实际遮盖输入；主动许可、来源、摘要和记录版本事务核对。摘要绑定资料与模型配置，新增原文即使 claims 版本不变也重验。沿用整个项目许可，明确包含远程计数、自动提取、问答及新增资料；撤回后缓存也先检查许可，不删除原件。页面停止等待只取消浏览器等待，已发出的请求可能继续处理。
- 界面：项目草稿与结果分别保存在页面内存，请求序号、鉴权代次和完整问题意图匹配后才显示；改问题/范围/双截止后旧结果显式标旧，迟到不能覆盖新页面。封装 JSON 只作数据，原文 HTML 作为文字；引用定位同事件、对象内窗口与 hash，来源文件行位置另标。候选、确认、驳回、替换和采用/证据状态分别保留，无需先逐条人工确认。
- uv sync --locked：Resolved 28 packages in 14ms、Checked 27 packages in 62ms；uv run rg --help、serve --help 退出码均 0。uv run pytest tests/golden/test_qa_api.py tests/golden/test_qa.py tests/test_api.py -q：71 passed in 29.53s；新增 23 项 HTTP 固定案例和 1 项元数据不能当原文引用的回归。
- uv run pytest -q：528 passed in 177.04s；uv run pytest tests/golden -q：481 passed in 163.96s。全量与固定案例并行，不作性能基准。uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。
- cd web && pnpm test：6 files、45 tests passed、692ms；pnpm build：199 modules、1.05s；pnpm test:browser：43 passed (33.7s)。父 agent 实看桌面/390 小屏四图，最终标签更新后再核对桌面及原尺寸手机预览；无横向溢出。13 项冻结清单全部 OK，摘要 573a36a0c958e8c7bf605d6bb9eaec1079dbd3751b5d6e4694e6ca036d72f84c。真实 HTTP 主链与外部模型协议模拟、界面异常拦截分别记录，不冒充真实模型质量。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；wheel_files=94 source_resources=89 missing=0 mismatches=0 actual_secret_matches=0 private_or_static_files=0，问答 API/提示和词表许可证包含。独立环境安装最终 wheel、隔离解释器及实际 CLI serve 与本机 TCP 模拟服务连通：检索/预览逻辑库不变，计数 2/生成 1、重问同 run、L0 不变、claims=0、SIGINT 退出 0。
- 用户授权的最终合成 DeepSeek HTTP 实测：权限拒绝和预览阶段零提供方调用；授权后来源 1、陈述 3、实测输入 1400、报告输入/输出 1380/420、状态 ok，生成 1、重问新增网络 0。遮盖合成邮箱，服务地址/密钥不回传；L0 不变、claims=0。前两次未发布，不计为通过；留存诊断为错误引用 records 元数据，实测输入 1328、报告 1308/297，严格拒绝后补提示及回归，不放宽校验。未读完整真实会话、不发 Atlas 原文、不改个人设置。
- 验收修复：项目回执误写会话编号、问题可为空类型、回答沿用 CLI 的地址元数据分别修正。前端初次专项两项失败为 API hash 路径/文字断言；完整回归的健康路径预期随两条合成来源从 5 改为 7，登记来源 2 和队列 54 保持真实口径。中间 527/480 通过后因追加防元数据引用案例再验最终 528/481，早期结果不替代最终源码。
- 下一步：允许清单提交，更新既有草稿 PR，按本轮新 HEAD 核对推送/PR 两种 CI；刷新公开代码包、独立还原全部跟踪文件后结束本任务。后续继续客户端包装、自动关联串联、完整运行 I/O、后台大文件摘要和全图语义；note/propose_note 类型待答复，正式参考与真实 M1–M4 门槛保留，整个目标不标完成。
- 提交前允许清单检查：tracked_files=245 staged_files=25 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True frontend_frozen_files_unchanged=True；git diff --cached --check 退出码 0。仅公开源码、中文记录与合成测试提交；凭据、实际研究材料、截图、数据库和缓存继续留忽略目录，原规格保留原文。


## 2026-10-10 · Codex/Claude 项目客户端接入包

- 任务：落实 §2、§8.1、§10/11/14 的 question/decide/context 包装；开始读进度末尾、相关章节和决定，c6c9e2a 基线包 SHA256 及独立恢复的 245 个跟踪文件已核对。采用 OpenAI Docs 与 skill-creator 核对当前原生格式；不创建后端子 agent。整个 M0–M4 仍未验收。
- 改动的文件：新增 rg/clients/__init__.py、package.py、record.py、tests/golden/test_client_package.py、scripts/check_client_package.py 和中文客户端验收；修改 CLI、CI、README、需求落实、决定、PR 草稿与本文件。前端源码未改，13 项冻结摘要全部 OK。
- 行为：client-pack 只读生成 19 文件、两套原生技能/stdio 配置和逐文件摘要，已有输出不覆盖、不改个人配置、不装钩子。固定项目及环境解释器，保留虚拟环境链接，-I 隔离工作区；上下文自动选用、人工写入仅明确调用。包内绝对路径仅适于本机环境，不复制研究原件或凭据。
- 写入：client-record 接收有限 UTF8 JSON 文件或 stdin，固定 human:本机用户/项目，拒绝重复、未知字段、覆盖身份/项目及缺失 UUID。脚本 shell=False、用户原话不插入命令；复用 question/decide 的原话证据、完整范围、双时间、版本冲突、跨操作重试和歧义候选。技能策略不是操作系统身份认证，MCP 仍只读，note/propose_note 待约定。
- uv sync --locked：Resolved 28 packages in 12ms，Checked 27 packages in 60ms；uv run rg --help、client-pack --help、client-record --help 退出码均 0。uv run pytest -q：544 passed in 184.63s；uv run pytest tests/golden -q：497 passed in 171.46s。两者并行，不作性能基准；16 项新增固定案例覆盖真实子进程与错误历史风险。
- uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；97 文件/92 源码资源、缺失/差异/实际密钥/私有文件均 0。独立环境重新安装最终 wheel，模块来自安装目录；两种上下文脚本逻辑库不变、人工问题写入 1 次且重试同回执、raw_events=1/model_attempts=0。
- PYTHONPATH=. uv run --no-sync --with 'mcp==2.3.0' python scripts/check_client_package.py：官方 SDK 2.3.0 从两种生成配置分别启动 2026-07-28 子进程，各列出五个工具并读取状态卡，逻辑库不变；新增 CI 步骤。本机 Codex 0.162.0 的 mcp get 显式覆盖解析通过，不宣称自动项目发现或真实模型触发验收。
- skill-creator quick_validate 对三个 Codex 技能均输出 Skill is valid!；三个 Claude YAML 前置字段及明确调用策略另核对。无动态 shell 块，UI 字段/提示及策略通过；通用验证器不支持 Claude 专属字段，不冒充原生加载。开发仅合成项目，未读完整真实会话、未发 Atlas 原文、未调用提取/Agent 模型。
- 验收修复：初次专项两项失败为测试误读详情中不存在的 project_id，改按实体表实际项目核对；旧专项 105 passed in 42.30s，随后追加的路径/缺库案例由最终 544/497 验证，早期结果不替代最终源码。
- 下一步：允许清单提交，更新现有草稿 PR，按新 HEAD 核对推送/PR 两种 CI；刷新公开代码包、独立恢复全部跟踪文件后结束本任务。后续继续自动关联串联、完整运行 I/O、后台大文件摘要、全图语义与 export/隐私清除；真实客户端会话/钩子 p95、正式参考与真实 M1–M4 门槛保留，整个目标不标完成。
- 提交前允许清单检查：tracked_files=251 staged_files=13 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True frontend_unchanged=True；git diff --check 退出码 0。仅公开源码、中文文档和合成测试；凭据、原件、生成的本机包、数据库、截图及缓存继续留忽略目录。


## 2026-10-10 · 自动关联、概览与阶段健康

- 任务：落实 §2、§7.2/7.7–7.10、§9/11 的自动后续阶段，沿用户日常零人工干预方向；按既有授权使用前端子 agent。开始核对进度末尾、相关章节和决定，04171d5 基线恢复包及 251 个跟踪文件已验证；整个 M0–M4 不标完成。
- 改动的文件：新增 rg/extract/pipeline.py、tests/golden/test_auto_pipeline.py 及两份中文验收；修改 CLI、linker、overview、队列健康、Store、备份、版本 12 迁移及原队列诊断测试。前端新增 PipelineQueuePanel 与四个浏览器案例，修改 HealthView、health 类型/样式/单测、合成服务；更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：自动批处理/watch 串接提取、关联和项目/会话概览；持久不可变输入、归属提交、进程锁接管和只追加历史。关联不由新关系自循环触发，审核只刷新后续阶段。各阶段默认 20 项、关联每项目 50 个新尝试可调，extract-only 保留诊断；权限、候选、提供方完整实测与作废合同不变。
- 等待：额度 UTC 次日、服务 30 秒、阶段忙碌 2 秒，新输入继承同配置等待；跨页失败每对一轮最多重试一次。关联仍在分页/等待时暂缓概览，partial 保留缺口并允许阅读已有记录，done 不代表全覆盖或审核。概览直接读取 claims，输入变化不覆盖旧文件；回执绑定本次发布字节，另一发布者替换可识别。备份含概览，移除原目录后恢复、文件缺失/修改均经核对缓存重建。
- uv sync --locked：Resolved 28 packages in 11ms、Checked 27 packages in 54ms；rg --help、extract --help、health --help 退出码均 0，health 有独立 pipeline-offset。专项 uv run pytest tests/golden/test_auto_pipeline.py tests/golden/test_links_overview.py -q：35 passed in 13.36s；19 项新增固定案例包含最终全量。
- uv run pytest -q：563 passed in 202.43s (0:03:22)；uv run pytest tests/golden -q：516 passed in 189.67s (0:03:09)。两者并行，不作性能基准。uv run ruff check rg tests scripts：All checks passed!；uv run pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 退出码 0。
- 新阶段实际 SIGKILL：发送后未知用量预留 1010 保留，恢复 1 项、关联完成及三概览，已知用量另增 120；系统锁释放、旧执行器不能提交，重跑零生成。备份避开阶段/发布锁，目标忙时未创建目录。此合成案例不替代真实客户端或 Windows 验收。
- cd web && pnpm test：6 files/47 passed/857ms；pnpm build：200 modules/1.03s；专项浏览器 11 passed/12.4s，完整浏览器 47 passed/34.1s。父 agent 实看桌面/390 手机/等待任务三图，无横向溢出，12 项冻结全部 OK；摘要 7a187eaf495e1077eca3a97eeb0a24513161c4e86f1f97f578c1f92b0704d9d6。两个队列独立分页、迟到隔离、目标矛盾与纯文本诊断保留；旧截图因合成重跑变化，旧问答源码未改。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；98 文件/93 源码资源，缺失/差异/实际密钥/私有文件均 0。独立环境安装最终包，仓库外真实 CLI watch 连明确本机 TCP 模拟服务：首轮 8/累计 14 次生成、45 次计数请求，逐次核对完整输入已计数；无变化轮次零网络、旧 L0 不变、模型仅候选、四份概览、SIGINT 退出 0。模拟不冒充提供方计数，本轮无真实模型调用、无 Atlas 外发或个人配置修改。
- 验收修复：初次失败包含合成关系方向非法、测试日额度不足容纳输出、未带概览备份、未变会话不应重复概览、原队列测试需用诊断入口；缓存重建的分量计数改为复用实测缓存。另补 CLI 独立分页和发布回执竞争回归。前端首次兼容案例因未等待加载失败，补等待后重跑，不放宽生产行为。早期 562/515 不替代最终 563/516 验收。
- 下一步：允许清单提交、维护已有草稿 PR，按新 HEAD 核对推送与 PR 两种 CI，刷新代码包并独立恢复全部跟踪文件后结束本任务。后续继续完整运行 I/O、后台大文件摘要、全图语义、export 与隐私清除；note 类型待答复，正式参考及真实 M1–M4/规模性能/实际客户端门槛继续保留。
- 提交前允许清单检查：tracked_files=257 staged_files=26 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True frontend_frozen_files_unchanged=True；git diff --cached --check 退出码 0。仅公开源码、中文记录及合成测试；凭据、研究原件、数据库、截图、缓存及包留忽略目录，原规格不改。


## 2026-10-10 · 物理文件版本与后台完整摘要

- 任务：落实 §2、§8.2/8.3 与原设计 v1 §5；开始核对进度末尾、相关章节与既有 L1 决定，b5a9b78 基线包 SHA256 与独立还原的 257 个跟踪文件字节已验证。按用户既有授权由前端子 agent 做版本/健康页面，后端父 agent 独立完成；整个 M0–M4 不标完成。
- 改动的文件：新增 rg/artifacts/ 的 files/worker/views/service 与包入口、tests/golden/test_artifacts.py 及中文验收；修改版本 13 迁移、Store 健康、scan/extract watch、CLI 和只读 HTTP。前端新增 VersionsView、ArtifactHealthPanel、来源/观察 helper 与样式、单测和浏览器案例；修改导航、类型、L1 元数据、health 样式及 tests/frontend_server.py 合成种子。更新 README、需求落实、决定、PR 草稿与本文件。
- 行为：快照 Git 原对象入物理版本目录；大文件由遗漏清单发现、后台全文 SHA256，缓存元数据含 ctime，变化重读。current_file 与旧快照的 snapshot_id 留空，另存发现线索及实际读取窗口；缓存沿用原窗口，不复制大文件、不补 run_io。工具报告与物理字节分开，版本仍 candidate。
- 恢复：只取得实际进程锁后接管 running，owner 原子提交、输入及观察只追加。慢读取不占写事务，读前/提交前复核根目录登记；根绝对路径祖先、文件父目录和文件都 nofollow。中途变化作废等五秒，缺失文件/对象留下失败，发现故障轮转避免阻塞后续快照。watch 持实际子进程句柄与管道，正常结束或父强杀的 EOF 后退出；钩子仍离线且未安装常驻服务。
- uv sync --locked：Resolved 28 packages in 12ms、Checked 27 packages in 53ms；rg --help、hash-files --help、versions --help 退出码均 0。专项 uv run pytest tests/golden/test_artifacts.py -q：21 passed in 8.88s，含实际 SIGKILL、父进程管道断开、源目录丢失和备份恢复等历史风险。
- uv run pytest -q：584 passed in 213.24s (0:03:33)；uv run pytest tests/golden -q：537 passed in 200.12s (0:03:20)。两者并行，不作性能基准；ruff check rg tests scripts：All checks passed!；pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。
- uv build --wheel：Successfully built dist/researchgraph-0.1.0-py3-none-any.whl；103 文件/98 源码资源，缺失/字节差异/实际密钥/私有文件均 0。独立环境安装最终 wheel、仓库外真实 CLI scan --watch：小文件版本 1、大文件完整 SHA256 版本 2、六任务完成、缓存复用 1/读取窗口不变；没有正文副本/旧快照绑定/模型调用/推定 run_io；只读分页和备份恢复通过，SIGINT 退出 0。
- 验收修复：最初五个专项失败来自测试误用 Store 上下文、合成静态目录缺文件，修正环境后通过。随后发现根目录的祖先链接也应提前拒绝，补 nofollow 遍历与新案例；早期 583/536 不替代最终 584/537。前端另收紧缺项目回包拒绝、观察条数/partial 矛盾及纳秒签名的浏览器整数精度，最终结果单独记录。
- 前端最终 pnpm test：7 files/57 passed/656ms；pnpm build：204 modules/1.07s；pnpm test:browser：54 passed/37.2s。父 agent 实看最终五张桌面/390 手机/缓存窗口图，无横向溢出；18 项冻结全部 OK，摘要 9ece6869e130cefcfa49de3cd098924e57cf40c7ac4855b77d6d0db6fcf76d31。未知、矛盾、缺项目、迟到回包和纳秒舍入诊断保留；真实 HTTP 合成统计不冒充后台引擎或研究质量。
- 下一步：允许清单提交并维护现有草稿 PR，按新 HEAD 核对推送/PR 两种 CI，刷新公开恢复包并独立还原全部跟踪文件后结束本任务。后续继续物理版本与研究对象/运行/MCP 双时间关联、全图语义、export、临床编号与隐私清除；note 类型、正式参考及真实 M0–M4/规模性能/实际客户端验收仍保留，整个目标不标完成。
- 提交前允许清单检查：tracked_files=271 staged_files=32 actual_secret_matches=0 forbidden_tracked_files=0 original_spec_unchanged=True frontend_frozen_files_unchanged=True；git diff --cached --check 退出码 0。仅公开源码、中文记录与合成测试提交；凭据、原件、数据库、图片和缓存继续留忽略目录，原规格保留原文。


## 2026-10-10 · 物理版本的 MCP 双时间读取

- 任务：落实 §2、§7.11/8.2，验收修复另核对 §11。开始核对进度末尾、相关章节与决定；642469b 基线恢复包 SHA256 和独立恢复的 271 个跟踪文件字节通过。父 agent 独立后端，无新子 agent 工作，整个 M0–M4 不标完成。
- 改动的文件：新增 rg/query/artifacts.py、tests/golden/test_version_queries.py 与中文验收；修改 reader/context、MCP 描述、版本 14 迁移、官方 SDK 检查、HTTP 服务及问答 API 固定案例；更新 README、需求落实、决定、PR 草稿与本文件。前端源码未改。
- 行为：版本证据和状态卡共用不可变观察的双时间；捕获快照按明确 ID/项目/根目录核对，当前摘要采用完整读取窗口。必要快照晚入库时使用较晚已知时间，原观察时间另留；缓存保留原窗口，发现信息截止外不展示。条数和时间摘要只计算可见观察，首次插入时间不绕过截止，指定分析范围不猜文件归属，不读文件或补造 L0/run_io。
- 修订：新增版本、观察和快照改变 revision；即使复用版本，新观察也拒绝旧 expected_revision 续页。已有数据升级失效旧修订，空库仍为零；迁移失败回滚，原件保留、重开不重复变更。
- uv sync --locked：Resolved 28 packages in 14ms、Checked 27 packages in 67ms；rg --help 退出码 0。专项 uv run pytest tests/golden/test_version_queries.py -q：16 passed in 3.39s；HTTP 专项 tests/golden/test_qa_api.py tests/test_api.py：42 passed in 26.91s。
- uv run pytest -q：603 passed in 221.99s (0:03:41)；uv run pytest tests/golden -q：556 passed in 208.21s (0:03:28)。两者并行，不作性能基准；ruff check rg tests scripts：All checks passed!；pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。
- 验收修复：初次十二个新案例因合成种子误把整数 job_id 当文字 ID，修正后通过；另补可空类型检查。两次全量分别遇到超限 POST/鉴权拒绝的 TCP 重置，已补拒绝后的有限收尾，最多 0.2 秒/128 KiB、单块 8 KiB；三个分段请求回归不进库或模型，鉴权和 64 KiB 接收上限不变。旧收尾模拟三项亦通过，不冒充稳定复现；早期 551/600 和失败全量不替代最终 603/556。
- 官方 MCP SDK 2.3.0：新版 2026-07-28/旧版 2025-11-25 各列五工具、七查询含两个物理版本，状态卡包含两者，逻辑库不变/run_io 为零；Codex/Claude 两种生成配置仍通过。本轮仅合成材料，未读完整真实会话或发 Atlas 原文、未调用模型。
- uv build --wheel：104 文件/99 源码资源全部匹配，缺失/差异/实际密钥/私有文件均 0；独立环境重新安装最终包，模块来自安装目录。仓库外真实 scan --watch 形成三版本/六完成任务/一缓存，原窗口保留、SIGINT 退出 0；删除工作区后备份只读 MCP 的三版本/状态卡/旧截止通过，库不变、model_attempts/run_io 为零。安装目录 HTTP 分段 401/403/413 通过；首次验证受本机 SOCKS 环境缺包阻止，显式 trust_env=False 限定本机后通过，未改生产代理或外发资料。
- cd web && pnpm test：7 files/57 passed/651ms；pnpm build：204 modules/1.10s；最终 pnpm test:browser：54 passed/40.2s。十三项前端源码与旧基线一致，截图因新增修订号/合成时间变化，父 agent 实看变更的桌面/390 手机图，最终健康桌面再次实看；无横向溢出。当前十八项冻结全部 OK，SHA256 91a4a1afabe21191467cf08b061028c5fa705a7c02af688969067212c0fd9a26。
- 下一步：允许清单提交、维护现有草稿 PR，核对新 HEAD 的推送/PR 两种 CI，刷新公开包并独立恢复全部跟踪文件后结束本任务。后续继续版本与研究对象/实际运行 I/O 的直接关联、全图语义、export、临床编号与隐私清除；note 类型、正式参考、真实工具/性能/完整 M0–M4 验收继续保留。
- 提交前允许清单：tracked_files=271 staged_files=15 actual_secret_matches=0 forbidden_tracked_files=0 spec_unchanged=True frontend_source_unchanged=True；git diff --cached --check 退出码 0。只提交公开源码、合成测试及中文记录，真实材料、凭据、数据库、截图和缓存仍忽略。


## 2026-10-10 · 离线运行清单与双时间关联

- 任务：继续 §2/5/7.11/8/9/10 的明确版本关联，原设计 v1 §5 已定义可选 run manifest。开始核对进度末尾、相关规格与决定；5164758 基线恢复包 SHA256 a50d7913f6bbb5c5d9dd485a7a1fe57e19b706d9fcdf709b94cf776d27dda1bd，独立恢复 274 文件均字节一致。按用户既有授权恢复前端子 agent，后端由父 agent 完成，完整 M0–M4 不标完成。
- 改动的文件：新增 record/manifest.py、manifest_schema.py、query/runs.py、api/runs.py、tests/golden/test_run_manifests.py 及中文验收；修改版本 15 迁移、回执账本、health、reader、derive/views、MCP、CLI、HTTP 与官方 SDK 检查；前端新增运行清单组件、helper、分页 hook、样式、语义案例及合成浏览器验收；更新 README、需求落实、决定、PR 草稿和本文件。
- 行为：本地 64 KiB 清单原件、报告、最多 256 项角色 I/O 及 UUID 回执原子追加，全部 candidate/direct_record。未报告和明确空列表分开；种子保存十进制字符串；原生执行、报告退出码、尝试和实际 I/O 完整性独立。只保存同项目明确 ID，未知运行/版本可以先报告、读取时解析晚到记录，不回写原观察；已知跨项目拒绝。入口不执行命令、打开声明文件或调用模型。
- 读取：五个 MCP 增加 R: 运行引用；CLI/HTTP/MCP 共用双时间。原生状态从可见不可变观察重新计算后才分页，清单和来源分别过滤截止，scope 只约束报告。清单、原生运行与观察改变 revision，HTTP 409 拒绝旧修订续页。I/O 每份 100 项按 io_offset 续读、manifest_id 限定清单；版本和尝试反查先筛匹配清单再分页，较早报告不被无关首页遮蔽。
- uv sync --locked：Resolved 28 packages in 22ms、Checked 27 packages in 76ms；rg --help 退出码 0。专项 uv run pytest tests/golden/test_run_manifests.py -q：41 passed in 7.93s；真实 HTTP 200/401/403/409、修订不变。
- uv run pytest -q：644 passed in 269.03s (0:04:29)；uv run pytest tests/golden -q：597 passed in 280.79s (0:04:40)。两者并行，不作性能基准；ruff check rg tests scripts：All checks passed!；pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。
- 验收修复：初次专项的四项失败是测试调用 helper/monkeypatch、缺人工请求修订及误用 Store 上下文协议；修正后通过。另两项分别是 attempt helper 调用错误与 HTTP 冲突落到通用 ValueError 的 400，后者已补 409 并实际网络回归。最终 41 与全量成功，不引用早期失败次数作为验收。
- 官方 SDK 2.3.0：新版 2026-07-28 和旧版 2025-11-25 各五工具/八查询，含两物理版本、一候选清单/两项报告 I/O，逻辑库不变；Codex/Claude 生成接入包继续通过。MCP 固定 1500 上限已按用户取消；状态卡仍本地精确编码计预算，不为计数外发研究资料。
- 独立 wheel：108 文件、103 源码资源字节全匹配，缺失/差异/实际密钥/私有路径均 0。仓库外安装模块来自 site-packages；写入提交前真实 SIGKILL 返回 -9，原件/清单/256 I/O 全回滚；同 UUID 重试及重放通过，CLI 精确读取最后 56 项、种子文本保留，报告退出 0 不改变原生 requested。删除工作区后的 SQLite 备份只读查询/MCP 通过，schema=15、完整性 ok、model_attempts=0。读取脚本最末换行拆解曾失败，修正脚本后通过，生产封装未改。
- 边界：界面为已有原生运行显示清单；暂无原生运行者保留原文和 CLI/MCP/HTTP 读取，不制造执行卡。候选报告不证明系统调用实际 I/O、完整环境或研究结论，不替代 Atlas 正式参考/真实质量/规模性能/完整 M0–M4 验收。
- 下一步：完成前端浏览器及截图冻结后允许清单提交，维护现有草稿 PR，按新 HEAD 核对推送/PR 两种 CI 并独立恢复全部跟踪文件，然后结束本任务。后续继续完整语义图、报告浏览及实际 I/O 采集、export、临床编号配置与隐私清除；note 类型和正式参考门槛继续保留。
- 前端最终 cd web && pnpm test：8 files/65 passed/651ms；pnpm build：208 modules/1.06s；pnpm test:browser：60 passed/41.6s，原 54 项保留；专项 6 passed/9.9s。父 agent 实看桌面角色/参数、原生状态与报告冲突同屏、390 手机角色与参数；首次滚动断言过早读取已改等待真实位置，异常版本对象字段保留诊断。最终冻结后停止前端编辑，再统一提交。
- 前端冻结最终 12 文件/5 图全部 17 项 OK，清单 SHA256 8f0df6b075158fd32875fb3525f4831f85a2d03fd564bf077c64791a9fba5117；父 agent 五图均实看，包括续页后的工具报告文本/未知补丁，随后未再修改前端文件。
- 提交前允许清单：tracked_files=274、allowed_changed_files=32、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=17。只提交公开源码、中文记录与合成案例；凭据、真实原件、数据库、图片和缓存仍忽略。

## 2026-10-10 · 双时间历史导出与本地下载页面

- 任务：继续 §2/10/11 及原设计 v1 §9。开始读进度末尾、对应章节和决定；25e051c 基线恢复包 SHA256 f74db61b79e65be6796ab878eab4e71cf2113284d2a9e844ff4a9946a2072f43，独立恢复 287 个跟踪文件均字节一致。按用户既有授权继续前端子 agent，后端由父 agent 开发，完整 M0–M4 不标完成。
- 改动的文件：新增 rg/export/{__init__,build,privacy,package}.py、tests/golden/test_exports.py 和中文后端验收；修改 CLI、HTTP、README、需求落实、决定与本文件。前端新增 HistoryExportView、useHistoryExport、historyExport/CSS、语义/浏览器案例及中文验收，修改 App/api；没有改原规格、解析器、库结构或钩子。
- 行为：CLI export/verify-export 及同源令牌 POST /api/exports，全部可见断言/审核/状态/图关系/共同输入/合并/来源引用/文件观察/候选报告共用只读快照和双截止/完整范围/revision；遍历全部分页，不用当前画面投影。来源当前路径/状态、未来审核/退出/观察不进入旧条件；指定范围只经明确报告关联运行/文件，保留 reported_only 和实际 I/O 未知。
- 隐私：默认没有正文、完整会话、二进制或环境副本。显式证据校验原始摘要/UTF8 范围，先遮盖完整事件再取选定片段；分别保留原始/导出摘要、恒等映射和遮盖范围。支持本次临床正则，凭据字段另行清空；身份或字段碰撞拒绝发布。默认引用包在原件丢失时仍标未读取，正文缺原件则不发布；校验不解包或执行，不证明清单真实性或结论。
- uv sync --locked：Resolved 28 packages in 1ms、Checked 27 packages in 46ms；rg --help 退出码 0。导出专项：34 passed in 10.48s。最终 uv run pytest -q：678 passed in 247.20s (0:04:07)；uv run pytest tests/golden -q：631 passed in 233.84s (0:03:53)。两者并行，不作性能基准；ruff check rg tests scripts：All checks passed!；pyright rg：0 errors, 0 warnings, 0 informations；git diff --check 无输出、退出码 0。
- 验收修复：首次两项失败分别为遮盖误判程序生成摘要身份、测试误用未启动 CLI 的模块入口，修正后补转义 JSON 密钥、损坏原件、未知发生时间等案例。初轮全量 678/245.23 秒、golden 631/232.00 秒，不替代最终权限观测/算法摘要补齐后的重跑。
- uv build --wheel：112 文件/107 源码资源全匹配，缺失/差异/实际密钥/私有文件均 0。仓库外隔离安装模块来自 site-packages；来源工作区已删除仍导出完整 256 报告 I/O、原生 requested、种子文本精确。发布前真实 SIGKILL 返回 -9，没有半包目标、逻辑库不变、/tmp 临时文件 0600，同目标重试通过；实际 HTTP 200 ZIP 可校验。删整个证据库后离线 verify-export 仍校验十个成员，不重建数据目录，schema=15、model_attempts=0。
- 实际 /mnt/d 独占硬链接与 ZIP 校验通过，但模式显示 0777。CLI 回执请求 0600/实际 0777/posix_private=false；Windows ACL 不在 POSIX 模式证明范围。强杀可能留下临时包，随机名称不保证挂载目录私有；没有保留 HTTP 服务端副本，也没有声称可删除用户已保存/分享的副本。
- 前端最终 cd web && pnpm test：9 files/73 passed/997ms；pnpm build：212 modules/1.16s；pnpm test:browser：69 passed/51.8s，原 60 项保留。9 专项最终 12.3s，补 tsc --noEmit 无输出/退出 0。真实 HTTP 包的十一成员、每个长度/SHA、默认无正文/显式遮盖/双截止/完整或 null 范围/400/409/401/手机项目切换通过；延迟只模拟实际回包交付时序，库和模型不变。
- 前端实看后补小屏页面内项目选择；父 agent 再实看桌面默认/隐私、390 完整/隐私四图，没有横向溢出。最终 9 文件/4 图共 13 项冻结全部 OK，SHA256 900aa7483e10e874b62b056ad5f1ffe40ef1596e29900b0b13ca1eb7592d7056；子 agent 已停止编辑，再统一提交。
- 下一步：只提交允许清单的公开源码/中文记录/合成测试，维护草稿 PR，核对新 HEAD 的推送/PR 两种 CI 和恢复包独立还原。后续继续完整语义图/影响传播、项目临床配置与隐私清除、实际 I/O/真实工具和规模验收；note 类型、Atlas 正式参考和完整 M0–M4 门槛保留。
- 提交前允许清单：baseline_tracked_files=287、allowed_changed_files=21、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=13；git diff --cached --check 退出码 0。只提交公开源码、中文记录和合成案例；真实资料、密钥、原件、数据库、下载 ZIP、截图与缓存继续忽略。


## 2026-10-10 · 完整 L2 语义图与折叠历史保留

- 任务：继续 §2/5/9/10/15 A 与原设计 v1 §7。开始核对末尾进度、对应章节和决定；a6cc282 基线恢复包 SHA256 58116222b2d85eaabc8b7ecdd171bc62473ce14e2a1cce48ff918a1d79ce80c8，300 个跟踪文件独立恢复字节一致。按用户既有授权继续前端子 agent；整体 M0–M4 仍未标完成。
- 改动的文件：新增 query/graph.py、api/graph.py、tests/golden/test_semantic_graph.py、tests/graph_browser.py 和两份中文验收。修改 CLI、API、export/build 与 privacy、浏览器合成入口、model/GraphView/styles/语义单测/新图浏览器专项，以及 README、需求落实、决定与本文件；未改源规格、解析器、库结构或钩子。
- 行为：完整 L2 图共用 Reader 与读快照，保留全部内容版本、审核、更正、独立状态、循环、明确范围、原关系/端口及所有证据页。节点用规范身份/范围摘要；同范围版本缺失不借用其它条件/项目，异常方向和汇合保留并标无效，多版本不按 ID 宣称当前内容。六集合 CLI/HTTP 分页固定双截止/修订，改动返回409；历史 ZIP 同源记录 semantic。查询不读原件/当前文件、不联网或写缓存；L1完整性仍false。
- 前端：比较未选只表示比较，主动显示内容版本不改变事实；过程组保留撤回/阴性/多scope/候选/全部内外关系与证据，展开恢复原ID和位置。旧更正退出有效版本/关系/汇合/采用/证据集合，历史与原文入口仍保留；partial/隐藏候选/缺端点继续拒绝折叠。
- uv sync --locked：Resolved 28 packages in 12ms、Checked 27 packages in 60ms；rg --help 退出0。语义图与导出专项：59 passed in 20.62s；最终 uv run pytest -q：703 passed in 259.38s (0:04:19)；uv run pytest tests/golden -q：656 passed in 245.84s (0:04:05)。两者并行，不作性能基准；ruff：All checks passed!；pyright：0 errors, 0 warnings, 0 informations；git diff --check 无输出/退出0。
- 初次失败与修复：测试空scope不合法、HTTP构造及静态页缺失修正；图seed补schema必填结构后真实人工更正通过，不放宽后端。初轮全部702通过/固定654通过1失败，暴露随机UUID数字尾段被误作手机号；只保护应用结构UUID，文字仍遮盖，自定义规则涉及UUID仍拒绝，临床字段不借例外。最终703/656替代早期结果。前端选择布局同步循环与新意图选择清空已修，虚拟fixture来源避免物理日志路径8误增9。
- uv build --wheel：114文件/109源码资源全匹配，缺失/差异/实际密钥/私有文件均0。隔离安装109资源逐字节一致，模块来自site-packages；删除原库目录后仍读备份三节点/九断言的完整图，多个内容版本未选、循环和compare保留、accepted/refuted独立。真实CLI/HTTP同结果、401/409/export200通过，库不变/model_attempts=0；删除备份后离线ZIP校验十成员通过且不创建库。
- 前端最终：pnpm test 9 files/79 passed/872ms；build 212 modules/1.15s；pnpm test:browser 74 passed/57.4s，原69保留；五专项14.2s，tsc退出0。父agent实看完整/聚焦桌面及390原边/重要历史四图，无横向溢出。六文件/四图共十项冻结全OK，SHA256 8a25bbfb86648d4d29f8e0ecdf88ea46d8861f746ac445931543c4fc3792ffd4；子agent已停止编辑。
- 下一步：按允许清单提交、更新现有草稿PR，核对新HEAD的推送/PR两种CI及独立恢复包后结束本任务。影响传播未选比较合同已提问，尚无回复，不猜规则或写提醒；后续继续完整L1输入输出、项目临床配置/隐私清除、规模/真实工具与Atlas正式参考门槛，note/propose_note约定及完整M0–M4验收继续保留。
- 提交前允许清单：baseline=a6cc282、staged_files=20、tracked_files=307、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=10；暂存差异检查退出0。只提交公开源码、中文说明和合成测试；凭据、真实资料、数据库、ZIP、截图及缓存不入仓库。


## 2026-10-10 · 项目临床编号遮盖配置

- 任务：继续 §2/7.2/7.3/11。开始核对进度末尾、对应章节与决定；aa3569a 恢复包 SHA256 f42810e79f9d3dea744919fe6d9bc267168267a08b02ed2a5be5cd2e962e0731，独立恢复的 307 个基线跟踪文件再次核对字节一致。按用户既有授权继续前端子 agent，整体 M0–M4 保持未完成。
- 改动的文件：新增 store/privacy.py、extract/privacy.py、golden/test_project_privacy.py、privacy_browser.py 和两份中文验收。修改版本迁移/系统锁、瘦身/切片/工作集/定位校验/worker/关联/概览/问答/队列、CLI/HTTP/导出、合成浏览器入口、前端共用配置面板及 QA/导出失效合同、README/需求落实/决定与本文件；原规格、解析器、L0 和钩子未改。
- 行为：版本 16 按项目只追加规则，限制 32 条/1000 字符，明确人工入口及修订冲突；内置遮盖始终生效，规则不改原件或已有许可。所有模型阶段的计数/生成、预览及导出共用规则，先在完整原文/工具输出/规范记录中遮盖再截窗，引用跨敏感区拒绝；旧样例回执、缓存、调度身份失效，无新增原文也可重新调度。导出用当前项目规则叠加本次规则，只带规则摘要。
- 并发：POSIX 发送共享系统锁、配置写入独占，前后核对固定配置和许可；未生成不标已发送，网络期间不持数据库写事务。首轮全量 728 通过/2 失败、golden 681 通过/2 失败，独占发送误阻塞其它会话；修成共享发送、保留旧案例并补独立进程验证。原生 Windows 仍保守串行分支，本次只验证 WSL/Linux，不宣称原生并发兼容。
- uv sync --locked：Resolved 28 packages in 1ms、Checked 27 packages in 61ms。最终 uv run pytest -q：731 passed in 276.96s (0:04:36)；uv run pytest tests/golden -q：684 passed in 261.90s (0:04:21)；项目规则/旧并发/监控专项：52 passed in 19.70s。两组全量并行，不作性能基准。ruff：All checks passed!；pyright：0 errors, 0 warnings, 0 informations；rg --help、project privacy --help 与 git diff --check 退出 0。
- 验收装配修正：初次专项的一个片段包含多窗口、无匹配关键词、空 scope 和 preview 位置参数修正；工具 helper 改用 text 关键字。早期回归命令两次含不存在的测试文件，未执行测试；改用实际 auto_queue 路径后 165 项通过，最终全量/固定成功替代早期结果。
- 独立 wheel：116 文件/111 源码资源全匹配，缺失/差异/实际密钥/私有资料均 0。隔离安装 111 资源再次逐字相同；实际 CLI 保存、真实本机 HTTP 401/409/问答 200/导出 200，显式本地模拟协议生成 1 次，预览等于生成输入，计数期间设置返回 409，原文不变、claim 保持 candidate。删原库后 SQLite API 备份仍读规则及只读导出，删备份后离线十成员 ZIP 校验通过，不重建数据目录；schema=16。没有发送真实临床或 Atlas 会话。
- 前端最终：pnpm test 10 files/83 passed/831ms；build 216 modules/994ms；tsc 退出 0；8 真实 HTTP 专项 11.4s、全量浏览器 82 passed/1.0m，原 74 项保留。修姓名变化导致旧读取被忽略却不重读的等待边界并重跑；旧预览/授权勾选及迟到响应隔离、保存后新输入、原文保留、许可保留、导出叠加及手机操作通过。父 agent 实看桌面规则/导出与 390 手机规则/保存/预览五图，16 项冻结全 OK，SHA256 0f6120044d55ed76d98c3141352d3e1a24fe062c700f8b3ab13d13f51329ea59；子 agent 已停止编辑。
- 边界：配置不清除旧原件/索引/缓存/快照，也不收回远端已发送资料和用户分享副本；完整隐私清除仍待实现。影响传播未选比较合同仍无用户回复，不猜规则；完整 L1、实际 I/O、规模/真实工具验收、note/propose_note 与 Atlas 正式参考和完整 M0–M4 门槛保留。
- 下一步：按允许清单提交、维护现有草稿 PR，核对新 HEAD 的推送/PR 两种 CI 与恢复包独立还原后结束本任务。后续继续完整隐私清除及 L1 实际输入输出，待传播合同答案再实现提醒。
- 提交前允许清单：baseline=aa3569a、staged_files=41、tracked_files=319、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=16；暂存差异检查退出 0。只提交公开源码、中文说明和合成案例；凭据、真实资料、数据库、ZIP、截图与缓存仍忽略。

## 2026-10-10 · CI 进程退出测试竞态修正

- 任务：收尾项目临床编号配置的 PR 验收，读取 §2/15 及已有决定。45efe80 推送 CI 成功；PR CI 全量 730 通过、1 失败，旧进程退出测试在检查文件存在后读取 `/proc/<pid>/stat`，进程此时已被回收而报 FileNotFoundError。
- 改动的文件：tests/golden/test_artifacts.py、本文件及 docs/acceptance/M4项目临床编号遮盖验收_20261010.md。单次读取进程状态，文件消失表示进程已退出；保留真实父进程强杀、五秒子进程等待和存活失败断言。没有改生产代码、安装包资源、前端或规格。
- 验收命令：uv run pytest tests/golden/test_artifacts.py::test_background_child_exits_when_actual_parent_pipe_disappears -q 独立运行十次，各 1 passed；重新 uv run pytest -q：731 passed in 281.34s (0:04:41)；uv run pytest tests/golden -q：684 passed in 266.88s (0:04:26)。ruff：All checks passed!；pyright：0 errors, 0 warnings, 0 informations；git diff --check 退出 0。两组全量并行，不作性能基准。
- 下一步：仅提交上述三文件，维护草稿 PR，重新核对修正后 HEAD 的推送/PR CI 与恢复包；项目遮盖其它验收保持，整体 M0–M4 尚未完成。

## 2026-10-10 · 已登记 L1 文件运行图与历史原文一致性

- 任务：继续 §2/5/8.2/8.3/9/10/15 与原设计 §4/5；开始核对末尾进度、对应章节与既有决定。230c16a 基线恢复包 SHA256 e3271eac8bd4c45ddf5970b044f2999793eeaaf230c5a971bab6033f77def95f，319 个跟踪文件独立恢复字节一致。按用户既有前端子 agent 授权继续界面；整体 M0–M4 未标完成。
- 改动的文件：新增 query/l1.py、l1_records.py、tests/l1_graph_browser.py、golden/test_l1_graph.py、前端文件运行图组件/读取钩子/模型/样式及单测和浏览器专项、两份中文验收。修改 API 图入口与 server、CLI、export/build 与 privacy、migrations、原导出并发/项目规则迁移测试、前端 App 与合成服务两行，以及 README、需求落实、决定与本文件；不改源规格、解析器、钩子或模型流水线。
- 行为：完整遍历已登记文件/原生运行/候选清单与编辑/快照及明确同范围尝试，重复端口、未知/空列表、缺失端点与全部内容版本保留。五集合 CLI/HTTP 固定双截止/完整范围/修订，报告输入输出不代替原生执行观察。发现快照不证明旧字节，后来观察不补旧图；源证据接口只按同图条件续读原事件，无后来派生上下文。历史 ZIP 同源保存 l1，十一成员不变。只读不打开当前文件或执行历史命令，实际 I/O 完整性仍 unknown。版本 17 增加编辑修订并保留原子升级和原件。
- uv sync --locked：Resolved 28 packages in 14ms、Checked 27 packages in 31ms；rg --help、l1-graph --help 退出0。L1/导出/L2 合并回归：80 passed in 28.62s；加强旧编辑迁移断言后 L1 专项 21 passed in 8.52s。最终 uv run pytest -q：752 passed in 291.98s (0:04:51)；uv run pytest tests/golden -q：705 passed in 277.93s (0:04:37)。两组全量并行，不作性能基准。ruff check rg tests scripts：All checks passed!；pyright：0 errors, 0 warnings, 0 informations；git diff --check 退出0。
- 初次回归失败及修正：新测试漏导入 ConflictError、旧并发导出测试包装仍为两参数，修正装配后 80 项通过；保留并发写入与同一 SQLite 快照断言。合成 SQL 占位与必填字段修正，虚拟来源不计物理健康记录；没有放宽校验或读取真实会话。
- 隔离 wheel：118 文件/113 源码资源全匹配，缺失/差异/实际密钥/私有资料均0。安装后113资源逐字一致、模块来自 site-packages、schema17。实际 CLI/127.0.0.1 HTTP 完整核对五集合8/113/2/7/2，四个原文字节窗口完整还原，历史截止只有请求，401/403/400/409/图及原文和导出200通过；数据库与原事件不变、model_attempts=0。关闭并删除临时合成原库后备份图完全一致；删除临时备份后ZIP十成员离线校验通过，不重建数据目录。
- 用户明确选择整项目隐私清除，合同记入 DECISIONS，下一任务实现；本次没有清除真实资料。实际 I/O、影响传播合同、note/propose_note、真实工具和规模验收、Atlas 正式参考及完整 M0–M4 门槛继续保留。
- 下一步：完成前端全量、真实浏览器、手机画布实际可见与截图验收，冻结后按允许清单提交，维护草稿PR，核对新HEAD两种CI与恢复包，再结束本任务。

- 前端最终：pnpm test 11 files/92 passed/655ms；build 220 modules/1.02s；tsc 退出0；七个真实 HTTP 专项 20.1s；全量浏览器89 passed/1.3m，原83单测/82浏览器案例保留。手机切回项目的空白是真实视图拟合问题，修正受控节点测量回写及布局/尺寸后适配，保留节点与画布/屏幕实际相交断言，没有把 DOM 数量当可见证明。初验新增测试的文本换行和项目名称误判修正，物理观察按实际根/双截止保留未知，不修改数据或放宽校验。
- 父任务实看最终桌面画布、清单详情、物理观察及390手机画布/摘要、详情五图，无横向溢出；14项冻结全OK，SHA256 10f05a9e25440bd5e847ad92c9c3ee83f72c17e75d35239723599947239b42c9；前端子 agent 已停止编辑。下一步按允许清单提交与维护草稿PR，核对当前HEAD推送/PR两种CI和独立恢复包后结束本任务，再推进整项目隐私清除。
- 提交前允许清单：baseline=230c16a、staged_files=26、tracked_files=331、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=14；git diff --cached --check 退出0。只提交公开源码、中文记录和合成案例；凭据、真实资料、数据库、ZIP、截图与缓存仍忽略。

## 2026-10-10 · 整项目清除占用保护与导出、滚动回归

- 任务：按用户已确认的整项目粒度继续 §2/6.8/11/15。开始核对进度末尾、对应章节和决定；faaface 基线恢复包 SHA256 ab75c01e448ceb97bac60f06fd07dfcda45f6c9790342160524d6a245e8f37d4，独立恢复的331个基线文件再次核对字节一致。整体 M0–M4 保持未完成。
- 改动的文件：新增 store/lease.py、golden/test_store_lease.py、整项目清除占用保护和前端L1滚动竞态两份中文验收。修改 store/database.py、backup.py、ingest/spool.py、hooks/entry.py、snapshot/capture.py、api/server.py、export/build.py、golden/test_exports.py、前端 FileRunGraphView.tsx 与其浏览器专项，以及决定、需求落实和本文件；原规格、L0、模型流水线和包格式未改。
- 行为：Store 存活期间、钩子/spool、影子快照及备份目标写入共享占用锁；独占操作排除仍在读写的进程，普通连接与嵌套共享调用继续并行。构造失败和进程退出释放锁，目录别名同锁；HTTP 占用返回409，真实钩子始终exit0且不发布正文/快照，只记非正文错误类型。POSIX真实进程验收通过；原生Windows接口实现但未运行验收，旧数据目录先经写入口初始化锁文件。
- 初轮全量759通过、1失败；golden713通过。合法软件请求UUID恰有手机号样式数字导致来源导出被拒绝，补明确回执/rg来源及解析器/同事件与请求/已知截止核对，只对这些应用请求使用已有UUID规则。四类本软件记录和临床/外部/缺回执/后来回执/自定义规则固定验证，原件与引用保持；新测试Reader参数装配错误修正，不通过随机重试或放宽遮盖掩盖。
- uv sync --locked：Resolved28/11ms、Checked27/57ms。占用专项8 passed in2.69s，导出/占用最终46 passed in14.80s；最终 uv run pytest -q：764 passed in293.38s (0:04:53)，uv run pytest tests/golden -q：717 passed in278.61s (0:04:38)。两组全量并行，不作性能基准。ruff check rg tests scripts：All checks passed!；pyright：0 errors,0 warnings,0 informations；rg --help、git diff --check退出0。
- 隔离wheel：119成员/114运行资源与源码及隔离安装逐字一致，缺失/差异/实际密钥/私有文件均0。安装后的独立进程/真实钩子/127.0.0.1 HTTP占用案例8 passed in3.68s；实际CLI导出固定请求UUID、十成员摘要/字节数、SQLite备份读取通过，schema17、原件和修订不变、model_attempts=0、清单candidate。只使用临时合成项目，没有清除真实项目或发送真实资料。
- 前端按用户既有subagent授权处理真实回归：首轮全量88通过、1失败，画布已拟合但整体y=-487.59在屏外；旧平滑滚动和未取消RAF跨项目移动页面。定位限制本组件、项目/目标变化取消旧帧、即时滚动；保留真实可视相交断言及原89用例。专项7 passed/14.9s，迟到回包与延迟RAF用例连续10次10 passed/34.0s；pnpm test 92 passed/11files/717ms，build220modules/1.10s，tsc退出0。父任务最终pnpm test:browser：89 passed (1.1m)，父任务实看桌面画布/详情/观察和390手机摘要/详情五图，无横向溢出。
- 边界：本任务落实清除前占用保护，未提供删除入口。清除清单、共享对象归属、数据库/索引/缓存/快照/导出副本清理、无原文清除记录与中断恢复仍需实现。外部副本和Claude/Codex原始日志范围已提问尚无答复，不删除真实资料、不自动清理研究工作区。影响传播合同、note类型、实际I/O、真实工具/规模与Atlas正式参考及完整M0–M4门槛继续保留。
- 下一步：冻结前端证据、按允许清单提交、维护现有草稿PR，核对当前HEAD两种CI和独立恢复包后结束本任务。下个任务继续整项目清除的只读归属清单及执行恢复，不把系统锁或项目遮盖配置当作隐私清除完成。
- 前端最终冻结17项全OK，清单SHA256 59a0d1a7cf7a6a34edbe8802f2d3fe7c4d24bc770778c5903c70de13f40f1636；子agent已停止编辑。隔离Vite只加载HEAD旧组件，在9791运行加强案例，切项目后待滚动帧预期0实际1，准确失败；没有回退共享源码或覆盖web/dist。父任务全量89成功使用最终新组件，旧行为反证不计入成功测试数量。
- 提交前允许清单：baseline=faaface、staged_files=17、tracked_files=335、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=17；暂存差异检查退出0。只提交公开源码、中文说明和合成案例，凭据、原会话、数据库、ZIP、截图和缓存不入仓库。

## 2026-10-10 · 管理目录整项目清除、恢复与前端

- 任务：按用户已选择的整项目粒度继续 §2/11/15 与既有占用保护决定。开始核对末尾进度和对应章节；270b099 基线恢复包 SHA256 80e24c01ebfef0479727a27741c7810f8e96745e2848f1fc1b904f4e93257eb2，独立恢复335个跟踪文件字节一致。整体 M0–M4 保持未完成。
- 改动的文件：新增 store/clear.py、clear_selection.py、clear_files.py、clear_denials.py、golden/test_project_clear.py、独立前端合成服务与清除组件/状态/样式/单测/浏览器专项、两份中文验收。修改 Store占用屏障、scanner/sources/spool、snapshot/capture、backup、API server、CLI main、前端 App，以及 README、需求落实、决定和本文件；未改源规格、schema17或模型调用合同。
- 行为：只读预览完整归属和数量，独占写事务复算预览摘要；清除原件与目标派生、模型尝试/输出和队列、索引/缓存、对象、spool、快照/概览与根登记。共享字节保留；跨项目行依赖、命名空间/根冲突、未登记对象、无归属输出和链接路径拒绝执行。不可变触发器在隐私事务内暂停并恢复，两个FTS重建、VACUUM、WAL实际截断和完整性检查后才成功。
- 行为：持久无原文恢复记录覆盖提交前后和部分文件删除；普通读取/写入/钩子被阻止，状态和恢复独立可用。同一请求UUID幂等回放，最终仅保留计数/身份/时间和来源摘要屏障，扫描/钩子/快照不会重导入，后续备份携带屏障。CLI服务释放初始化Store，中断后可直接启动界面恢复。清除完成页面自动重载，确认区提示未保存草稿丢失；旧回包、切项目、401/409、双击及重试均保留正确意图。
- uv sync --locked：Resolved28/13ms、Checked27/50ms。清除专项20 passed in11.44s；清除/占用/快照钩子/导出合并80 passed in23.19s。最终 uv run pytest -q：784 passed in310.87s (0:05:10)；uv run pytest tests/golden -q：737 passed in294.95s (0:04:54)。两组全量并行，不作性能基准。ruff：All checks passed!；pyright：0 errors,0 warnings,0 informations；rg帮助、清除帮助及git diff --check退出0。
- 初轮装配失败及修正：合成模型尝试漏必填segment_id，CLI测试模块没有启动入口且后续漏Path导入；改为实际rg入口并保留真实启动/重启恢复断言。只读预览测试核对业务文件，允许SQLite WAL/SHM协调文件；本地HTTP不继承环境代理。没有放宽归属、原件、完整性或中断保护断言。
- 隔离wheel：123成员/118运行资源与源码及Python3.12隔离安装逐字一致，实际密钥匹配0。源码不可见的独立测试目录执行清除/恢复/真实CLI/HTTP/钩子及占用28 passed in15.70s；所有清除仅为临时合成项目，外部合成原日志和工作区保留，剩余对象仅共享字节，SQLite/FTS/管理文件无独有标记，原生Windows未验收。
- 前端按用户既有subagent授权完成：pnpm test 12files/96passed/880ms；build224modules/1.21s；tsc退出0；真实HTTP专项7 passed/15.6s，父任务最终pnpm test:browser 96 passed (1.2m)，原89项未删。首轮专项6/7失败是硬刷新销毁旧页面响应，改独立status核对同请求，不修改产品重载行为。父任务实看桌面预览/完成和390手机预览/恢复四图，无横向溢出；20项冻结全OK，SHA256 f0f304f98427068d1ce9a778993bed39000c31155b9443da585265347d68dca5，子agent停止编辑。
- 边界：当前仅清除本数据目录管理的整项目资料；外部Claude/Codex原日志、研究工作区及用户保存/分享的备份或导出副本沿用既有保留范围，明确外部副本问题尚无答复。恢复清单4MiB上限，超出在删除前拒绝；不是原生Windows或规模验收，不保证物理介质取证不可恢复。没有执行真实项目清除。实际I/O完整性、影响传播合同、note类型、真实工具/规模、Atlas正式参考及完整M0–M4门槛继续保留。
- 下一步：按允许清单提交，维护现有草稿PR，核对当前HEAD推送/PR两种CI及独立恢复包后结束本任务；随后继续已授权的开发，不把管理目录清除当作全部外部副本清除或整个目标完成。
- 提交前允许清单：baseline=270b099、staged_files=27、tracked_files=349、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=20；暂存差异检查退出0。只提交公开源码、中文记录和合成案例，凭据、真实会话、数据库、ZIP、截图和缓存不入仓库。

## 2026-10-10 · 清除屏障最后一步失败的状态修复

- 任务：继续同一管理目录清除验收，按 §2/11/15 复查恢复边界。基线 38e5588 已推送；推送 CI 38045565116 和 PR CI 38045567966 均通过22步骤与13类输出核对。基线恢复包 SHA256 44c5cf3294f87c5df4d9f27ed95f4b94d07c3e889bad7e7dcc20a35e840e232d，独立还原349文件逐字一致，历史私有路径与实际密钥匹配均0。整体目标保持进行中。
- 改动的文件：store/clear.py、golden/test_project_clear.py、管理目录整项目清除中文验收、DECISIONS.md 和本文件。源规格、schema17、模型合同及冻结前端均不改。
- 行为：完成回执先持久保存，但屏障删除也可能失败；状态查询和幂等执行先检查恢复屏障，不能只凭已有回执报告完成。恢复成功解除屏障后才返回完成；普通 Store 继续拒绝访问。新增故障注入在旧隔离安装包准确失败：1 failed in5.15s，complete与pending不符；不把旧行为反证计入成功验收。
- 最终清除专项：21 passed in12.71s。uv run pytest -q：785 passed in309.84s (0:05:09)；uv run pytest tests/golden -q：738 passed in295.72s (0:04:55)，两组并行不作性能基准。ruff：All checks passed!；pyright：0 errors,0 warnings,0 informations；清除帮助与git diff --check退出0。
- 重建wheel123成员/118源码资源逐字一致，实际密钥匹配0；Python3.12隔离重新安装后118资源一致，源码不可见的清除/恢复/真实CLI/HTTP/钩子和占用测试29 passed in17.38s。最终pnpm test:browser：96 passed (1.3m)；前端20项冻结仍全部OK。浏览器首次命令因未设置本地工具PATH退出127，补工具路径后全量通过；端口检查最初受TIME_WAIT影响，按服务的SO_REUSEADDR设置核对8791/9792均可重新绑定，没有遗留监听服务。
- 边界：本轮仍只用临时合成项目，真实资料删除和远程模型调用均0；外部原日志、研究工作区及用户保存/分享副本沿用保留边界。原生Windows、规模、实际I/O、影响传播、note类型、真实工具与Atlas正式参考及完整M0–M4继续保留。
- 下一步：只按上述五文件允许清单提交，更新现有草稿PR，核对新HEAD两种CI及独立恢复包后结束本任务；整体开发目标不标为完成。

## 2026-10-10 · 已保存物理版本差异与前端

- 任务：继续 §2/8.2/9.5/10/15 的保存文件版本差异，开始核对进度末尾、对应章节和决定。2956527 基线恢复包再次核对SHA256 52067c0df86ee817ef9d633870bba8e2d0875668a1814688ddca256ce619d816，独立恢复349个HEAD文件逐字一致。整体M0–M4保持未完成。
- 改动的文件：新增 query/version_diff.py、golden/test_version_diff.py、独立前端合成服务、差异面板/状态/解析/样式、单测和浏览器专项、两份中文验收。修改 API server/views、CLI main、VersionsView，以及 README、PR草稿、决定、需求落实和本文件；源规格、schema17、模型、钩子及清除生产代码不改。
- 行为：明确比较同项目、同登记根与路径的两个版本，复用不可变物理观察双截止与同一读快照，核对修订。有效复制捕获证明、长度/SHA256/Git blob摘要和对象路径身份通过后才比较保存字节；返回实际选用观察，模式变化独立且缺证明为未知。后来模式矛盾不进入较早视图。链接仅比较目标文字，CRLF、U+2028正文和无末尾换行保留；输入合计64000字节、每侧2000行、输出64000字节，缺失/损坏/非文本/编码无效或超限不返回部分正文。不读当前文件、不运行Git、不写库/缓存、不提升候选。
- 后端最终：uv sync --locked 为Resolved28/14ms、Checked27/52ms；差异专项25 passed in8.47s；uv run pytest -q 为810 passed in316.17s (0:05:16)，uv run pytest tests/golden -q 为763 passed in302.12s (0:05:02)，两组并行不作为性能基准。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations，version-diff帮助与差异检查退出0。初轮装配任务类型与备份时机修正，不放宽完整性；加入后来模式矛盾案例前的809/762为中间结果。
- 隔离wheel124成员、119运行资源与源码及Python3.12.3安装逐字一致，实际密钥匹配0；SHA256 ed8cce2dca95bf831a7e4740c329bf4c8661a690580a3e98b112f0238b469b8a。源码不可见的独立目录执行差异/物理查询/占用49 passed in16.61s，包括真实CLI和HTTP。实际快照合成案例删除工作区、影子Git和原库后，备份对象仍可只读比较，原件/修订不变，模型尝试0。
- 前端沿用户已有subagent授权完成：pnpm test为13files/103passed/795ms；build为228modules/1.09s，tsc退出0。六项专项首轮通过，但父完整浏览器回归实际101通过、1失败 (1.9m)：同次捕获的37个新版本按时间/完整ID排序，测试错误假定比较后版本总在第一页。只将合成服务改为三次真实捕获，核对实际两页ID并往返翻页验证选择，不修改产品、不加超时或重试。十次不同进程、项目、根及临时路径复验10 passed (1.2m)，最终六项6 passed (15.7s)。
- 最终前端29项冻结全部OK，清单SHA256 7c86383e59d3f3d91771c9d2c05dd6b7094fb3f116f3ff528946155e118c9474；旧22项因分页装配修复弃用并保留失败证据。父任务实看最终桌面和390手机选择、来源、捕获观察、正文七图，无横向溢出，子agent停止编辑。父任务接续最终102项完整浏览器，不预报成功。
- 边界：本任务仅用临时合成资料，没有删除真实项目或调用远程模型。未保存和非文本产物差异、实际I/O完整性、影响传播合同、note类型、真实客户端及规模、Atlas正式参考、原生Windows及完整M0–M4继续保留；整项目清除仍只作用于管理目录，外部原日志、研究工作区和用户保存/分享副本沿用既有范围。
- 下一步：完成最终全浏览器与允许文件清单核对，提交并维护现有草稿PR，检查当前HEAD推送/PR两种CI与独立恢复包后结束本任务；随后继续剩余授权开发，不标整体目标完成。
- 父任务最终 pnpm test:browser：102 passed (1.4m)，原96项保留；ruff再次为All checks passed!，差异检查退出0。产品未变，后端全量和隔离安装结果继续对应最终119运行资源，不作机械重跑。
- 提交前允许清单：baseline=2956527、staged_files=20、tracked_files=360、actual_secret_matches=0、forbidden_files=0、spec_unchanged=True、frontend_frozen_items=29、wheel_source_resources_equal=119。暂存差异检查退出0，8791/9792/9793均可重新绑定。仅公开源码、中文说明和合成案例入仓库，凭据、真实会话、数据库、ZIP、截图与缓存继续忽略。

## 2026-10-10 · 精确状态时间与未知诊断

- 任务：继续 §2/5.3/9/15 的状态隔离，开始读取进度末尾、对应章节及决定。e81a0ea 基线恢复包 SHA256 为928f79d146828f21cd72bc0f9ebaa072acc462c56dc64f6c331adfc6f155e54e，独立恢复360个HEAD文件逐字一致。用户选定的整项目清除范围沿用，不操作真实项目；整体M0–M4仍未完成。
- 改动的文件：新增query/time.py、golden/test_state_time.py、state_time_browser.py、前端exactTime.ts及时间状态单测/浏览器专项、两份中文验收。修改API views、Reader、语义图来源、前端model/types/图/问题卡、原状态单测，以及README、决定、需求落实、PR草稿和本文件。源规格与schema17不改。
- 行为：原始双时间旁追加UTC六位微秒只读规范值；无时区、非法日期、缺失及UTC越界为null，越界不再抛查询异常。采用/证据按完整微秒、同范围有效确认事件计算，同毫秒不同微秒不再误判；真同刻相反为冲突，单条未知时间也不假定有效证据。未知范围证据需复核。时间诊断不加入人工科学证据枚举，候选/驳回/替换及双截止规则不改。
- 反证与修正：旧后端18专项为14 failed/4 passed，极端UTC偏移触发OverflowError；旧前端7项6 failed/1 passed。新HTTP测试首版原文路径和详情claim外层写错，按现接口修正测试，不修改生产合同；在专项通过前中止的两组全量不计最终验收。失败日志保留在忽略目录。
- 后端最终：uv sync --locked为Resolved28/14ms、Checked27/66ms；专项19 passed in30.51s；uv run pytest -q为829 passed in377.84s(0:06:17)，uv run pytest tests/golden -q为782 passed in362.77s(0:06:02)，两组并行不作性能基准。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations，rg --help退出0。
- 隔离Python3.12.3 wheel状态/语义图44 passed in70.93s(0:01:10)；125文件成员、120运行资源与源码和安装逐字一致，实际密钥匹配0，SHA256 9e06054d3fa95b09ad7aabee3fcae43df7942b92c2103c1fb0c66af858b4a89b。真实HTTP规范字段/权威状态/原文字节摘要一致，库转储与修订号未变，模型尝试0。
- 前端沿已有subagent授权继续，父任务实看最终桌面问题/折叠/原文和390冲突/未知范围/候选原文六图。首轮浏览器2 pass/2 fail：过程组无单一状态，测试错误要求组的node-dimensions；手机样式隐藏项目下拉，测试等待不可见控件。按保留原记录/展开原成员核对，桌面选择项目后再于390复核，不伪造聚合、不改手机产品入口、不加超时或重试；最终4 passed(15.9s)。
- 下一步：完成前端最终冻结与原102加4专项的完整浏览器回归、允许文件和密钥核对，提交维护现有草稿PR，核对当前HEAD的两种CI与独立恢复包后结束本任务；实际I/O、传播/note合同、真实客户端、Atlas正式参考、规模/Windows及完整M0–M4仍保留。
- 前端最终：pnpm test为14 files/112 passed/744ms，build为229 modules/1.04s，tsc退出0无诊断；35项冻结全OK，清单SHA256 b545e62f8288c4bdd0fb52911dee99634753e7370ac642cc3903d300f18e7196。子agent停止编辑，父任务接续106项全浏览器，不预报成功。实际390项目下拉仍隐藏，验证通过桌面切换再缩窄检查，不声称手机入口可用。
- 父任务最终 `pnpm test:browser` 为106 passed(1.5m)，原102保留；35冻结再次全OK，差异检查退出0。源码、120运行资源及前端不再修改，后端全量和隔离安装结果继续对应最终代码；随后核对允许21文件，维护现有草稿PR与当前HEAD两种CI/恢复包，整体目标保持未完成。

## 2026-10-10 · Codex 原生补丁与单侧候选全文

- 任务：继续 §2/6.3/6.4/6.5/6.6/8.3/9.5/15 的原生补丁适配，开始读取进度末尾、对应章节及决定。9e83e95 基线恢复包 SHA256 8f89e83ede4556a7070345505b31e5b9c7d7580567e067940f0105494d05673d，独立恢复368文件逐字一致。用户选定的整项目清除范围沿用；本轮不操作真实项目，整体目标不标完成。
- 改动的文件：新增 derive/native_patch.py、golden/test_native_patch.py、native_patch_browser.py、前端原生补丁浏览器专项及两份中文验收；修改 derive/edits/records/views/worker、ingest/codex、query/l1、既有L1案例、前端l1/types/EvidenceRecords/单测/既有L1浏览器，以及README、决定、需求落实、PR草稿和本文件。源规格与schema17不改。
- 行为：同实际会话唯一调用关联开始/结束，已有原始补丁请求优先；外层执行器与补丁状态独立，缺开始等待，重复ID拒猜。现代结构化回执允许空stdout，旧缺变更回执需成功及严格路径摘要；普通输出JSON不冒充原生状态。新增后/删除前全文为直接报告候选，更新/移动不补全文，失败可保留一致前像，矛盾不生成全文版本。单侧明确reported_before/after及complete_versions=false，另一侧未知；沿用单项64k UTF8/2000行、事件正文合计64k，超限不截断。
- 迟到边界：派生后重复开始不改不可变事实；证据页与文件图当前标明关联歧义，较早已知截止只使用当时可见记录。原始补丁请求与唯一原生开始分别计数，不将两种来源本身当重复。
- 反证与修正：首批22项旧后端全部失败5.80s，前端旧单侧语义1 failed/6 passed737ms；失败日志留忽略目录。测试初版误用Store上下文管理器、误要求包装请求零观察，按现合同修正测试。迟到歧义补齐前中止的两组全量不计验收。SQL仅分行修正ruff长度，前后AST逐字一致；重建安装包含最终源码。
- 后端最终：uv sync --locked为Resolved28/3ms、Checked27/46ms；原生补丁/L1/文件图111 passed20.16s；uv run pytest -q为870 passed382.31s(0:06:22)，uv run pytest tests/golden -q为823 passed365.73s(0:06:05)，两组并行不作性能基准。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations，rg --help及差异检查退出0。
- 隔离Python3.12.3安装：同三组111 passed22.32s；wheel126成员/121运行资源与源码和安装逐字一致，实际密钥匹配0，SHA256 9d759d436c657d7b7ea8f247754c2a5341279e6e7a2d081069f093b24c4f80a2。事务崩溃、备份、源文件消失、幂等和L0游标保留均有合成固定案例；模型调用0。
- 前端沿已有subagent授权：115单测/14files/896ms、build229modules/1.12s、tsc退出0；独立原生专项4 passed12.7s，父任务实看最终两张桌面与三张390图。32项最终冻结全OK，清单SHA256 10f9404b8c2fd519fffb5e6b57ce27188ff0815a5013dcd3960a060212bfdd25。七文件恢复包逐字核对；子agent停止编辑。
- 父任务首轮完整浏览器99 pass/3 fail/8未运行1.7m：既有L1文案精确断言遇逗号，改为前后全文标签均不存在，保留API/零版本/正文/导航，其原5项通过9.7s；两个合成服务日志在监听前fsync/Git准备被等待清理SIGINT。后端结束后串行完整110 passed1.6m，原106保留，没有改超时或重试。初次日志归档路径错误，但子agent事先保留的原始三份失败日志仍完整冻结，没有重构日志。
- 边界：仅合成原生协议，不证明真实所有工具版本、完整实际I/O、worktree/跨项目归属、影响传播/note合同、真实客户端、Atlas正式人工参考、规模/原生Windows或完整M0–M4门槛。源规格、模型和清除生产代码不改，整项目清除仍只限已授权管理目录。
- 下一步：按23文件允许清单提交并维护现有草稿PR，核对新HEAD推送/PR两种CI与独立恢复包后结束本任务；整体开发继续，不把本轮协议/界面验收当全部目标完成。


## 2026-10-10 · 本机根目录登记与手机项目切换

- 任务：继续 §2/6.6/10/11/15 B 和原设计书第5节的显式项目登记，前端沿已有子agent授权修复§9手机入口。开始核对进度末尾、相关章节和决定，找到目录内原设计docx并只读相关段落；ecbac1d起始恢复包SHA256为47d2822bcc1c699a4cc6840049f19c972e8e7b789314a57b7359013a249869a8，独立恢复HEAD及干净状态再次核对。整项目清除沿用户选择，本轮只删除临时合成项目，整体目标仍未完成。
- 改动的文件：新增store/roots.py、golden/test_project_roots.py、前端移动项目浏览器专项及两份中文验收；修改CLI、Store.project、migrations、手机样式和README、DECISIONS、REQUIREMENTS_STATUS、PR草稿、本文件。源规格不改，数据库升级18只增加Git登记元数据。
- 行为：已有项目显式追加仓库/worktree/数据/别名根；两个worktree不同根ID，共同Git目录独立保存。URL只存本地配置字节SHA256，不写原始地址或凭据，不联网、不跑仓库命令；缺失/超限/失败未知。重复不覆盖旧身份，项目/类型冲突拒绝，相同远端/共同目录不合并，不改会话归属。data不读Git、不加入快照。只读清单同快照分页和修订，旧库升级不扫描旧根，迁移原子回滚；备份和清除屏障纳入新登记。
- 专项：初轮4失败/21通过来自测试错误模块入口及临时目录与Store夹具重名，后续2失败/79通过仍为入口错误；改用真实rg可执行入口，不改产品合同。关联组81 passed25.44s；随后增加换行/shell路径、超限和同共同目录不同项目三案例，最终28 passed8.28s。原始失败日志保留忽略目录。
- 后端最终：uv sync --locked为Resolved28/10ms、Checked27/51ms；uv run pytest -q为898 passed392.43s(0:06:32)，uv run pytest tests/golden -q为851 passed377.92s(0:06:17)，两组并行不作性能基准。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations，rg --help/root-add/roots帮助及差异检查退出0。
- 隔离Python3.12.3安装：根目录28 passed11.16s，包含实际CLI；仓库外临时目录-I运行，模块来自安装目录。wheel127成员、122运行资源与源码/安装逐字一致，实际密钥匹配0，SHA256 d81b2d3d1de57e2dd4d5ede1721ff38fcebacd052dbdf2017118345a6e2a5922。源码冻结后未再修改运行资源。
- 前端：仅样式显示原唯一项目选择器，保留App/请求隔离。115单测/14files/739ms、build229modules/1.05s、tsc退出0；移动三专项初次3 passed8.6s、最终3 passed8.1s。父实看三张390图及桌面图，17项冻结全部OK，清单SHA256 ec9bfc16510fcd6a6eab101c2e8abff988ad1553f7bab08d59c25e42f16a08b4；子agent停止编辑。后端结束后父串行完整113 passed1.6m，原110项保留，不加超时/重试。
- 边界：未读取真实完整会话、不调用模型、不删除真实资料或改个人客户端设置。本机根登记不等于自动worktree发现、跨设备路径或跨项目片段归属；实际I/O、影响传播/note合同、真实客户端/规模/原生Windows、Atlas正式人工参考和完整M0–M4继续保留。手机为Chromium视口验收，草稿仍只页面内存。
- 下一步：按14文件清单提交并维护现有草稿PR，核对当前HEAD推送/PR两种CI、独立恢复包和服务端口后结束本任务。随后继续跨项目片段归属与剩余采集/真实门槛，不把本轮登记和界面验收当整体完成。


## 2026-10-10 · 解析类型与工具版本账本

- 任务：继续 §2/6.1/9/10/11/15 B 的明确采集缺口，开始核对进度末40行、对应章节及决定。fdcbad5基线已验证恢复包SHA256为86b4d4cac5ee00b5016547ec0eb298ec989be25f0fcfa282a1fe545aa4713451，独立恢复379文件一致；前端沿用户既有子agent授权。用户明确的整项目清除范围继续适用，不操作真实项目；跨项目优先级已提问，等待答复时先做独立账本。
- 改动的文件：新增ingest/catalog.py、query/parsers.py、golden/test_parser_health.py、解析账本中文验收及前端账本组件/helper/样式/单测/浏览器与独立合成服务；修改scanner、migration19、清除归属表清单、API、CLI、HealthView及README、需求落实、决定、PR草稿和本文件。两份源规格不改。
- 行为：每完整物理行的类型集合、工具版本及依据与L0/游标一起提交，副本与多块计数区分；非法声明重置同文件版本上下文，复制／轮换／旧库不借版本。类型过长或控制／代理码点只存标签，正文留L0。旧库不补造历史；CLI／HTTP项目分页冻结观测截止、归属变更拒绝拼页。
- 初验：新36项首轮2 failed/34 passed in8.90s，原因是测试写UTF8孤立代理字符、误用原文响应未提供的object_sha256；改为JSON转义及实际引用摘要合同，不改生产接口。解析／导入／迁移／清除关联82 passed in22.54s；随后首末指针增强纳入全量最终测试。失败日志保留忽略目录。ruff通过、pyright0错误；全量正在执行，结果随后追加，不预报通过。
- 下一步：核对全量及隔离安装，后端结束后依次跑前端专项与完整浏览器，实看最终桌面／390截图并冻结；维护既有草稿PR和当前HEAD两种CI／恢复包。本轮采集诊断不等于所有工具版本、跨项目归属、真实Atlas参考或整体目标完成。
- 后端最终：uv sync --locked 为Resolved28/12ms、Checked27/55ms；uv run pytest -q 为934 passed in395.09s(0:06:35)，uv run pytest tests/golden -q 为887 passed in380.98s(0:06:20)，两组并行不作为性能基准。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations；rg及parser-health帮助退出0。
- 隔离Python3.12.3安装：仓库外-I运行新36项为36 passed in11.34s；实际CLI、HTTP、迁移回滚、故障、分页／归属变更、备份和临时整项目清除都有案例。wheel129成员、124运行资源与源码／安装逐字一致，实际密钥0、私有文件0，SHA256为cdf2af7cc90220b04306664e7451707630338704704171ecf80e074d80a8e7b5。运行资源冻结后不再编辑，未调用模型。
- 前端首轮专项2 passed/2 failed(19.8s)：未带字节窗口时摘要本来为null，StrictMode及同页导航使只等两次JSON回包的假设错误。保留初日志，测试改为另真实全文引用、标记实际延迟响应并隔离导航；产品取消／身份逻辑、超时及重试不改。修正后四项已通过11.0s，随后补手机类型卡的截图及几何核对；未将第一次失败算作通过。
- 前端最终：122单测/15files/965ms、build232modules/1.43s、tsc退出0；补几何后四专项4 passed(11.2s)。父实看桌面统计／类型／原文与390统计／类型五图；35项冻结全部OK，清单SHA256为949640135f0ec7894cd62b81010195ca2111ec62559a8db46d411a6ad6aa9f63。8文件恢复包独立解包逐字一致，子agent停止编辑。父首次冻结核对重定向误用web工作目录，随即在根目录实际重新核对全35项；没有将失败准备当验收。全117浏览器串行进行。

- 父任务最终串行 `cd web && pnpm test:browser` 为117 passed(1.7m)，原113项全部保留；35项前端冻结再次全OK，差异检查及最终ruff退出0。运行资源、前端及验收代码冻结，不再修改；随后按22文件清单提交维护既有草稿PR，核对当前HEAD推送／PR两种CI及独立恢复包后结束本任务。完整M0–M4与整体目标保持未完成。


## 2026-10-10 · 钩子失败报告的持久采集

- 任务：继续§2/6.1/6.8/9/10/11/15 B、D，开始核对进度末40行、对应章节和决定。73430d1基线恢复包完整验证，沿用户整项目清除选择，只对临时合成库执行清除。前端沿已有subagent授权；源规格及钩子生产代码不改，整体目标仍未完成。
- 改动的文件：新增ingest/hook_errors、query/hook_errors、golden/test_hook_errors、账本验收及前端独立组件/helper/样式/单测/浏览器/合成服务/中文验收；修改migration20、扫描循环、Store.health、API、CLI、清除表清单、HealthView及README、DECISIONS、REQUIREMENTS_STATUS、PR草稿与本文件。
- 行为：全管理目录的运维报告，不写科研L0或对象库；白名单外类只存摘要，非法正文不复制。流式读取、前缀／提交边界核对、轮换／缩短／已观测缺失后重现建立新实例；报告、游标与不可变观测同事务。残片、积压、超长行、读取变化、缺失和不可安全读取分别显示。相同字节的不同实例分别计数，完整实际失败历史始终未知；未观测不补零，状态变化时间不证明进程存活。分页固定观测及报告截止，备份保留账本，整项目清除保留仅运维的无正文全库元数据。
- 初验：新增1 failed/35 passed in8.05s，测试误用钩子命名参数，改实际位置参数并把RG_DATA_DIR固定到临时目录。错误调用可能向默认管理目录追加无正文参数异常，本轮不删除真实管理数据。关联组127 passed in34.78s；随后补强提交前实际进程退出、不可变约束、inode重用与日期溢出，40 passed in6.91s。首失败日志留忽略目录。
- 隔离安装：Python3.12.3仓库外-I运行40 passed in9.29s，含实际CLI／HTTP、进程退出／事务恢复、迁移回滚、源消失备份、合成整项目清除。wheel131成员、126运行资源与源码／安装逐字一致；实际密钥0、私有文件0，SHA256 f394c0b410d74fa99c03be92598a3679f4c55049f1bbc6bfb1d454b116058f64。源码冻结后未改运行资源，模型调用0。
- 下一步：等两组全量实际结束后，再串行前端专项和完整浏览器，实看桌面／390最终图并冻结；维护既有草稿PR，核对当前HEAD两种CI和独立恢复包。真实客户端漏报、原生Windows、完整I/O、跨项目优先级与M0–M4保留，未预报通过。

- 后端最终：uv sync --locked为Resolved28/15ms、Checked27/63ms；uv run pytest -q为974 passed in413.88s(0:06:53)，uv run pytest tests/golden -q为927 passed in399.07s(0:06:39)，两组并行不作为性能基准。ruff为All checks passed!、pyright为0 errors,0 warnings,0 informations；rg及hook-errors帮助退出0。后端运行资源已冻结，前端专项在两组结束后才启动。

- 前端最终：128单测/16files/1.06s，build235modules/1.04s，tsc无诊断；4项真实专项首轮全部通过9.9s，没有失败、超时或重试调整。父实看桌面统计／报告与390统计／报告四图；33项冻结全OK，清单SHA256 a2dd1240de54812f915e36e05e4936275eec0cff92822e071a3a9b4a482b8e56。8文件恢复包ab446ad37ad7aae34d43f7b8af75de37a588ea2a23964161567599ead899101d逐字一致；子agent停止编辑，8791/9797释放。父在后端两组结束后串行完整121浏览器，未预报最终结果。

- 父任务最终串行cd web && pnpm test:browser为121 passed(1.8m)，原117项完整保留；33项冻结复核全OK，实际8文件恢复包内容逐字一致。后端126运行资源与已安装wheel再次逐字核对，差异检查退出0。随后按24文件清单提交维护既有草稿PR，核对新HEAD推送／PR两种CI与独立恢复包后结束本任务；整体目标与M0–M4保持未完成。

## 2026-10-11 · Codex原生父线程关联

- 任务：继续§2/6.3/9/10/11/15 B，开始核对进度末40行、对应章节及决定。aa92e9c基线恢复包SHA256为8657a2c338bd7f72a03e3d19b0296a1ef0551c213197256a7989470e69679863，401文件独立恢复及干净状态再次核对。沿用户整项目清除选择，本轮只使用临时合成项目；前端沿已有subagent授权，整体目标仍未完成。
- 改动的文件：新增ingest/parents、query/session_parent、golden/test_session_parent、后端中文验收和前端父线程组件／helper／样式／单测／浏览器合成服务／验收；修改migration21、清除表清单、scanner、API证据及只读接口、CLI、EvidencePanel/types，以及README、DECISIONS、REQUIREMENTS_STATUS、PR草稿、本文件。源规格及个人客户端设置不改。
- 行为：原生顶层及旧thread_spawn直接父ID声明与L0／游标同事务，唯一父身份才关联；父头迟到恢复，冲突、无效身份、自引用、歧义及循环不猜。根session_id、forked_from_id和目录不作直接父依据；Claude原关联保持。可变投影可恢复，只读查询依据不可变声明；全部判定与最近20条部分展示区分。项目归属不继承，跨项目只保留子头源声明，不落父外键／返回父编号或引用。升级只建表，重扫从已存可识别头补记并保留原L0时间；备份和清除纳入新表。
- 首验：50项2 failed/48 passed in11.41s，测试误用数据库文件名；跨项目父外键阻止原清除保护。修正路径并收紧跨项目投影，原清除拒绝规则不变。一次关联组误用不存在的test_spool.py为0项，改实际文件后100 passed in29.44s。补强真实进程退出、旧多头补记、并发快照、旧Codex投影恢复及直接父身份案例，最终55 passed in9.74s；失败日志保留忽略目录。
- 隔离安装：Python3.12.3仓库外-I运行55 passed in14.61s，含真实CLI／HTTP、提交前进程退出79、投影故障、只读并发快照、迁移回滚、源消失备份及合成整项目清除。wheel133成员、128运行资源与源码／安装逐字一致，实际密钥0、私有文件0，SHA256 e43586ae455cd8c9b37e20ebbbf1b3046e9a5e73e680deba67615fe046a70f90。首安装准备误引用不存在的golden/__init__.py，移除错误准备项后取得上述结果；没有当作通过。运行资源已冻结。
- 进行中：uv sync --locked为Resolved28/13ms、Checked27/51ms；ruff通过、pyright0错误，rg及session-parent帮助退出0。两组全量正在运行，不预报结果；后端结束后才跑前端浏览器专项／完整验收，实看最终桌面及390截图并冻结，再维护既有草稿PR及当前HEAD两种CI、独立恢复包。
- 边界与下一步：本轮不读取完整真实会话、不调用模型、不删除真实资料。完整Claude parentUuid链、全部工具版本／实际规模、跨项目片段优先级、完整I/O、影响传播／note合同、真实客户端／原生Windows、Atlas正式人工参考及M0–M4继续保留；本轮关联与界面通过也不代表整体完成。实际全量和最终提交证据随后追加，完成本任务后结束本会话。

- 后端最终：uv run pytest -q为1029 passed in435.58s(0:07:15)，uv run pytest tests/golden -q为982 passed in420.83s(0:07:00)，均退出0；两组并行不作性能基准。uv sync --locked为Resolved28/13ms、Checked27/51ms；ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations；rg及session-parent帮助退出0。后端128运行资源冻结，前端135单测／17files／976ms、build238modules／1.08s、tsc退出0；浏览器专项在后端结束后启动，不预报结果。

- 前端专项：4项真实浏览器首次全部通过11.3s，无失败／超时／重试调整。父实看桌面关联／父头／冲突及390关联／跨项目五图；32项冻结全OK，清单SHA256为823d6dbb1c063bded9e4f1771dd8b1362a64ef66ce318aa4565b8f2ccc4f322f。9文件恢复包c90ef293e595b93f6c3c887d49ca657c074559b9d36008f5813f4a3d5becf4c5独立解包逐字一致，前端停止编辑且8791／9798释放。父开始串行完整浏览器，结束后追加实际结果；源声明与当前父关系分开，源文件行位置不当成对象窗口，只传事件ID导航。

- 父任务最终串行cd web && pnpm test:browser为125 passed(1.8m)，原121项完整保留；32项前端冻结再核对全OK，9文件恢复包逐字一致，后端128运行资源与已安装wheel再核对一致。最终ruff、差异检查退出0，运行资源／前端／验收代码不再修改。随后按24文件清单提交维护既有草稿PR，核对新HEAD推送／PR两种CI和独立恢复包后结束本任务；下一任务继续Claude parentUuid链及其余明确采集缺口，整体目标与M0–M4保持未完成。

## 2026-10-11 · Claude来源事件父链

- 任务：开始核对进度末40行、§2/6.2/15 B及决定，f384f7d基线的412文件、恢复包、隔离运行资源、前端冻结与当前HEAD两种CI实际再次核对。沿用户整项目清除选择，本轮只用临时合成库；前端沿已有subagent授权，整体目标保持未完成。
- 改动的文件：新增ingest/claude_chain、query/event_chain、golden/test_event_chain、后端中文验收及前端父链组件／helper／样式／单测／专项浏览器／合成服务／中文验收；修改migration22、scanner、watch、清除表清单、CLI、API及原文响应、EvidencePanel/types、README、DECISIONS、REQUIREMENTS_STATUS、PR草稿和本文件。
- 行为：正文UUID去重不改，逐物理来源保存父UUID／严格旁支声明；多块共原文锚点。父记录迟到可恢复，同来源优先／项目唯一等价组回退；副本变体、歧义、循环、积压和多来源祖先不猜。全候选判定与20引用部分展示分开，祖先最多64步。旧元数据每来源每轮补至多256条，原件消失仍从已存对象恢复，批内事务失败整体回滚，新尾不跨缺口，原L0及时间不改。跨项目无父引用／外键，备份和整项目清除纳入新表。
- 首验：54项为2 failed/52 passed in15.52s，测试遗漏正文ID块序号、取错时间列；修正为既有身份与命名列。关联准备误用不存在的test_clear.py导致0项，已改实际test_project_clear.py，不计通过。初日志留忽略目录；补强轮换、对象损坏、源消失旧备份、实际补记进程退出和批内回滚后，最终验证进行中，不预报结果。
- 下一步：后端全部与固定案例、隔离Python安装实际结束后，再串行前端专项及完整浏览器，实看桌面／390图并冻结。维护既有草稿PR、核对当前HEAD两种CI和独立恢复包后结束本任务；全版本／实际规模、跨项目片段优先级、完整I/O、影响传播／note合同、真实客户端／原生Windows、Atlas正式参考与M0–M4继续保留。

- 关联最终：新增63 passed in18.77s；事件链／原父线程／导入／spool轮询／整项目清除共168 passed in46.60s。ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations；uv sync --locked为Resolved28/1ms、Checked27/58ms，rg及event-chain帮助退出0。运行资源冻结；全量和隔离安装仍在执行，未开始前端浏览器。

- 复核修正：祖先循环需按来源实例及UUID组合判断，避免不同fork的相同UUID被误报循环。仅UUID的实现已修正并追加回归；正在执行的两组全量显式停止，旧结果不作最终验收。重新构建并隔离安装，再运行最终64项与两组全量；前端接口合同不变，浏览器仍未启动。

- 修正最终专项：64 passed in18.31s；仓库外Python3.12.3 -I隔离安装64 passed in26.99s，包135成员／130运行资源与源码及实际安装逐字一致，实际密钥0、私有文件0，wheel SHA256为6ea7b25a8b92d05a326bf0be36cf642a4903c5999554aad78d1648a6ffc86dd3。ruff通过、pyright0错误；两组旧全量退出143，旧日志保留忽略目录，修正后正在重跑。运行资源再次冻结，没有启动前端浏览器或调用模型。

- 后端最终：uv run pytest -q为1093 passed in470.74s(0:07:50)，uv run pytest tests/golden -q为1046 passed in455.59s(0:07:35)，均退出0；两组并行不作性能基准。ruff为All checks passed!、pyright为0 errors,0 warnings,0 informations；sync及CLI帮助沿上述结果。后端130运行资源冻结，已通知前端在两组实际结束后启动真实专项，未预报浏览器结果。

- 前端专项最终：143单测/18files/978ms，build241modules/1.51s，tsc退出0；4项真实浏览器专项首轮全部通过15.0s，无失败／超时／重试变动。父实看桌面关联／积压／副本与390关联／引用卡／跨项目六图，父再次核对33项冻结全部OK，清单SHA256为de76885ada0da182edac070efb4e6b5b8696e7595d6abe7758583d73c4593d4e；9文件恢复包22b66332cf08ee91c0a88b2ecb2d39a9bacaed1a248e849c39e20396b9b74c2d内容逐字一致。前端停止编辑，8791／9799已释放；父在后端与专项实际结束后串行完整浏览器，未预报结果。

- 父任务最终串行cd web && pnpm test:browser为129 passed(2.0m)，原125项完整保留；33项前端冻结再核对全部OK、恢复包九文件逐字一致，后端130运行资源与已安装wheel一致。最终ruff、差异检查退出0，运行资源／前端／验收代码不再修改。随后按25文件清单维护既有草稿PR，核对新HEAD推送／PR两种CI及423文件独立恢复包后结束本任务；下一任务继续明确需求缺口，整体目标与M0–M4保持未完成。

- 终检补强：3b10094已按25文件清单提交，423文件独立恢复／历史实际密钥0／私有路径0验证后推送，草稿正文逐字一致且未合并。但真实循环新增损坏压缩原文案例复现1 failed/64 deselected in1.85s：ZstdError不属于循环已有的错误类别，会中止其他来源。父链补记现将其规范为无正文ValueError，保留缺口、回滚批次并由既有扫描错误处理继续其他来源；不改生产钩子、对象库或前端合同。旧验收不作为修正最终结果，重建隔离安装并重跑后端；前端33项冻结不变，当前HEAD的CI及恢复包仍待修正后再次核对。

- 补强专项：65 passed in19.87s；仓库外Python3.12.3 -I隔离安装65 passed in32.14s，135成员／130运行资源与源码及实际安装逐字一致，实际密钥0、私有文件0，wheel SHA256为112ed629560b723c591cde71dbf63da9f9215dbadf6f92c3c8fbf4c087a3fe03。ruff通过、pyright0错误。明确256为每次补批上限，spool／重试可启动独立批次，不将无提示的两轮案例冒充全部入口I/O上限。两组修正全量仍在执行；前端合同与冻结保持，旧提交两种CI已退出0，不替代新提交验收。

- 补强后端最终：uv run pytest -q为1094 passed in469.39s(0:07:49)，uv run pytest tests/golden -q为1047 passed in454.13s(0:07:34)，均退出0；并行不作性能基准。65项隔离安装及130运行资源按上述最终包验证，ruff／pyright通过；前端合同未变、33项冻结再次全部OK，保留143单测及129浏览器实际验收，不机械重跑无变更界面。随后按8文件补强清单提交，独立恢复423文件及密钥／私有文件检查通过后才推送，验证新HEAD的推送／PR两种CI与精确草稿正文。完成本任务后结束本会话，下一任务继续明确采集和功能缺口；整体目标与M0–M4仍未完成。

## 2026-10-11 · Codex已存父线程自动补记

- 任务：开始核对进度末40行、§2/5/6.1/6.3/9/10/15 B及决定；a28c915基线实际核对干净工作区、远端、精确草稿、当前HEAD两种CI、423文件独立恢复、130运行资源、33项前端冻结及已释放端口。恢复包SHA256为3215e25350c6139499e225ff4251a8a2de8303c61ebd0606f10275bae8695e77，整体目标保持未完成。
- 改动的文件：迁移23、ingest/parents、scanner、watch、query/session_parent、新golden/test_parent_backfill和原父线程升级期待；前端沿用户已有subagent授权更新父线程覆盖字段／积压展示／单测及合成浏览器，不改Claude父链与用户配置。README、DECISIONS、REQUIREMENTS_STATUS、PR草稿、本文件及中文验收随实际结果更新。
- 行为：已存Codex可识别头每来源每批256条自动核对，原件消失和旧轮换实例仍可补记；批内声明与游标事务提交，新尾不跨缺口，已有头不重读，损坏／被锁来源不阻塞其它来源。覆盖未齐不判唯一父，已观察非法／冲突继续保留；后台齐备自动恢复投影，读接口只查同一快照，无人工或模型调用。
- 首验：父线程专项80 passed in20.79s。随后补强游标比较失败、原文摘要损坏及既有头无重读，关联组为198 passed in57.18s、ruff All checks passed!、pyright0错误；uv sync --locked为Resolved28/1ms、Checked27/47ms，rg及session-parent帮助退出0。运行实现已冻结，两组后端全量和隔离安装正在执行，不预报结果，浏览器等待两组实际结束。
- 边界及下一步：只用临时合成库，不读取整份真实会话、不调用模型、不删除真实项目。补记游标不证明全部历史或所有工具版本；§12、真实客户端／原生Windows／实际规模、完整I/O、跨项目片段优先级、note与传播合同等继续保留。完成后端／前端实际验收、维护既有草稿PR、验证当前HEAD两种CI和独立恢复包后结束本任务。

- 隔离安装最终：仓库外Python3.12.3 -I实际运行父线程及补记84 passed in36.87s，135包成员／130运行资源与源码及安装逐字一致，实际目录密钥0／私有文件0，wheel SHA256为af22723342f17ff92ced4b33db1ddcbec960b35f11615bf5105016c167ccdcc9。覆盖真实CLI／令牌HTTP、读快照与事务／进程退出79、原文损坏和旧备份恢复；两组全量仍在运行，没有预报完整结果或启动浏览器。

- 后端最终：uv run pytest -q为1123 passed in489.71s(0:08:09)，uv run pytest tests/golden -q为1076 passed in474.66s(0:07:54)，两组均退出0；并行不作性能基准。130运行资源保持上述隔离包冻结，最终ruff通过。前端147单测／18files／905ms、build241modules／1.49s、tsc退出0；两组结束后才通知启动原9798四例及新增9800四例浏览器专项，未预报结果。

- 前端专项最终：首次7 passed/1 failed(23.2s)，唯一失败为390卡边框滚动取整相差0.171875px；只把测试滚动改居中，整卡／标题／按钮严格位置断言保留，产品／超时／重试不改。复验原四项及新四项为8 passed(21.5s)、退出0；原始失败日志／图和最初helper复现4 failed/143 passed(1.59s)保留。父实看七张桌面及390图，43项冻结全部OK，清单SHA256为5dd68a4c579549d14001de9c39ff5161009c60a2fd5ef9236f8b6cc8c9ebb877；九文件恢复包149cbabdc4005d43b33d36b8cd772bc1e2b1eb7227656aba820661337b66d089实际独立解包逐字一致。原件七图与稳定副本逐字一致；前端停止编辑，8791／9798／9800已实际可绑定，父随后串行完整133项浏览器，未预报完整结果。

- 父任务完整浏览器最终：cd web && pnpm test:browser为133 passed(2.1m)、退出0，原129项保留。两组后端与专项均实际结束后才串行启动；43项前端冻结再次全部OK、130运行资源与最终wheel仍逐字一致，最终ruff及差异检查退出0。实现及验收源码不再修改，按22文件精确清单提交；验证428文件独立恢复、历史密钥／私有路径检查后才推送既有草稿PR，核对当前HEAD推送／PR两种CI与精确正文。完成本任务后结束本会话；下一任务继续明确核心需求，note与传播规则问题已提出，真实工具版本／客户端／规模／Windows及Atlas正式参考门槛继续保留，整体目标与M0–M4未完成。


## 2026-10-11 · 完整研究图读取与历史折叠

- 任务：核对进度末40行、§2/5/9/10/15 A、原设计第7节及决定。实际再运行上一轮终检，基线3d39d19的工作区／远端／草稿正文／双CI／428文件独立恢复／130运行资源／43项前端冻结及测试端口均通过。上一轮为实质进展，本轮继续整体开发。
- 改动的文件：新增query/graph_records和golden/test_graph_records，更新query/graph、api/graph、api/server及graph CLI集合；前端沿用户已有subagent授权补完整研究图分页、历史详情及原文条件和折叠语义，后端／总文档由父负责。
- 当前行为：完整记录页最多100条，按同一双截止保存审核／替换与全部可见引用；历史详情不取今天状态，历史原文先筛项目与双时间再读已存对象。前2000条简版接口保留给其它视图，不能作为完整研究图的依据。
- 首验：uv run pytest tests/golden/test_graph_records.py -q为15 passed in7.45s，ruff为All checks passed!，pyright为0 errors,0 warnings,0 informations，均实际退出0。覆盖2006条完整页、105引用、迟到审核／更正、未知时间／范围、跨项目、并发写快照、删除来源的字节窗口、真实令牌HTTP及CLI。没有读取真实全文、调用模型或删除真实资料。
- 下一步：加强历史原文的并发及错误参数验证、运行关联组和两组全量，再串行前端真实专项及完整浏览器、实看桌面与390图。更新中文验收与需求状态、维护草稿PR、核对新HEAD的双CI和独立恢复后结束本任务；note／传播等待原问题答复，实际规模、真实客户端和M0–M4门槛继续保留。

- 关联复验：首轮为1 failed/151 passed in53.69s，新增并发原文案例只追加L0却期待语义图修订变化，属于测试设定错误；改为同时追加审核记录，保留原失败日志，不改变修订合同。最终152 passed in53.33s；仓库外Python3.12.3 -I隔离安装48 passed in19.69s，136包成员／131运行资源与源码及实际安装逐字一致，实际密钥0／私有文件0，wheel SHA256为8b7194393d87f1a4caf6aaae5013aebbe79c1f34899d97482f75a133beb459a3。两组全量已实际启动，未预报结果；前端浏览器仍等待它们实际结束。

- 后端最终：uv run pytest -q为1146 passed in491.73s(0:08:11)，uv run pytest tests/golden -q为1099 passed in476.83s(0:07:56)，两组均退出0；并行不作性能基准。131运行资源保持已安装wheel冻结，已通知前端在最终单测／构建／类型检查结束后开启真实专项；未预报浏览器结果。

- 前端首轮：158项单测通过，但新增测试类型推断使build／tsc实际退出1；子任务在读取该返回值前提前启动专项，已停止并保留原始日志。该轮4 passed／2 failed／4未运行(24.7s)，分别为旧dist未包含旧最小记录兼容修正、2005条合成种子的逐条提交拖住监听准备。修正测试类型及一次合成导入事务后，158单测、build243modules／1.06s及tsc实际退出0，再重新启动原五项与新增五项专项；不调整超时／重试，不预报复验结果。

- 前端专项最终：其后两轮5 passed／1 failed／4未运行(16.3s／15.8s)均为镜像夹具断言错误，按Claude实际raw_events.alias_of保留原件、归一正文核对后，原五项与新增五项10 passed(32.2s)，实际退出0；最终158单测／19files／875ms、build243modules／1.06s及tsc退出0。2033条真实合成HTTP记录21页读齐、35引用保留，历史／409／错页／迟到200及401／390几何通过。父实看六图，56项冻结全部OK，13文件恢复包独立解包逐字一致；清单SHA256为f61298bad29fe6be6921c89eb67d7b7ef203ac834a44d6b69026710c2f682ae5，恢复包4f5bb2c254330860f8560165b2b6583396919a451f59894c04fd3e2c82c25be3。前端停止编辑，8791／9801实际可绑定，最终全局ruff为All checks passed!、差异检查退出0；父接续串行完整浏览器，不预报结果。

- 父任务最终串行cd web && pnpm test:browser为138 passed(2.4m)，实际退出0，原133项保留；两组后端及专项实际结束后才启动。131运行资源与源码／最终wheel／隔离安装仍逐字一致，56项前端冻结和13文件恢复已核对，最终全局ruff及差异检查通过。实现与验收源码不再修改；按25文件清单提交、独立恢复437文件及历史密钥／私有路径检查后再推送维护既有草稿PR，核对新HEAD两种CI和精确正文后结束本任务。下一任务继续真实工具／规模、个人ViewState、完整十类折叠及其它需求缺口；note与传播原问题仍待答复，整体目标和M0–M4保持未完成。
