本机 Codex／Claude Code 会话已接通项目登记、持续采集、提取及桌面查询。首次打开可直接创建项目，表单仅名称、绝对工作区和本地采集开关；列表加载／失败不冒充空库，保存失败保留输入，同名同根重放返回原项目。项目与根事务发布，重叠的不同项目拒绝创建；来源明确勾选才登记服务用户已有的默认共享目录，缺失／不可用分别提示，项目仍保留。创建不安装钩子、不读会话正文、不授予模型外发。

新增包含界面的通用桌面wheel，从任意目录运行，无需用户安装Node／pnpm。安装包优先自身资源，源码使用自己的构建目录；默认不会选择当前目录中无关页面，缺少界面时在创建数据目录／启动后台前拒绝。普通后端安装仍轻量，桌面构建由脚本显式包含静态资源；新增CI构建及仓库外安装检查。

既有 `client-install` 合并项目技能和MCP，`start` 管理扫描／spool／离线派生／文件处理，明确加 `--extract` 后串接模型流水线。保留外发许可、128k可调实测预算、候选约束及每日额度。只读 `impact` 固定项目、完整范围、双截止和修订，确认关系与候选／报告依赖分开，不改变科研状态或推定完整实际I/O。界面仍默认三个主入口，影响入口收在详情折叠区。原生Windows Codex基本四回调已验收，状态读取与后台退出故障已修复。

## 验证

本地仅跑相关检查，完整回归交现有CI；原断言／超时／重试保留。

```text
创建／资源／后台／根目录四组 pytest         48 passed in 17.99s，退出0
ruff check rg tests scripts               All checks passed!，退出0
pyright                                  0 errors, 0 warnings, 0 informations，退出0
Windows3.12.3 -I 仓库外安装三组            20 passed in 6.04s，退出0，utf8_mode=0
pnpm test                                238 passed／26文件／1.18s，退出0
pnpm build                               265模块／1.12s，类型检查通过，退出0
创建项目专项浏览器                        2 passed (13.8s)，退出0
build_desktop.py                         265模块／1.08s、wheel成功，退出0
Linux／Windows仓库外完整包                9资源请求成功，重放一致，后台退出[0,0]，模型0，实际退出0
```

浏览器通过真实空Store、本机HTTP和公开合成home验证明确来源登记、目录缺失仍保存、重复请求以及503／400不丢输入。安装检查加载全部基础与动态资源，后台实际进程受管道和句柄管理；源仓库之外运行。首次HTTP测试代理缺依赖、前端无用导入及Windows脚本临时cwd清理失败均保留；修正后才记通过，没有将失败算成功。此前9b57238的PR38372647304／push38372639625实际都成功，但不能代替本次提交；完整后端／固定案例／浏览器见[当前检查页](https://github.com/FangWHao/ResearchGraph/pull/1/checks)。

## 实际验收边界

Atlas参考候选未取得正式人工参考回执；研究找回率、解释质量与成本、个人接入、全八类钩子、实际I/O、提示时刻p95、规模和两周使用仍待实际验收。跨工作树统一检索与未保存的大文件历史正文保留限制。工程安装结果不代替科学参考，未宣称完整M0–M4验收完成；资料、密钥、数据库及缓存不提交，草稿保持未合并。

说明见[首次启动与桌面完整包验收](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/acceptance/首次启动与桌面完整包验收_20261013.md)、[使用说明](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/README.md)和[需求落实情况](https://github.com/FangWHao/ResearchGraph/blob/codex/m1-backend-20261009/docs/REQUIREMENTS_STATUS.md)。
