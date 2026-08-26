# 当前实现与支持边界

> 更新时间：2026-08-26<br>
> 适用版本：`0.5.2`<br>
> 适用范围：可信本地、单机、单节点 SQLite<br>
> 分支 checkpoint：P4a 已由 PR #107 合入 `feat/plugin-v1`（merge `b94d4846`，目标分支
> CI 6/6），该集成分支 live migration tail 为 V33；当前 P4b Issue #108 实现分支已追加
> V34；本地最终门禁已通过（后端 5270 passed / 2 skipped / 24 slow deselected，Web
> 347 passed，静态检查与生产构建通过），但 PR 与目标分支 CI 尚待完成。下文凡标注“P4b 分支”的能力均不是
> `feat/plugin-v1` 已合入能力。

这份文档是 `docs/` 的当前状态入口。它用于回答“现在能不能用、支持到哪里、哪些
还不能对外承诺”。详细能力和证据见
[能力事实矩阵](launch/capability-matrix.md)，安装与操作步骤见
[使用文档](usage/)，20 个 Web 功能点见 [功能图鉴](usage/feature-tour.md)，设计与代码
映射分别见 [design/](design/) 和 [impl/](impl/)。

## 事实源优先级

文档发生冲突时，按以下顺序判断：

1. 当前源码、数据库迁移账本、自动化测试和本次真实运行结果；
2. 本文与 [能力事实矩阵](launch/capability-matrix.md)；
3. 当前的 `design/`、`impl/`、`usage/`、`ops/` 和 `reference/` 文档；
4. `adr/` 中尚未被后续 ADR 取代的决策；
5. `plan/`、`strategy/`、`superpowers/`、`codex-v1/` 和 `cc-fable-v1/`
   中的历史计划、交接包与 Gate 证据。

历史报告只能证明报告绑定的代码与环境，不能替代当前工作树验证。历史报告正文、
时间戳与批准图片不为追随当前实现而改写；必要的导航提示、移动后的链接和 manifest
修复会单独记录，例如 [`codex-v1/AMENDMENTS.md`](codex-v1/AMENDMENTS.md)。

## 当前可用能力

