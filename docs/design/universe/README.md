# 平行位面（universe）设计总览

> 平行位面让天枢的自进化从「单线」升级为「可分叉 + 可回滚 + 可选优」：把行为配置（乃至代码）捕获成可命名、可切换、可对比的快照，让几套长法并行赛跑，再据适应度择优。
> 设计源头：`docs/superpowers/specs/2026-06-07-parallel-universe-design.md`（Phase 1 行为配置层）、`docs/superpowers/specs/2026-06-08-code-variant-universe-design.md`（Phase 2 代码变体层）。

## 1. 职责定位

| 项 | 说明 |
|---|---|
| 解决什么 | 自进化原本只有一条时间线：改坏不好回滚、不能多版本赛跑、发版后衍生的不适配只能就地覆盖 |
| 提供什么 | 「宫殿版 git」——存照 / 分支 / 切换 / 对比 / 回滚 / 归档，叠加演化引擎的变异选优 |
| 默认态 | 总开关 `parallel_universe_enabled=False`：默认单位面、行为等同今日、演化引擎不运转、零探索流量（opt-in） |
| 两步走 | Phase 1 分叉「行为配置」（人格/技能/策略/config），Phase 2 把分叉延伸到「代码」（git worktree 变体） |

## 2. 核心设计判断（铁律）

**位面分叉的是「怎么做」，全局共享的是「知道什么 + 做过什么」。**

| 进位面快照（可分叉/进化） | 全局共享（切换不丢） |
|---|---|
| 人格 SOUL.md / ROLE.md、技能集与状态、策略规则、agent/LLM config、prompt 层组合 | 记忆宫殿（`memory_entries`）、官员工作记忆（MEMORY.md）、全部工作历史（edicts/memorials/events/成本/审计） |

两个直接后果：

1. **切换位面不失忆、不丢历史**——换的是「宫殿的性格与规矩」，不是「宫殿对你的记忆」。
2. **适应度可干净归因**——诏令执行开始即固化 `memorials.universe_id`，在途任务不被切换打断，打分能归因到具体位面。

其余关键判断：

| 判断 | 取舍 |
|---|---|
| 冠军=工作副本 | 在役期间自进化持续写 live 目录，冠军存盘快照与 live 漂移；branch/diff/switch-away 前先 `snapshot_live` 回写。非冠军=冻结快照 |
| 单真相源 | 无独立 `active_universe_id` 字段，以 `status=champion` 唯一行（同一时刻仅一个 champion）为在役指针 |
| 全量拷贝 | v1 不做 COW 差量存储——小文本文件，全量拷贝简单又安全 |
| 评估安全 | 评估采历史诏令 goal 的沙箱回放（EvalHarness 隔离子进程 + 隔离 DB）；候选位面不真正运行，适应度由配对评估产生（基线与变体同集回放打分，delta 超阈值即推荐晋升，未超则归档） |
| 代码层隔离 | Python 不能进程内热替换代码：代码变体靠 worktree + 子进程隔离评估，绝不碰生产 DB；晋升=重定向 + 重启 + 健康检查自动回滚 |

## 3. 与相邻子系统的关系

| 相邻子系统 | 关系 |
|---|---|
| persona / skills | 位面快照的主体；切换位面时 PersonaLoader/SkillsLoader 的 runtime 根目录被重定向 + 缓存失效 |
| config_manager | config 类快照存于 manifest，切换时读回并 `update_agent_config`；演化/代码变体的全部开关都是 `AgentConfigState` 字段 |
| executor | 执行开始时按 `route_for_memorial` 固化 `universe_id`；探索路由已退役，一律归冠军，候选的适应度改由沙箱配对评估产生 |
| bus / scheduler | memorial 完成事件触发 fitness 更新；演化引擎可由 scheduler 周期 + 空闲触发（类比 SkillCurator「修撰」） |
| 单线自进化（修撰/reviewer） | 正交：演化选「哪套配置」，修撰优化「在役这套里的技能」 |

## 4. 本目录子文档索引

| 文档 | 内容 |
|---|---|
| [evolution.md](./evolution.md) | Phase 1 位面模型、UniverseStore 快照、UniverseManager 切换/分支/对比/回滚、UniverseEvolver 演化、FitnessCalculator 适应度、PersonaMutator 变异、Gate 探索路由 |
| [code-variant.md](./code-variant.md) | Phase 2 代码变体位面、CodeVariantStore 与 git worktree、相对 fork 起点的 diff、SandboxRunner/Gate/EvalHarness 评估门禁、CodeMutator 自改代码、Deployer 晋升回滚、archive/restore、来源分类 |
| [eval.md](./eval.md) | Eval Harness 与 Fitness 门禁：沙箱回放历史目标、统一适应度打分、与现冠军回归比对、过门禁才配人工晋升 |

**相关实现**：[../../impl/universe/](../../impl/universe/)
