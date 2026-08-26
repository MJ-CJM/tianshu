# 评审与实现计划（2026-08-24）

> **文档性质：对本目录 codex 报告的独立评审 + 可直接开工的 PR 级实现计划。**
> 评审基线：本目录 9 篇文档 + 工作树 `88462b2a`（`feat/plugin-v1` 未提交状态）。
> 所有"当前源码事实"均在本轮重新读代码核对，不转引报告结论。
>
> **后续勘误（2026-08-26）**：以上是历史评审基线。P4a 已由 PR #107 合入（merge
> `b94d4846`，CI 6/6）；P4b 已由 PR #109 合入 `feat/plugin-v1`（merge `a8a03071`）。P4b 权威 reader
> 为无 subject 参数的 plural `get_routable_candidates()`；V33 policy 当前字段不包含
> allowed_surfaces/approval/budget；runtime key 为 `kind.value:subject_key`。Web 只读展示
> availability/source/curator protection，只编辑 mode 与 max canary basis points；`pinned`
> 不是版本 pin。sticky 必须来自持久 assignment set；manual 的 Decision override 尚未实现。
> P5 已由 PR #111 合入 `feat/plugin-v1`。下文“只有 Skill adapter、其余映射
> `UnavailablePromotionAdapter`”是 `88462b2a` 的历史事实；P5 后 EXECUTOR 始终映射
> `ExecutorPromotionAdapter`，`executor_generation_enabled` 只关闭新的前向演化，保留 recovery、
> rollback 与 reconcile。P6 已由 PR #114 合入 `feat/plugin-v1`（merge `8f32cc4c`），
> 实现 process snapshot generation 与 strict binding。P7 当前开发完成、PR 待创建；
> 实现仅限 Skills，且无数据迁移。

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
| `RuntimeReleaseV1` + `RuntimeGenerationV1` | executor runtime release、`RuntimeGeneration`、`PluginInstance`、active/last-good 指针 | release 行保存不可变 canonical material；generation 行保存 `generation_id / release_digest / scope / state / version / activated_at`；不持久化 refcount |
| `ExecutionAssignmentV1` | `ExecutionAssignment` | 现有 `RunAssignmentV1` **不动**；V31 `run_system_bindings` 保存可选 snapshot shadow，V32 `run_generation_bindings` 独立保存 exact-attempt generation marker；后续若形成完整聚合再合并 |

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
              → exact attempt 与 OPEN continuity 引用释放且非 last-good → dispose
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

- 六项逻辑职责 → 一个后台 control-plane tick：保留现有 `EvolutionRollbackReconciler`，新增
  独立 `GenerationReconciler`，各自持锁并顺序执行；
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

> **迁移编号基线（2026-08-25）**：实际 live tail 是 V30（`0030_consultation_rounds`）。新迁移依次为 V31 `0031_system_snapshots`、V32 `0032_runtime_generations`、V33 `0033_evolution_policies`、V34 `0034_run_subject_assignments`、V35 `0035_executor_candidate_kind`；正式阶段细化以[落地方案](../../plan/2026-08-25-self-evolving-agent-os-landing.md)为准。PR-4 的 per-subject 权威必须先于 PR-3b 的 EXECUTOR Candidate 全链路。

顺序原则：**先让每个 run 能回答"我用了什么"，再让执行器能换代，再让多插件能各自灰度。**
每个 PR 独立可合、可回退，遵循 issue → 分支 → PR 流程。

### PR-1 `SystemSnapshotV1` 影子双写（≈3–4 天）

**改动面**

