# 从当前 Tianshu 到目标架构

> **Status: Proposed migration plan。**
> 每一阶段都必须保持当前受管主链、Evidence 和 fail-closed 边界，不采用大爆炸重写。
>
> **进度（2026-08-27）**：P4b PR #109 与 P5 PR #111 已合入 `feat/plugin-v1`，完成
> V34 assignment set（1..64 封存原子写）、continuity 选择、运行时深冻结、Evidence 与 truthful
> UI 及 V35 Pi EXECUTOR 治理垂直切片。P6 已由 PR #114 合入该分支（merge
> `8f32cc4c`），完成无迁移的 process snapshot generation、strict binding 与
> Evolution 投影。P7 已由 PR #116 合入（merge `feba5a91`，CI 6/6），仅冻结 Skills，
> 同样无数据迁移。P0–P7 与 X1–X5 计划项已全部合入，等待用户总体验证。
> V34 应用后的回退必须保留
> schema/ledger 并使用兼容 reader，不能部署纯 V33 二进制；
> 只将 active CANARY authorities 经正常 promote/rollback 收敛到至多 1 个、最好 0 个并完成
> pending rollback，不得强退已 PROMOTED subject。

## 1. 总体判断

这不是推倒重来。Tianshu 最稀缺的资产是治理、持久运行和证据，不是插件 API 的数量。
重构方向应是把这些资产升格为稳定 OS 微内核，再让可替换能力逐步进入代际化 PluginHost。

首个产品里程碑不应是插件市场，而应是：

> **完整运行快照 + continuity generation 固定 + last-good 回滚。**

## 2. 保留、重构与淘汰

### 2.1 保留并强化

- Edict、Memorial、Decision 和 Governance Contract；
- RunState、Attempt lease/fencing、fenced completion；
- Workspace Lease、Restore Point、Canonical Change Set；
- UoW、durable outbox、Effect journal、SystemAudit；
- ArtifactStore、Evidence close/verify/import/export；
- Candidate lifecycle、Gate、Canary、Promotion journal、Rollback；
- ChallengerRouter“先持久化 assignment 再执行”的模式；
- 静态 DAG 的完整预审、确定取消边界和 follow-up 外环；
- Keqing“只替换执行面，不替换治理面”的边界。

### 2.2 需要重构

| 当前对象 | 目标方向 |
|---|---|
| `PluginLoader` / `PluginApi` | Manifest Catalog 与 PluginHost 分离 |
| 进程级 Tool/Hook/Provider/Channel registry | generation-scoped、owner-aware Contribution Registry |
| `RunAssignmentV1` 单 Candidate overlay | 完整 `ExecutionAssignment` + `SystemSnapshot` |
| 单一全局 canary 权威 | 按 `(kind, subject_key)` 路由；同 subject 仍只允许一个 canary 并 fail closed，不同 subject 可并行灰度 |
| 五种固定 `CandidateKind` | 保留兼容层，新增 `target_plugin_id + patch_surface` |
| SkillsWatcher 直接刷新 active loader | P7 开启时只失效缓存并通知；run 冻结 Skills 视图。“文件变化只产生 Candidate/新 generation”仍是后续目标 |
| channel anchor、follow-up、各种 Session 概念 | 按 conversation/长任务 Edict、scheduled root、DAG/retry 分别确定 continuity binding；首期不引入 AgentSession，未来出现跨 Edict 需求再另行 ADR |
| `UniverseManager` | 保留 branch/diff/eval，保持无生产 active 所有权且不得恢复 live writer |
| `PromotionService` 大类 | 逻辑拆分授权、Gate、分流、激活和回滚，但暂不拆服务 |
| `Storage` 全局 facade | 插件只获得 namespaced repository/state handle |
| best-effort EventBus 权威用法 | 正确性走 durable event/outbox；EventBus 留给 UI/缓存通知 |
| `wire_*` 静态装配 | 保留 composition root，built-in 解析成默认 PluginSetSnapshot |

### 2.3 迁移后淘汰

- 对全局 registry 无 owner 的 `PluginApi.register_*` 直写；
- “已发现 manifest”与“已安装/激活插件”混用；
- 活跃 Skill 目录直接 repoint/reload；
- 把 `universe_id` 或 Legacy champion 当作运行权威的残余兼容投影和读取路径；当前正式
  Candidate 路径已经不以它们作为生产 active pointer；
