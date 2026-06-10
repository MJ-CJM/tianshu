# 位面演化（Phase 1：行为配置层）

> 行为配置层的平行位面：把人格/技能/策略/config 捕获成快照，支持存照、分支、切换、对比、回滚，再叠加演化引擎的「变异—探索—选优」。本篇讲「为什么/契约/边界」，代码落点见 [../../impl/universe/](../../impl/universe/)。

## 1. 位面模型（契约）

一个**位面（Universe）** = 一份可命名、可分支、可切换、可回滚的「行为配置快照」。

| 字段 | 取值 / 含义 |
|---|---|
| `status` | `champion`（在役，唯一）/ `challenger`（候选）/ `archived`（已归档，可恢复） |
| `origin` | `genesis`（首次启用捕获的初始位面）/ `manual_branch`（人工分支）/ `mutation`（演化变异）/ `code_variant`（代码变体，见 [code-variant.md](./code-variant.md)） |
| `parent_universe_id` | 分支来源；genesis 为 None |
| `mutation_reason` | 变异理由（origin=mutation 时记录改了哪点、为何） |
| `code_ref` | 代码变体的 git 分支名/SHA；行为层位面为 None |
| `fitness` | 滚动累积的适应度 JSON |

`Universe` 是 `frozen` dataclass（不可变；演化路径用 `dataclasses.replace` 派生新值）。

## 2. 快照的表示与存储（UniverseStore）

落盘布局：`~/.tianshu/universes/{id}/`

| 内容 | 形态 | 进快照理由 |
|---|---|---|
| `personas/` | live persona 目录全量拷贝（SOUL.md/ROLE.md 等） | 人格灵魂/职责可分叉进化 |
| `skills/` | live skills 目录全量拷贝（含 pin/active/state） | 技能集与状态可分叉 |
| `manifest.json` | config 类快照 JSON（agent/LLM/providers/policy/prompt 层组合） | 切换时读回应用 |

设计约束：

- **全量拷贝**而非差量——小文本文件，简单安全（决策点：否决 COW）。
- **冠军是工作副本**：在役期间自进化持续写 live 目录，使冠军的存盘快照与 live 漂移。因此任何需要冠军存盘状态准确的时刻（branch-from / diff / switch-away）都先 `snapshot_live` 回写。
- **创世位面**：首次启用时把当前运行态捕获为 `genesis` 并设冠军，保证开启前后行为连续。

## 3. 切换 / 分支 / 回滚语义（UniverseManager）

| 动作 | 契约 |
|---|---|
| **branch** | 若父=冠军先回写 live → 全量拷贝父位面目录 + manifest → 新位面 `status=challenger` |
| **switch** | 先把原冠军 live 漂移回写其目录 → 把目标位面 `restore_to_live` 覆盖回 live → loader 重定向 + config 重载 + 缓存失效 → 翻状态（先降原冠军避免唯一冲突）。切到自身=no-op；切到 archived/不存在=拒绝 |
| **rollback** | 语义等同「切换到某历史位面」，历史位面是冻结快照，回滚不破坏任何全局共享数据 |
| **archive** | `champion` 不可归档（须先切走）；归档=可恢复，非删除 |
| **restore** | `archived → challenger` |
| **delete** | 彻底删除（不可恢复）；冠军不可删 |

**切换的边界**：新诏令立即采用新位面；在途诏令在其 `universe_id` 标记的位面内跑完（执行开始即固化，不随切换改变）。

## 4. 对比（diff）

`diff(a, b)` 覆盖三个维度，对比前先回写冠军 live 漂移：

| 维度 | 比较内容 |
|---|---|
| `personas` | 人格目录文件：`only_in_a` / `only_in_b` / `changed` |
| `skills` | 技能目录文件，同上 |
| `config` | manifest 键值差异：`{key: {a, b}}` |

## 5. 演化引擎（UniverseEvolver）

