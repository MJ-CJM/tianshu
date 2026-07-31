# Superpowers 特性索引

`superpowers/` 是**特性级开发沉淀**：每个特性一份 `plans/` 实现计划，通常配一份 `specs/` 设计规范。这是历史记录，反映特性「如何一步步落地」，不保证与最新代码逐字一致。

- 当前状态与支持边界先看 [`../CURRENT-STATE.md`](../CURRENT-STATE.md) 和
  [能力事实矩阵](../launch/capability-matrix.md)。
- 当前设计说明见 [`../design/`](../design/)，实现映射见 [`../impl/`](../impl/)；
  [`../plan/`](../plan/) 同样是历史路线图。

> 下表的“已落地/进行中”是文档形成时的历史标签。模块存在不等于端到端可用，旧分支名
> 也不表示当前状态；需要当前结论时请回到能力事实矩阵。

## 索引

| 日期 | 特性 | 子系统 | 状态 | 计划 | 规范 |
|---|---|---|---|---|---|
| 2026-04-02 | Phase1 Agent Loop 重设计（ExitReason/不可变 LoopState/压缩） | agent | 已落地 | [plan](plans/2026-04-02-phase1-agent-loop-redesign.md) | [spec](specs/2026-04-02-agent-core-optimization-design.md) |
| 2026-04-09 | Hermes 借鉴增强（Guard/模糊匹配/缓存/截断） | skills·agent | 已落地 | [plan](plans/2026-04-09-hermes-inspired-enhancements.md) | [spec](specs/2026-04-09-hermes-inspired-enhancements-design.md) |
| 2026-04-14 | 工具策略管线（多层 Policy） | tools | 已落地 | [plan](plans/2026-04-14-tool-policy-pipeline.md) | [spec](specs/2026-04-14-tool-policy-pipeline-design.md) |
| 2026-04-16 | 记忆宫殿（Drawer/Closet） | memory | 已落地 | [plan](plans/2026-04-16-memory-palace.md) | [spec](specs/2026-04-16-memory-palace-design.md) |
| 2026-04-18 | 人格成长画像（画像合成/信任度） | persona | 已落地 | [plan](plans/2026-04-18-persona-growth-profile.md) | [spec](specs/2026-04-18-persona-growth-profile-design.md) |
| 2026-04-21 | Web 访问工具（防 SSRF/凭证安全） | tools | 已落地 | [plan](plans/2026-04-21-web-access-tools.md) | [spec](specs/2026-04-21-web-access-tools-design.md) |
| 2026-04-22 | 外部网络能力扩展（鸿胪寺） | tools | 已落地 | [plan](plans/2026-04-22-external-network-capability-expansion.md) | [spec](specs/2026-04-22-external-network-capability-expansion-design.md) |
| 2026-04-26 | 长任务迭代（Orchestrator/验收循环） | agent | 已落地 | [plan](plans/2026-04-26-long-task-iteration.md) | [spec](specs/2026-04-26-long-task-iteration-design.md) |
| 2026-04-27 | LLM 成本归因（多维追踪） | llm | 已落地 | [plan](plans/2026-04-27-llm-cost-attribution.md) | [spec](specs/2026-04-27-llm-cost-attribution-design.md) |
| 2026-04-28 | 飞书机器人（WS + Webhook） | interfaces | 已落地 | [plan](plans/2026-04-28-feishu-bot.md) | [spec](specs/2026-04-28-feishu-bot-design.md) |
| 2026-04-29 | 飞书助手模式 v1.1 | interfaces | ⚠️ 已被 v2 取代 | [plan](plans/2026-04-29-feishu-assistant-mode.md) | [spec](specs/2026-04-29-feishu-assistant-mode-design.md) |
| 2026-04-29 | 飞书助手模式 v2（极简化） | interfaces | 已落地 | [plan](plans/2026-04-29-feishu-assistant-mode-v2.md) | （复用 v1 spec） |
| 2026-05-03 | 诏令目标循环（Edict goal loop） | agent | 已落地 | [plan](plans/2026-05-03-edict-goal-loop.md) | [spec](specs/2026-05-02-edict-goal-loop-design.md) |
| 2026-05-06 | i18n 迁移交接 | interfaces·web | ✅ 已交接完成 | [plan](plans/2026-05-06-i18n-migration-handoff.md) | — |
| 2026-05-06 | Skills 加载器增强 | skills | 已落地 | [plan](plans/2026-05-06-skills-loader-enhancements.md) | — |
| 2026-05-19 | 免费引擎集成 | llm | 已落地 | [plan](plans/2026-05-19-free-engines.md) | [spec](specs/2026-05-19-free-engines-design.md) |
| 2026-05-21 | 自然语言敕令（NL Edict） | scheduling·interfaces | 已落地 | [plan](plans/2026-05-21-nl-edict.md) | [spec](specs/2026-05-21-nl-edict-design.md) |
| 2026-05-29 | Telegram 机器人 | interfaces | 已落地 | [plan](plans/2026-05-29-telegram-bot.md) | — |
| 2026-06-04 | 多 Bot 实例 | interfaces | 已落地 | [plan](plans/2026-06-04-multi-bot-instances.md) | — |
| 2026-06-06 | 记忆全文检索（FTS5） | memory | 已落地 | [plan](plans/2026-06-06-memory-fulltext-recall.md) | [spec](specs/2026-06-06-memory-fulltext-recall-design.md) |
| 2026-06-06 | 官员记忆全局读 | memory·persona | 已落地 | [plan](plans/2026-06-06-persona-memory-global-read.md) | [spec](specs/2026-06-06-persona-memory-global-read-design.md) |
| 2026-06-07 | 前台技能学习 | skills | 已落地 | [plan](plans/2026-06-07-foreground-skill-learning.md) | [spec](specs/2026-06-06-foreground-skill-learning-design.md) |
| 2026-06-07 | 平行位面（行为配置分叉/自进化） | universe | 🚧 进行中 | [plan](plans/2026-06-07-parallel-universe.md) | [spec](specs/2026-06-07-parallel-universe-design.md) |
| 2026-06-07 | 技能并入内阁（标准化） | skills | 已落地 | [plan](plans/2026-06-07-skills-merge-into-cabinet.md) | [spec](specs/2026-06-07-skills-merge-into-cabinet-design.md) |
| 2026-06-08 | 代码变体位面（worktree/沙箱/晋升） | universe | 🚧 进行中 | [plan](plans/2026-06-08-code-variant-universe-2a.md) | [spec](specs/2026-06-08-code-variant-universe-design.md) |

## 无独立 spec 的特性

i18n 迁移、Skills 加载器增强、Telegram 机器人、多 Bot 实例 —— 这几项较轻量，计划文档内含设计说明，未单独出 spec。

## 待办 / 维护提示

- `2026-04-29-feishu-assistant-mode.md`（v1.1）已被同日 `-v2` 极简版取代，保留作演进记录，新读者直接看 v2。
- 飞书助手模式 v2 复用 v1 的 design spec，未另出 spec。
