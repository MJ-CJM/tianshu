# 目标领域模型与治理契约

> **Status: Target model；P1/P3/P4a 已合入，P4b Issue #108 已在实现分支完成但 PR/CI 待创建。**
> `SystemSnapshot`（典制）、`RuntimeGeneration`（朝）和 `EvolutionPolicy`（进化策略）已进入
> `CONTEXT.md` 与 ADR；下表其余状态描述代码实现成熟度，不以术语已接受反推能力已实现。

## 1. 目标术语

| 术语 | 定义 | 当前状态 |
|---|---|---|
| `Artifact` | 不可变、内容寻址的字节对象 | Partial：已有 ArtifactStore；供应链证明未完整 |
| `Plugin` | 扩展能力的治理和生命周期身份 | 名称/catalog record Current；生命周期语义 Proposed |
| `PluginRelease` | 某 Plugin 的不可变发布，绑定 package Artifact 与 manifest | Proposed |
| `Capability` | Host 提供的稳定、类型化扩展契约 | Proposed 的统一模型 |
| `Contribution` | PluginRelease 对某 Capability 的具体实现或注册项 | Proposed |
| `EvolutionPolicy` | 每插件允许的模式与 canary 上限等治理约束 | Current/Partial：V33 已合入；allowed surfaces、approval、budget 仍是目标态 |
| `PluginSetSpec` | 用户期望选择、版本约束、配置、权限及 EvolutionPolicy 引用 | Proposed |
| `PluginSetSnapshot` | Resolver 产出的完整依赖闭包、Capability binding 与有效 Policy digest | Proposed |
| `AgentDeployment` | 期望 SystemSnapshot、rollout 策略及 observed/active/last-good 状态 | Proposed |
| `RuntimeGeneration` | SystemSnapshot 中某个受管 release 在具体 Host/scope 上 materialize 后的运行实例；ID 非内容摘要 | Current/Partial：P3 已实现 `keqing:pi` 内部代际机械；尚无公开晋升入口与通用 PluginHost |
| `PluginInstance` | RuntimeGeneration 内一个 PluginRelease 的实际实例 | Proposed |
| `SystemSnapshot` | 完整有效运行配置的不可变身份 | Shadow/Partial：P1 已实现组件摘要、内容寻址存储与 Evidence 归因；尚无完整依赖闭包、prompt 摘要和 generation 激活 |
| `ExecutionAssignment` | Memorial 与 SystemSnapshot、generation、实验选择的不可变绑定 | Partial：V31/V32 分别承载 snapshot shadow 与 exact-attempt 代际；P4b V34 以封存的 `RunAssignmentSetV1` 承载 per-subject 选择 |
| `EvolutionCandidate` | 精确基线上的候选变化、来源、Gate、Evidence 和生命周期 | Current/Partial |
| `EvaluationCampaign` | 版本化数据集、Evaluator、对照组、预算和评测结果的组合 | Proposed |
| `Universe` | 实验分支、谱系和评估容器，不拥有生产 active pointer | Legacy/Partial |

### 1.1 第 1–3 阶段最小代码词汇

目标术语不等于需要立即建立同名代码对象。前三阶段新增五个代码级承载：

| 代码承载 | 吸收的目标语义 | 首期形态 |
|---|---|---|
| `SystemSnapshotV1` | `SystemSnapshot`、`PluginSetSnapshot`，以及作为 components 条目的 `PluginRelease` 身份 | P1 Current/Shadow：frozen 内容摘要模型 + `system_snapshots`；当前 components 是最小语义投影，不等于完整 PluginSet 依赖锁 |
| `RuntimeReleaseV1` | 宿主已解析、可跨重启精确物化的 executor release；不等于完整生态通用 `PluginRelease` | P3 Current：canonical material + `runtime_generation_releases`；内容寻址、不可变，可被多个朝复用 |
| `RuntimeGenerationV1` | `RuntimeGeneration`、`PluginInstance`，以及 active/last-good 运行指针 | P3 Current/Partial：`keqing:pi` executor scope 的七态记录；process scope 留待 P6 |
| `run_system_bindings` | `ExecutionAssignment` 中 SystemSnapshot 关联事实 | P1 snapshot shadow；snapshot 启用时每 `(memorial_id, attempt_id)` insert-once，P3 仅作 V31 generation fallback；现有 `RunAssignmentV1` 不改 |
| `run_generation_bindings` | `ExecutionAssignment` 中 exact-attempt generation 关联事实 | P3 独立 insert-once 权威；`bound` 可显式为 `[]`，无法证明的历史 Pi 为 `unresolved`；与 system binding 同在必须一致 |
| `run_subject_assignments` | `ExecutionAssignment` 中 per-subject 进化选择 | P4b V34 分支实现：set hash/size 封存，1..64 原子写；不是 exact-attempt generation marker |