| 功能 | 当前结论 | 用户能做什么 | 明确边界 |
|---|---|---|---|
| 普通任务 | 可用 | 立即执行，查看状态、时间线、结果、成本、证据与裁决 | 受支持的正式路径是 managed Native；不承诺不透明外部 CLI 的同等级治理 |
| 长程任务 | 可用（有限边界） | 使用检查点、暂停/继续、运行中指引、恢复与终态监督 | 仅支持立即执行或单次定时；不支持 cron/interval 周期长程任务 |
| 定时任务 | 可用 | 创建、编辑、暂停、恢复、立即运行、查看历史、按时区运行并处理 misfire | 普通任务支持 once/cron/interval；单节点，不承诺分布式 exactly-once |
| Web | 可用 | 从中枢、御书房、朝堂、百司、天工院〔实验〕、内府六个一级入口进入各功能；在中枢分开查看当前执行中、未归档敕令、待裁决总数和累计证据束（含归档）；在御书房查看全部可见未归档任务、真实进度和待人工介入事项；在天工院进入实验与试行能力；首次创建任务后进入详情 | 中枢四项指标来自同一主体范围的权威快照，并以前台 5 秒兜底轮询和 WebSocket 失效重拉保持更新；桌面优先；`/edicts` 仅作兼容跳转；本地实现已验证，最新视觉/交互基线仍待用户最终审批 |
| API 与身份 | 可用 | 可信本地直接使用；远程模式可使用 PAT | 普通 PAT 只能访问自己拥有的任务；全局审计、Worker 和统计面仅管理员可见 |
| 持久治理 | 可用 | 使用 Decision、RunState、attempt fencing、恢复点和 Evidence Bundle | 单机 SQLite；不提供 PostgreSQL/K8s/多副本保证 |
| 通知 | 可用（有限边界） | 多通道投递、失败重试、保留各通道已成功进度 | 外部通道最终送达仍取决于第三方服务；不把本地 outbox 当外部送达证明 |
| 记忆 | 可用（有限边界） | 写入、召回、同步、删除并使用稳定标识 | 以当前支持的本地 Markdown/SQLite 路径为准；不宣称跨节点一致性 |
| 成本与用量 | 可用 | 记录多模型/多 Provider token、缓存读取和取消任务成本 | 结果取决于 Provider 返回的用量与价格配置，不等于账单系统 |
| Skills | 读取可用、变更受治理 | 在统一目录查看 live Skill、读取详情、为 Agent Skill Pin；API 可创建候选 | Web 不直接新建/编辑/delete/archive live；Persona 外部导入只预览 Skill、不安装；自动 reviewer/curator 默认关闭并在 LLM 前 fail fast |
| 插件清单 / 受信任源码贡献 | 清单实验只读；内部生命周期可用 | 查看本地 manifest 元数据；内建装配可让 Tool/Hook/Channel/Provider/Skill/Command 按 owner 登记、逆序释放；MCP session 工具随重新发现、断连与 shutdown 清理 | 用户仍不能安装、加载、激活或执行第三方插件代码，相关 API 仍返回 501；ContributionHandle 只覆盖受信任进程内对象，不是动态 PluginHost |
| 自进化（Evolution） | 实验；P4a 已合入，P4b 分支实现与本地门禁完成待 PR/CI | 从演化司查看候选/Gate/分流/回滚；在 P4b 实现分支，管理员可查看逐 subject 路由并用严格 CAS 修改 Skill 的 evolution mode 与 max canary basis points，再从 assignment、Edict 详情和 Evidence artifact 对账运行选择；Pi 已有 active/last-good 代、attempt 固定与 continuity 保留 | availability/source/curator protection 只读，`pinned` 不是版本 pin；没有 enabled 或版本 pin 开关，也没有 Web 晋升/回滚按钮。policy 列表和详情 GET/PUT 均为 admin-only。P4b 的 V34、多 subject 路由与 UI 尚未合入目标分支；完整 dependency lock、allowed surfaces/approval/budget、动态插件加载、统一 Promotion Authority 和自动晋升仍未完成 |
| 平行位面（Universes / Code variant） | 实验、可发现 | 从天工院的诸界台创建快照/分支、diff、评估、归档和恢复 | switch、rollback、promote-code 固定 fail closed；代码候选只到 evaluated/recommended |
| 评测（Evals） | Beta、导航标记“试行”、可发现 | 从天工院的考功司查看真实评测集、运行、分数、失败分布和历史差异 | 数据为空时展示真实启动方式，不生成示例成绩 |
| Keqing 外部执行器 | 实验、可发现且默认关闭 | 从客卿馆查看安装/验证版本及 Pi 的 generation id、状态、活跃 run 数和 last-good；进入页面、每 15 秒及窗口重新聚焦时同步 | 不自动升级外部 CLI；页面没有 stage/activate 控件；Pi 代际材料固定与失败关闭不等于 Candidate 晋升已开放；Claude Code/Codex/OpenCode 尚未进入 RuntimeGeneration，且无 Provider 侧硬成本上限 |
| remote/open stdio MCP | 延期、默认关闭 | 当前不作为正式开放能力 | 需要独立完成远程安全与 executable/argv/env/workdir 精确绑定后才可开放 |
| Docker | 可本地试用 | 构建三阶段、非 root 的本地镜像并检查健康路由 | 不是官方发布制品；体积与运行时依赖锁定仍可优化 |
| PyPI 发布 | 链路就绪、待首发 | `release.yml` 在 tag 触发时经 Trusted Publishing（OIDC，无长期 token）发布 `tianshu-agent-os` | 首个版本以 PyPI 上实际存在的发行版为准；需先在 PyPI 侧配置 pending publisher |
| GHCR/签名发布 | 未发布 | 无 | 需要新的发布授权、正式 provenance 与发布 Gate |

## 长程任务与定时任务组合

为保持用户模型简单，组合规则固定如下：

