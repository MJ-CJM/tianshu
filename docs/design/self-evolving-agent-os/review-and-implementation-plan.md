# 评审与实现计划（2026-08-24）

> **文档性质：对本目录 codex 报告的独立评审 + 可直接开工的 PR 级实现计划。**
> 评审基线：本目录 9 篇文档 + 工作树 `88462b2a`（`feat/plugin-v1` 未提交状态）。
> 所有"当前源码事实"均在本轮重新读代码核对，不转引报告结论。

## 1. 总评

报告的核心结论是对的，而且比市面上大多数"插件化 + 自进化"方案诚实得多：

> 稳定治理微内核 + 声明式控制面 + 不可变插件代际 + 可回放执行面 + 独立演化/评测面；
> 热更新是新旧代际并存后的路由切换，不是活体模块 reload；提案者不能给自己评分并批准上线。

这条主线应当接受。三处需要修正的地方：

| # | 问题 | 后果 | 本文的处理 |
|---|---|---|---|
| 1 | 目标词汇 13 个新领域名词、6 个 Reconciler、7 个 ADR | 对单进程 SQLite、单人维护的产品是 K8s 级词汇量，正是报告 §5 自己列的"抽象债"风险 | 收敛为 3 个代码级对象 + 1 个 Reconciler + 2 个 ADR（§3.1、§3.6） |
| 2 | Phase 2（全部 built-in 进程内代际并存）排在 Phase 3（Pi 垂直切片）之前 | 最重的工程在没有任何消费者验证前先做；进程内 Python 代际并存对本产品收益极低 | 顺序倒转；代际边界改为"进程级 + 每 run 内容快照"两类（§3.2） |
| 3 | 漏掉一个硬阻塞：当前只允许**一个**全局 canary 权威 | 报告的 per-plugin `EvolutionPolicy` 与独立 canary 在现有路由上跑不起来 | 列为 PR-4 的前置改造（§3.4） |

前后架构的图示见 [architecture-comparison.md](architecture-comparison.md)。

另外报告低估了一件好事：Evidence 今天已经持有目标 `SystemSnapshot` 约三分之二的组成，
Phase 1 比报告写的便宜得多（§3.5）。

## 2. 核对结果：报告说对了什么

以下均为本轮读源码确认的事实，报告表述准确：

| 报告断言 | 源码证据 |
|---|---|
| 插件是 metadata-only catalog，不 import `entry_point` | [`loader.py`](../../../src/tianshu/plugins/loader.py) 只做 `json.loads` + `PluginManifest(**data)`；[`api.py`](../../../src/tianshu/plugins/api.py) `register_plugin` 只写 SQLite `status=manifest_only` |
| `register_*` 无 owner / disposer / generation | `PluginApi.register_tool` 直写 `ToolRegistry.register`；`ToolRegistry`、`ChannelRegistry` 根本没有 `unregister`；`register_command` 用 `hasattr(self, "_commands")` 临时挂属性 |
| `RunAssignmentV1` 只绑定单一 Candidate overlay，不描述完整运行环境 | [`run_assignment.py`](../../../src/tianshu/models/run_assignment.py)：`candidate_id / champion_ref / selected_ref / bucket`，无任何环境、执行器、skills 版本字段 |
| "先持久化 assignment 再执行"模式已成立 | `managed_run_ingress.py`、`edicts.py`、`scheduled_runs.py` 共 12 处在插入 Memorial 的同一 UoW 内调 `assign_current`；`run_dispatcher.py:225` 执行前 `bind_runtime`，缺失即 fail closed（`run_assignment_unavailable`） |
| 只有 Skill Candidate 有真实 activation/rollback adapter | [`wiring_skills.py:108-121`](../../../src/tianshu/bootstrap/wiring_skills.py)：其余四种 `CandidateKind` 全部映射 `UnavailablePromotionAdapter` |
| Universe switch/rollback/promote-code fail closed | `universe/manager.py:166-180` 三个方法均 `raise RuntimeError("promotion_service_required")` |
| SkillsWatcher 直接刷新 active loader | `skills/loader.py:815+`：SKILL.md 变化 → debounce → `invalidate_cache()` + `load_all()`，运行中的 run 下一次 `skill_view` 即看到新内容 |
| 自动晋升被类型级锁死 | `EvolutionContractV1.automatic_promotion_allowed: Literal[False]` |
| `PromotionService` 是大类 | `evolution/promotion.py` 1960 行，`SkillPromotionAdapter`（原子交换/marker/隔离区）与 `PromotionService`（canary/promote/rollback/journal/reconcile）同文件 |

