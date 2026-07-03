# 平行位面（universe）实现现状

**相关设计**：[../../design/universe/](../../design/universe/)

> 代码位于 `src/tianshu/universe/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图见 design 篇。

## 1. 模块清单（`src/tianshu/universe/`）

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `model.py` | `Universe`(frozen dataclass)、`UniverseStatus`、`UniverseOrigin` | 位面数据契约；`to_row` / `from_row` 与 DB 互转 |
| `store.py` | `UniverseStore` | 行为快照落盘/还原：`snapshot_live` / `branch_from` / `restore_to_live` / `read_manifest` / `remove` |
| `code_store.py` | `CodeVariantStore` | 代码层 git worktree 生命周期：`branch_code_variant` / `diff` / `gc_worktree` / `restore_worktree` / `remove` |
| `manager.py` | `UniverseManager` | 编排：`ensure_genesis` / `branch` / `switch`(=`rollback`) / `diff` / `archive` / `restore` / `delete` / `route_for_memorial`；代码变体 `branch_code_variant` / `promote_code_variant` / `code_diff` |
| `evolver.py` | `UniverseEvolver`、`EvolveResult` | 演化：`run`（人格路径）/ `propose_code_variant`（代码路径） |
| `fitness.py` | `compute_fitness` | 五维加权适应度（纯函数） |
| `mutator.py` | `apply_mutation`、`parse_persona_target` | 人格文件变异落地（SOUL.md/ROLE.md） |
| `code_mutator.py` | `CodeMutator` | worktree 内代码改写 + 演化域 allowlist + traversal-safe |
| `gate.py` | `Gate`、`GateResult` | 三关门禁：compileall / import / pytest |
| `sandbox.py` | `SandboxRunner`、`SandboxHandle`、`SandboxError` | 隔离子进程拉起 + 健康检查 + 销毁 |
| `eval_harness.py` | `EvalHarness` | 回放评估集 → `compute_fitness` 打分 |
| `deployer.py` | `Deployer`、`DeployPointer`、`DeployRecord` | `current_ref` 指针 + re-exec + 健康检查自动回滚 |
| `launcher.py` | `resolve_boot_plan`、`main` | 按 deploy 指针决定从哪份代码 exec uvicorn |

## 2. 落盘布局

| 路径 | 内容 |
|---|---|
| `~/.tianshu/universes/{id}/personas/` `skills/` `manifest.json` | 行为层位面快照（全量拷贝 + config JSON） |
| `~/.tianshu/universes/worktrees/{id}/` | 代码变体 worktree（git 分支 `universe/<id>`） |
| `~/.tianshu/universes/worktrees/_meta/{id}.json` | 代码变体 sidecar：`{branch, start_ref}` |
| `~/.tianshu/universes/deploy_ptr.json` | Deployer 指针 `{current, previous}` |

## 3. 数据库（`storage/universe_repo.py`）

| 表 / 列 | 说明 |
|---|---|
| `universes` | `id / name / parent_universe_id / status / origin / mutation_reason / description / fitness_json / code_ref / created_at`（建表见 `storage/schema.py:224`） |
| `memorials.universe_id` | 迁移加列（`storage/migrations.py:157`），执行开始固化诏令归属 |
| `variant_eval_runs` | 代码变体每次评估记录，按 `universe_id` 建索引（`storage/schema.py:249`） |

主要方法：`save_universe` / `get_universe` / `list_universes` / `get_champion_universe` / `set_universe_status` / `update_universe_fitness` / `delete_universe` / `universe_memorial_stats` / `save_variant_eval_run` / `list_variant_eval_runs`。

## 4. 装配（`app.py` lifespan）

```text
UniverseStore(root=~/.tianshu/universes, live_personas/live_skills)
CodeVariantStore(repo_root, worktrees_root)
DeployPointer + Deployer
UniverseManager(storage, store, persona_loader, skills_loader,
                config_snapshot, config_apply, code_store, deployer)
  → 若 parallel_universe_enabled 或已有 champion：update_agent_config(enabled=True) + ensure_genesis()
  → executor.set_universe_manager(universe_manager)
  → app.state.universe_manager
Gate / SandboxRunner / EvalHarness / CodeMutator → UniverseEvolver → app.state.universe_evolver
```

config 的 snapshot/apply 是注入回调（`_universe_config_snapshot` / `_universe_config_apply`），解耦 manager 与 ConfigManager 细节。

## 5. 核心流程

### 5.1 诏令归因（fitness 闭环）

- `executor.py:167` 执行开始时，若 memorial 未带 `universe_id`，调 `universe_manager.route_for_memorial(memorial.id)` 固化归属（默认冠军；按 explore_ratio 确定性哈希分给在线候选）。
- `app.py` 注册 `_update_universe_fitness` 订阅 `execution.completed/failed`、`audit.completed`（priority 250）→ 聚合 `universe_memorial_stats` → `compute_fitness` → `update_universe_fitness`。

### 5.2 行为层切换

`manager.switch`：回写原冠军 live → `restore_to_live` 目标 → `persona_loader.repoint_runtime` + `skills_loader.repoint_user_dir`（清缓存重载）+ `config_apply(manifest)` → 翻状态 → 发 `universe.switched`。

### 5.3 代码变体闭环

`evolver.propose_code_variant`：`branch_code_variant`(worktree) → `code_mutator.mutate` → `gate.run`（编译/import/pytest）→ `eval_harness.evaluate`（沙箱回放）→ `save_variant_eval_run` + `update_universe_fitness` → 超 margin 则 `recommended`，否则 `evaluated`。晋升走 `manager.promote_code_variant`（翻冠军 + `deployer.stage` 暂存指针，重启另行受控触发）。

## 6. HTTP 路由

见 [../../design/interfaces/gateway.md](../../design/interfaces/gateway.md)（`/api/universes*`），实现在 `gateway/universes_api.py`，前端在 `web/src/pages/UniversePage.tsx`。

## 7. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 让变异落地支持 policy/config/skillset | 先扩展位面快照范围（把 session_rules、完整 config、技能状态纳入 `snapshot_live`/`restore_to_live`），再放开 `mutator.py` 的 allowlist |
| 放宽代码演化域 | 调 `code_variant_evolvable_paths`（`CodeMutator._within_evolvable` 支持精确文件与目录前缀） |
| 新增适应度维度 | 改 `fitness.compute_fitness` 的 stats 入参与权重；同步 `universe_memorial_stats` 与 `EvalHarness.aggregate_db_stats` 两处聚合 |
| 调整门禁 | `Gate.run(run_tests=...)`；新增关在 `Gate.run` 内按 fail-fast 顺序插入 |
| cassette 回归评估 | EvalHarness 设计预留两模式，当前以 live 评估为主路径，cassette 录放可在 `evaluate` 旁路扩展 |
