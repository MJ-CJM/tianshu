# 术语表 · 古风隐喻中英对照

天枢以明朝「六部」官制为隐喻组织系统。本表把**古风名 ↔ 代码标识符 ↔ 实际含义**对齐，便于读代码与读文档时互相印证。隐喻只是组织外壳，落到代码就是普通的模块与类。当前能力边界只以 [v0.4.2 能力事实矩阵](../quality/public-capability-matrix-v0.4.2.md) 为准。

## 领域核心对象

| 古风名 | 代码标识 | 含义 |
|---|---|---|
| 诏令 / 敕令 | `Edict` | 用户下达的任务，整个执行链路的根对象 |
| 题本 / 奏折 | `Memorial` | 一次执行记录；一个诏令可对应多条（初次、follow-up、DAG 节点、重试） |
| 裁决 | `Decree`（历史代码名） | 人对待决动作作出的治理决定；公开界面统一使用“裁决”，内部 `decree`、`approval` 与 v1 API 为兼容保留 |
| 规划 | `Plan` / `PlanTask` | 内阁规划产出的任务计划，可展开为 DAG |
| 验收标准 | `AcceptanceCriteria` | 长任务 outer loop 的触发器与验收契约 |
| 事件 | `EventEnvelope` / `EventBus` | 模块间主协议，带 `edict_id` 的事件落 `events` 表成时间线 |

## 六部官员（Persona）

| 古风名 | persona id | 职责（代码角色） |
|---|---|---|
| 内阁（首辅） | `neige` | 战略规划、任务分解（Planner 默认规划官） |
| 兵部 | `bingbu` | 默认执行官（DEFAULT_EXECUTOR） |
| 都察院 | `ducha` | 审计、代码审查、风控（Auditor） |
| 通政司 | `tongzheng` | 渲染、通知、会诊主持（Notifier） |
| 文渊阁 | `wenyuan` | 文档与知识管理（Memory） |
| 户部 | `hubu` | 成本审查、配额裁决（Cost） |
| 朝廷 | `court` | 所有官员共享的上下文层 |

> 官员的人格资料以 Markdown 文件承载：`SOUL.md`（性格/价值观）、`ROLE.md`（职责/边界）、`MEMORY.md`（长期记忆）。Git 模板在 `personas/{id}/`，运行时覆盖在 `~/.tianshu/personas/{id}/`。

## 其他官署与隐喻

| 古风名 | 代码标识 | 含义 |
|---|---|---|
| 鸿胪寺 | `tools/hongluisi/` | 对外邦交 → 对外网络访问工具（web_fetch / web_search / web_extract / api_request） |
| 记忆宫殿 | Memory Palace | 多层记忆系统（宫殿/偏殿/房间/抽屉隐喻） |
| 抽屉 | `Drawer` / `DrawerStore` | L1 记忆快照，独立 SQLite，检索优先 |
| 会诊 | `ConsultationSession` | 多官员协作决策 |
| 通政（前端） | Tongzheng 页 | 通知渠道配置页 |

## 位面演化（Universe）

| 术语 | 代码标识 | 含义 |
|---|---|---|
| 位面 | `Universe` | 一份可分叉、可自进化的行为配置快照 |
| 冠军 | status=`champion` | 在役位面（含工作副本，会随使用 live 漂移） |
| 挑战者 / 候选 | status=`challenger` | 冻结快照，接受小流量探索 |
| 归档 | status=`archived` | 退役位面（保留可恢复） |
| 来源 | `origin` | `genesis` / `manual_branch` / `mutation` / `code_variant` |
| 适应度 | `FitnessCalculator` | 评估函数：成功率、成本、审计通过率、用户反馈 |
| 代码变体 | `CodeVariantStore` | 改的是代码（Git worktree）而非仅行为配置 |

## 执行与治理

| 术语 | 代码标识 | 含义 |
|---|---|---|
| ReAct 循环 | `Agent` / `LoopState` | Reason+Act 主循环；LoopState 不可变，每轮返回新对象 |
| 退出原因 | `ExitReason` | 显式枚举（completed / max_iterations / context_overflow / …），取代 bool+string |
| 外层循环 | `Orchestrator` / Outer Loop | 长任务自检、critic 监督、L1/L2/L3 升级 |
| 三层压缩 | `compaction/` | reactive（溢出触发）/ micro（每轮预防）/ auto（LLM 摘要） |
| 工具分级 | tier | 工具按风险分级，决定是否需要人工裁决 |
| 会话规则 | `SessionRule` | 会话级 allow/deny 工具权限 |
| 副作用拦截 | `winding_down` | 生命周期收尾阶段拦截有副作用的工具调用 |
| 钩点 | `HookRegistry` / `HookType` | Agent 生命周期扩展点，按 priority 排序 |

## 三档执行路径

| 路径 | 触发条件 | 设计意图 |
|---|---|---|
| 单 Agent | 单任务或 passthrough plan | 最短闭环，默认路径 |
| DAG | `Plan.tasks > 1` 且有依赖 | 多任务并发、节点级 Memorial |
| Outer Loop | `Edict.acceptance != None` | 长任务自检、critic、升级、人工 L3 |

---

更完整的对象契约见 [../design/domain-model.md](./domain-model.md)，运行时主链路见 [../design/runtime-flow.md](./runtime-flow.md)。