报告对上游（DeepSeek Harness / Pi）的"不能照搬"边界（HMR 关闭、reload 非原子、无 last-good
自动回滚、扩展与宿主同权限）与我对这两个项目的认识一致，不再重复核对。

### 2.1 报告没写、但对实现顺序重要的现有资产

| 资产 | 位置 | 意义 |
|---|---|---|
| `ExecutorAdapterRegistry` 已是一个完整的 Capability seam | [`executor/adapters/__init__.py`](../../../src/tianshu/executor/adapters/__init__.py)：`register / replace / get / prepare / bind_effective`，每次 run 走 `prepare()` 拿到 `PreparedExecutor`，manifest 与执行模式在注册时校验 | Pi 切片不需要"先建 PluginHost 再迁 executor"——seam 已经在，缺的只是 generation 与引用计数 |
| `ExecutorCapabilityManifestV1` 已有 `manifest_id / manifest_version / adapter_id / level / capabilities` | [`executor/capabilities.py`](../../../src/tianshu/executor/capabilities.py) | 就是报告要的 executor 侧 `PluginRelease` 元数据 |
| `EvidenceSnapshotV1` 已绑定 `effective_contract_hash / executor_manifest_hash / environment(dependency_lock_hash, environment_fingerprint) / plan_revision`，并把 assignment 作为 artifact 挂入 `required_artifact_digests` | [`evidence/models.py:266-283`](../../../src/tianshu/evidence/models.py)、[`evidence/service.py:911-928`](../../../src/tianshu/evidence/service.py) | `SystemSnapshot` 的大半组件已在证据里，只差 skills/persona/policy/provider 四个内容指纹与一个总摘要 |
| `HookRegistry.run` 已有 per-type timeout 与 fail-secure 集合 | [`kernel/hooks.py:86-125`](../../../src/tianshu/kernel/hooks.py) | 报告 §4.2 把"handler 有 timeout"列为目标，实际已部分成立；缺的是 budget/熔断/crash-loop quarantine |
| `RunDispatcher` 已有 stop-claims → drain → cancel 的关停序列 | `application/run_dispatcher.py:195-216` | "drain 旧代"在进程级已经存在，不必在进程内再造一套 |
| `EvolutionRollbackReconciler` 已是 level-based、串行、可重入的 reconciler 雏形 | [`evolution/reconciler.py`](../../../src/tianshu/evolution/reconciler.py) | 扩成 `GenerationReconciler` 即可，不需要六个 |
| import-linter 三层契约 | `pyproject.toml [tool.importlinter]`：`gateway/executor/scheduler/bootstrap/universe : storage/secrets/memory/persona/skills : kernel/models/config/bus` | "微内核不可被插件反向依赖"可以直接用它落成可执行约束；注意 `evolution / evidence / application / plugins / governance` 目前不在任何层里 |

## 3. 不同意与补充

### 3.1 词汇：13 个目标名词收敛为 3 个代码对象

报告 `domain-and-governance.md §1` 提出 13 个目标态术语。第 1–3 阶段真正需要成为代码的只有：

| 代码对象 | 吸收了报告中的 | 形态 |
|---|---|---|
| `SystemSnapshotV1` | `SystemSnapshot`、`PluginSetSnapshot`、`PluginRelease`（作为 components 中的条目） | 一个 frozen pydantic：`components: dict[str, str]`（组件 id → 内容 digest）+ `digest`；内容寻址，落 `system_snapshots` 表 |
| `RuntimeGenerationV1` | `RuntimeGeneration`、`PluginInstance`、`AgentDeployment`（active/last-good 指针） | 一行：`generation_id / snapshot_digest / scope / state / refcount / activated_at`；`scope` 第一阶段只有 `executor:<adapter_id>` 与 `process` |
| `ExecutionAssignmentV1` | `ExecutionAssignment` | 现有 `RunAssignmentV1` **不动**，另起一张 `run_system_bindings(memorial_id, attempt_id, snapshot_digest, generation_ids_json)`；后续若合并再合并 |

