# 位面演化（行为配置层）— 当前边界

Legacy Universe 是人格、技能和配置的实验快照系统。当前支持存照、分支、diff、归档、
恢复、变异和沙箱配对评估；不提供把任意 Universe 直接切成 live 的生产路径。

## 1. 模型与存储

`Universe` 保存 `champion/challenger/archived` 状态、origin、parent、mutation reason、
code ref 和 fitness。`UniverseStore` 在 `~/.tianshu/universes/{id}/` 保存 personas、
skills 与 manifest 的全量快照。

这里的 `champion` 是 Legacy Universe 评估语义，不等于 PromotionService 已激活的
生产 candidate。

## 2. 当前动作

| 动作 | 当前状态 |
|---|---|
| enable / genesis | 可用，捕获初始快照 |
| branch | 可用，生成 challenger 快照 |
| diff | 可用，比较 personas / skills / config |
| archive / restore / delete | 可用，受 champion 与状态约束 |
| behavior mutation | 实验；当前只改写允许的人格文件 |
| paired evaluation | 实验；候选与基线同集评估 |
| recommendation | 可用，只产生 `recommended` |
| switch / rollback | Legacy manager 固定 `promotion_service_required`，Gateway 返回 409 |
| promote-code | 固定 fail closed；不写 deploy pointer、不重启 |

Web 实验页只展示创建、查看、评估、归档和恢复，不显示固定失败的切换或晋升按钮。

## 3. 演化流程

```text
空闲与互斥 gate
  → 收集失败/审计/成本信号
  → LLM 提一处变异
  → 从基线分支 challenger
  → PersonaMutator 或 CodeMutator
  → static / import / test Gate
  → 同评估集配对评分
  → archived / evaluated / recommended
```

`recommended` 只是建议。`universe_auto_promote` 与 `code_variant_auto_promote` 是旧配置
兼容字段，当前执行链不读取它们，设置后也不会自动上线；当前文档和 Web 不把它们作为
有效控制项。

## 4. 行为变异

当前 PersonaMutator 只允许 `SOUL.md` / `ROLE.md`，并校验 allowlist、路径穿越、隐藏路径、
文件大小、空输出和无变化。policy、完整 config 和任意 Skill 内容不通过这条 Legacy
路径自动激活。

变异历史会进入下一次提示，减少重复方向。评估领先 margin 时只发推荐，不自动 switch。

## 5. Code variant

CodeMutator 只接受 allowlist 内具体、仓库相对的 `.py` 文件。目录、绝对路径、`..` 和
非 Python 目标会在创建 worktree/调用 LLM 前拒绝。Web 默认值使用真实文件
`src/tianshu/persona/selector.py`，不再用必然失败的目录。

代码候选可完成 worktree 分支、单文件变异、Gate、配对评估和推荐，但当前没有生产
activation adapter，不能宣称部署、进程重启、健康检查回滚或 live 晋升。

## 6. 运行归因

Legacy `UniverseManager.route_for_memorial` 不再是生产 canary 权威。通用 Evolution
Candidate 在存在受管 canary 时，由 `ChallengerRouter` 按 allocation bucket 建立不可变
`RunAssignmentV1`，`RunDispatcher` 绑定 effective overlay；无 canary 的普通运行保存
`LegacyRunAssignmentV1` 且 overlay 为 `None`。

当前生产只有 Skill Candidate 具备真实 activation/rollback adapter。行为 Universe 与
Code variant 即使获得 `recommended`，也不会通过 assignment 自动改变运行时。

## 7. 配置

| 配置 | 默认 | 当前含义 |
|---|---:|---|
| `parallel_universe_enabled` | False | Legacy Universe 实验总开关 |
| `universe_min_samples` | 20 | 配对评估最低样本 |
| `universe_promote_margin` | 0.05 | 推荐阈值 |
| `universe_auto_promote` | False | 兼容 no-op，不是有效上线开关 |
| `code_variant_enabled` | False | 代码提案/评估开关 |
| `code_variant_auto_promote` | False | 兼容 no-op |
| `universe_evolver_idle_hours` | 2 | 非手动触发的空闲门槛 |

**相关实现**：[../../impl/universe/](../../impl/universe/)
