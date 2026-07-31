# 天枢 · 文档导航

天枢的长期定位是一个**可治理、可验证、持续成长的自进化 Agent OS**。用户可通过 Web、API、CLI、飞书或 Telegram 下达「诏令(Edict)」，系统把目标转成可调度、可审计、可裁决、可复盘的执行链路，并以明朝「六部」隐喻组织职责。

> 先看 [CURRENT-STATE.md](CURRENT-STATE.md)：它是当前实现、验证快照、支持边界和发布
> 状态的总入口。
>
> 当前版本是 v0.4.2，正式支持边界仍是可信本地、单机、单节点使用。HTTP、WebSocket
> 与 MCP 已共用身份边界；`secure-remote` 支持 PAT/session 和任务所有权隔离，但尚不是
> 对公网生产部署承诺。不要把默认 `trusted-local` 服务暴露到不可信网络。稳定（有限
> 边界）、实验与规划能力的唯一公开事实源是
> [launch/capability-matrix.md](launch/capability-matrix.md)。

核心闭环：

```text
下旨 Edict → 排期 Scheduler → 规划 Planner → 执行 Agent/DAG/Outer Loop
  → 审计 Auditor → 通知 Notifier → 记忆与成长 Memory/Profile/Skill
```

## 核心概念 (Key Concepts)

- **长程自治 (long-horizon autonomy)** — 长任务不靠 LLM「一次输出即终态」，而由外层循环反复 actor→checks→critic→completion audit，直到验收通过或预算/迭代耗尽。详见 [design/agent/orchestrator.md](design/agent/orchestrator.md)、[reference/glossary.md](reference/glossary.md)。
- **执行壳 (agent harness)** — 包裹 LLM 的执行框架：prompt 构建、工具调度、hook 治理、压缩、取消与续跑都由 harness 负责，LLM 只管思考与决策。详见 [design/agent/README.md](design/agent/README.md)、[reference/glossary.md](reference/glossary.md)。
- **ReAct loop (ReAct 主循环)** — 单任务的 Reason+Act 循环：思考→调用工具→观察结果→再思考，每轮状态做成可快照、可恢复的不可变对象。详见 [design/agent/react-loop.md](design/agent/react-loop.md)、[reference/glossary.md](reference/glossary.md)。
- **context compaction (上下文压缩)** — 三层兜底（micro 每轮预防 / auto 阈值摘要 / reactive 溢出救急），「先便宜后昂贵、先预防后补救」地控制上下文体积。详见 [design/agent/compaction.md](design/agent/compaction.md)、[reference/glossary.md](reference/glossary.md)。
- **checkpoint 续跑 (checkpoint resume)** — `checkpointed/background` 长任务在轮次边界
  保存 checkpoint，支持暂停、恢复、运行中补充要求及受控故障恢复；不保证任意外部
  副作用、多节点或任意指令位置的 exactly-once。详见
  [usage/long-task-walkthrough.md](usage/long-task-walkthrough.md)、
  [design/agent/orchestrator.md](design/agent/orchestrator.md)。
- **调度边界 (scheduling boundary)** — 普通任务支持立即、单次、cron 与 interval；
  长程任务只支持立即或单次定时，周期长程组合会被 Web、API 与调度工具明确拒绝。
  详见 [usage/getting-started.md](usage/getting-started.md)。
- **consultation 会诊 (consultation)** — 监督升级到 L2 时触发 `ConsultationSession`，多名官员(Persona)协作给 actor 改进建议；L2 失败再降级到 L3 人工决策。详见 [design/agent/orchestrator.md](design/agent/orchestrator.md)、[reference/glossary.md](reference/glossary.md)。

## 文档地图

| 目录 | 面向 | 内容 |
|---|---|---|
| [design/](design/) | 想理解「为什么这样设计」 | 架构、领域模型、主链路；按功能子系统分目录的设计文档 |
| [impl/](impl/) | 想看「代码怎么实现」 | 与 design 同构、一一对应的实现现状文档 |
| [usage/](usage/) | 使用者 / 二次开发者 | 快速开始、使用指南、开发者扩展指南、前端开发 |
| [ops/](ops/) | 部署 / 运维 | 凭证、飞书/Telegram 接入、多 Bot、MCP 配置 |
| [launch/](launch/) | 发布审批者 | 当前能力事实、验证状态、非保证和发布门禁 |
| [reference/](reference/) | 想了解借鉴与术语 | 借鉴融合的开源项目总览、六部隐喻术语表 |
| [plan/](plan/) | 想了解路线图 | Phase 0–3 分阶段交付计划 |
| [strategy/](strategy/) | 想了解竞争力与发展战略 | 2026-07 竞争力复盘、发展战略与迭代排期、当期迭代实施计划 |
| [superpowers/](superpowers/) | 想追溯某个特性怎么落地 | 50+ 特性的设计 spec 与实现 plan（见 [INDEX](superpowers/INDEX.md)） |
| [codex-v1/](codex-v1/) / [cc-fable-v1/](cc-fable-v1/) | 想追溯历史 | 交接包、阶段台账与绑定当时代码/环境的 Gate 证据；不是当前完成度事实源 |

## 推荐阅读路径

- **我要用起来** → [usage/getting-started.md](usage/getting-started.md) → [usage/user-guide.md](usage/user-guide.md)
- **我要知道当前到底能不能用** → [CURRENT-STATE.md](CURRENT-STATE.md)
- **我要审批开源前状态** → [CURRENT-STATE.md](CURRENT-STATE.md) →
  [launch/final-approval-proposal.md](launch/final-approval-proposal.md) →
  [launch/web-functional-validation-2026-07-31.md](launch/web-functional-validation-2026-07-31.md) →
  [launch/capability-matrix.md](launch/capability-matrix.md) →
  [launch/checklist.md](launch/checklist.md)
- **我要做二次开发** → [usage/developer-guide.md](usage/developer-guide.md) → [impl/README.md](impl/README.md) → 对应 `impl/<子系统>/`
- **我要理解架构** → [design/README.md](design/README.md) → [design/architecture.md](design/architecture.md) → [design/runtime-flow.md](design/runtime-flow.md) → 各 `design/<子系统>/`
- **我想知道借鉴了哪些优秀项目** → [reference/reference-projects.md](reference/reference-projects.md)
- **术语 / 古风隐喻看不懂** → [reference/glossary.md](reference/glossary.md)

## 设计 ↔ 实现的对应关系

`design/` 与 `impl/` 按相同的功能子系统分目录、一一对应：前者讲「设计意图与当前设计」，后者讲「代码现状」。

```text
agent · persona · memory · skills · tools · llm · storage · scheduling · universe · interfaces
```

每个子系统目录内文档互相以「相关」链接对照，可从设计跳到实现、再跳到对应源码。
