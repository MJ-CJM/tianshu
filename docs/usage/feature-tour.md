# 功能图鉴

这份图鉴按真实 Web 入口介绍天枢当前的 20 项用户功能。截图来自当前本地实现；空状态也
保持真实，不用示例成绩或虚构运行替代。功能是否可用以及支持到哪里，以
[当前实现与支持边界](../CURRENT-STATE.md)和
[能力事实矩阵](../launch/capability-matrix.md)为准；页面可达不等于实验能力已经转为正式
支持，也不等于完成发布审批。

## 1. 总览

### 中枢总览

**功能 1 / 20**

- **入口**：侧栏“中枢” → `/control`。
- **用户能做**：查看当前执行中、未归档敕令、待裁决、累计证据束，以及长程治理、
  自进化、位面和客卿的真实状态；进入相关任务、裁决或证据。
- **成熟度**：可用；整体 Web 产品壳的视觉状态仍待最终审批。
- **明确边界**：四项数字口径不同，`0` 不等于数据没有同步。普通用户只看本人范围，
  管理员看全局；前台 5 秒轮询和 WebSocket 只负责及时刷新同一个权威数据源。
- **相关文档**：[使用指南](user-guide.md)、[Web 前端](../design/interfaces/web.md)、
  [当前状态](../CURRENT-STATE.md)。

![中枢真实页面](../assets/features/control.jpg)

## 2. 任务与治理

### 御书房

**功能 2 / 20 · 全部敕令**

- **入口**：御书房 → 全部敕令 → `/approvals`；旧 `/edicts` 只作兼容跳转。
- **用户能做**：搜索、按状态筛选、刷新，查看立即、单次定时、周期、长程、对话和客卿
  等叠加类型标签，进入详情，并重命名、编辑或归档任务。
- **成熟度**：可用。
- **明确边界**：默认只展示当前主体可见且未归档的任务。“未结案”表示仍可续接，不代表
  正在执行；归档隐藏正常列表，但不删除治理与审计历史。
- **相关文档**：[使用指南](user-guide.md)、[Web 路由与状态同步](../design/interfaces/web.md)、
  [可观测性](../ops/observability.md)。

![御书房全部敕令](../assets/features/task-workspace.jpg)

### 颁发敕令

**功能 3 / 20**

- **入口**：御书房 → 颁发敕令 → `/edicts/create`。
- **用户能做**：选择快速、分析、编码或研究任务，填写目标，选择立即、单次或重复执行；
  需要时展开专家模式配置官员、模型、预算、工具策略和验收契约。
- **成熟度**：可用。
- **明确边界**：单次执行时间必须在未来；长程任务只支持立即或单次，周期选项会被禁用；
  专家参数是可选项，不要求普通用户理解完整治理模型。
- **相关文档**：[使用指南](user-guide.md)、[运行时主流程](../design/runtime-flow.md)、
  [调度设计](../design/scheduling/README.md)。

![颁发敕令表单](../assets/features/edict-create.jpg)

### 长程任务治理

#### 4. 任务详情与续接

- **入口**：从御书房进入 `/edicts/:edictId`；多节点计划可进入 `/dag/:dagId`。
- **用户能做**：查看结果、时间线、真实执行阶段、计划与工具裁决、成本和证据；根据任务
  状态编辑、追加指令、结案、撤回或处理人工介入。
- **成熟度**：可用。
- **明确边界**：可见操作随任务状态变化，并非每个按钮始终可用；只有闭合证据束可下载；
  任务详情受所有权和管理员权限约束。
- **相关文档**：[使用指南](user-guide.md)、[运行时主流程](../design/runtime-flow.md)、
  [可观测性](../ops/observability.md)。

![敕令详情与时间线](../assets/features/task-detail.jpg)

#### 5. 长程检查点与监督

- **入口**：在颁发敕令时选择分析、编码或研究任务，并在任务详情中查看长程治理信息。
- **用户能做**：确认验收契约，查看外层迭代、检查点和监督报告，暂停或恢复任务，运行中
  补充下一轮要求，并在升级到 L3 时作人工裁决。
- **成熟度**：稳定（有限边界）。
- **明确边界**：只支持立即或单次定时；暂停在轮次边界生效；不保证任意外部副作用、
  任意指令位置或多节点环境的 exactly-once。