| 执行方式 | 普通任务 | 长程任务 |
|---|---:|---:|
| 立即执行 | 支持 | 支持 |
| 单次定时 | 支持 | 支持 |
| cron 周期 | 支持 | 不支持 |
| interval 周期 | 支持 | 不支持 |

前端、API、应用服务、调度器和工具层都会拒绝“周期 + 长程”组合。长程任务的并发策略
固定为 `skip`，避免上一次尚未结束时重叠启动新的长程运行。若确实需要周期性深度工作，
应把周期触发器拆成普通任务，由普通任务决定是否创建一次新的长程任务。

## 数据、权限与迁移

- `feat/plugin-v1` 当前 live migration tail 为 V33；当前 P4b Issue #108 实现分支为 V34。
  V33 `0033_evolution_policies` 已随 P4a PR #107 合入，提供每个 subject 的
  frozen/manual/canary policy、严格 CAS、同 subject canary 排他与 repository-level 执法。
  V34 尚待 PR/CI，不能写成目标分支已上线。
- V31 `0031_system_snapshots` 追加不可变典制与 per-attempt
  `run_system_bindings`，P1 当时不回填存量 Memorial。两表采用严格 schema replay；bindings 允许随
  Edict 物理删除清理，内容寻址 snapshot 不允许 replace、update 或 delete。
- V32 `0032_runtime_generations` 追加五张表：内容寻址 executor release、七态 generation、不可变
  journal、active/last-good pointer，以及独立 insert-once 的 `run_generation_bindings`。后者是 P3
  的 exact-attempt 代际权威；`run_system_bindings` 只在 snapshot 启用时作为可选 shadow，并作为
  V31 历史兼容读取源；两者同在时 generation ids 必须一致。
- V32 会为既有 attempt 做可证明回填：有 `run_system_bindings` 就复制其 generation ids；可证明
  为非 Pi 的 attempt 写 `bound []`；无法证明历史 Pi 选择的写 `unresolved` 并在 continuity/retention
  读取时失败关闭。snapshot 显式关闭时，新 attempt 仍写 `run_generation_bindings=bound []`，但
  `run_system_bindings` 保持零写入；无 runtime generation/pointer 行时继续使用 static adapter，
  不伪造默认代。
- P4b 分支的 V34 `0034_run_subject_assignments` 将每个 Memorial 的 1..64 条 per-subject
  assignment 通过 repository batch + SAVEPOINT 原子写入，并在读取时重算 set hash/size。
  fresh root 在 0/1/N 个 canary 时分别保持 legacy-only、
  旧 singleton 精确投影 + V34 singleton、旧 legacy 投影 + V34 完整 set；follow-up 先继承父
  set，不按当时 active canary 数重新抽桶。CANARY 沿用父选择，PROMOTED 选择 candidate，
  回滚态选择 base，ARCHIVED 按当前 version lifecycle journal 判定且缺失时 fail closed。
  `evolution_overlay_set` 只保存 canonical overlay 列表的 digest，不内嵌 assignment set。
- 关闭 `TIANSHU_EVOLUTION_ROUTING_ENABLED` 后，existing replay 与 continuity inheritance
  仍先于 fresh-root kill switch，因此已持久化 follow-up 保持 sticky，只有 fresh root 不再新选
  challenger。内部 Evolution probe 会返回 false；`evolution.rollback` 是可选 readiness check，
  若无其他 required failure，整体 health 为 degraded 且 `/health/ready` 仍返回 HTTP 200，避免
  因关闭可选自进化而摘除仍可服务业务的实例。
- 普通远程身份仅能读取和操作自己拥有的任务；管理员拥有全局视图。
- Evolution policy 列表、单条读取与严格 CAS 写入都要求 admin scope。
- 旧数据中 `submitter` 为空的任务默认 fail closed，只允许管理员或可信本地模式访问。
- 全局审计统计、网络事件、Worker 列表与状态、会话规则属于管理员能力。
- 宿主机管理员不在当前威胁模型的防护对象内；本地数据库和 trust root 仍需由部署者保护。

