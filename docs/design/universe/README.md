# 平行位面（universe）设计总览

> 平行位面的原始设计把行为配置和代码捕获成可分叉、可切换、可回滚的快照。
> **当前实现已收紧边界**：旧 Universe 面保留存照、分支、对比、归档、恢复和评估，
> 但不再直接切换 live 位面或部署代码；运行期灰度由受治理 Candidate、
> `RunAssignmentV1` 与 effective overlay 承担。
>
> 设计源头位于历史 specs；其中 live switch、DeployPointer、自重启和健康检查自动回滚
> 是历史方案，不是当前可用能力。

## 1. 职责定位

| 项 | 说明 |
|---|---|
| 解决什么 | 为行为配置和代码试验保留可比较、可审计的候选，不直接覆盖当前运行态 |
| 当前提供 | Legacy Universe 的存照 / 分支 / diff / 归档 / 恢复，以及代码 worktree 的门禁、沙箱评估与 `recommended` 结论 |
| 运行期治理 | `PromotionService` 独占 Candidate canary / promote / rollback 写入口；每个 Memorial 固化 assignment，只有命中 governed canary 时才是 `RunAssignmentV1` 并携带 effective overlay |
| 当前边界 | 生产装配只有 Skill Candidate 具备真实激活/回滚 adapter；Code Candidate 的 live activation fail-closed，旧 live switch / code promote 接口固定拒绝 |
| 默认态 | `parallel_universe_enabled=False`；当前发布状态也没有 active Candidate |

## 2. 核心设计判断（铁律）

**位面分叉的是「怎么做」，全局共享的是「知道什么 + 做过什么」。**

| 进位面快照（可分叉/进化） | 试验外共享（不随快照分支） |
|---|---|
| 人格 SOUL.md / ROLE.md、技能集与状态、策略规则、agent/LLM config、prompt 层组合 | 记忆宫殿（`memory_entries`）、官员工作记忆（MEMORY.md）、全部工作历史（edicts/memorials/events/成本/审计） |

两个当前后果：

1. **试验不直接覆盖 live**——旧 Universe 的 branch/diff/archive/restore 只管理快照或
   worktree；`UniverseManager.switch()` / `rollback()` 固定 fail-closed。
2. **运行归因固化到每次执行**——创建 Memorial 的同一事务内写入不可变 assignment；
   无可路由 canary 时写 `LegacyRunAssignmentV1` 且没有 overlay，命中 governed canary
   时才写 `RunAssignmentV1` 并绑定 effective overlay；重试不会重新分桶。

其余关键判断：

| 判断 | 取舍 |
|---|---|
| Legacy champion | 旧 Universe 模型仍以 `status=champion` 表示基线快照；branch/diff 前可 `snapshot_live`，但它不再是可直接切换 live 的运行指针 |
| Candidate 真相源 | `evolution_candidates`、routing allocation、不可变 lifecycle/promotion journal 与 per-run assignment 共同构成受治理真相；不得由 UniverseManager 旁路写入 |
| 全量拷贝 | v1 不做 COW 差量存储——小文本文件，全量拷贝简单又安全 |
| 评估安全 | 评估采历史诏令 goal 的受治理回放（EvalHarness 受管子进程 + 独立 DB）；delta 超阈值只产生 `recommended`，不会自动晋升 |
| 代码层边界 | worktree、Gate、独立 DB、wall timeout 与进程组收敛用于提案/评估；当前没有 code live writer、DeployPointer、自重启或自动健康回滚 |

## 3. 与相邻子系统的关系

| 相邻子系统 | 关系 |
|---|---|
| persona / skills | Legacy Universe 可保存快照；当前受治理运行 overlay 由 `ChallengerRouter.bind_runtime()` 按 Memorial 绑定，生产路径已实际消费 Skill overlay |
| config_manager | Legacy manifest 可保存 config 快照；当前没有通过 Universe switch 把 manifest 应用到 live 的入口 |
| executor | 提交时固化 assignment；dispatcher claim 后加载同一 assignment/effective overlay；非 demo profile 的 canary 使用带 secret 的确定性 bucket，失败解析则 fail-closed |
| bus / scheduler | memorial 完成事件触发 fitness 更新；可选系统 job 只形成 Legacy 实验/推荐，不获得 live 切换权限 |
| reviewer / curator | 兼容 Hook 与 cron 仍可装配，但 governed live 写入未开放；默认在 LLM 前 fail fast，显式 dry-run 只做预览 |

## 4. 本目录子文档索引

| 文档 | 内容 |
|---|---|
| [evolution.md](./evolution.md) | 当前行为演化链路、受治理 overlay，以及 Legacy Universe 的 fail-closed 边界 |
| [code-variant.md](./code-variant.md) | CodeVariantStore、worktree、Gate/Sandbox/EvalHarness，以及当前“只到推荐、不部署”的边界 |
| [eval.md](./eval.md) | Eval Harness 与 Fitness：同集配对评估、预算闸、推荐结论与受治理 Candidate 的边界 |

**相关实现**：[../../impl/universe/](../../impl/universe/)

目标态中，Legacy Universe 保留 branch/diff/eval/lineage，mutation 只产生 Candidate；
生产运行身份由完整 SystemSnapshot 与受管 RuntimeGeneration 绑定。详见
[自进化 Agent OS 目标架构](../self-evolving-agent-os/)。该方向尚未成为当前运行事实。
