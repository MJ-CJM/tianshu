# 平行位面（universe）实现现状

**相关设计**：[../../design/universe/](../../design/universe/)

> Legacy 快照、worktree 和评估代码位于 `src/tianshu/universe/`；当前受治理 Candidate
> 权威位于 `src/tianshu/evolution/`、`models/run_assignment.py` 与
> `gateway/evolution_api.py`。两者不能互相冒充。

## 1. 模块清单（`src/tianshu/universe/`）

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `model.py` | `Universe`(frozen dataclass)、`UniverseStatus`、`UniverseOrigin` | 位面数据契约；`to_row` / `from_row` 与 DB 互转 |
| `store.py` | `UniverseStore` | 行为快照落盘/还原：`snapshot_live` / `branch_from` / `restore_to_live` / `read_manifest` / `remove` |
| `code_store.py` | `CodeVariantStore` | 代码层 git worktree 生命周期：`branch_code_variant` / `diff` / `gc_worktree` / `restore_worktree` / `remove` |
| `manager.py` | `UniverseManager` | Legacy 编排：`ensure_genesis` / `branch` / `diff` / `archive` / `restore` / `delete` / `code_diff`；`switch` / `rollback` / `promote_code_variant` 固定抛 `promotion_service_required` |
| `router.py` | `ChallengerRouter` | 确定性 canary 分桶、不可变 `RunAssignmentV1` / effective overlay 持久化与运行期绑定 |
| `evolver.py` | `UniverseEvolver`、`EvolveResult` | 演化：`run`（人格路径）/ `propose_code_variant`（代码路径） |
| `fitness.py` | `compute_fitness` | 五维加权适应度（纯函数） |
| `mutator.py` | `apply_mutation`、`parse_persona_target` | 人格文件变异落地（SOUL.md/ROLE.md） |
| `diagnostician.py` | `Diagnostician` | 太医：失败 memorial + 已试假设 → 演化域内代码改进假设清单，供 `auto_propose_codes` 消费 |
| `code_mutator.py` | `CodeMutator` | worktree 内代码改写 + 演化域 allowlist + traversal-safe |
| `gate.py` | `Gate`、`GateResult` | 三关门禁：compileall / import / pytest |
| `sandbox.py` | `SandboxRunner`、`SandboxHandle`、`SandboxError` | 受管子进程拉起 + 健康检查 + wall timeout + 进程组收敛 + DB/WAL/SHM 清理 |
| `eval_harness.py` | `EvalHarness` | 回放评估集 → `compute_fitness` 打分 |

受治理链的关键文件：

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `evolution/candidate_service.py` | `CandidateService` | 五类 Candidate 的来源校验、artifact/provenance 和 effective payload 解析 |
| `evolution/promotion.py` | `PromotionService` | canary / promote / rollback 的唯一 lifecycle 与 routing 写权威；幂等 journal、认证、Gate 绑定和 effect receipt |
| `models/run_assignment.py` | `RunAssignmentV1`、`EffectiveEvolutionOverlayV1` | 每个 Memorial 的不可变路由与实际资源归因 |
| `gateway/evolution_api.py` | `evolution_router` | Candidate、Gate、assignment 查询及受治理 mutation HTTP 入口 |

`SandboxRunner` 是执行生命周期边界，不是强沙箱证明：`trusted-local` 不提供内存、
CPU、文件系统或网络强隔离；`secure-remote` 没有受验证后端时 fail-closed。

## 2. 落盘布局

| 路径 | 内容 |
|---|---|
| `~/.tianshu/universes/{id}/personas/` `skills/` `manifest.json` | 行为层位面快照（全量拷贝 + config JSON） |
| `~/.tianshu/universes/worktrees/{id}/` | 代码变体 worktree（git 分支 `universe/<id>`） |
| `~/.tianshu/universes/worktrees/_meta/{id}.json` | 代码变体 sidecar：`{branch, start_ref}` |

## 3. 数据库（`storage/universe_repo.py` + `storage/evolution_repo.py`）

| 表 / 列 | 说明 |
|---|---|
| `universes` | `id / name / parent_universe_id / status / origin / mutation_reason / description / fitness_json / code_ref / created_at`（建表见 `storage/schema.py:224`） |
| `memorials.universe_id` | Legacy Universe 兼容投影，不是当前 canary 归因权威 |
| `variant_eval_runs` | 代码变体每次评估记录，含 `baseline_json`（配对评估的基线分），按 `universe_id` 建索引（`storage/schema.py:249`） |
| `evolution_candidates` / `evolution_gate_snapshots` | Candidate 身份、版本、Gate 与 artifact/evidence 绑定 |
| `evolution_routing_allocations` | `PromotionService` 独占的 canary allocation 与 routing version |
| `run_evolution_assignments` | 每个 Memorial 一次写入的 assignment、selected ref 与 overlay digest |
| `evolution_lifecycle_journal` / `evolution_promotion_journal` | append-only lifecycle、意图、effect 与完成收据 |

主要方法：`save_universe` / `get_universe` / `list_universes` / `get_champion_universe` / `set_universe_status` / `update_universe_fitness` / `delete_universe` / `universe_memorial_stats` / `save_variant_eval_run` / `list_variant_eval_runs`。

## 4. 装配

`bootstrap/wiring_skills.py::wire_evolution_services()` 在 Universe 模块之前装配 governed
Candidate 权威：