| 文件 | 内容 |
|---|---|
| `src/tianshu/models/system_snapshot.py`（新） | `SystemSnapshotV1(_StrictModel)`：`schema_version: Literal[1]`、`components: dict[str, str]`（key 形如 `kernel`、`executor:keqing:pi`、`skills`、`personas`、`policy_rules`、`provider_profiles`、`evolution_overlay`；value 为 64 位 hex）、`digest` = `canonical_sha256(components)`；`model_validator` 校验 digest 一致 |
| `src/tianshu/skills/loader.py`、`persona/loader.py`、`tools/policy_rules.py`、`providers/registry.py` | 各加一个 `content_digest() -> str`：对已加载文件按相对路径排序后 canonical hash（persona/skills）；对规则/profile 列表做 `canonical_sha256`。只读，不改现有行为 |
| `src/tianshu/evolution/system_snapshot.py`（新） | `SystemSnapshotResolver`：装配期从 `app.state` 收集组件（kernel = `tianshu_version + dependency_lock_hash`，复用 `evidence/service.py:664` 的 lock hash 计算；executor = 每个 adapter 的 `canonical_sha256(manifest)` + `probe()` 中的版本字段）；`resolve_for_run(assignment)` 追加 `evolution_overlay = overlay_digest`（legacy assignment 时省略该 key） |
| `src/tianshu/storage/migrations.py` | V31 `0031_system_snapshots`：`system_snapshots(snapshot_digest PK, schema_version, components_json, first_seen_at)`；`run_system_bindings(memorial_id, attempt_id, snapshot_digest, generation_ids_json DEFAULT '[]', created_at, PRIMARY KEY(memorial_id, attempt_id))`。沿用 `_*_STATEMENTS + _*_CHECKSUM` 模式并登记 callback 指纹；V31 形状冻结不 ALTER，P3 另建 generation authority |
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
4. 行为回退通过关闭双写开关或 revert 消费代码完成；迁移 append-only，V31 两表留存且无消费者时无害，不通过删表回退。

### PR-2 `ContributionHandle`（≈1–2 天）

| 文件 | 内容 |
|---|---|
| `src/tianshu/plugins/contribution.py`（新） | `@dataclass(frozen=True) ContributionHandle(owner: str, kind: Literal["tool","hook","channel","provider","skill","command"], name: str, dispose: Callable[[], None])` |
| `tools/registry.py`、`notifier/channel_registry.py` | 补 `unregister(name)`；`ExecutorAdapterRegistry.replace` 改为 `unregister + register` 组合或直接删掉（PR-3 会换成 generation API） |
| `plugins/api.py` | `register_*(owner=...)` 返回 `ContributionHandle`；`_contributions: dict[owner, list[handle]]`；新增 `dispose_owner(owner)` 逆序调用 dispose；`register_command` 改为正式字段 |
| `tests/test_plugin_api.py` | 注册 → dispose_owner → 注册表恢复原状；重复 name 冲突给结构化诊断 |

不引入 generation 概念，只补 owner/disposer。

### PR-3a Pi 执行器代际、attempt lease 与 continuity retention（≈1.5–2 周）

| 文件 | 内容 |
|---|---|
| `src/tianshu/models/runtime_generation.py`（新） | `RuntimeReleaseV1` 保存完整可重建 executor material；`RuntimeGenerationV1` 保存 `generation_id`（`rg-` + uuid4，非内容摘要）、`scope`、`release_digest`、七态、CAS version 与时间戳；不存 refcount |
| `migrations.py` V32 `0032_runtime_generations` | 不可变 `runtime_generation_releases`、`runtime_generations`、不可变 `runtime_generation_journal`、复合 scope FK 的 `generation_pointers`，以及独立 insert-once `run_generation_bindings`，合计五表；升级按 system-copy / native-empty / ambiguous-Pi-unresolved 三分回填 |
| `generation_controller.py` + `executor/adapters/__init__.py` | Controller 独占 stage/warm/activate/rollback/recovery；Registry 保管同代 single/session bundle、按 exact `attempt_id` lease、同锁 selection/manifest；Dispatcher 唯一 release |
| `run_generation_bindings` + `run_system_bindings` | 前者是 P3 exact-attempt generation 权威，空选择也写 `bound []`；后者保持 P1 snapshot shadow，只作 V31 fallback；两者同在必须一致，不修改 V31 表形状 |
| `application/managed_run_ingress.py`、`edicts.py`、`scheduled_runs.py` | continuity 规则：conversation/深度 Edict 的 follow-up 读 root Memorial 的 binding 复用其 `generation_id`；若该代已 `disposed` → fail closed `generation_retired`（等待 Decision 显式换代）；cron/interval 每次 fire 取当时 active；DAG 子节点与基础设施重试继承 root |
| `evolution/reconciler.py` + control-plane wiring | 新 `GenerationReconciler` 与既有 rollback reconciler 顺序组合、各自持锁；draining 仅在无 exact-attempt/OPEN-continuity 引用且非 active/last-good 时 disposed；重启按 release material 重建 |