其余名词的去向：

- `Artifact` → 已有 `ArtifactRefV1` + `ArtifactStore`，不新增；
- `Capability` / `Contribution` → 是注册表重构（§3.3），不是领域对象；
- `EvolutionPolicy` → 一张 `evolution_policies(subject_key PK, mode, ...)` 表（PR-4），不是独立聚合；
- `PluginSetSpec` → 第一阶段就是 `wire_*` 装配代码本身；等第三方插件真出现再谈 spec；
- `EvaluationCampaign` → 已有 `GateEvaluator` + `universe/eval_harness`，不另起名；
- `AgentSession` → 同意报告：不引入。

**命名提醒**：项目里"快照"已被三样东西占用——位面快照（Universe）、影子快照（ADR-0011）、
Restore Point。再来一个 `SystemSnapshot` 中文只能叫"系统快照"，用户界面上必然混淆。建议在
ADR 里同时定中文 canonical 词；宫廷隐喻下一个可选方案：`SystemSnapshot`=「典制」（不可变
的整套制度），`RuntimeGeneration`=「朝」（一朝一代：新朝预热 → 登基 active → 逊位 draining
→ 退位 disposed）。是否采用由产品拍板，本文只指出冲突。

### 3.2 代际边界：进程级 + 每 run 内容快照，不做进程内 built-in 代际并存

报告 Phase 2 要求 Tool/Hook/Provider/Channel 等 built-in 全部进入 generation-scoped registry，
新旧 generation 在同一 Python 进程内并存、引用计数归零后逆序 dispose。对天枢这个
**单用户、单进程、asyncio、SQLite** 的产品，这是最重且收益最低的一段：

- 真正需要"不换代"的对象是**正在执行的 run**，而不是进程里的对象图。run 需要的保证是：
  它开始时看到的 skills/persona/policy/executor 版本，到结束都不变；
- 对**子进程执行器**（Keqing/Pi/Claude Code/Codex），OS 已经提供代际：磁盘上换了 CLI，
  已启动的进程继续跑旧二进制。缺的只是"新 run 用哪一个"的指针和引用计数；
- 对**声明式内容**（Skill/Persona/Prompt/Policy），代际 = 每个 run 在 `bind_runtime` 时冻结
  一份内容寻址的只读视图（ArtifactStore 里本来就有 Skill 包），而不是让 `SkillsLoader` 变成
  多代对象；
- 对**进程本身**（Provider/Tool/Hook 的 Python 实现），代际 = 一次带 drain 的优雅重启进入指定
  snapshot。`RunDispatcher` 的 drain 序列已存在；缺的是"启动时校验 snapshot、失败回 last-good"。

因此本文把报告的 4.1 热更新语义改写为两条：

```text
子进程执行器：stage(new release) → warm(probe + 契约验证) → activate(指针) → 新 run 取新代
              → 旧代 refcount 归零 → dispose
声明式内容：  candidate 晋升 = 写新 artifact + 原子换指针；run 在 bind_runtime 冻结视图，
              晋升不影响已开始的 run；SkillsWatcher 只失效缓存，不再等于"换代"
进程实现：    优雅重启进入 snapshot；warm-up 失败 → 回 last-good；不做进程内并存
```

报告 Phase 2 的"连续 100 次启动、切换、回滚无 contribution 泄漏或混代"退出条件，在这个
模型下变成"连续 100 次 Pi 换代无 run 混代"（PR-3）+"连续 100 次重启无 snapshot 漂移"（PR-5）。

### 3.3 ContributionHandle 是现在就能做的事，不必等 Phase 2

给现有六个注册表加 owner/disposer 大约 100 行，能立刻解决三件事：`register_command` 的
`hasattr` 挂属性、`ToolRegistry`/`ChannelRegistry` 没有 `unregister`、测试里无法干净卸载
一个插件的贡献。这是 PR-2，与代际无关。

### 3.4 硬阻塞：单一全局 canary 权威

[`evolution_repo.py:270-298`](../../../src/tianshu/storage/evolution_repo.py) `get_routable_candidate`
查询 `lifecycle='canary'` 且 **多于一个即抛 `multiple canary routing authorities`**；
`RunAssignmentV1` 每个 Memorial 只有一个 `candidate_id`。这意味着：

