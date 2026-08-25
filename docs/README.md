# 天枢 · 文档导航

天枢的长期定位是一个**可治理、可验证、持续成长的自进化 Agent OS**。用户可通过 Web、API、CLI、飞书或 Telegram 下达「敕令（Edict）」，系统把目标转成可调度、可审计、可裁决、可复盘的执行链路，并以明朝官署隐喻组织职责。

> 先看 [CURRENT-STATE.md](CURRENT-STATE.md)：它是当前实现、验证快照、支持边界和发布
> 状态的总入口。
>
> 当前版本是 v0.4.2，正式支持边界仍是可信本地、单机、单节点使用。HTTP、WebSocket
> 与 MCP 已共用身份边界；`secure-remote` 支持 PAT/session 和任务所有权隔离，但尚不是
> 对公网生产部署承诺。不要把默认 `trusted-local` 服务暴露到不可信网络。稳定（有限
> 边界）、实验与规划能力的唯一公开事实源是
> [launch/capability-matrix.md](launch/capability-matrix.md)。

## 从这里开始

| 你想了解什么 | 先看 |
|---|---|
| 产品有哪些功能、页面怎么用 | [功能图鉴](usage/feature-tour.md)（[English](usage/feature-tour.en.md)） |
| 当前哪些能力真的可用 | [当前实现与验证状态](CURRENT-STATE.md) |
| 第一次安装和启动 | [快速开始](usage/getting-started.md) |
| 日常下达和管理任务 | [使用指南](usage/user-guide.md) |
| 每项能力的成熟度、保证与非保证 | [能力事实矩阵](launch/capability-matrix.md) |

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

## 当前文档地图

| 目录 | 面向 | 内容 |
|---|---|---|
| [design/](design/) | 想理解「为什么这样设计」 | 架构、领域模型、主链路；按功能子系统分目录的设计文档 |
| [impl/](impl/) | 想看「代码怎么实现」 | 与主要 design 子系统同名对应的实现现状文档 |
| [usage/](usage/) | 使用者 / 二次开发者 | 快速开始、使用指南、开发者扩展指南、前端开发 |
| [ops/](ops/) | 部署 / 运维 | 凭证、飞书/Telegram 接入、多 Bot、MCP 配置 |
| [reference/](reference/) | 想了解借鉴与术语 | 借鉴融合的开源项目总览、六部隐喻术语表 |

`assets/` 只存放 README 与文档引用的图片，不是独立阅读入口。

## 发布、安全与决策

| 目录 | 内容 |
|---|---|
| [launch/](launch/) | 当前能力事实、验证状态、非保证和发布门禁 |
| [security/lean-preview-threat-model.md](security/lean-preview-threat-model.md) | 当前安全边界与威胁模型 |
| [adr/](adr/) | 仍有效的架构决策；若有后续 ADR，以后者为准 |

## 路线与历史资料

以下目录保留决策过程、阶段计划和绑定当时代码/环境的证据，便于追溯；它们不是当前
完成度事实源，也不会在本次整理中搬移或重写。

| 目录 | 内容 |
|---|---|
| [plan/](plan/) | Phase 0–3 路线与阶段实施记录 |
| [strategy/](strategy/) | 竞争力复盘、发展战略与当期迭代方案 |
| [superpowers/](superpowers/) | 特性设计 spec 与实现 plan（见 [INDEX](superpowers/INDEX.md)） |
| [audit/](audit/) | 带日期的专项审计快照，不替代当前源码复核 |
| [codex-v1/](codex-v1/) / [cc-fable-v1/](cc-fable-v1/) | 历史交接包、阶段台账与不可变 Gate 证据 |

## 推荐阅读路径

- **我想先看产品全貌** → [usage/feature-tour.md](usage/feature-tour.md)
- **我要用起来** → [usage/getting-started.md](usage/getting-started.md) → [usage/user-guide.md](usage/user-guide.md)
- **我要知道当前到底能不能用** → [CURRENT-STATE.md](CURRENT-STATE.md)
- **我要审批开源前状态** → [CURRENT-STATE.md](CURRENT-STATE.md) →
  [launch/final-approval-proposal.md](launch/final-approval-proposal.md) →
  [launch/web-functional-validation-2026-07-31.md](launch/web-functional-validation-2026-07-31.md) →
  [launch/capability-matrix.md](launch/capability-matrix.md) →
  [launch/checklist.md](launch/checklist.md)
- **我要做二次开发** → [usage/developer-guide.md](usage/developer-guide.md) → [impl/README.md](impl/README.md) → 对应 `impl/<子系统>/`
- **我要理解架构** → [design/README.md](design/README.md) → [design/architecture.md](design/architecture.md) → [design/runtime-flow.md](design/runtime-flow.md) → 各 `design/<子系统>/`
- **我要理解插件化与自进化 Agent OS 的目标架构** →
  [design/self-evolving-agent-os/](design/self-evolving-agent-os/)
- **我想知道借鉴了哪些优秀项目** → [reference/reference-projects.md](reference/reference-projects.md)
- **术语 / 古风隐喻看不懂** → [reference/glossary.md](reference/glossary.md)

## 设计 ↔ 实现的对应关系

`design/` 与 `impl/` 的主要功能子系统使用同名目录：前者讲「设计意图与当前设计」，
后者讲「代码现状」。少数设计总览没有独立实现目录，例如 `design/growth/` 是跨子系统的
设计视图；因此不要把目录是否同名当成能力已实现的证据。

```text
agent · persona · memory · skills · tools · llm · storage · scheduling · universe · interfaces
```

每个子系统目录内文档互相以「相关」链接对照，可从设计跳到实现、再跳到对应源码。