**退出条件**：活跃长任务换代期间不换 executor（故障注入：换代中途 kill 新 Pi，旧任务不受影响）；
新代 warm 失败 active/last-good 不变；旧 task、OPEN continuity 与 last-good 均释放后旧代才 `disposed`；rollback 在
`rollback_slo_seconds` 内恢复指针。

### PR-4 按 subject 路由 + per-subject EvolutionPolicy（≈1.5 周）

| 文件 | 内容 |
|---|---|
| `storage/evolution_repo.py` | 无 subject 参数的 `get_routable_candidates(connection)` 返回权威多值集合；不同 `(kind, subject_key)` 允许并存 canary，同 pair 仍 fail closed |
| `storage/migrations.py` V34 `0034_run_subject_assignments` | 新增不可变 per-subject assignment 表；现有 `RunAssignmentV1` 与旧表不改。fresh root singleton 保持旧表投影与旧 Evidence artifact 逐字节兼容，同时有意新增 V34 set 与 assignment API 字段 |
| `universe/router.py` | `assign_current` 对每个有 canary 的 subject 独立分桶（`allocation_seed_id` 已按 candidate 区分，bucket 天然不同） |
| `evolution/runtime_context.py` | `overlays` / `payloads` 以 `kind.value:subject_key` 为 key 并深冻结；N>1 时 singular accessor 返回 `None` |
| `storage/migrations.py` V33 `0033_evolution_policies` | 实际冻结列为 `subject_key PK, kind, mode ∈ {frozen,manual,canary}, max_canary_basis_points, version, created_at, updated_at`；无行时 Skill 祖父化为 `canary`，其余 kind 为 `manual`；allowed surfaces / approval / budget 留在目标态 |
| `candidate_service.py` / `promotion.py` | `propose`：`frozen` → 拒绝；`start_canary`：mode≠`canary` → 拒绝，bp 取 `min(contract, policy)`；`promote` 已要求 Decision，不变 |
| Web 天工院 | P4b 实际交付：availability/source/curator protection 只读；mode 与 max canary basis points 严格 CAS。无 enabled/version-pin 开关 |

`auto` 模式不实现，`Literal[False]` 保持。

### PR-3b / P5 版本漂移 → Candidate → 晋升（在 PR-4 之后）

| 文件 | 内容 |
|---|---|
| `models/evolution_candidate.py` | `CandidateKind.EXECUTOR = "executor"`；V35 `0035_executor_candidate_kind` 扩展数据库 kind CHECK，并登记 callback 指纹 |
| `storage/executor_generation_authority_repo.py` + V35 | 持久化每次 canary epoch 的精确 `candidate_id + candidate_version + release_digest → generation_id` 授权及不可变 journal；有效授权是 READY 的 recovery/retention root，撤销后允许下一 epoch 建新代；无/错/歧义授权一律 fail closed |
| `evolution/promotion.py` → 拆出 `evolution/adapters/executor.py` | `start_canary` 经 `GenerationController.stage → warm` 后建立 durable authority；`activate` 只激活已授权 READY 代，不二次 stage；`rollback` 走 controller last-good 并撤权；`verify_rollback` 同时验证 pointer、候选代终态和授权撤销 |
| 客卿馆状态接口 | 检测到 Pi 版本漂移时，不再只显示“待兼容验证”，而是创建 `subject_key=executor:keqing:pi` 的 `PROPOSED` Candidate（`source_channel=SYSTEM`），走 Gate → per-subject canary → Decision 路径 |
| Evidence | `executor_manifest` 已在；补 `generation_id` 进 system-snapshot artifact |