- 同一时刻只能有一个插件处于 canary，`keqing.pi` 在灰度时 `skill:foo` 不能灰度；
- 报告 §8 的 per-plugin `EvolutionPolicy`（每插件独立 mode/预算/canary）在路由层无法成立。

这是 Phase 5 的真实前置，报告 `migration-roadmap.md §2.2` 的重构表没有列。本文放在 PR-4。

### 3.5 Phase 1 比报告写的便宜

报告 Phase 1 要"把 `wire_*` 解析为 `builtin/default` PluginSetSnapshot、建 `legacy/default`
snapshot、在 Memorial/RunState/Evidence 三处双写"。实际：

- Evidence 已绑定 `effective_contract_hash`、`executor_manifest_hash`、
  `environment.dependency_lock_hash`、`environment_fingerprint`、`plan_revision`；
- assignment 已作为独立 artifact 挂进 `required_artifact_digests`，**同样的挂法**再挂一个
  `application/vnd.tianshu.system-snapshot.v1+json` 即可，不必改 `EvidenceSnapshotV1` 的
  schema_version；
- 双写点只需要一个：`run_dispatcher._execute` 的 `bind_runtime`（第一个受管副作用前），
  不需要碰 Memorial 与 RunState 的契约。

缺的只是四个内容指纹（skills / personas / policy rules / provider profiles）和一个总摘要。

### 3.6 Reconciler 与 ADR 数量

- 六个 Reconciler → 一个 `GenerationReconciler`，扩自现有 `EvolutionRollbackReconciler`：
  同一把锁、同一个 `reconcile_once()`，内部按 `runtime_generations.state` 分支；
- 七个 ADR → 先两个：ADR-0013「代际并存 + drain，不做进程内 reload；治理微内核不由普通
  Evolution 自动修改」、ADR-0014「每个 Memorial 在第一个受管副作用前绑定 SystemSnapshot；
  continuity 固定规则：conversation/深度 Edict 固定、scheduled root 每次选择、DAG/retry 继承
  root」。其余五项（插件状态迁移、第三方隔离 Host、auto 风险上限等）等到有代码需要它们时再写。

### 3.7 报告中的事实性小修

| 位置 | 原文 | 修正 |
|---|---|---|
| comparative-research §1.1 | "缺少 Capability seam" | executor 域已有 `ExecutorAdapterRegistry` + `ExecutorCapabilityManifestV1`；缺的是 Tool/Hook/Channel/Provider 域的统一 seam |
| target-architecture §4.2 | "handler 有 timeout、budget、熔断和 crash-loop quarantine"列为目标 | timeout 与 fail-secure 已有（`HOOK_TIMEOUTS`、`_FAIL_SECURE_HOOKS`）；缺 budget/熔断/quarantine |
| current-plugin-state §4 | 只提 `register_*` 无 disposer | `HookRegistry.unregister`、`ProviderManager.unregister` 已存在；缺的是 `ToolRegistry`、`ChannelRegistry`、`ExecutorAdapterRegistry` 三处，以及跨注册表的 owner 归属 |
| docs/impl/README.md（本轮改动） | impl 索引链到 design 目录 | 打破了 impl → impl 的既有约定，建议保留一页 `docs/impl/plugins/README.md` 只做 3 行转发 |

## 4. 推荐路线（PR 级）

> **迁移编号勘误（2026-08-25）**：本文初稿误判迁移尾为 V24；实际 live tail 是 V30（`0030_consultation_rounds`）。下文出现的 V25–V28 应读作 V31–V35；正式编号与完整阶段细化以[落地方案](../../plan/2026-08-25-self-evolving-agent-os-landing.md)为准（该方案对本文的 PR-3b/PR-4 顺序也做了对调并给出理由）。

顺序原则：**先让每个 run 能回答"我用了什么"，再让执行器能换代，再让多插件能各自灰度。**
每个 PR 独立可合、可回退，遵循 issue → 分支 → PR 流程。

### PR-1 `SystemSnapshotV1` 影子双写（≈3–4 天）

**改动面**