P1 的 `run_system_bindings` 按 `(memorial_id, attempt_id)` insert-once，并把同一典制作为 required
Evidence artifact 保存；这是需要保留的 P1 历史语义。P3 没有把该 shadow 升格为代际权威，而是
在 V32 新增 `run_generation_bindings`：每个新 attempt 无论 snapshot 开关状态都必须写 exact marker，
空选择写 `bound []`，非空选择的解析、材料或 marker 写入失败都在受管副作用前拒绝。snapshot
启用时两张表可同在且 generation ids 必须一致；典制整体严格翻转仍留到 P6。

`Artifact` 继续复用现有 `ArtifactRefV1`/`ArtifactStore`；`Capability`、`Contribution` 先作为注册表
契约；`EvolutionPolicy` 到 per-subject 阶段才落表。`PluginSetSpec`、独立
`EvaluationCampaign`、`AgentDeployment`、`PluginInstance` 和 `AgentSession` 等代码对象均
deferred，出现真实消费者后再引入。

当前 policy 语义应按实现理解：`frozen` 阻止新的 propose、canary 与 promote；`manual` 当前同样
不允许进入 canary，尚未实现“有 Decision 即覆盖 manual”的通道。已经开始的 stage/evaluate
可收口，rollback 始终允许。目标 YAML 中的 enabled、版本约束、allowed surfaces、approval
和 budget 不是当前 P4b UI 能力；当前 UI 只修改 mode 与 max canary basis points。

不要混淆：

- Artifact 不是 Plugin；
- Plugin 不是 Capability；
- PluginRelease 不是 PluginInstance；
- PluginSetSnapshot 不是 RuntimeGeneration；
- Universe 不是 SystemSnapshot；
- Candidate 不是 active generation；
- Restore Point 是用户工作区基线，不是 SystemSnapshot。

当前代码中 `ArtifactRefV1` 表示内容寻址 Artifact；`models.common.ArtifactRef` 则是较宽松的
输出路径/URL 描述。目标模型采用前一种语义，后者未来可考虑改称 `OutputReference`，本轮
不改现有代码。

## 2. 对象关系

```text
Plugin
  └── PluginRelease ──references──> Artifact
          └── contributes──> Capability

PluginSetSpec
  ├── references per-plugin EvolutionPolicy
  └── resolve + admit ──> PluginSetSnapshot

SystemSnapshot
  ├── PluginSetSnapshot
  ├── kernel/API revision
  ├── model/provider revision
  ├── persona/harness/prompt revisions
  ├── policy/evaluator revisions
  └── deployable configuration digests
          └── materialize ──> RuntimeGeneration
                                 └── PluginInstance

Memorial
  └── exactly one ExecutionAssignment
          ├── SystemSnapshot
          ├── RuntimeGeneration
          ├── candidate/experiment selection
          ├── run-local memory view
          ├── plan revision
          └── Workspace Lease / Restore Point

EvolutionCandidate
  ├── exact baseline
  ├── candidate Artifact or PluginSet patch
  └── gates / evidence / promotion lifecycle
```

## 3. Continuity scope：首期复用 Edict，不引入 AgentSession

当前代码中的 Session 一词已经用于 auth session、SessionRule、SessionLane、
ConsultationSession、KeqingSession 和 channel anchor，它们都不是“跨 Memorial、固定
RuntimeGeneration 的持久交互谱系”。

因此第一阶段不新增 AgentSession，而按现有执行语义明确三条规则：