本段后移保证 EXECUTOR Candidate 从产生起就在 per-subject 世界中，不占用旧的全局唯一 canary 槽。
`executor_generation_enabled=true` 与 `system_snapshot_enabled=false` 在配置期互斥，保持 P1 关闭
snapshot 时 system snapshot/binding 零写入的兼容语义（P3 exact marker 仍写 `bound []`），同时
阻断之后才激活新代的生产路径。

### PR-5 / P6 进程级 snapshot 重启与 last-good（≈3 天）

**状态**：已由 PR #114 合入 `feat/plugin-v1`（merge `8f32cc4c`）。

- `tianshu serve --system-snapshot <digest>`（缺省 = `generation_pointers` 中 `scope=process` 的 active）；
- 启动时 `SystemSnapshotResolver.resolve()` 与目标 digest 不等 → 若 `TIANSHU_SNAPSHOT_STRICT=1`
  则退出并提示 last-good，否则记 `system_snapshot_drift` 并继续（影子期默认）；
- 专用 `ProcessSnapshotBootstrap` 在 run/scheduler 启动前处理 `scope=process` 的 active/last-good
  指针；executor `GenerationReconciler` 显式只处理 Pi scope，避免 process 材料进入 Pi registry；
- 退出条件：连续 100 次重启 digest 稳定；改一个 provider profile 后 digest 变化且只有该组件变化。

### P7 Skills 每 run 冻结视图（≈4 天–1 周）

**状态**：Issue #115 开发完成，PR 待创建；聚焦回归已通过，完整 PR/CI
门禁仍以待创建 PR 的实际检查为准。

| 改动面 | 当前落点 |
|---|---|
| 不可变模型 | `FrozenSkillV1` / `FrozenSkillsViewV1` / `FrozenContentViewsV1`；当前容器仅含 Skills |
| loader/context | 每轮通过目录 fd 捕获来源、内容与 overlay；搜索路径祖先只比较 `dev/ino/mode` identity，搜索根及捕获树内文件/目录比较 `dev/ino/mode/size/mtime_ns/ctime_ns` witness，既拒绝路径交换又不受树外 sibling churn 误伤；injected generation、按名排序的注入 Skill 均进入比较，连续两次全量 capture 一致才接受，三轮持续 churn 后 fail closed；搜索路径、成员、嵌套资源 symlink 均拒绝。requirements/max-size/load_all/metadata/injected/fallback 与 live 同义，requirements 环境 eligibility 进入 source identity。selected base absent 只显露低层，challenger/unknown absent 保留历史 hide-lower；详情、列表、index、always、all、tool 共用 task-local 视图，嵌套/异常/取消恢复外层 context |
| router/dispatcher/scheduler | legacy、单 subject、多 subject 均覆盖，每个绑定阶段最多冻结一个已验证 view。`off` 不调工厂；`shadow` 构建、在 snapshot 身份可用时比对，但读 live；`enforce` 绑定视图，已解码 Skills 身份缺失、视图构建失败或摘要漂移归类为 `skills_view_unavailable`。prebind 仅在 audit+outbox 记录成功时登记 caller UoW post-commit failure；scheduled fire 已提交则按 durable cursor/root 收口并显式唤醒 reconciler，证据提交前失败整笔回滚且不推进 cursor/清 initial root。同-key marker 仅让 claimable attempt 重验，成功写 `skills_view_binding_recovered`，终态精确重放不重冻；默认 UoW 路径不变 |
| 身份与审计 | frozen view 的 `source_digest` 与 run 的 SystemSnapshot `skills` 组件对账；只有真实 digest mismatch 原子持久 succeeded `skills_view_drift`；视图工厂、整体捕获、模型校验等致命失败，以及 enforce 下已解码 Skills 身份缺失，原子持久 failed `skills_view_binding_failed`。shadow 身份缺失只跳过对账；单个 SKILL.md 解析异常沿用 warning + skip。持久 snapshot/binding 结构损坏仍沿用 P6 的 `system_snapshot_unavailable`（strict）或 `generation_binding_unavailable`；不记内容/原始错误，不静默配对旧 snapshot 与新 view |
| 失效/absence 边界 | watcher 统一使用 polling observer，避免 macOS 原子交换时的 FSEvents 崩溃；P7 callback 只 invalidate + notify，legacy 无 callback 仍 reload。promotion cache invalidator 无论 frozen flag 开关均装配，Skill 成功 activate/rollback、desired/no-op 重试及 `verify_rollback` 命中均 invalidate。新的 absent candidate 在 start-canary/promote/activate 以 `skill_absence_requires_durable_tombstone` 拒绝 |