- 生产正确性依赖 best-effort `emit/fire`；
- 默认 import 任意本地 Python entry point；
- 迁移完成后的重复 legacy assignment 字段和无效 auto-promote 配置。

## 3. 分阶段路线

### Phase 0：冻结术语和不变量

工作：

- 只先写 ADR-0013（代际发布、治理微内核边界）和 ADR-0014（Memorial 绑定与 continuity
  规则）；其余目标术语保留在设计词汇表，不提前各建一个 ADR；
- 冻结第一阶段 continuity 规则：conversation/长任务 Edict 固定、scheduled root 每次选择、
  DAG/retry 继承 root Assignment；AgentSession 暂不引入；
- 用 characterization tests 锁定 ingress、fencing、Evidence、Candidate 和 rollback；
- 明确微内核永不开放的 API。

退出条件：

- 当前事实和目标术语无混用；
- 所有关键不变量有可执行测试或明确的 deferred Gate；
- 设计不承诺多节点、第三方安装或自动晋升。

### Phase 1：Shadow SystemSnapshot

工作：

- 将当前 `wire_*` 装配的 kernel、已注册 executor manifest、skills、personas、policy rules、
  provider profiles 与 governed overlay 解析为 `SystemSnapshotV1` 的确定性组件摘要；本阶段不另建
  `PluginSetSnapshot` 聚合；
- 用 V31 `system_snapshots` 保存内容身份，并在第一个受管执行作用域前按
  `(memorial_id, attempt_id)` insert-once 写 `run_system_bindings`；旧数据不回填；
- Evidence 关闭时增加包含 `snapshot + generation_ids` 的 required artifact，assignment API 与
  Edict 详情只读投影内容摘要；不修改 `EvidenceSnapshotV1` 与既有 `RunAssignmentV1`；
- 解析、binding、漂移与审计都保持 shadow：失败不改变 active 行为，关闭开关后旧运行路径保持
  兼容；严格拒绝留到落地方案 P6；
- 不伪造 `legacy/default` RuntimeGeneration，不提前引入完整 `ExecutionAssignment` 聚合。

退出条件：

- 开关启用且影子绑定成功的每个新受管 attempt 都能回答“实际使用了什么”；
- 同一输入解析出的 snapshot digest 确定一致；
- Evidence artifact 独立重算后与持久 binding 完全匹配；
- API/UI 不会把典制摘要误报为已经具备 warming、drain、generation 或动态卸载；
- 关闭新双写后旧路径仍保持行为兼容。

### Phase 2：Continuity pinning 与首条垂直切片

第一条切片建议选择 Keqing/Pi ExecutorAdapter：

- 已有 ExecutorAdapter Protocol、`AgentCapabilities` 声明基础和受限的外部执行边界；完整
  Capability Manifest 仍是目标设计；
- “只换执行面、不换治理面”天然适合作为 Plugin Capability；
- 可以真实验证进程隔离、版本漂移、generation pinning 和 drain；
- 相比先迁移高耦合 ToolRegistry，风险更容易限制在执行适配边界。

工作：

- V32 增加独立 `run_generation_bindings` 作为 exact-attempt 代际权威；每个新 attempt 都写
  `bound`（包括显式 `[]`），snapshot 开关只控制 V31 system shadow，不控制该 marker；
- 升级存量 attempt 时只写可证明事实：有 system binding 就复制 generation ids，可证明非 Pi
  写 `bound []`，历史 Pi/契约歧义写 `unresolved` 并在 continuity/retention 读取时失败关闭；
- conversation/长任务 Edict 固定 RuntimeGeneration，follow-up 继承；
- cron/interval 每次触发的新 root Memorial 选择当时 active generation；
- DAG 子节点和基础设施重试继承 root/source attempt 的 exact marker；只有 V31 历史缺 marker 时
  才读取 `run_system_bindings` fallback，两者同在必须一致；
- Pi 新旧版本 side-by-side；只有新的 root assignment 或命中 Canary 的新 continuity scope
  使用新版本，已有 continuity 不换代；
- 版本漂移先产生 Candidate，不自动改 active；
- Evidence 绑定实际 executor release、probe 和 generation；snapshot 启用时 system shadow 与
  exact marker 对账，关闭时 system binding 为零行但 generation marker 仍可独立证明选择。

退出条件：