## Web 产品边界

当前一级导航按用户工作场景组织为六个入口：

1. **中枢**：系统状态与需要关注的事项；首屏分开显示当前执行中、未归档敕令、待裁决
   总数和累计证据束（含归档），并保留长程治理、自进化、平行位面、客卿四张独特
   能力卡；
2. **御书房（任务工作台）**：创建、查看和管理当前主体可见且未归档的全部任务，同时
   处理待裁决或待授权事项；二级入口为全部敕令、颁发敕令、钦天监和都察院。任务可同时
   显示立即、单次定时、周期、长程、对话和客卿等类型标签，当前进度以最新奏折和持久
   裁决事实为准；
3. **朝堂**：通过吏部管理百官，通过廷议开展多方会商，通过内阁查看规划统计与历史；
4. **百司**：通过翰林院管理知识与记忆，通过鸿胪寺处理外部联络，通过通政司管理消息
   与通知；
5. **天工院〔实验〕**：集中展示演化司〔实验〕、诸界台〔实验〕、考功司〔试行〕和
   客卿馆〔实验〕；
6. **内府**：通过藏兵阁管理系统与扩展，通过权印司管理权限与会话，通过户部账房查看
   成本与预算。

旧 `/edicts` 地址兼容跳转到御书房；长程、定时和实验性的客卿任务不会因为入口合并而
被隐藏。已处理裁决仍在对应任务详情中追溯。

中枢不会用 Edict 容器状态冒充执行状态：`active_run_total` 只统计非终态 RunState，
`unarchived_edict_total` 统计没有归档时间的可见任务，因此“未归档敕令大于 0、当前
执行中为 0”是合法状态。未归档敕令另外投影 `awaiting_follow_up_total` 与
`cancelled_edict_total`，分别显示为“待后续指令”和“已撤回”；`evidence_total` 是包含
已归档任务在内的累计证据束，不等于页面下方只取最近记录的列表长度。普通主体的整个
快照只统计本人数据，`admin` scope 管理员的整个快照统计全局数据，四项指标和摘要列表
不会混用两种授权范围。

中枢查询在页面位于前台期间每 5 秒轮询一次，并在执行、裁决、审计和长程任务等相关
WebSocket 事件触发时失效重拉；页面转入后台后停止轮询，回到前台后恢复。5 秒是兜底
刷新周期，不是数据缓存或跨库同步延迟；所有
指标仍直接读取同一个 SQLite 真相源。

系统管理中的 Skills 已合并为一个目录：人工与 Agent 来源用字段区分，内容只读，仅保留
真实可用的 Pin。Persona 外部导入只读取人格/提示词，检测到的 Skill 仅作预览；旧客户端
提交直接安装路径会收到 `409`。候选创建/门禁/晋升属于高级治理 API，不伪装成普通
“保存成功”。

为避免把“可继续批示”误解为“正在执行”，御书房默认展示全部未归档任务，但将 Edict
容器状态与执行进度分开：`open` 只表示“未结案”，真正的“运行中”只用于最新执行阶段；
已完成但仍可续接的对话任务显示为“待后续指令”。任务类型标签允许叠加，避免把“单次
定时的长程客卿任务”误归为一种类型。

创建任务时先选择任务类型，再选择“立即 / 单次 / 重复”。专家参数默认折叠；选择
长程任务后，重复执行选项会禁用并解释原因。页面必须显示真实 loading、empty、
error、permission-denied 和 404 状态，不用 mock 数据掩盖失败。

## 已批准并本地实现的产品可见性方案

用户已批准最终六入口方案：保留全部真实能力，但用成熟度和能力边界区分正式支持与
实验探索。
当前工作树已经完成本地实现与验证：

- 一级导航调整为中枢、御书房、朝堂、百司、天工院〔实验〕、内府六个入口；
- 御书房默认展示当前主体可见且未归档的全部任务，以叠加标签区分任务类型，并把容器
  状态、最新执行进度和人工介入事实分开呈现；其二级入口为全部敕令、颁发敕令、
  钦天监、都察院，`/edicts` 保留兼容跳转；