```text
conversation / deep Edict → 首个 root Memorial 选择 generation，后续 continuity 固定
cron / interval Edict      → 每次 fire 的新 root Memorial 选择当时有效 generation
DAG child / infra retry    → 继承所属 root Memorial 的 ExecutionAssignment
每个 Memorial             → 绑定且只绑定一个 ExecutionAssignment
```

只有当以下需求真实出现时，再通过 ADR 引入独立 `AgentSession`：

- 一次交互包含多个独立 Edict；
- continuity 可以 fork；
- continuity 可显式迁移 generation；
- channel anchor 不再直接锚定 Edict。

无论最终对象叫 Edict 还是 AgentSession，连续交互都不能隐式混用 generation。

## 4. 两套状态机

Candidate lifecycle 和运行代际生命周期必须分开。

### 4.1 Candidate

现有生命周期可继续作为基础：

```text
PROPOSED → STAGED → EVALUATING ─────────────→ READY
                         └→ BLOCKED ─┬──────→ EVALUATING
                                     └──────→ REJECTED → ARCHIVED

READY ─────→ CANARY ─────→ PROMOTED ───────→ ARCHIVED
  └────────→ REJECTED       └→ ROLLBACK_PENDING
CANARY ├───→ READY                  ↓
       ├───→ REJECTED        ROLLED_BACK → ARCHIVED
       └───→ ROLLBACK_PENDING
```

它描述候选是否通过治理，不描述进程是否已经健康运行。

现有合法转移如下，后续文档和实现应以契约表为准，而不是只依赖示意图：

| 来源 | 允许目标 |
|---|---|
| `PROPOSED` | `STAGED` |
| `STAGED` | `EVALUATING` |
| `EVALUATING` | `BLOCKED`, `READY` |
| `BLOCKED` | `EVALUATING`, `REJECTED` |
| `READY` | `CANARY`, `REJECTED` |
| `CANARY` | `READY`, `PROMOTED`, `REJECTED`, `ROLLBACK_PENDING` |
| `PROMOTED` | `ROLLBACK_PENDING`, `ARCHIVED` |
| `ROLLBACK_PENDING` | `ROLLED_BACK` |
| `ROLLED_BACK`, `REJECTED` | `ARCHIVED` |
| `ARCHIVED` | 无 |

### 4.2 RuntimeGeneration

P3 当前可持久化的精确七态为：

```text
STAGED ─→ WARMING ─→ READY ─→ ACTIVE ─→ DRAINING ─→ DISPOSED
  └──────────┴──────────┘
               └────────────→ FAILED
```

常规 API 不允许 `DRAINING → ACTIVE`；只有 repository 的 last-good rollback 专用入口可以在
重新 materialize/verify 后执行该边。`RESOLVED / VERIFIED / QUARANTINED` 仍是目标流程概念，
不是 V32 可写状态，文档和调用方不得伪造。

`Verified` 不等于 `Ready`，Candidate `PROMOTED` 也不意味着旧 generation 可以立刻销毁。
同一个 SystemSnapshot 在重启、不同 Host 或并行预热时可以产生多个 RuntimeGeneration。
P3 启动恢复会把没有生产 binding authority 的遗留 STAGED/WARMING/READY 失败化；P5 引入
executor canary 后，以每次 canary epoch 的精确 candidate-version/release/generation 映射作为
READY 的 recovery 与 retention authority。只有映射仍有效且与 pending/active Candidate 生命周期
相符的 READY 才能重建；无映射、摘要不符、歧义或已撤销的 READY 仍失败化，不能直接放宽所有 READY。

## 5. PluginSet 不变量

1. PluginSetSnapshot 是规范化、内容寻址、不可变的 lock；
2. 包含精确 PluginRelease digest、依赖闭包、API/ABI、状态 schema、配置摘要和有效权限；
3. 解析必须确定性，循环依赖、缺失 Capability 和冲突 Provider 默认 fail closed；
4. install、stage、ready、active 是不同状态；
5. 新旧 RuntimeGeneration 可以并存；
6. contribution 必须命名空间化并带 owner/disposer；
7. 插件状态必须 namespaced、schema-versioned；
8. Python 进程内不承担任意第三方代码的可靠卸载与安全隔离。

