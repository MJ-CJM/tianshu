# 天枢 Agent OS · Codex v1 开发交接包

> **历史快照（2026-07-12）：** 本目录记录当时 `feat_codex_phase_1` 的交接现场，
> 不再是当前分支的执行权威。当前实现和验证状态请先读
> [当前实现与支持边界](../CURRENT-STATE.md) 与
> [能力事实矩阵](../launch/capability-matrix.md)。包内 commit、报告和图片仍用于
> 追溯当时事实，不因后续实现变化而改写结论。

本目录是 G0–G5 产品设计、架构设计、实施计划、当前状态、验收口径和
UI 图的自包含快照。目标是让一个不了解此前对话的 coding agent 能先判断
真实状态，再从正确切片继续开发，而不是把历史规划误认为已经实现。

产品定位固定为：

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

## 当前暂停点

- 生成日期：2026-07-12（Asia/Shanghai）
- 分支：`feat_codex_phase_1`
- 业务实现基线 HEAD：`1b51bcd9`（`docs: rebaseline Agent OS execution plan`）；
  本资料包由其后的独立 `docs: add Codex v1 development handoff` 提交保存。
- G0 与 G1.1–G1.4b2 已提交。
- G1.4b3 正在 dirty worktree 中收口，尚无最终报告或完整 Gate 结果。
- G1.5、G1.6、G2、G3、G4、G5 尚未实现。
- G0 UI 原型已审批；正式生产 Web 的 G3 迁移尚未开始。
- 用户已要求暂停实施；任何 coding agent 恢复前必须重新冻结工作区状态。

完整机器/人工状态见 [STATUS.md](./archive/STATUS.md)。

## 新 coding agent 必读顺序

1. [STATUS.md](./archive/STATUS.md)：现在停在哪里、哪些证据仍有效。
2. [SOURCE-OF-TRUTH.md](./SOURCE-OF-TRUTH.md)：文档冲突时听谁的。
3. [DEVELOPMENT-HANDOFF.md](./archive/DEVELOPMENT-HANDOFF.md)：如何安全恢复当前 dirty tree。
4. [PRODUCT-ARCHITECTURE.md](./PRODUCT-ARCHITECTURE.md)：产品定位、差异点和目标架构。
5. [IMPLEMENTATION-GUIDE.md](./IMPLEMENTATION-GUIDE.md)：G0–G5 入口、出口和依赖。
6. [plans/01-rebaselined-execution.md](./plans/01-rebaselined-execution.md)：唯一拥有执行顺序、切片和 Gate 的计划。
7. 只阅读当前阶段的 brief/recon；不要一开始加载全部 12,000+ 行阶段计划。

恢复 S0/G1.4b3 时，再按顺序阅读：

1. [plans/02-s0-g1.4b3-close.md](./plans/02-s0-g1.4b3-close.md)
2. [design/14-g1.4b3-governed-apply-brief.md](./design/14-g1.4b3-governed-apply-brief.md)
3. [design/13-g1-root-anchored-filesystem-design.md](./design/13-g1-root-anchored-filesystem-design.md)
4. [evidence/active-s0-core-brief.md](./evidence/active-s0-core-brief.md)
5. 当前真实 `git diff` 与相关测试，不以快照替代现场证据。

## 目录导航

| 目录/文件 | 用途 | 状态属性 |
|---|---|---|
| `product/` | 定位、术语、历史领域词汇 | 当前定位 + 兼容参考 |
| `design/00-*`、`01-*` | Agent OS 总设计与 UI 审批设计 | 权威设计 |
| `design/10-*` 至 `24-*` | G1/G2 briefs、recon、差距分析 | 阶段修订或历史快照 |
| `plans/00-master-roadmap.md` | G0–G5 产品 Gate 和总范围 | 总体参考 |
| `plans/01-rebaselined-execution.md` | 61 个切片、顺序、审批边界 | 当前执行权威 |
| `plans/10-*` 至 `15-*` | G0–G5 详细 TDD 技术计划 | 技术参考，迁移号需重算 |
| `evidence/` | 已完成 G1 报告与暂停时进度快照 | 运行时快照，不自动代表当前树 |
| `ui/` | UI 规范、12 张权威图、历史稿和反例 | 见 UI 自身分级 |
| `quality/` | 公开 capability/release 清单快照 | v0.4.2 公开事实，不等于 feature branch 状态 |
| `RISK-REGISTER.md` | 已知架构、计划和外部验证风险 | 恢复前必读 |
| `VERIFICATION.md` | 实际可运行命令和证据状态规则 | 当前工程口径 |

## 不可擅自变更的产品决策

- 只做桌面 Web，不开发手机端。
- 使用现有 `web/public/brand.png`，不得重绘或替换 Logo。
- 保留格言：`成功只有一个——按照自己的方式，去度过人生。`
- 保留右上角：`彩蛋 / 通用 / English / 实时 / 通政`。
- 保留 `中枢总览`、四组十四部门、左下主题切换和侧栏折叠。
- 用户可见治理术语使用 `裁决`；禁用 `批红 / 朱批 / 司礼监代批`。
- 视觉为克制的新中式：“墨为骨、朱为睛、纸为气”。
- 删除无口径的“系统可信”“3 个执行器可用”等宣称。
- 正式 Web 只消费真实 API；原型 `mockData` 和截图数字不得进入生产逻辑。
- 实际公开仓库、tag、PyPI、GHCR 和宣发需要用户最终单独授权。

## 资料包使用原则

- 文档中的 `superpowers:*`、Codex-specific skill 或 subagent 提示仅是原执行环境的
  加速方式，不是项目依赖。当前 coding agent 没有这些能力时，不得因此阻断；按本包
  展开的 TDD → focused verification → independent review → commit → ledger 流程执行。
- `plans/01-rebaselined-execution.md` 决定先后顺序，详细 phase plan 决定技术细节。
- 真实 migration ledger 决定版本号；旧计划中的 v3–v11 只作历史描述。
- `ui/assets/approved/` 决定视觉目标；`historical/`、`references/` 和 `negative/` 不能照搬。
- 测试通过必须对应当前 commit/working tree；旧报告不能替代新运行。
- 外部证据缺失时使用 `external_pending`，不得用本机 fixture 冒充完成。