```text
CandidateService + GateEvaluator
PromotionService(adapter_resolver)
  → production: SkillPromotionAdapter；其余 CandidateKind: UnavailablePromotionAdapter
ChallengerRouter(payload_resolver=CandidateService.resolve_effective_payload_current)
  → EdictApplicationService
```

随后 `bootstrap/wiring_universe.py::wire_universe()` 装配 Legacy Universe 与评估链，并
复用已存在的 `ChallengerRouter`：

```text
UniverseStore(root=~/.tianshu/universes, live_personas/live_skills)
CodeVariantStore(repo_root, worktrees_root)
UniverseManager(storage, store, persona_loader, skills_loader,
                config_snapshot, config_apply, code_store, challenger_router)
  → 若 parallel_universe_enabled 或已有 champion：update_agent_config(enabled=True) + ensure_genesis()
  → executor.set_universe_manager(universe_manager)
Gate / SandboxRunner / EvalHarness / CodeMutator → Diagnostician → UniverseEvolver
  → event_bus 绑定，app.state.universe_evolver
RunDispatcher 复用 ChallengerRouter
```

config 的 snapshot/apply 是注入回调（`_universe_config_snapshot` / `_universe_config_apply`），解耦 manager 与 ConfigManager 细节。

## 5. 核心流程

### 5.1 每次运行的受治理归因

- `EdictApplicationService` / managed ingress 在创建 Memorial 的同一事务内调用
  `ChallengerRouter.assign_current`。无 routable canary 时写
  `LegacyRunAssignmentV1`；有 canary 时按确定性 bucket（非 demo profile 使用 HMAC）固化 champion/candidate
  selected ref，并写 `RunAssignmentV1` + effective overlay。
- `RunDispatcher` claim attempt 后调用 `bind_runtime(memorial_id)`，重新校验 artifact 与
  overlay digest，再把 payload 绑定到 task-local context。当前 Skill loader 会消费该
  overlay；坏 assignment 或 payload 解析失败时执行 fail-closed。
- `GET /api/evolution/runs/{memorial_id}/assignment` 按任务 owner 返回 assignment 与
  `effective_overlay`，用于证明实际选择；`memorials.universe_id` 只保留 legacy 投影。

### 5.2 Legacy live mutation 已退役

`manager.switch`、`manager.rollback`、`manager.promote_code_variant` 不再执行
`restore_to_live`、状态翻转或代码部署，均固定抛 `promotion_service_required`。对应旧
`/api/universes/{id}/switch` 与 `/promote-code` 固定返回 409
`promotion_preconditions_not_met`。branch/diff/archive/restore 仍可用，但 restore
只恢复 archived 快照/worktree 为 challenger，不激活 live。

### 5.3 代码变体闭环

`evolver.propose_code_variant`：`branch_code_variant`(worktree) → `code_mutator.mutate` →
`gate.run`（编译/import/pytest）→ `select_eval_set`（60% 成功 + 40% 失败混采）→
`eval_harness.evaluate_paired` → `save_variant_eval_run` + `update_universe_fitness`。配对差
超过 `universe_promote_margin` 只返回 `recommended`，否则 `evaluated`；两者都不改变
live 代码。

真实 Candidate mutation 另走 `PromotionService` 与 `/api/evolution/candidates/{id}/...`。
生产 wiring 只有 Skill 使用可执行 adapter；Code Candidate 即使具备已批准高风险
Decision，当前也会因 activation adapter unavailable 而 fail-closed。

## 6. HTTP 路由

Legacy 快照/评估接口是 `/api/universes*`；受治理 Candidate、Gate、assignment 与
promotion 接口是 `/api/evolution*`。完整表见
[../../design/interfaces/gateway.md](../../design/interfaces/gateway.md)。

## 7. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 让行为变异影响运行 | 为对应 CandidateKind 补齐 payload/overlay consumer、Gate 和 promotion/rollback adapter，经 `PromotionService` 灰度；Legacy 快照仍不恢复 live mutation |
| 放宽代码演化域 | 调 `code_variant_evolvable_paths`（`CodeMutator._within_evolvable` 支持精确文件与目录前缀） |
| 新增适应度维度 | 改 `fitness.compute_fitness` 的 stats 入参与权重；同步 `universe_memorial_stats` 与 `EvalHarness.aggregate_db_stats` 两处聚合 |
| 调整门禁 | `Gate.run(run_tests=...)`；新增关在 `Gate.run` 内按 fail-fast 顺序插入 |
| cassette 回归评估 | EvalHarness 设计预留两模式，当前以在线模型评估为主路径，cassette 录放可在 `evaluate` 旁路扩展 |
| 新增可灰度的资源类型 | 为对应 CandidateKind 增加 task-local overlay consumer，并在 payload 校验、Gate、promotion/rollback adapter 与 evidence 上形成闭环；不能恢复 UniverseManager 旁路 |
| 开放 Code live activation | 需要受验证的 Code promotion/rollback adapter、精确 resolved high-risk Decision、独立部署恢复边界和可验证 effect receipt；当前保持 fail-closed |
| 太医纳入更多诊断信号 | `Diagnostician._collect_failures` 目前只读 `status="failed"` 的 memorial；可扩展纳入负反馈（`feedback_score<0`）或审计 `verdict!=pass` 但终态非 failed 的记录 |
| 自主提案吞吐更高 | `auto_propose_codes` 当前在配额内串行调用 `propose_code_variant`；`SandboxRunner` 本身支持并行拉起多个（见 [../../design/universe/code-variant.md](../../design/universe/code-variant.md) §5.1），可在此基础上并行化评估 |