## 6. Memorial/Attempt 与 Evidence 不变量

1. 第一个副作用前原子绑定 Principal、Edict、Memorial、Attempt、治理契约、
   SystemSnapshot、workspace lease 与 outbox；
2. 旧 fencing token 不能 checkpoint、发起受管副作用或完成；
3. 基础设施重试继承 root Assignment；用户主动重试生成新 Memorial，并按治理语义决定继承或
   显式选择新的 Assignment；
4. terminal 单调，Evidence 关闭晚于执行和最终治理状态；
5. 已关闭 Evidence 不可变，并绑定实际使用的 PluginSet、generation、模型、策略、Effect、
   成本和环境；
6. Evidence producer 不能替代固定内核中的 verifier；
7. green Evidence 是事实证明，不是自动激活授权。

## 7. Evolution 不变量

1. Candidate 绑定精确 base SystemSnapshot 或 PluginSetSnapshot；
2. 默认一次只改变一个 Plugin；多插件变化作为一个原子 PluginSet 完整评测；
3. Candidate 不能修改自己的 Policy、Evaluator、权限或 Promotion Authority；
4. Promotion 必须确认当前 active 仍是 Candidate 评测时的精确基线，拒绝 stale green；
5. 有状态交互按 continuity scope sticky；只有声明为无状态的插件才可按单 Run 分流；
6. 回滚先把 Candidate 流量降到零，再处理制品、配置和状态；
7. Trust、Evidence verifier、Promotion Authority 和运行账本永久 frozen 或 code-level manual；
8. 自动晋升只能开放给用户明确批准的低风险叶子插件。

## 8. 用户插件策略

插件启用和进化授权正交：

```yaml
plugin: keqing.pi
enabled: true
version_policy:
  mode: pinned
  digest: sha256:...
evolution:
  mode: frozen        # frozen / propose / manual / canary / auto
  allowed_surfaces: []
  max_canary_basis_points: 0
  approval: owner
  budget:
    candidates_per_day: 0
    token_limit: 0
    cost_limit: 0
```

模式语义：

| 模式 | 允许产生候选 | 允许评测 | 允许生产流量 | 允许自动晋升 |
|---|---:|---:|---:|---:|
| `frozen` | 否 | 否 | 只运行 pinned active | 否 |
| `propose` | 是 | 是 | 否 | 否 |
| `manual` | 是 | 是 | Decision 后 | 否 |
| `canary` | 是 | 是 | 受限、sticky | 否 |
| `auto` | 是 | 是 | 受策略限制 | 仅低风险白名单 |

第一阶段只实现 `frozen/manual/canary`。`auto` 必须等独立评测、状态回滚、隔离、供应链和
kill switch 全部成立后再开放。

## 9. 职责分离

| 角色 | 可以做什么 | 不能做什么 |
|---|---|---|
| Proposer | 分析 Evidence、生成 Candidate | 修改 active 或评测规则 |
| Builder | 从声明输入构建 Artifact | 持有 Promotion key |
| Evaluator | 执行版本化 Gate 和数据集 | 修改 Candidate 或批准上线 |
| Admission | 验证签名、权限、依赖和策略 | 为 Candidate 扩权 |
| Promoter | 根据 Evidence 和 Decision 改变路由 | 生成或重写评测证据 |
| Runtime | 执行指定 SystemSnapshot | 修改 Catalog、Policy 或 active pointer |

相同模型可以在不同角色中提供建议，但权限主体、输入集和持久 Decision 必须分开，不能以
“调用了两次同一模型”冒充职责分离。

## 10. Universe 的目标位置

Legacy Universe 保留 branch、diff、lineage、paired evaluation 和历史探索价值。目标态下：

- Universe head 可以引用一个 SystemSnapshot（Proposed；当前数据模型尚无该字段）；
- Universe mutation 产出 EvolutionCandidate；
- Universe champion 只称为 `Legacy champion baseline`；
- 生产 active pointer 只由 Promotion/Rollout 控制面拥有；
- 不恢复 Legacy manager 的直接 live writer。