P7 无数据迁移，仅保证同进程 mid-run 稳定。prebind/重启时若旧 snapshot 不能从
当前 Skills 字节重建，shadow 审计后读 live，enforce 失败关闭。跨重启耐久
回放旧内容需要 artifact-backed `skills_view`；durable global Skill tombstone 也需要新的持久
治理对象，两者因持久化、retention、配额、secret scanning 与 rollback 契约扩展而延期到
P7b。Persona/Prompt/Provider 冻结不在本阶段。
因 prebind 与 dispatch 可能跨进程，生产路径是“prebind 身份捕获一次 + dispatch
执行重建一次”，每阶段最多 freeze 一次，而不是跨阶段复用同一内存 view；无 prebind 的 run
只冻结一次。
稳定 witness 依赖本地 POSIX 普通写者下可信的 ctime/stat 变化，不宣称抵抗特权写者或
ctime 不可靠的文件系统。

### 之后（按报告 Phase 4–6，不在本文展开）

叶子能力迁入 Capability seam 的顺序同意报告：Provider/Channel → 其他 Executor → Tool →
Skill/Persona/Memory → 声明式 UI。每迁一种补 owner、冲突规则、health、状态 schema。
第三方 Process/Wasm host、签名/SBOM/TUF、`auto` 模式仍然是 Phase 6，前置条件不变。

## 5. 对报告文档本身的处理建议

本目录采用以下最小修订口径（均为文档约束，不代表运行能力已实现）：

1. `migration-roadmap.md`：Phase 2 与 Phase 3 已对调；§2.2 重构表增加"单一全局 canary 权威 →
   按 subject 路由"；§3 每阶段退出条件对齐本文 §4；
2. `domain-and-governance.md §1`：在目标术语表后增加"第 1–3 阶段最小代码词汇"，只列
   `SystemSnapshotV1 / RuntimeReleaseV1 / RuntimeGenerationV1 / run_system_bindings / run_generation_bindings`，其余标 deferred；
3. `target-architecture.md §3`：六项 Reconciler 职责是逻辑职责，实现为一个 `GenerationReconciler`；
   §4.1 热更新语义按本文 §3.2 拆为三类；
4. `comparative-research.md §1.1`：注明 `ExecutorAdapterRegistry` 已是 Executor Capability seam；
5. `migration-roadmap.md §6`：只先写 2 个 ADR（ADR-0013 / ADR-0014），其余列为 deferred；
6. `docs/impl/plugins/README.md` 保持为 3 行转发页，impl 索引不跨目录；
7. `source-map.md §7` 记录同批收敛的三条文档漂移：旧版本号、Universe“人工切换”和“运行时 SOUL 演化”过度承诺。

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