| 文件 | 内容 |
|---|---|
| `src/tianshu/models/system_snapshot.py`（新） | `SystemSnapshotV1(_StrictModel)`：`schema_version: Literal[1]`、`components: dict[str, str]`（key 形如 `kernel`、`executor:keqing:pi`、`skills`、`personas`、`policy_rules`、`provider_profiles`、`evolution_overlay`；value 为 64 位 hex）、`digest` = `canonical_sha256(components)`；`model_validator` 校验 digest 一致 |
| `src/tianshu/skills/loader.py`、`persona/loader.py`、`tools/policy_rules.py`、`providers/registry.py` | 各加一个 `content_digest() -> str`：对已加载文件按相对路径排序后 canonical hash（persona/skills）；对规则/profile 列表做 `canonical_sha256`。只读，不改现有行为 |
| `src/tianshu/evolution/system_snapshot.py`（新） | `SystemSnapshotResolver`：装配期从 `app.state` 收集组件（kernel = `tianshu_version + dependency_lock_hash`，复用 `evidence/service.py:664` 的 lock hash 计算；executor = 每个 adapter 的 `canonical_sha256(manifest)` + `probe()` 中的版本字段）；`resolve_for_run(assignment)` 追加 `evolution_overlay = overlay_digest`（legacy assignment 时省略该 key） |
| `src/tianshu/storage/migrations.py` | V25：`system_snapshots(digest PK, components_json, first_seen_at)`；`run_system_bindings(memorial_id, attempt_id, snapshot_digest, created_at, PRIMARY KEY(memorial_id, attempt_id))`。沿用 `_*_STATEMENTS + _*_CHECKSUM` 模式并登记 callback 指纹 |
| `src/tianshu/storage/evolution_repo.py` | `insert_system_binding(connection, memorial_id, attempt_id, snapshot)`：先 `INSERT OR IGNORE system_snapshots`，再插 binding；同 attempt 重复写入必须等值，否则 `EvolutionAssignmentConflict` |
| `src/tianshu/universe/router.py` `bind_runtime` | 在现有 UoW 内解析并写入 binding；`EvolutionRuntimeContext` 增加 `system_snapshot: SystemSnapshotV1`（`runtime_context.py`） |
| `src/tianshu/evidence/service.py` ~L911 | 关闭 Evidence 时读取该 Memorial 最后一个 attempt 的 binding，以 artifact `application/vnd.tianshu.system-snapshot.v1+json` 挂入 `required_artifact_digests`（与 assignment artifact 同一段代码模式） |
| `src/tianshu/gateway/…`（可选） | `GET /api/edicts/{id}` 详情投影 `system_snapshot_digest`，Web 暂不展示 |

**不改**：`RunAssignmentV1`、`EvidenceSnapshotV1` schema_version、任何 active 行为。

**退出条件（转为测试）**

1. 同一进程内两次 `resolve()` digest 相同；改动任一 SKILL.md 后 digest 变化且只有 `skills` 组件变化；
2. 每个新 Memorial 的 Evidence 独立重算 snapshot 与 binding 等值（`tests/evidence/`）；
3. 同一 Memorial 的两次 attempt 若 snapshot 不同，binding 表记录两行，Evidence 使用最后一行，
   并在 `SystemAudit` 记一条 `system_snapshot_drift`（影子期只记不拒）；
4. 迁移回退：V25 表可整体删除，其余路径不受影响（`tests/evolution/test_evolution_migration_schema.py` 模式）。

### PR-2 `ContributionHandle`（≈1–2 天）

| 文件 | 内容 |
|---|---|
| `src/tianshu/plugins/contribution.py`（新） | `@dataclass(frozen=True) ContributionHandle(owner: str, kind: Literal["tool","hook","channel","provider","skill","command"], name: str, dispose: Callable[[], None])` |
| `tools/registry.py`、`notifier/channel_registry.py` | 补 `unregister(name)`；`ExecutorAdapterRegistry.replace` 改为 `unregister + register` 组合或直接删掉（PR-3 会换成 generation API） |
| `plugins/api.py` | `register_*(owner=...)` 返回 `ContributionHandle`；`_contributions: dict[owner, list[handle]]`；新增 `dispose_owner(owner)` 逆序调用 dispose；`register_command` 改为正式字段 |
| `tests/test_plugin_api.py` | 注册 → dispose_owner → 注册表恢复原状；重复 name 冲突给结构化诊断 |

不引入 generation 概念，只补 owner/disposer。

### PR-3 Pi 执行器代际切片（≈2 周，可拆 3a/3b）

**3a 代际与引用计数**