- **相关文档**：[长任务端到端走查](long-task-walkthrough.md)、
  [长程编排设计](../design/agent/orchestrator.md)、[故障排查](../ops/observability.md)。

![长程任务治理](../assets/features/long-task-governance.jpg)

### 钦天监

**功能 6 / 20 · 页面标题“定时差事”**

- **入口**：御书房 → 钦天监 → `/scheduler`。
- **用户能做**：管理单次、Cron 和固定间隔任务，编辑时间，暂停、恢复、立即运行、查看
  历史或撤回排期。
- **成熟度**：稳定（有限边界）。
- **明确边界**：页面只展示可管理的 `once`、`cron`、`interval`；立即任务的内部游标只
  用于幂等与审计，不进入定时任务列表。立即运行不改变原排期；当前是单节点调度。
- **相关文档**：[调度总览](../design/scheduling/README.md)、
  [Scheduler 设计](../design/scheduling/scheduler.md)、[调度排查](../ops/observability.md)。

![钦天监定时任务](../assets/features/scheduler.jpg)

### 都察院

**功能 7 / 20**

- **入口**：御书房 → 都察院 → `/audit`；任务级裁决和证据仍从任务详情进入。
- **用户能做**：查看用量和失败归因、裁决策略、事件总线、Worker、Hook、网络事件、审计
  规则和系统审计；沿任务链接追溯裁决与证据。
- **成熟度**：可用（单机边界）。
- **明确边界**：全局统计、网络事件和系统审计需要管理员权限；普通用户只看自己任务的
  事实。防篡改链不是外部 WORM，也不抵抗宿主机管理员替换本地数据库。
- **相关文档**：[审计设计](../design/auditor/README.md)、
  [工具与裁决策略](../design/tools/policy.md)、[可观测性](../ops/observability.md)。

![都察院审计页](../assets/features/audit.jpg)

## 3. 协同与知识

### 百官阁

**功能 8 / 20 · 导航名“吏部”，页面名“百官阁”**

- **入口**：朝堂 → 吏部 → `/personas`；官员详情为 `/personas/:personaId`。
- **用户能做**：创建、编辑和删除官员或部门，查看详情与成长档案，配置模型、工具、技能、
  委派关系和路由规则，并预览外部人格导入。
- **成熟度**：可用，主操作已完成本地网页验证。
- **明确边界**：外部导入只取人格和提示词；发现的 Skill 仅预览，不直接安装或写入 live；
  高权限工具和全局记忆读取需要部署者审慎授权。
- **相关文档**：[Persona 总览](../design/persona/README.md)、
  [百官与路由](../design/persona/officials.md)、[模型注册表](../design/model-registry.md)。

![百官阁官员管理](../assets/features/officials.jpg)

### 廷议与内阁

#### 9. 廷议

- **入口**：朝堂 → 廷议 → `/consultation`。
- **用户能做**：填写议题和背景，选择参与官员，查看各方立场、关键点、综合意见和最终
  决议，并在当前页面会话中切换已发起的轮次。
- **成熟度**：可用，主路径已完成本地网页验证。
- **明确边界**：真实会诊会调用配置的模型并产生用量；页面轮次导航不是完整的长期会诊
  档案浏览器，不能据此宣称所有历史都在页面中可检索。
- **相关文档**：[廷议设计](../design/consultation/README.md)、
  [长程编排](../design/agent/orchestrator.md)、[成本设计](../design/llm/cost.md)。

![廷议页面](../assets/features/consultation.jpg)

#### 10. 内阁

- **入口**：朝堂 → 内阁 → `/cabinet`。
- **用户能做**：查看 Planner 处理总量、直通与 DAG 占比、平均任务数，以及规划官、执行官
  和任务分解历史。
- **成熟度**：可用的只读总览。
- **明确边界**：当前 Web 页不提供交互式决策编排、修改计划或发起规划；真正的规划由敕令
  执行链触发。
- **相关文档**：[Planner 设计](../design/scheduling/planner.md)、
  [系统架构](../design/architecture.md)、[运行时主流程](../design/runtime-flow.md)。

![内阁规划总览](../assets/features/cabinet.jpg)

### 翰林院、鸿胪寺与通政司

#### 11. 翰林院（文渊阁）

- **入口**：百司 → 翰林院 → `/memory`；页面标题为“文渊阁”。
- **用户能做**：按官员检索记忆，查看访问策略、对话历史和统计，删除或批量删除记忆，
  执行压缩和反思。
