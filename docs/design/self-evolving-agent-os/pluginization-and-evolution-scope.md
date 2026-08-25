# 插件化与自进化范围总览

> **Status: Proposed target scope。**
> 本页回答“哪些能力可以插件化、哪些变化可以自进化、哪些权威必须保持稳定”。当前插件
> 实现边界见 [current-plugin-state.md](current-plugin-state.md)。

本页是便于快速决策的范围摘要；进化定义、治理契约和阶段退出条件分别以
[first-principles.md](first-principles.md)、
[domain-and-governance.md](domain-and-governance.md) 和
[migration-roadmap.md](migration-roadmap.md) 为准。

## 1. 一句话边界

> **可插件化的是 Agent 能力，不可插件化的是治理权威；可自进化的是候选能力，不能自进化
> 的是裁判、权限和发布权。**

插件化不等于允许自进化，允许产生 Candidate 也不等于允许自动晋升。每个插件都必须分别
配置启用、版本、允许变化的 surface、进化模式、预算、审批、Canary 和回滚。

## 2. 可插件化能力矩阵

| 能力域 | 插件化内容 | 目标自进化边界 |
|---|---|---|
| Prompt / Skill / Persona | 系统提示、技能说明与脚本、示例、人格、普通配置 | 主要进化面；先 manual/canary，未来低风险项可由用户选择 auto |
| Router / Workflow | 模型路由、任务分流、声明式工作流和规划参数 | 声明式部分可逐步自动；Planner 代码保持人工/灰度 |
| Memory / Context | 记忆提取、检索、摘要、压缩、Context Contributor | 策略可进化；普通 Memory 写入只是持久适应，不等于插件进化 |
| Tool / Command / Hook | 工具 Schema、描述、实现、中间件、MCP Connector | 默认 manual/canary；仅低权限、无副作用叶子能力后期考虑 auto |
| Provider / Model Adapter | 模型供应商、协议适配、模型选择和路由 | 可以进化；默认 manual/canary |
| Executor / Harness | Pi、Codex、Claude Code 和其他执行器 | 高风险可执行插件；独立进程或服务隔离，初期禁止 auto |
| Agent Loop / Planner | 推理循环、Context Compiler 和工具调度策略 | 可以插件化，但必须满足稳定 Memorial/Attempt 契约；默认 frozen/manual |
| Channel / Notifier | 飞书、邮件、Webhook、消息渠道和通知策略 | 默认 frozen/manual，通常不是优先自动进化对象 |
| 声明式 UI / Renderer | 页面贡献、结果渲染和状态组件 | 可以进化；不能借 UI 绕过 Decision 和权限检查 |
| 附加 Evaluator / Guardrail | 质量、安全和领域检查 | 只能追加或收紧；独立版本化，不能和被评 Candidate 同批变化 |
| Telemetry / Evidence Producer | 指标提取、诊断和 Trace 导出 | 可以插件化，但不能替代固定内核的 Evidence verifier |

模型权重、LoRA 和训练数据版本可以作为签名 `ModelArtifact` 被 `SystemSnapshot` 引用，但
应使用独立的离线训练、评测和发布管线，而不是作为普通运行时插件自动替换。

## 3. 自进化可以改变什么

每个 `EvolutionPolicy` 可以独立允许以下变化面：

- `content`：Prompt、Persona、Skill、示例和知识说明；
- `configuration`：模型选择、路由权重、工具组合、工作流参数；
- `implementation`：Tool、Provider、Executor、Agent Loop 等插件代码；
- `dependencies`：插件版本和依赖闭包；
- `state`：Memory 策略、状态 schema 和迁移程序；
- `composition`：PluginSet 中启用的插件、精确版本和 Capability binding。

Candidate 只能在 manifest 的 Capability ceiling 和用户授权范围内收窄或修改，不能：

- 给自己增加文件、网络、Secret、预算或晋升权限；
- 修改评测自己的 Evaluator、held-out 数据集和 Gate；
- 修改 Promotion Policy、Promotion Authority 或 active pointer；
- 删除或放宽治理微内核的安全约束。

## 4. 什么才构成完整自进化