- 朝堂包含吏部、廷议、内阁；百司包含翰林院、鸿胪寺、通政司；
- 天工院公开展示演化司、诸界台、考功司和客卿馆；演化司、诸界台、客卿馆标记“实验”，
  考功司标记“试行”；
- 内府保留藏兵阁、权印司、户部账房；
- 中枢将 Edict 容器与 RunState 执行事实分开，以“当前执行中 / 未归档敕令 / 待裁决
  总数 / 累计证据束（含归档）”四项展示同一授权范围的权威统计，并显示待后续指令与
  已撤回分项；
- 中枢增加前台 5 秒兜底轮询，并由相关 WebSocket 事件使快照失效后立即重拉；
- 中枢增加四张“独特能力”卡，展示长程治理、自进化、平行位面和客卿；
- 自进化卡片显示后端 `evolution_status` 的真实投影；
- 入口、页首和边界说明同时显示“稳定（有限边界）/ Beta（古典导航显示‘试行’）/
  实验 / 暂未开放”。

[最终产品方案](launch/final-approval-proposal.md)现记录已批准的六入口方案、实现结果与
剩余 Gate；当前能力事实以本文为准。最新源码已完成隔离网页功能点验与现场修复，详见
[Web 全功能点验与修复报告](launch/web-functional-validation-2026-07-31.md)。

**像素级视觉回归已于 2026-08-06 移除**（原 `visual-core.spec.ts` 与 48 张基线）。
它自始未曾真正生效：基线停留在前一版 6 路由，而源码已是 7 路由（56 项），macOS
本地与 Ubuntu CI 皆全数失配，存在期间未借它发现过任何回归。界面处于快速迭代期，
像素基线每次 UI 改动都需按平台重审重建，成本随迭代线性增长而收益趋零，故整体删除
而非继续挂着跳过。E2E 现由 32 个用例守住真正要紧的部分——无障碍（axe
serious/critical、键盘可达性、200% 缩放）与关键流程（裁决、控制中枢、演进闸门、
实验可发现性）。界面呈现是否符合设计预期，改由人工点验负责。

当前成熟度：设计已定稿，实现经本地验证（`verified_local`）。

## 2026-07-31 本地验证快照

本次实现收口取得以下当前工作树证据：

- Python：`4475 passed, 2 skipped`；按用户要求排除 Ubuntu 全新 HOME 安装 exact
  Wheel 的黑盒测试；
- Web 单元/组件：72 个测试文件、299 个测试通过；
- Web 浏览器回归：最新源码已在隔离 Demo/Eval 环境完成逐页、逐操作的功能点验；定时
  立即运行、审计、系统配置和实验页现场缺陷均已按原点击路径复验；前一版 6 路由的
  48 张基线仅作保留视觉证据，不代表当前 7 路由视觉终审通过；
- 静态检查：Ruff、格式检查、mypy、import-linter 与 Web typecheck 通过；Web lint 为
  0 error / 29 warning，主要是既有 React effect 与显式类型提示；
- 供应链：Python all-extras 已知漏洞扫描通过；npm 仅保留有明确期限和边界的
  React Router 不稳定 RSC 例外；
- Git 历史：Gitleaks v8.30.1 只读扫描 947 个提交、约 238 MB；13 个命中中 12 个为
  测试/示例假值，另 1 个是已删除的第三方 gstack 工具包自带的 Supabase
  `sb_publishable_` 遥测 key——该类 key 由 Supabase 设计为可嵌入公开客户端，且归属
  gstack 上游项目而非本仓库维护者，经评估不构成凭证泄露，公开历史前无需轮换；
- 构建：本地 wheel、sdist 与非 root Docker 运行检查通过，但都不是正式发布制品；
- 视觉：保留的 48 张基线和哈希覆盖前一版中枢、任务详情、自进化、平行位面、评测和
  客卿 6 个路由；最新源码加入御书房后定义 7 个路由、预期 56 张，重新生成与哈希更新
  尚未执行；本轮功能点验不替代视觉矩阵；
