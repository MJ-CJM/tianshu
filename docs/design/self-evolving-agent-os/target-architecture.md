# 目标架构

> **Status: Proposed target architecture。**
> 本文描述目标态，不代表当前已实现能力。

## 1. 总体结构

```text
                       User / Organization Intent
                                  │
                                  ▼
┌──────────────── Declarative Agent OS API ─────────────────┐
│ Edict │ PluginSetSpec │ EvolutionPolicy │ AgentDeployment │
│                    spec / status / generation              │
└─────────────────────────┬──────────────────────────────────┘
                          ▼
┌────────────────── Governance Microkernel ──────────────────┐
│ Identity / Decision / Policy / Artifact Verification       │
│ Durable Ledger / Budget / Secret / Promotion / Rollback    │
│ Effect Gateway / Runtime Supervisor / Kill Switch          │
└──────────────┬───────────────────────────┬─────────────────┘
               ▼                           ▼
┌──── Declarative Control Plane ────┐  ┌── Evolution & Evaluation ──┐
│ Catalog / Resolver / Admission    │  │ Observe / Diagnose         │
│ Generation / Warming / Routing    │  │ Candidate / Replay         │
│ Health / Drain / Reconcile        │  │ Shadow / Canary / Attest   │
└──────────────┬────────────────────┘  └────────────┬───────────────┘
               └──────── immutable SystemSnapshot ─┘
                                  │
                                  ▼
┌──────────────── Generation-based Runtime Plane ────────────┐
│ Continuity Router → RuntimeGeneration A / B                 │
│ Durable Memorial/Attempt Supervisor / Context Compiler      │
│ Capability Broker / Effect Gateway / Plugin Instances       │
│ In-process trusted / Process / Wasm / Container / Service  │
└─────────────────────────┬──────────────────────────────────┘
                          ▼
┌──────────────── State, Artifact & Evidence Plane ───────────┐
│ Event History / RunState / Checkpoint / Effect Journal      │
│ Artifact CAS / Plugin State / Memory / Lineage / Evidence   │
│ Audit / Usage / Cost / Derived Indexes                      │
└────────────────────── feedback ─────────────────────────────┘
```

这些“平面”首先是职责、权限和代码所有权边界。迁移早期继续使用单体进程和 SQLite，不以拆
微服务作为完成标志。

## 2. 治理微内核

微内核拥有不能交给普通插件的权威能力：

- Principal、身份、授权和 Decision；
- Edict、Memorial、Attempt、lease/fencing 和终态账本；
- durable outbox、Effect intent/receipt 和幂等边界；
- Artifact digest、签名、provenance 与 Admission；
- Policy Enforcement、Secret Broker、预算和 kill switch；
- PluginSet resolver、RuntimeGeneration ledger；
- Promotion Authority、last-good 和 Rollback Reconciler；
- Evidence verifier、审计与状态迁移账本。

插件可以贡献更严格的检查，但不能替换或放宽上述权威。

## 3. 声明式控制面

控制面不直接执行插件正文，而是维护期望状态并驱动收敛。

建议的资源：

| 资源 | 职责 |
|---|---|
| `PluginRelease` | 某插件的不可变发布，引用 package Artifact 和 manifest |
| `PluginSetSpec` | 用户期望启用的插件、版本约束、配置及每插件 EvolutionPolicy 引用 |
| `PluginSetSnapshot` | Resolver 产出的精确依赖闭包、Capability binding 和有效权限 |
| `SystemSnapshot` | 完整、可部署的有效系统配置，引用 PluginSet、模型、策略等版本 |
| `AgentDeployment` | 期望使用的 SystemSnapshot、rollout 策略和有效状态 |
| `RuntimeGeneration` | 一个 SystemSnapshot 在具体 Host 上 materialize 后的运行实例；ID 不是内容摘要 |
| `EvolutionPolicy` | 每插件允许的变化 surface、模式、预算、审批和回滚要求 |
| `EvolutionCandidate` | 针对精确基线提出的变化 |
| `EvaluationCampaign` | 数据集、Evaluator、预算、对照组和评测证据 |

资源应区分 `spec` 和 `status`：

```yaml
metadata:
  spec_revision: 42
spec:
  desired_system_snapshot_digest: sha256:...
status:
  observed_spec_revision: 42
  active_runtime_generation_id: rg-...
  active_system_snapshot_digest: sha256:...
  last_good_system_snapshot_digest: sha256:...
  conditions:
    - type: Verified
    - type: Warming
    - type: Ready
```

Reconciler 必须幂等、level-based。即使错过事件或进程崩溃，也要根据最新持久状态继续
收敛，而不是依赖“刚才执行到哪一步”的内存变量。

同一个 SystemSnapshot 在进程重启、不同 Host 或并行预热时可以产生多个 RuntimeGeneration；
SystemSnapshot 用 digest 标识内容，RuntimeGeneration 用独立运行身份标识实例。

控制面有六项逻辑职责，但首期不建立六个 Reconciler 类或服务：

- Artifact 获取、验证和登记；
- PluginSet 依赖、Capability、冲突与有效权限解析；
- Runtime 实例创建、预热、健康检查和隔离；
- Replay、Shadow 和离线 Gate 评测；
- Canary、路由、晋升、排空和回滚；
- 兼容状态迁移和回滚检查。

首期只有一个 `GenerationReconciler`，扩展现有 `EvolutionRollbackReconciler`：复用同一把锁和
同一个 `reconcile_once()`，按持久化 generation state/scope 分支，并继续通过既有授权服务执行
晋升或回滚。其余逻辑职责保留在现有服务中，出现独立持久状态机需求后再拆分。

