# ADR-0014:奏折在首个受管副作用前绑定典制与朝

- 状态:已接受
- 日期:2026-08-25
- 相关:[ADR-0013](0013-generation-based-rollout.md)(典制、朝与代际发布)

## 背景

现有 assignment 与 Evidence 能证明候选分流、执行器 manifest 和部分环境，但不能完整回答
一次奏折实际使用了哪套系统内容与运行实例。若在外部动作发生后才记录，失败或进程崩溃
会留下无法归因的副作用，因此 binding 必须先于首个受管副作用完成。

## 决策

### 1. 每个奏折按 attempt insert-once 绑定

每个受管 Memorial 必须在首个受管副作用前确定实际使用的朝；snapshot 启用时还绑定一个
不可变典制。两类事实都以 `(memorial_id, attempt_id)` 为身份 insert-once：P1
`run_system_bindings` 保存典制 shadow，P3 `run_generation_bindings` 独立保存 exact generation
marker。重试产生新 attempt，并复制源 marker，不改写旧记录。

binding 是独立关联事实，不塞入 `RunAssignmentV1`、`LegacyRunAssignmentV1` 或
`EvidenceSnapshotV1`。完整典制作为独立 Evidence artifact 留证；P1 当时把预留的空
generation ids 随 `run_system_bindings` 保存。P3 没有改写这段历史语义，而是在 V32 新增
`run_generation_bindings`：`bound` 保存实际朝集合（包括显式 `[]`），`unresolved` 只表示升级时
无法可靠还原的历史 Pi attempt。两张表同在时 generation ids 必须一致。

P3 起，generation selection、对应 bundle 与 executor manifest digests 必须来自同一个锁内
快照，并按 `(scope, generation_id)` canonical 排序。非空 generation 集合必须逐项验证存在、
scope 唯一、状态可绑定且 material 可用；未知、重复 scope、重复 ID、跨 scope 或不可物化
任一项都在首个受管副作用前 fail closed。空集合也必须以 exact `bound []` marker 持久化，
表达“该 attempt 明确不使用受管 Pi 代”，不伪造 `legacy/default` 朝，也不会在后来出现 active
Pi 时被重新解释。snapshot 关闭只会让 system binding 保持零写入，不会省略 generation marker。

### 2. continuity 固定规则只有四条

1. conversation 与深度 Edict 固定 root Memorial 的典制、朝和 assignment 选择；follow-up
   root Memorial 继承 parent root 的 `selected_ref`、`candidate_id` 与 `bucket`，不重新分桶。
2. scheduled root 的每次新触发都是新的 continuity，在触发时选择当时 active 的典制与朝。
3. DAG 子节点与基础设施重试继承 root/source attempt 的 exact generation marker，不自行选择新朝；
   只有 V31 历史 attempt 尚无 marker 时才回退 `run_system_bindings`。
4. canary 选择随 continuity 固定；continuity 内任何 follow-up、子节点或重试都不得改选
   candidate 或重新计算 bucket。

第一阶段不引入独立 `AgentSession`。现有 Edict/Memorial 谱系足以表达上述边界；只有跨 Edict
continuity、显式 fork 或代际迁移成为真实需求时，才另立 ADR 决定是否引入 AgentSession。

### 3. P1 影子写，P6 翻转严格

P1 以 shadow 模式双写 `run_system_bindings`：解析或写入失败只记录结构化审计，不改变 active
行为，存量或失败记录可以没有 system binding。P6 在观测与回放验收通过后翻转 snapshot
strict；届时 system binding 不可用必须在首个受管副作用前让运行 fail closed，不得静默回退
live 状态。

shadow 豁免只覆盖 binding 的可用性，不放宽 assignment、Candidate、Evidence 或其他既有
fail-closed 契约。

P3 另立严格的 generation marker 边界：每个新 attempt 都必须有 `run_generation_bindings`，
`bound []` 与非空集合同样是不可改写事实；marker 缺失/冲突、`unresolved`、指定朝不可用或
materialization 失败都不能靠 P1 shadow 豁免。P6 仍只负责把 snapshot/system-binding 完整性
整体翻转为 strict，这两个翻转时点互不混淆。

### 4. 典制组件与版本语义

典制首版覆盖 kernel、executor、skills、personas、policy rules、provider profiles 和
evolution overlay 的内容摘要；`prompts` 组件 key 从首版预留，本轮不填值，待 prompt/harness
模板内容数字化后再接入，不因此修改 schema version。

kernel 组成包含 `tianshu_version` 与 dependency lock identity。发布时修改
`tianshu_version` 会产生新的典制 digest，这是预期的内容身份变化，不是误报漂移。

## 影响

- 每次新受管 attempt 都能在副作用发生前由 exact marker 确定“使用了哪个 generation set”；
  snapshot 启用时再由 Evidence 独立对账完整典制。
- 新朝切换不会改变已有 continuity 的执行身份；回滚只影响之后创建的新 continuity。
- P1 保持零 active 行为变化，P6 才把 binding 完整性升级为运行前硬门。