| 文件 | 内容 |
|---|---|
| `src/tianshu/models/runtime_generation.py`（新） | `RuntimeGenerationV1`：`generation_id`（`rg-` + uuid4，非内容摘要）、`scope`（`executor:keqing:pi`）、`release_digest`（= `canonical_sha256({manifest, cli_version, argv_shape})`）、`state ∈ {staged, warming, ready, active, draining, disposed, failed}`、`refcount`、时间戳 |
| `migrations.py` V26 | `runtime_generations` 表 + `generation_pointers(scope PK, active_generation_id, last_good_generation_id)` |
| `executor/adapters/__init__.py` | `ExecutorAdapterRegistry` 增加 `stage(adapter) -> gen`、`warm(gen)`（`probe()` + Pi 离线 RPC 契约验证，失败 → `failed`，指针不动）、`activate(gen)`（仅 `ready` 可切；旧 active → `draining`）、`prepare()` 对 active 代 `refcount += 1` 并把 `generation_id` 写进 `PreparedExecutor`、`release(run_id)`、`draining` 且 refcount==0 → `disposed` |
| `run_system_bindings` | 加列 `generation_ids_json`（PR-1 的表，V26 一并 ALTER） |
| `application/managed_run_ingress.py`、`edicts.py`、`scheduled_runs.py` | continuity 规则：conversation/深度 Edict 的 follow-up 读 root Memorial 的 binding 复用其 `generation_id`；若该代已 `disposed` → fail closed `generation_retired`（等待 Decision 显式换代）；cron/interval 每次 fire 取当时 active；DAG 子节点与基础设施重试继承 root |
| `evolution/reconciler.py` → `GenerationReconciler` | 在现有 `reconcile_once()` 里追加：`draining && refcount==0 → disposed`；进程重启后把上一进程遗留的 `warming` 置 `failed`、`active` 保持并 refcount 归零重算 |

**3b 版本漂移 → Candidate → 晋升**

| 文件 | 内容 |
|---|---|
| `models/evolution_candidate.py` | `CandidateKind.EXECUTOR = "executor"`（枚举新增；V27 迁移登记 callback 指纹） |
| `evolution/promotion.py` → 拆出 `evolution/adapters/executor.py` | `ExecutorPromotionAdapter.activate` = `stage → warm → activate`，返回 `ActivationReceiptV1`；`rollback` = `activate(last_good)`；`verify_rollback` = 指针等于 last_good 且新代 `disposed/failed` |
| 客卿馆状态接口 | 检测到 Pi 版本漂移时，不再只显示"待兼容验证"，而是创建 `subject_key=executor:keqing:pi` 的 `PROPOSED` Candidate（`source_channel=SYSTEM`），走现有 Gate → canary → Decision 路径 |
| Evidence | `executor_manifest` 已在；补 `generation_id` 进 system-snapshot artifact |

**退出条件**：活跃长任务换代期间不换 executor（故障注入：换代中途 kill 新 Pi，旧任务不受影响）；
新代 warm 失败 active/last-good 不变；旧任务完成后旧代 `disposed`；rollback 在
`rollback_slo_seconds` 内恢复指针。

### PR-4 按 subject 路由 + per-subject EvolutionPolicy（≈1.5 周）

| 文件 | 内容 |
|---|---|
| `storage/evolution_repo.py` | `get_routable_candidate(connection, subject_key)`；不同 `subject_key` 允许并存 canary，同 subject 仍 fail closed |
| `models/run_assignment.py` | 新增 `RunAssignmentSetV1(memorial_id, assignments: tuple[RunAssignmentV1, ...], set_hash)`；现有单条 assignment 成为 set 的退化形式，旧行照读 |
| `universe/router.py` | `assign_current` 对每个有 canary 的 subject 独立分桶（`allocation_seed_id` 已按 candidate 区分，bucket 天然不同） |
| `evolution/runtime_context.py` | `overlays: dict[subject_key, payload]` |
| `migrations.py` V28 | `evolution_policies(subject_key PK, mode ∈ {frozen,manual,canary}, allowed_surfaces_json, max_canary_basis_points, approval, budget_json, version)`；默认无行 = `manual` |
| `candidate_service.py` / `promotion.py` | `propose`：`frozen` → 拒绝；`start_canary`：mode≠`canary` → 拒绝，bp 取 `min(contract, policy)`；`promote` 已要求 Decision，不变 |
| Web 天工院 | 每插件一行：enabled / pinned digest / evolution mode 三个独立开关（报告 §8 的 YAML 只在 API 层） |