## 4. 代际化运行面

### 4.1 热更新语义

热更新按承载形态分成三类，不把所有 built-in 都塞进同一 Python 进程做多代并存：

```text
子进程执行器：resolve → verify → stage → warm/probe → ready → activate pointer
              → 新 run 取新代 → 旧 run 排空/refcount=0 → dispose
声明式内容：  candidate 晋升写不可变 artifact + 原子切指针
              → run 在 bind_runtime 冻结只读视图；watcher 只失效缓存
进程内实现：  优雅重启进入指定 SystemSnapshot → 启动校验
              → warm-up 失败回 last-good；不做 importlib/module reload
```

三类更新共同满足：active pointer 只在新目标 Ready 后切换；已开始的 run 不换所绑定内容或
执行器；失败时保留 last-good；状态不兼容时排空或显式 fork，不能暗中迁移。只有子进程
执行器需要在宿主进程生命周期内 side-by-side；声明式内容靠每 run 冻结，Python 实现靠进程
重启形成代际。

首期明确不引入独立 `AgentSession`，按 Edict 类型确定边界：conversation 和长任务 Edict 固定
RuntimeGeneration；cron/interval 每次触发的新 root Memorial 选择当时有效 generation；DAG
子节点和基础设施重试继承 root Assignment。所有 Memorial 都绑定完整
`ExecutionAssignment`。只有跨 Edict continuity、fork 或显式代际迁移成为真实需求时，才另立
ADR 讨论持久 AgentSession。

### 4.2 Generation-scoped Capability Registry

全局 registry 改为 generation scope：

```text
PluginInstance
  └─ contribute(capability, implementation)
       └─ ContributionHandle(owner, generation, disposer)
```

基本要求：

- 每项 contribution 都有 owner 和 generation；
- 注册成功返回统一 disposer；
- 依赖按拓扑顺序启动、逆序停止；
- Tool、Hook、Provider、Channel 等冲突使用确定性规则并给出诊断；
- observe、transform、veto 三类 hook 分开；
- 当前 `HookRegistry` 已有 per-type timeout 与 fail-secure hook 集合；统一 Capability 层仍需补
  budget、熔断和 crash-loop quarantine，并把等价约束扩到其他 handler；
- transform 后重新做 schema validation。

### 4.3 隔离层级

| 插件类型 | 默认 Host | 信任边界 |
|---|---|---|
| 固定、受信 built-in | 同进程 | 需要 owner/disposer；Python 实现以进程 snapshot + 优雅重启换代，不做进程内多代 reload |
| 可演化的可执行插件 | 独立 Worker Process | clean env、资源限额、受管 RPC |
| 第三方受限插件 | Process 或 Wasm | capability grant、文件/网络范围 |
| 大型外部能力 | 独立 Service/MCP Server | Host 保留上下文、权限和 secret 控制 |

Wasm 可以作为未来 typed ABI，但不是第一阶段前置。无论使用 Process、Wasm 还是 Container，
隔离都不能替代供应链审查和业务 Policy。

## 5. 状态、事件与可回放执行

需要明确区分三类记录：

| 记录 | 用途 | 是否允许采样 |
|---|---|---:|
| Durable Event History | 恢复、重放和正确性 | 否 |
| Tamper-evident Audit Ledger | 权限、晋升、回滚和追责 | 否 |
| Telemetry / Trace | 性能、诊断和候选发现 | 可以 |

权威执行骨架必须可重放；模型、工具、网络和文件等非确定性调用放入 Effect：

```text
effect_id
memorial_id / attempt_id
system_snapshot_digest
plugin_instance
idempotency_key
policy_decision_id
request_digest / result_digest
retry_attempt / terminal_state
```

Replay 读取已记录结果，不重新调用模型或外部服务。Memory 的原始记录、跨线程长期 Store 和
当前运行 checkpoint 也必须分开；摘要和索引都是可重建投影，不替代原始 Event History。

## 6. Artifact 与供应链

每个可演化对象都成为内容寻址 Artifact：

```text
PluginArtifact
├── executable / declarative content
├── manifest and typed interfaces
├── dependency lock
├── state schema and migrations
├── capability ceiling
├── SBOM and provenance
├── signatures
├── evaluation attestations
└── digest
```

职责不可混淆：digest 证明字节一致，不证明发布者可信；签名证明发布权，不证明质量；
provenance 证明来源和构建过程，不证明行为安全；Evaluation Attestation 才证明该候选在指定
数据集、策略和预算下通过了哪些 Gate。

运行身份必须固定 digest；`stable`、`canary`、`active` 只能是可审计、可回滚的指针。

## 7. Evolution 与 Evaluation Plane

Evolution Plane 读取 Evidence、失败、人工反馈、成本和长期趋势，产生 Candidate，但没有
修改 active pointer 的权限。Evaluation Plane 使用版本化的数据集、Evaluator 和 Policy
执行：

```text
Candidate
→ schema / contract / unit
→ security / privacy / prompt-injection
→ historical replay
→ held-out tasks
→ shadow
→ continuity-scope-sticky canary
→ signed evaluation evidence
→ independent Promotion Decision
```

评分采用带硬约束的向量，而不是单一 fitness：

- 任务成功率和质量；
- 安全、隐私和权限违规（硬 Gate）；
- 延迟、Token、成本；
- 故障恢复和稳定性；
- 用户 reject、amend、rollback 信号；
- 相对 active、父版本和历史最佳版本的回归。

Candidate 不能修改负责评价自己的 Evaluator、held-out 数据集、Policy 或 Promotion
Authority。
