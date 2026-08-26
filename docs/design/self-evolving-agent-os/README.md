# 自进化 Agent OS 目标架构

> **文档性质：目标架构与迁移建议，不是当前实现或发布承诺。**
> 当前能力以[当前实现与支持边界](../../CURRENT-STATE.md)和
> [能力事实矩阵](../../launch/capability-matrix.md)为准。

本目录沉淀 2026-08 对 Tianshu、DeepSeek Harness、Pi 以及相关业界机制的源码与一手资料
调研，回答一个长期问题：天枢怎样从“有治理能力的 Agent 应用”演进为真正可持续、可验证、
可回滚的自进化 Agent OS。

**插件扩展现状、对比调研、插件化范围、自进化边界和目标架构的完整正文统一放在本目录。**
其他 `docs/` 页面只保留面向各自读者的入口或当前子系统事实，不再作为并行的插件目标设计。

调研时的代码快照：

- Tianshu 对比调研基线：`b1c55336`；本文落盘复核：`88462b2a`；
- DeepSeek Harness：`47f94385`；
- Pi：`d3ab2af9`。

## 核心结论

天枢不应重构成“所有东西都能动态 import 的插件框架”，而应成为：

> **稳定治理微内核 + 声明式控制面 + 不可变插件代际 + 可回放执行面 + 独立演化/评测面。**

真正的自进化不是运行中的 Agent 原地覆盖自己，而是：

```text
观察 → 诊断 → 生成不可变候选 → 独立评测 → 预热 → Shadow → Canary
     → 晋升 → 监控 → 回滚或退役
```

其中：

- 插件是最小的进化策略和 Candidate 目标单元；
- 完整 `SystemSnapshot` 是原子部署、回滚和运行归因单元；
- `RuntimeGeneration` 承担具体 rollout、continuity 固定和 drain；
- 热更新按能力形态实现：子进程执行器切 generation，声明式内容按 run 冻结视图，Python
  进程实现通过优雅重启进入 snapshot；都不是活体模块 reload；
- Agent 可以提出变化，但不能同时修改评测标准、给自己评分并批准上线；
- 用户可以继续使用某个插件，同时将其进化模式设为 `frozen`。

## 阅读顺序

| 文档 | 回答的问题 |
|---|---|
| [current-plugin-state.md](current-plugin-state.md) | 当前插件清单、源码扩展门面和明确不支持的能力 |
| [pluginization-and-evolution-scope.md](pluginization-and-evolution-scope.md) | 哪些能力可插件化、哪些可自进化、哪些必须冻结 |
| [first-principles.md](first-principles.md) | 什么才算进化，哪些东西必须保持稳定 |
| [comparative-research.md](comparative-research.md) | DeepSeek Harness、Pi 和业界机制分别证明了什么 |
| [target-architecture.md](target-architecture.md) | 最终态的平面、组件、数据流和运行方式是什么 |
| [domain-and-governance.md](domain-and-governance.md) | 目标领域对象、不变量、状态机和用户控制如何定义 |
| [migration-roadmap.md](migration-roadmap.md) | 当前天枢保留什么、重构什么、按什么顺序落地 |
| [source-map.md](source-map.md) | 证据等级、源码快照和外部一手资料索引 |
| [review-and-implementation-plan.md](review-and-implementation-plan.md) | 2026-08-24 独立评审：同意什么、修正什么，以及 PR 级实现顺序 |
| [architecture-comparison.md](architecture-comparison.md) | 现在 → 目标的逐行架构对照图、Pi 换代时序与落地顺序 |
| [落地方案（docs/plan）](../../plan/2026-08-25-self-evolving-agent-os-landing.md) | 2026-08-25 合成并经三路校验的分阶段实施方案（P0–P7、V31–V35 迁移、逐阶段验收） |

## 事实与建议的标记

本目录采用以下证据层级：