- 活跃长任务不会在执行中换 executor；
- 新 Pi 启动或契约失败不会影响旧任务；
- 旧任务完成后旧进程可被确定回收；
- 回滚在目标 SLO 内恢复 last-good 路由。

### Phase 3：Capability 所有权与三类热更新边界

本阶段不建立“所有 built-in 都在同一 Python 进程内多代并存”的 PluginHost。先补统一的
owner/disposer，再按能力形态选择最小的换代边界：

```text
子进程执行器：stage → warm → activate 指针；新 run 取新代，旧 run 排空后 dispose
声明式内容：晋升写不可变 artifact 并切指针；run 在 bind_runtime 冻结只读视图
进程内实现：优雅重启进入指定 SystemSnapshot；预热失败回 last-good，不做模块 reload
```

**当前落点**：P2 owner/disposer、P3/P5 Pi 代际和 P6 process snapshot 已合入。P7
也已由 PR #116 合入；它只为 Skills 实现 `off` / `shadow` / `enforce`
的每 run 视图。prebind/重启时旧 SystemSnapshot 不能从当前 Skills 重建，shadow
审计后读 live，enforce 失败关闭；不会静默混用旧 snapshot 和新 view。跨重启
耐久回放旧内容的 artifact-backed `skills_view` 与 durable global Skill tombstone 延期到
P7b。Persona/Prompt/Provider 的
冻结和“变化必须先进 Candidate”仍是后续阶段。当前 Skills 切片以目录 fd、文件/目录完整
stability witness、injected generation 和连续两次全量 capture 锁定视图；三轮持续 churn 后
fail closed，并拒绝搜索路径/成员/嵌套资源 symlink。requirements/max-size/load_all/metadata/
injected/fallback 与 live 同义，requirements 环境 eligibility 进入 source identity；selected base
absent 只显露低层，challenger/unknown absent 保持历史 hide-lower，新的 absent candidate 在
canary/promote/activate 稳定拒绝。watcher 统一用 polling observer，避免 macOS 原子交换时的
FSEvents 崩溃。enforce prebind 只有 audit+outbox 成功才在 caller UoW commit 后抛错；scheduled
fire 已提交则按 durable cursor/root 收口并唤醒 reconciler，提交前失败整笔回滚且不推进 cursor/
清 initial root。同-key marker 仅让 claimable attempt 重冻，成功写
`skills_view_binding_recovered`，终态重放不重冻；生产 prebind/dispatch 是两个阶段且每阶段最多
freeze 一次。promotion invalidator 与 frozen flag 解耦，desired/no-op 重试和
`verify_rollback` 命中也主动失效缓存。该保证限于本地
POSIX 普通写者与可靠 ctime，不覆盖特权写者或不可靠 ctime 文件系统。

工作：

- 给 Tool、Hook、Provider、Channel、Skill、Command 等注册贡献补 owner 和统一 disposer；
- 保留 composition root，冲突、缺失依赖和卸载错误产生结构化诊断；
- `SkillsWatcher` 只失效缓存，不再把刷新 active loader 等同于换代；
- Provider/Tool/Hook 等 Python 实现通过进程级 snapshot 与 drain 重启换代；
- 暂不加载第三方代码。

退出条件：

- 单个 owner 的贡献可逆序释放，重复释放幂等，其他 owner 不受影响；
- 连续 100 次 Pi 换代不混用 executor generation；
- 连续 100 次 snapshot 重启无内容漂移，失败时 active/last-good 不变；
- 文档与测试均不承诺进程内 Python 模块 side-by-side 或 reload。

### Phase 4：从叶子能力向内迁移

推荐顺序：

```text
Provider / Channel / Notifier
→ 其他 Executor Adapter
→ Tool
→ Skill / Persona / Memory / Context Contributor
→ 声明式 UI 扩展
```

每迁移一种 Capability，都必须补齐：owner、权限、冲突规则、health、timeout、状态 schema、
Evidence 和 rollback。Agent Loop 可以作为满足稳定 Memorial/Attempt 执行契约的实现插件，
但不能直接获得运行账本和 Promotion Authority。

### Phase 5：Evolution 在 P4 policy/assignment 基础上收敛到 PluginSet patch

工作：

- Candidate 从固定五类扩展为目标 Plugin、surface 和精确 base snapshot；
- Legacy Universe mutation 只产出 Candidate；
- PluginSetSnapshot 先进入新的 SystemSnapshot，AgentDeployment 再更新 desired snapshot；
  RuntimeGeneration 完成 warming/Ready 后才切换 active，回滚使用 last-good SystemSnapshot；