骨架对齐 SkillCurator「修撰」：`gate(idle + lock) → 采信号 → 一处 LLM 变异 → 分支候选 → 熔断下线劣质候选 → 晋升推荐`。

| 步骤 | 契约 |
|---|---|
| 触发闸 | 需 `parallel_universe_enabled`；非手动触发要求空闲（`universe_evolver_idle_hours`）；synthesis lock 防并发 |
| 采信号 | 读冠军行为概要 + 各位面适应度 |
| 提变异 | LLM 只产出**一处**定向变异 + 理由（一次只动一处，便于归因）。当前 cut 仅瞄准人格文件 |
| 生候选 | 从冠军分支出候选位面（`origin=mutation`，记 `mutation_reason`），再调 mutator 把意图改写进文件 |
| 熔断 | 候选样本数与失败数均超 `universe_challenger_fail_limit` → 自动归档下线 |
| 选优 | 候选在 ≥`universe_min_samples` 样本上超过冠军 + `universe_promote_margin` → 默认发 `universe.promotion_recommended`（人工确认）；`universe_auto_promote=True` 才自动 switch |

## 6. 变异落地（PersonaMutator）

**当前限制（诚实定性）**：变异落地只支持**人格文件**（`SOUL.md` / `ROLE.md`）改写——人格目录被位面完整快照、切换时 restore+reload，零改造即端到端生效。policy / config / skillset 类变异尚未落地（需先扩展快照范围把 session_rules、完整 config、技能状态纳入 snapshot/restore），在此之前走「只记录不改写」兜底（`mutation_applied=False`）。

安全约束：target 解析强制 allowlist（仅 SOUL.md/ROLE.md）+ 防路径穿越（拒 `/`、`..`、`.` 开头的 persona id）；改写后文件大小上限 64KB；空/超长/无变化均 no-op。

## 7. 适应度（FitnessCalculator）

按位面滚动聚合其 memorials，综合分越高越好：

| 维度 | 来源 | 方向 |
|---|---|---|
| `success_rate` | 成功 / 总数 | 越高越好 |
| `cost_score` | 平均成本反向归一 | 成本越低越好 |
| `audit_rate` | 审计通过 / 已审计 | 越高越好 |
| `retry_score` | 平均重试反向 | 重试越少越好 |
| `feedback` | 用户对结果赞踩累积（squash 到 [0,1]） | 越高越好 |

- 综合分 = 各维加权（默认权重 `(0.4, 0.15, 0.2, 0.1, 0.15)`，可配 `universe_fitness_weights`）。
- **小样本保护**：未达 `universe_min_samples` 不参与晋升判定；晋升要求超冠军一个 margin，避免噪声晋升。

## 8. 探索路由（Gate）

诏令入口的小流量探索分流（在 manager 的 `route_for_memorial`）：

- 总开关关闭 → 一律归冠军。
- 否则以 `universe_explore_ratio` 概率把新诏令分给在线候选，否则给冠军。
- 用 `memorial_id` 的稳定 sha256 哈希做**确定性分桶**（无随机源、可复现）。
- 无在线候选 → 归冠军。

边界：探索仅作用于新诏令；系统/定时诏令默认不参与（避免系统任务被实验配置影响）。

## 9. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `parallel_universe_enabled` | False | 总开关（opt-in） |
| `universe_explore_ratio` | 0.1 | 候选探索流量比例 |
| `universe_min_samples` | 20 | 参与晋升的最小样本量 |
| `universe_promote_margin` | 0.05 | 晋升所需领先幅度 |
| `universe_auto_promote` | False | 默认推荐人工确认；开启则阈值满足即切换 |
| `universe_evolver_idle_hours` | 2 | 非手动触发的空闲门槛 |
| `universe_challenger_fail_limit` | 5 | 候选连续失败下线阈值 |
| `universe_fitness_weights` | (0.4,0.15,0.2,0.1,0.15) | 五维适应度权重 |

**相关实现**：[../../impl/universe/](../../impl/universe/)