- **当前源码事实**：当前 Tianshu 源码、测试或能力事实矩阵可直接证明；
- **上游源码事实**：固定版本的 DeepSeek Harness 或 Pi 源码可直接证明；
- **官方机制**：官方文档定义的系统行为；
- **论文结果**：特定实验环境中的研究结果，不外推为生产能力；
- **目标设计**：面向 Tianshu 的架构判断，尚未实现，也尚未成为 ADR。

`SystemSnapshot`（典制）、`RuntimeGeneration`（朝）和 `EvolutionPolicy`（进化策略）已由
[ADR-0013](../../adr/0013-generation-based-rollout.md)、
[ADR-0014](../../adr/0014-memorial-system-snapshot-binding.md)与
[`CONTEXT.md`](../../../CONTEXT.md) 接受为 canonical 术语；这只冻结命名与不变量，不表示目标
运行能力已经完整实现。当前源码已落地 P1 的 SystemSnapshot 影子归因、P3 的
`keqing:pi` RuntimeGeneration 内部机械与 P4a 的 per-subject EvolutionPolicy；P4b 已由
PR #109 合入 `feat/plugin-v1`（merge `a8a03071`）。P5 已由 PR #111 合入同一集成分支
（merge `567b028e`），完成 Pi EXECUTOR Candidate、精确 generation authority、canary、高危
Decision、换代与回滚垂直切片。P6 已由 PR #114 合入 `feat/plugin-v1`（merge
`8f32cc4c`），完成 process scope 的 SystemSnapshot generation、启动漂移校验、strict
run binding 与 Evolution Center 只读 active/last-good 投影。P7 已由 PR #116 合入同一
集成分支（merge `feba5a91`，CI 6/6）：仅对 Skills 提供每 run 不可变视图和 `off` / `shadow` / `enforce`
语义，不新增数据迁移。它保证同进程 mid-run 稳定；重启后持久回放旧内容所需的
artifact-backed `skills_view` 延期到 P7b。其最终并发边界包括基于已打开目录 fd 与完整
stability witness 的连续双 capture 与 symlink fail-closed、live/frozen 读取语义等价、polling
watcher、enforce prebind 的有证据 commit 后稳定失败与 scheduler/reconciler 交接、仅对
claimable attempt 生效并带 recovered 审计的同-key 重试，以及与 frozen 开关无关的晋升缓存
失效。selected base absent 只显露低层，challenger/unknown absent 仍保持历史 hide-lower；新的
absent candidate 统一拒绝，durable global tombstone 与旧内容持久回放一起延期 P7b。这些机制不
扩大到其他内容类型，也不承诺抵抗特权写者或不可靠 ctime 文件系统。覆盖全部
PluginSet/第三方插件的通用
Promotion Authority 与 PluginHost 仍未完成。P3 的 attempt 代际权威是独立
`run_generation_bindings`；V31 `run_system_bindings` 仍只是 snapshot-on shadow 与历史 fallback，
两者同在必须一致。`PluginSetSpec`、`PluginSetSnapshot` 和
`ExecutionAssignment` 仍是目标态设计词汇；P4b 以 `SubjectRunAssignmentV1` / `RunAssignmentSetV1`
承载其中的 per-subject 选择，不提前建立同名大聚合。`AgentSession`
明确不在首期引入，continuity 先按 conversation/长任务、scheduled root、DAG/retry 的
Edict/Memorial 规则固定。

## 与现有文档的关系

- 当前插件设计与实现边界已合并到本目录的
  [current-plugin-state.md](current-plugin-state.md)；
- [自改进统一视图](../growth/)仍描述当前已经接通的信号、候选和 live 边界；
- [位面演化](../universe/evolution.md)仍描述 Legacy Universe 的当前能力；
- 后两者是既有子系统事实源，不是另一套插件化与自进化目标报告；完整目标设计只在本目录。

文档发生冲突时，遵循 [CURRENT-STATE 的事实源优先级](../../CURRENT-STATE.md#事实源优先级)，
不能用本目录的目标设计反向证明某项能力已经实现。