- **成熟度**：稳定（有限边界）。
- **明确边界**：页面没有直接手写新记忆的入口；Markdown 是真相源，SQLite/FTS 是可重建
  索引；不承诺跨主机一致性，反思可能消耗模型资源并受冷却时间限制。
- **相关文档**：[记忆总览](../design/memory/README.md)、
  [记忆宫殿](../design/memory/palace.md)、[记忆后端](../design/memory/backends.md)。

![翰林院记忆管理](../assets/features/memory.jpg)

#### 12. 鸿胪寺

- **入口**：百司 → 鸿胪寺 → `/hongluisi`。
- **用户能做**：查看网络工具和 Provider 状态，配置抓取、搜索、回退和浏览器引擎偏好，
  查看近期网络事件，并进入外部凭证管理。
- **成熟度**：可用（依赖外部环境）。
- **明确边界**：此页不是直接发起网络请求的调试台；实际能力取决于工具注册、Provider、
  凭证和网络。API 凭证在藏兵阁加密管理，截图和文档不得包含密钥。
- **相关文档**：[网络能力与安全](../design/tools/network.md)、
  [凭证托管](../design/secrets/README.md)、[网络审计](../ops/observability.md)。

![鸿胪寺外部联络](../assets/features/external.jpg)

#### 13. 通政司

- **入口**：百司 → 通政司 → `/tongzheng`。
- **用户能做**：创建、编辑、启停和删除飞书、Telegram 等渠道实例，并查看实例运行状态。
- **成熟度**：可用（有限边界）。
- **明确边界**：本地配置或 outbox 成功不等于第三方最终展示；最近网页验证在隔离 eval
  环境中没有真实外发。渠道 token、webhook 和机器人凭证不得进入截图或仓库。
- **相关文档**：[渠道设计](../design/interfaces/channels.md)、[多 Bot 运维](../ops/multi-bot.md)、
  [飞书接入](../ops/feishu-setup.md)、[Telegram 接入](../ops/telegram-setup.md)。

![通政司渠道管理](../assets/features/notifications.jpg)

## 4. 天工院

### 演化司〔实验〕

**功能 14 / 20 · 页面标题“演化中心”**

- **入口**：天工院〔实验〕 → 演化司〔实验〕 → `/evolution`。
- **用户能做**：查看当前启用状态、Skill/EXECUTOR 候选、Gate、灰度分流、晋升和回滚证据；
  管理员还可查看逐 subject 路由，并用严格 CAS 修改 evolution mode 与 max canary basis points。
- **成熟度**：实验。
- **明确边界**：availability、source 和 curator protection 只读，`pinned` 不是版本 pin；
  不提供 per-plugin enabled、版本 pin、晋升或回滚按钮，policy 列表/详情/写入均只允许管理员。
  P4b 已由 PR #109 合入 `feat/plugin-v1`；P5 当前 checkout 只为 Pi EXECUTOR 接通受治理
  Candidate/Gate/canary/Decision/promotion/rollback 垂直切片。系统不会自行晋升候选，其他
  非 Skill kind 的生产激活仍关闭，完整 G4 仍未完成。
- **相关文档**：[Skills 当前边界](../design/skills/README.md)、
  [技能学习](../design/skills/learning.md)、[能力事实矩阵](../launch/capability-matrix.md)。

![演化司实验页面](../assets/features/evolution.jpg)

### 位面〔实验〕

**功能 15 / 20 · 导航名“诸界台”，页面名“位面”**

- **入口**：天工院〔实验〕 → 诸界台〔实验〕 → `/universes`。
- **用户能做**：启用位面、创建 Genesis、分支，生成代码候选，查看 Diff 和评估记录，
  归档、恢复或删除位面，并在明确点击后生成太乙奏折。
- **成熟度**：实验。
- **明确边界**：Web 不切换在役运行时，也不晋升代码；候选不会自行写入 live。太乙报告
  的 GET 只读，显式 POST 才可能调用模型；eval 模式会拒绝耗模生成。
- **相关文档**：[位面总览](../design/universe/README.md)、
  [代码变体](../design/universe/code-variant.md)、[位面演化](../design/universe/evolution.md)。

![位面谱系](../assets/features/universes.jpg)

### 考功司〔试行〕

**功能 16 / 20 · 页面标题“考成院”**

