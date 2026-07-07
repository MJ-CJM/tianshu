# 天枢 · 文档导航

天枢（Tianshu）是一个**异步 AI 执行平台**：用户通过 Web、API、CLI、飞书或 Telegram 下达「诏令(Edict)」，系统把目标转成可调度、可审计、可审批、可复盘的执行链路，最终沉淀为执行记录、事件时间线、成本账本、记忆与监督报告。其核心是一套**长程自治 agent loop**（agent harness）：在 ReAct 主循环之外叠加 actor→critic→审计的外层循环，支持多级监督升级（L0–L3）、上下文压缩与 checkpoint 续跑，让长任务能自检、自纠并在必要时升级会诊或人工。系统以明朝「六部」隐喻组织，由若干「官员(Persona)」各司其职、与用户共同成长。

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
- **checkpoint 续跑 (checkpoint resume)** — `checkpointed/background` 执行档保存/恢复 `outer_loop_checkpoints`，长任务被中断或暂停后可从断点继续，无需从头重跑。详见 [design/agent/orchestrator.md](design/agent/orchestrator.md)、[reference/glossary.md](reference/glossary.md)。
- **consultation 会诊 (consultation)** — 监督升级到 L2 时触发 `ConsultationSession`，多名官员(Persona)协作给 actor 改进建议；L2 失败再降级到 L3 人工决策。详见 [design/agent/orchestrator.md](design/agent/orchestrator.md)、[reference/glossary.md](reference/glossary.md)。

## 文档地图

| 目录 | 面向 | 内容 |
|---|---|---|
| [design/](design/) | 想理解「为什么这样设计」 | 架构、领域模型、主链路；按功能子系统分目录的设计文档 |
| [impl/](impl/) | 想看「代码怎么实现」 | 与 design 同构、一一对应的实现现状文档 |
| [usage/](usage/) | 使用者 / 二次开发者 | 快速开始、使用指南、开发者扩展指南、前端开发 |
| [ops/](ops/) | 部署 / 运维 | 凭证、飞书/Telegram 接入、多 Bot、MCP 配置 |
| [reference/](reference/) | 想了解借鉴与术语 | 借鉴融合的开源项目总览、六部隐喻术语表 |
| [plan/](plan/) | 想了解路线图 | Phase 0–3 分阶段交付计划 |
| [strategy/](strategy/) | 想了解竞争力与发展战略 | 2026-07 竞争力复盘、发展战略与迭代排期、当期迭代实施计划 |
| [superpowers/](superpowers/) | 想追溯某个特性怎么落地 | 50+ 特性的设计 spec 与实现 plan（见 [INDEX](superpowers/INDEX.md)） |

## 推荐阅读路径

- **我要用起来** → [usage/getting-started.md](usage/getting-started.md) → [usage/user-guide.md](usage/user-guide.md)
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
