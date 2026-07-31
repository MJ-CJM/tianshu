# 事实源与冲突解决规则

> **历史规则快照（2026-07-12）：** 本文只解释 `codex-v1` 交接包内部冲突。
> 当前工作树的事实源顺序见 [当前实现与支持边界](../CURRENT-STATE.md#事实源优先级)；
> 本文的旧分支、迁移尾号和 Gate 状态不得覆盖当前源码与回归结果。

## 权威顺序

遇到冲突时按以下顺序裁定：

1. 当前代码、测试、真实 migration ledger、`git status` 和运行证据；
2. 本目录最新 [STATUS.md](./archive/STATUS.md)，但恢复开发前仍须刷新；
3. [plans/01-rebaselined-execution.md](./plans/01-rebaselined-execution.md)：顺序、切片、Gate、审批边界；
4. 当前 active brief 和 recon 修订；
5. G0–G5 phase plan：具体接口、文件、测试和故障矩阵；
6. Master Roadmap：产品 Gate、范围和发布叙事；
7. 历史 design/impl/spec：仅解释既有系统。

## 文档属性

| 范围 | 属性 | 是否可直接执行 |
|---|---|---|
| `plans/01-rebaselined-execution.md` | `authoritative-sequencing` | 是，先结合 STATUS |
| `plans/02-s0-g1.4b3-close.md` | `active-recovery-contract` | 是，先重新冻结现场 |
| `plans/10-*` 至 `15-*` | `detailed-technical-reference` | 需消费 recon 并重算 migration |
| `plans/00-master-roadmap.md` | `product-roadmap` | 不可单独驱动代码 |
| `design/00-*`、`01-*` | `approved-design` | 设计事实源 |
| `design/10-*` 至 `24-*` | `recon/amendment/snapshot` | 需看文件日期和阶段说明 |
| `evidence/progress-snapshot-*` | `historical-ledger-snapshot` | 只证明当时记录 |
| `evidence/g1*.md` | `completed-slice-report` | 不能替代当前回归 |
| `quality/public-capability-matrix-v0.4.2.md` | `published-v0.4.2-truth` | 不等于 feature branch 状态 |
| `ui/assets/approved/` | `approved-visual-target` | 是，但示例数字不是生产数据 |
| `ui/assets/references/` | `layout/shell-reference` | 只参考标注范围 |
| `ui/assets/historical/` | `superseded` | 禁止作为目标实现 |
| `ui/assets/negative/` | `explicit-anti-requirement` | 必须删除/避免 |

## 迁移号规则

旧 G2 计划把 G1 写成 v2、G2 写成 v3–v7；旧 G4 计划继续使用 v8–v11。
这些编号已经失效。当前 committed migration prefix 为 v1–v4，dirty G1.4b3
计划追加 v5。正确做法是：

1. 当前 Gate 完成并提交后读取 `MIGRATIONS[-1].version` 为 `N`；
2. 下一个 Gate 只追加 `N+1`；
3. 冻结既有 checksum 和 callback；
4. phase plan 中任何固定编号只解释表的相对顺序，不拥有实际版本号。

## UI 事实边界

- G0 12 张图证明视觉、布局和原型交互目标；不证明真实持久化或后端能力。
- `mockData.js`、图中金额、进度和样本数只用于示例，禁止进入生产默认值。
- 用户红框截图只冻结 Logo、格言、右上五项、部门侧栏和左下控件。
- “系统可信 / 3 个执行器可用”截图是明确反例，不得实现。
- G3 正式页面必须由真实 API 提供 actor、Decision、RunState、Evidence 和 gate truth。
- 领域类型、API 与数据库兼容名保持 `Edict`；历史资料可能写“诏令”。当前中文
  产品和 G3 可见 UI 的 canonical 名称是“敕令”，不得因旧文档将界面改回“诏令”。

## 状态枚举

不要把执行证据与路线图排期压成一个状态字段：

- slice/Gate 证据生命周期使用 `implemented`、`focused_verified`、
  `automation_passed`、`external_pending`、`user_approval_pending`、`passed`；
- 路线图/台账排期使用 `planned`、`in_progress`、`blocked_by_upstream`、
  `superseded_snapshot`、`passed`。

旧快照若出现 `pending_external`，按 `external_pending` 解释；旧的组合状态
`automation_passed_pending_user_approval` 必须拆成不可变 `automation_passed`
证据与当前 Gate 状态 `user_approval_pending`，新代码不得继续引入旧别名。

## 历史 recon 的解释

- `g1-auth-recon`、`g1-execution-recon` 描述实施前差距，其中多个缺口已在 G1.1–G1.4b2 关闭。
- `g1-release-recon` 被更细的 G1.5/G1.6 briefs 和 recon 部分取代。
- `g2-recon` 的 BLOCKED 状态仍有效：完整 G1 handoff 未冻结前不得写 G2 migration。
- `g2.1-2-brief` 使用动态 `N+1`，但只是草案，不表示 G2 已获入口。
- progress 文件同时包含另一轮工程治理的 G1–G7；本次 Agent OS 台账从
  `=== Agent OS G0-G5 连续实施` 开始。

## 发布权限边界

本资料包和既有用户批准只允许继续本地编辑、测试、文档、提交和候选制品准备；
不允许自动进行任何外部状态变更，包括：

- `git push`、创建 PR/Issue、发送外部消息；
- 将仓库改为 Public；
- 创建或推送 tag/release；
- 发布 PyPI/GHCR；
- 修改 GitHub branch protection、OIDC 或仓库公开设置；
- 对外正式宣称 1.0 或完整自进化闭环。

这些动作必须在最终候选完成后获得新的明确用户授权。