- **入口**：天工院〔实验〕 → 考功司〔试行〕 → `/evals`。
- **用户能做**：查看真实评测集、运行、分数、成功率、历史差异和失败分布。
- **成熟度**：Beta / 试行。
- **明确边界**：Web 不启动会消耗模型资源的跑批，跑批从 CLI 发起；没有评测数据时只
  展示真实启动方式，不生成示例成绩；评测结果不会自动触发晋升。
- **相关文档**：[评估设计](../design/universe/eval.md)、
  [评估套件运维](../ops/eval-harness.md)、[能力事实矩阵](../launch/capability-matrix.md)。

![考功司评测页](../assets/features/evals.jpg)

### 客卿馆〔实验〕

**功能 17 / 20 · 页面标题“客卿”**

- **入口**：天工院〔实验〕 → 客卿馆〔实验〕 → `/keqing`。
- **用户能做**：查看 Claude Code、Codex、Pi 和 OpenCode 的本机安装版本、已验证基线、
  能力与治理状态，并配置默认模型和单次预算；Pi 已进入内部代际管理时，还可查看当前代、
  状态、活跃 run 数、last-good 代和已经存在的治理候选，并跳转演化中心。
- **成熟度**：实验。
- **明确边界**：不会自动升级外部 CLI，不代管 CLI 凭证，也不在此页直接执行、stage 或
  activate generation。页面刷新不会运行漂移扫描或创建候选；默认关闭的 control-plane scanner
  才拥有提案权。P5 只覆盖 Pi，Claude Code/Codex/OpenCode 尚未代际化；可靠事前动作拦截和
  Provider 侧硬成本上限尚未具备，安装版本与兼容基线必须分开显示。
- **相关文档**：[客卿管理页边界](../design/keqing/management-page.md)、
  [当前状态](../CURRENT-STATE.md)、[能力事实矩阵](../launch/capability-matrix.md)。

![客卿馆状态](../assets/features/keqing.jpg)

## 5. 系统与成本

### 藏兵阁与权印司

#### 18. 藏兵阁

- **入口**：内府 → 藏兵阁 → `/system`。
- **用户能做**：查看 Skills，启停工具，管理 Prompt、Provider、模型、全局参数和外部凭证，
  查看插件 manifest，配置受支持的 MCP，并访问紧急停止状态。
- **成熟度**：混合；正式配置、只读预览、实验和默认关闭能力共存。
- **明确边界**：Skills 目录只读；插件只展示 manifest，不安装、加载或执行；remote/open
  stdio MCP 默认关闭；截图不得显示 API key、header、token 或本机私密路径。
- **相关文档**：[工具总览](../design/tools/README.md)、
  [Skills 边界](../design/skills/README.md)、
  [插件边界](../design/self-evolving-agent-os/current-plugin-state.md)、
  [凭证托管](../design/secrets/README.md)。

![藏兵阁系统管理](../assets/features/system.jpg)

#### 19. 权印司

- **入口**：内府 → 权印司 → `/session-rules`。
- **用户能做**：按范围和来源筛选规则，手动添加敕令级或全局会话规则，并撤销已有规则。
- **成熟度**：稳定（有限边界）。
- **明确边界**：全局会话规则属于管理员能力；撤销后相关工具调用重新进入裁决；
  `shell_exec` / `bash` 不允许设为全局免审。紧急停止入口实际位于藏兵阁。
- **相关文档**：[工具与会话规则](../design/tools/policy.md)、
  [运行边界](../ops/runtime-boundaries.md)、[系统审计](../ops/observability.md)。

![权印司会话规则](../assets/features/session-rules.jpg)

### 户部账房

**功能 20 / 20**

- **入口**：内府 → 户部账房 → `/cost`。
- **用户能做**：查看日、周、月成本汇总，预算进度、Provider 价格、成本趋势和明细，并在
  受支持的配置中维护预算与计价。
- **成熟度**：稳定（有限边界）。
- **明确边界**：账本依赖 Provider 返回的 token 用量与本地价格配置，是 best-effort 成本
  治理，不是 Provider 官方账单、预付余额或不可突破的外部硬额度。
- **相关文档**：[成本治理](../design/llm/cost.md)、
  [成本排查](../ops/observability.md)、[当前状态](../CURRENT-STATE.md)。

![户部账房](../assets/features/cost.jpg)