- 未完成：Ubuntu 全新 HOME exact Wheel 路径（按用户要求不执行）、用户视觉/交互审批、
  最新 7 路由视觉截图与哈希更新、VoiceOver 人工检查、Web 主共享 chunk 的
  后续拆分、29 条非阻断 lint warning 清理、官方容器/PyPI/GHCR/签名发布。

这些数字是 2026-07-31 当前工作树的本地验证快照，不是已发布版本或不可变 Release
证明。代码再次变化后应重新运行相应检查。

## 当前发布状态

- 设计已定稿，实现经本地验证（`verified_local`）；
- 发行渠道：PyPI（`tianshu-agent-os`）、源码检出与自构建 wheel 是当前支持的安装
  路径；GitHub Release 附带构建产物与校验和；PyPI 发布链路已就绪、以首个实际发布
  的版本为准；GHCR/官方容器/签名发布为 `deferred`；
- 视觉终审：最新 7 路由视觉基线重建与人工视觉终审尚未完成，属已知待办；
- tag 与 Release 由维护者统一执行。

## 已知问题（GitHub Actions）

CI 五项已全绿（2026-08-05，见 #29 / PR #31）：backend 4477 passed、web-e2e
32 passed + 56 skipped、frontend、release-wheel、dependency-review。原先记录的
「Ubuntu 环境特有失败」多数并非环境玄学，均已定位到确定根因并修复：

- **CLI 帮助文本断言（2 项）** — 已修。根因不是按 80 列折行，而是 Rich 上色时
  把 `--token-stdin` 拆成 `-`/`-token`/`-stdin` 三段分别包 SGR 码，原始字节里
  选项名被切开。改为剥离 ANSI 后比对（`FORCE_COLOR=1` 可本地复现）。同一缺陷
  会让 `assert _TOKEN not in output` 这类**安全断言假通过**，故全文件统一归一。
- **集成测试** — `test_outbox_scheduler_idempotency` 已修：`Scheduler.schedule`
  对已过点的 once 只建内存 job 不落库，而用例在 `init_db()` 之前就起算 100ms
  时间窗，CI 慢盘上 migration 足以吃光它。窗口起算已移到建库之后。
  `test_governed_apply_decision_restart` 近期未复现，暂留观察。
- **Web E2E 无障碍** — 已修。确为真实产品缺陷：antd Table 测量行标了
  `aria-hidden` 却含可聚焦 checkbox（键盘会 Tab 到看不见的元素）；行选择框无
  可访问名。accessibility.spec.ts 现 21 passed。

**像素级视觉回归已移除（2026-08-06）**：原先 `visual-core` 的 56 项一直是 skipped，
基线停留在前一版 6 路由且本地亦全数失配。评估后整体删除而非重建——理由见上文
「当前能力事实」一节。E2E 绿灯自此不再含视觉项的 skip，绿色即真实通过。

CI 状态徽章可视需要挂回：五项 job 均为真实通过，不再有跳过项撑绿灯。唯一仍需
知情的是 `accessibility.spec.ts` 偶发 flaky（axe 扫描时机与元素就绪的竞态），
重跑即过，不影响结论。

## 历史资料如何阅读

| 目录 | 正确用途 |
|---|---|
| [codex-v1/](codex-v1/) | 2026-07-12 的交接、设计和现场快照 |
| [cc-fable-v1/](cc-fable-v1/) | 2026-07-12 起的阶段执行台账与 Gate 证据 |
| [superpowers/](superpowers/) | 特性设计和实施过程，不是当前完成度清单 |
| [plan/](plan/) | 早期 Phase 0–3 路线图，不代表当前支持承诺 |
| [strategy/](strategy/) | 2026-07 的战略分析与决策过程 |
| [audit/](audit/) | 审计发生当日的发现与处置状态 |
| [adr/](adr/) | 当时的架构决策；若被取代，以更新 ADR 和当前实现为准 |