```text
执行 Evidence / 用户反馈 / 故障 / 成本
→ 诊断
→ 为精确基线生成 Plugin Candidate
→ 构建不可变 Artifact
→ 契约、安全、Replay、held-out 评测
→ Shadow
→ continuity-sticky Canary
→ 形成新 SystemSnapshot
→ 预热新 RuntimeGeneration
→ 独立 Decision 晋升
→ 监控、排空旧代或回滚
```

当前上下文反思、长期 Memory 写入、Prompt reload 和动态注册 Tool 分别属于在线适应、持久
适应、重新配置和运行时扩展。它们只有形成不可变 Candidate，并经过独立评测、晋升和可回滚
发布后，才进入受治理 Evolution 账本。

## 5. 必须留在稳定内核的权威

以下能力不能成为普通插件，也不能由运行中的 Agent 自动进化：

- Principal、身份、认证、授权和 Decision；
- Edict、Memorial、Attempt、lease/fencing 和终态账本；
- Durable Event History、outbox、Effect Gateway 和幂等边界；
- Artifact 验签、provenance、Admission 和 Capability ceiling；
- Secret Broker、预算、Policy Enforcement 和 kill switch；
- PluginSet resolver、ExecutionAssignment 和 RuntimeGeneration ledger；
- Promotion Authority、last-good 和 Rollback Reconciler；
- Evidence verifier、审计和状态迁移账本。

插件可以贡献更严格的检查，但不能替换或放宽这些权威。PluginHost、核心协议和数据库模型
可以通过常规软件发布升级，也可以接受自动提案和评测，但最终只能由外部签名、人工发布。

## 6. Candidate 与发布粒度

```text
Plugin
  = 常规最小 Candidate 和进化策略目标

PluginSetSnapshot
  = 插件版本、依赖、Capability、有效权限和 Policy digest 的精确 lock

SystemSnapshot
  = 原子部署、运行归因和回滚单元

RuntimeGeneration
  = 一个 SystemSnapshot 的实际运行实例
```

即使只改变 Pi 插件，也不能原地替换活体模块：

```text
Pi Candidate
→ 新 PluginSetSnapshot
→ 新 SystemSnapshot
→ 新 RuntimeGeneration warm / Ready
→ Sticky Canary
→ Active

失败 → last-good SystemSnapshot
```

默认一次只改变一个 Plugin；确实耦合的多插件变化形成一个原子 PluginSet patch，并对新的
完整 SystemSnapshot 重新评测。已有连续交互和长任务继续固定旧 generation，新 continuity
assignment 才进入新 generation。

## 7. 用户控制

```yaml
plugin: keqing.pi
enabled: true
version_policy:
  mode: pinned
  digest: sha256:...
evolution:
  mode: frozen
  allowed_surfaces: []
  max_canary_basis_points: 0
  approval: owner
  budget:
    candidates_per_day: 0
    token_limit: 0
    cost_limit: 0
```

| 模式 | 允许产生候选 | 允许评测 | 允许生产流量 | 自动晋升 |
|---|---:|---:|---:|---:|
| `frozen` | 否 | 否 | 只运行 pinned active | 否 |
| `propose` | 是 | 是 | 否 | 否 |
| `manual` | 是 | 是 | Decision 后 | 否 |
| `canary` | 是 | 是 | 受限、sticky | 否 |
| `auto` | 是 | 是 | 受策略限制 | 仅低风险白名单 |

第一阶段只实现 `frozen/manual/canary`。`auto` 必须等独立评测、供应链验证、隔离、状态
回滚、kill switch 和自动回滚演练全部成立后再开放。

## 8. 两条落地顺序

插件框架与自动进化的开放顺序不是一回事：

1. **PluginHost 第一条垂直切片**：选择 Keqing/Pi ExecutorAdapter，验证隔离、代际并存、
   continuity 固定、排空和 last-good 回滚；
2. **自进化第一批能力**：优先 Prompt、Skill、Persona、Router 和普通配置，稳定后再逐步
   开放 Tool、Provider、Executor 等可执行插件。

完整阶段计划见 [migration-roadmap.md](migration-roadmap.md)。