- 引入 per-plugin `frozen/manual/canary`；
- Canary 按上文三类 continuity scope sticky，不引用尚未引入的 AgentSession；
- historical replay、held-out、shadow、state migration 和 rollback rehearsal 成为 Gate。

退出条件：

- 用户可以启用插件但冻结其进化；
- Candidate 无法更改自己的 Gate、权限和 Evaluator；
- stale evaluation 无法晋升；
- 回滚先归零流量，并且 Reconciler 可在崩溃后继续。

### Phase 6：第三方生态与有限自动进化

完成以下基础后，才开放正式 install/activate/unload：

- 内容寻址 package、签名、SBOM、TUF/SLSA provenance；
- API/ABI 和 Host 版本协商；
- Process/Wasm/Container host；
- Secret handle、文件/网络 capability 和资源配额；
- 状态迁移、health probe、crash-loop quarantine；
- kill switch、last-good 和自动回滚演练。

`auto` 最初只给明确批准、无状态、低权限、可快速回滚的叶子插件。核心代码继续采用“自动提案、
自动评测、人工发布”。

## 4. 验收标准

只有同时满足以下条件，才可以对外称为“受治理的自进化 Agent OS”：

- 每个 Memorial 绑定唯一、完整、不可变的 SystemSnapshot；
- 同一连续交互不混用两个 RuntimeGeneration；
- 新 generation 失败时 active 和 last-good 不变；
- Canary 按 continuity sticky，分桶可以独立重算验证；
- Replay 不重新调用模型或外部 Tool；
- 单个插件可以保持 enabled，同时设为 frozen；
- Candidate 无法修改自己的 Evaluator、权限或 Promotion Policy；
- 第三方插件不能直接取得全局 Storage、宿主 secret 或未授予权限；
- 状态不可安全回退时，自动晋升 fail closed；
- Evidence 绑定实际模型、Prompt、PluginSet、策略、Effect、成本和环境；
- 旧 generation 在引用归零前不会销毁；
- 回滚满足明确 SLO，并有故障注入测试覆盖。

## 5. 主要风险

| 风险 | 后果 | 控制方式 |
|---|---|---|
| 把目标对象一次性铺满代码库 | 长期双轨和抽象债 | Shadow 双写、逐 Capability 迁移 |
| 插件粒度过细 | 依赖爆炸、难以原子发布 | Plugin 是策略/Candidate 单元，SystemSnapshot 原子发布回滚 |
| 评测过拟合单一分数 | Goodhart、质量倒退 | 硬 Gate + 多指标 + held-out + live Canary |
| 状态 schema 不可逆 | 回滚失效 | dual-read/write、排空、备份、人工 Gate |
| 同进程第三方代码 | secret、宿主和稳定性风险 | Process/Wasm/Container 隔离 |
| Reconciler 非幂等 | 重启后重复激活或破坏状态 | durable spec/status、CAS、fencing |
| EventBus 被当作权威账本 | 丢事件后无法恢复 | outbox/Event History 为正确性来源 |
| 过早开放 auto | 权限和质量事故 | 先 frozen/manual/canary，逐插件白名单 |

## 6. 实现前需要正式拍板的 ADR

首期只记录两个跨阶段、难以逆转的决策：

1. **ADR-0013：代际发布与治理微内核边界。** 热更新采用按能力形态的代际切换与 drain，
   不采用进程内模块 reload；治理微内核不由普通 Evolution 自动修改；Plugin 是 Candidate/
   策略单元，SystemSnapshot 是部署、回滚和归因单元。
2. **ADR-0014：Memorial 的 SystemSnapshot 绑定与 continuity 规则。** conversation/长任务固定，
   scheduled root 每次选择，DAG/retry 继承 root，canary 选择随 continuity 固定；第一阶段不引入
   AgentSession。

以下决策等到出现真实实现消费者时再单独立 ADR，不在首期预占编号：

- Plugin state 的兼容、迁移和不可逆变更规则；
- 第三方插件的默认隔离 Host 与 Capability grant 模型；
- `auto` 模式允许的风险上限、统计门槛和人工收权机制；
- 第三方 PluginSet spec/依赖解析与多 Host 部署语义；
- 需要跨 Edict continuity 时 AgentSession 的持久身份与迁移规则。