`auto` 模式不实现，`Literal[False]` 保持。

### PR-5 进程级 snapshot 重启与 last-good（≈3 天）

- `tianshu serve --system-snapshot <digest>`（缺省 = `generation_pointers` 中 `scope=process` 的 active）；
- 启动时 `SystemSnapshotResolver.resolve()` 与目标 digest 不等 → 若 `TIANSHU_SNAPSHOT_STRICT=1`
  则退出并提示 last-good，否则记 `system_snapshot_drift` 并继续（影子期默认）；
- `GenerationReconciler` 处理 `scope=process` 的 active/last-good 指针；
- 退出条件：连续 100 次重启 digest 稳定；改一个 provider profile 后 digest 变化且只有该组件变化。

### 之后（按报告 Phase 4–6，不在本文展开）

叶子能力迁入 Capability seam 的顺序同意报告：Provider/Channel → 其他 Executor → Tool →
Skill/Persona/Memory → 声明式 UI。每迁一种补 owner、冲突规则、health、状态 schema。
第三方 Process/Wasm host、签名/SBOM/TUF、`auto` 模式仍然是 Phase 6，前置条件不变。

## 5. 对报告文档本身的处理建议

建议本目录随 `feat/plugin-v1` 合入，合入前做以下最小修订（均为文档改动）：

1. `migration-roadmap.md`：Phase 2 与 Phase 3 对调；§2.2 重构表增加"单一全局 canary 权威 →
   按 subject 路由"；§3 每阶段退出条件对齐本文 §4；
2. `domain-and-governance.md §1`：在 13 个术语表后加一小节"第 1–3 阶段最小代码词汇"，只列
   `SystemSnapshotV1 / RuntimeGenerationV1 / run_system_bindings`，其余标 deferred；
3. `target-architecture.md §3`：六个 Reconciler 改为"逻辑职责，实现为一个 `GenerationReconciler`"；
   §4.1 热更新语义按本文 §3.2 拆为三类；
4. `comparative-research.md §1.1`：补 `ExecutorAdapterRegistry` 已是 Capability seam；
5. `migration-roadmap.md §6`：7 个 ADR 缩为先写 2 个（ADR-0013 / ADR-0014），其余列为 deferred；
6. 恢复 `docs/impl/plugins/README.md` 为 3 行转发页，保持 impl 索引不跨目录；
7. `source-map.md §7` 的三条文档漂移（`CONTEXT.md` 版本号、Universe"人工切换"表述、"运行时 SOUL
   演化"）应在合入同一 PR 里顺手修，不要再留一轮。

## 6. 验收标准对照

报告 `migration-roadmap.md §4` 列了 12 条。本文保留全部，并按 PR 标注何时可验：

| 标准 | 可验 PR |
|---|---|
| 每个 Memorial 绑定唯一、完整、不可变的 SystemSnapshot | PR-1（影子）→ PR-5（严格） |
| 同一连续交互不混用两个 RuntimeGeneration | PR-3 |
| 新 generation 失败时 active 和 last-good 不变 | PR-3 |
| Canary 按 continuity sticky，分桶可独立重算 | 已有（`test_routing_distribution.py`）+ PR-4 多 subject |
| Replay 不重新调用模型或外部 Tool | 现有 Effect journal；不在本文范围 |
| 单个插件可保持 enabled 同时 frozen | PR-4 |
| Candidate 无法修改自己的 Evaluator/权限/Promotion Policy | 现有 `EvolutionContractV1` 锁定 + PR-4 policy 表只允许 Decision 改 |
| 第三方插件不能直接取得全局 Storage/secret | Phase 6 |
| 状态不可安全回退时自动晋升 fail closed | `Literal[False]` 已成立 |
| Evidence 绑定实际模型、Prompt、PluginSet、策略、Effect、成本、环境 | PR-1 补齐 skills/persona/policy/provider 四项 |
| 旧 generation 引用归零前不销毁 | PR-3 |
| 回滚满足 SLO 且有故障注入测试 | 现有 `test_rollback_fault_matrix.py` + PR-3 executor 矩阵 |
