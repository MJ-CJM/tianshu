# 目标领域模型与治理契约

> **Status: Proposed。**
> 除特别标记为 Current/Partial 的对象外，本页术语尚未进入 `CONTEXT.md` 或正式 ADR。

## 1. 目标术语

| 术语 | 定义 | 当前状态 |
|---|---|---|
| `Artifact` | 不可变、内容寻址的字节对象 | Partial：已有 ArtifactStore；供应链证明未完整 |
| `Plugin` | 扩展能力的治理和生命周期身份 | 名称/catalog record Current；生命周期语义 Proposed |
| `PluginRelease` | 某 Plugin 的不可变发布，绑定 package Artifact 与 manifest | Proposed |
| `Capability` | Host 提供的稳定、类型化扩展契约 | Proposed 的统一模型 |
| `Contribution` | PluginRelease 对某 Capability 的具体实现或注册项 | Proposed |
| `EvolutionPolicy` | 每插件允许的变化 surface、模式、预算、审批和回滚要求 | Proposed |
| `PluginSetSpec` | 用户期望选择、版本约束、配置、权限及 EvolutionPolicy 引用 | Proposed |
| `PluginSetSnapshot` | Resolver 产出的完整依赖闭包、Capability binding 与有效 Policy digest | Proposed |
| `AgentDeployment` | 期望 SystemSnapshot、rollout 策略及 observed/active/last-good 状态 | Proposed |
| `RuntimeGeneration` | SystemSnapshot 在具体 Host 上 materialize 后的运行实例；ID 非内容摘要 | Proposed |
| `PluginInstance` | RuntimeGeneration 内一个 PluginRelease 的实际实例 | Proposed |
| `SystemSnapshot` | 完整有效运行配置的不可变身份 | Proposed |
| `ExecutionAssignment` | Memorial 与 SystemSnapshot、generation、实验选择的不可变绑定 | Proposed |
| `EvolutionCandidate` | 精确基线上的候选变化、来源、Gate、Evidence 和生命周期 | Current/Partial |
| `EvaluationCampaign` | 版本化数据集、Evaluator、对照组、预算和评测结果的组合 | Proposed |
| `Universe` | 实验分支、谱系和评估容器，不拥有生产 active pointer | Legacy/Partial |

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

## 3. Continuity scope：先复用 Edict，Session 待决

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

```text
RESOLVED → VERIFIED → STAGED → WARMING → READY → ACTIVE
                                      └→ FAILED / QUARANTINED
ACTIVE → DRAINING → DISPOSED
```

`Verified` 不等于 `Ready`，Candidate `PROMOTED` 也不意味着旧 generation 可以立刻销毁。
同一个 SystemSnapshot 在重启、不同 Host 或并行预热时可以产生多个 RuntimeGeneration。

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
