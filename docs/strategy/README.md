# 天枢发展战略（2026-07 历史快照）

> **历史战略快照：** 本目录记录 2026-07-07 当时的分析、假设与决策过程，不代表 v0.4.2 当前能力或当前发布承诺。当前能力只以 [能力事实矩阵](../launch/capability-matrix.md) 为准，当前 G0–G5 执行顺序以 [Agent OS Master Roadmap](../superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md) 为准；历史文档中的“批准”只表示当时形成过决策，不能替代实现证据与 Gate 验收。

## 文档与阅读顺序

| # | 文档 | 是什么 | 状态 |
|---|---|---|---|
| 0 | [**DECISIONS.md 决策台账**](./DECISIONS.md) | **追踪层**:全部决策的单一索引(S1–S21 已批准 + D1–D19 待验证),每项可追到分析上下文/ADR/排期落点;状态流转记录 | 持续维护 |
| 1 | [竞争力复盘与发展规划](./2026-07-07-competitiveness-review-and-roadmap.md) | **分析层**:全仓工程体检(当日实测)、2026 市场格局对标(经三路一手来源核查)、8 个参考项目源码级增量能力清单(deer-flow/claude-mem/mempalace/crush/opencode/zeroclaw/kimi-cli/multica)、P0–P3 机会全景与不做清单 | 已核查定稿 |
| 2 | [发展战略与迭代排期 spec](./2026-07-07-development-strategy-design.md) | **决策层**:十四项拍板(定位/发布/客卿/起居注/明制补全/宣发叙事/重资产分层/廷议/全功能审计)、核心竞争力定性、迭代 0–7 排期至 v0.4.x 年终版 | ✅ 已批准(#14 待验证) |
| 3 | [全功能竞争力审计](./2026-07-08-full-feature-competitiveness-audit.md) | **分析层**:19 个功能逐项四段式审计(现状/诊断/业界/拍板),决策点 D1–D19 的分析上下文 | ⏳ 自主拍板待验证 |
| 4 | [迭代 0「地基」实施计划](./2026-07-07-iteration-0-foundation.md) | **执行层**:7 个任务的逐步实施计划(CancelledError 修复/抖动定位/卫生四件套/前端质量线/CI/合 main/soft launch),每步含代码与验证命令 | 待执行 |

配套：不可逆决策的 why 沉淀在 [docs/adr/](../adr/)；战略层 canonical 术语在根目录 [CONTEXT.md](../../CONTEXT.md)。

## 历史假设速览（非当前能力事实）

当时形成的核心假设是：把“治理、证据、成长”组合成长期差异化方向，并以开源先验证真实使用价值。它仍是产品研究输入，但其中的唯一性、生产安全性和完整闭环都尚未由 v0.4.2 证明，不能作为当前宣传语。

当前对外定位保持为“天枢是一个可治理、可验证、持续成长的自进化 Agent OS”，同时明确这是一条长期方向；只有通过相应 Gate 并在能力事实矩阵补齐证据，才能把具体能力升格为公开承诺。

## 当前执行顺序

旧迭代日期和旧版本号已经归档，不再构成执行承诺。当前顺序是 G0 事实与数据安全 → G1 public-safe foundation → G2 durable governance → G3 verifiable runtime → G4 governed evolution → G5 open-source launch。任何阶段均以 Gate 证据而不是日历日期推进。

## 与其他文档目录的关系

- 长期分阶段路线图(Phase 0–3)在 [../plan/](../plan/);本目录是 2026-07 这一轮的**滚动战略与近期排期**,粒度更细、随迭代推进滚动修订。
- 单个特性的设计 spec 与实施 plan 沉淀在 [../superpowers/](../superpowers/);本目录的迭代计划执行时,产生的特性级文档仍归位 superpowers。
- 借鉴矩阵与原创设计边界见 [../reference/](../reference/)。
