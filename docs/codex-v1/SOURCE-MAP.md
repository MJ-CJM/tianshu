# 资料来源映射

本文件说明 `docs/codex-v1` 中的快照副本来自哪里、是什么性质。为保证资料包
自包含并可直接交给 coding agent，部分副本重写了相对链接、统一状态枚举和当前
UI 术语，并在交接层冻结了原快照中尚未裁定的实现选择；这些 amendments 以
[SOURCE-OF-TRUTH](./SOURCE-OF-TRUTH.md) 和根目录入口文档为准。
`MANIFEST.sha256` 只验证资料包当前字节完整性，不证明它与原来源 byte-identical。

## Tracked 设计与计划

| 目标 | 原始来源 | 属性 |
|---|---|---|
| `design/00-agent-os-optimization-design.md` | `docs/superpowers/specs/2026-07-10-open-source-agent-os-optimization-design.md` | approved design |
| `design/01-web-approval-prototype-design.md` | `docs/superpowers/specs/2026-07-10-agent-os-web-approval-prototype-design.md` | approved G0 design |
| `plans/00-master-roadmap.md` | `docs/superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md` | product roadmap |
| `plans/01-rebaselined-execution.md` | `docs/superpowers/plans/2026-07-12-agent-os-rebaselined-execution.md` | current sequencing authority |
| `plans/10-g0-truth-language-migrations.md` | `docs/superpowers/plans/2026-07-11-phase-0-truth-language-migrations.md` | detailed G0 plan |
| `plans/11-g1-public-safe-foundation.md` | `docs/superpowers/plans/2026-07-11-phase-1-public-safe-foundation.md` | detailed G1 reference |
| `plans/12-g2-durable-governance-evidence.md` | `docs/superpowers/plans/2026-07-11-phase-2-durable-governance-evidence.md` | detailed G2 reference; migration numbers stale |
| `plans/13-g3-desktop-web-productization.md` | `docs/superpowers/plans/2026-07-11-phase-3-desktop-web-productization.md` | detailed G3 reference |
| `plans/14-g4-governed-evolution-executors.md` | `docs/superpowers/plans/2026-07-11-phase-4-governed-evolution-executors.md` | detailed G4 reference; migration numbers stale |
| `plans/15-g5-open-source-launch.md` | `docs/superpowers/plans/2026-07-11-phase-5-open-source-launch.md` | detailed G5 reference |
| `plans/20-web-approval-prototype-plan.md` | `docs/superpowers/plans/2026-07-10-agent-os-web-approval-prototype.md` | historical prototype implementation |
| `plans/21-g1-auth-review-hardening.md` | `docs/superpowers/plans/2026-07-11-g1-auth-review-hardening.md` | completed G1 hardening reference |
| `plans/22-local-startup-fix.md` | `docs/superpowers/plans/2026-07-11-fix-local-startup-migration.md` | completed G0 startup reference |

## Ignored SDD 现场快照

以下来自 `.superpowers/sdd/`，不是 tracked source of truth；它们在暂停时复制，恢复开发前
必须与真实代码重新核对。

| 目标范围 | 原始来源 | 属性 |
|---|---|---|
| `plans/02-s0-g1.4b3-close.md` | `s0-g1.4b3-close-brief.md` | active recovery contract |
| `evidence/active-s0-core-brief.md` | `s0-core-brief.md` | interrupted active slice |
| `design/10-*` 至 `14-*` | G1 auth/executor/release/dirfd/apply recon/brief | preimplementation + active amendments |
| `design/15-*` 至 `21-*` | G1.5/G1.6 brief/slices/recon | planned, not implemented |
| `design/22-*` 至 `24-*` | G2 recon/brief/G2-G5 gap analysis | blocked/amendment snapshots |
| `evidence/g1*.md` | completed G1 task reports | historical evidence |
| `evidence/progress-snapshot-2026-07-12.md` | `progress.md` | historical ledger snapshot |

## 产品与公开事实副本

| 目标 | 原始来源 | 属性 |
|---|---|---|
| `product/terminology-and-positioning.md` | `CONTEXT.md` | current project terminology, supplemented by this package |
| `product/decision-terminology-adr.md` | `docs/adr/0012-decision-terminology-not-zhupi.md` | accepted terminology ADR |
| `product/legacy-domain-glossary.md` | `docs/reference/glossary.md` | compatibility/history |
| `quality/public-capability-matrix-v0.4.2.md` | `docs/launch/capability-matrix.md` | published v0.4.2 truth, not feature branch status |
| `quality/g0-g5-release-checklist.md` | `docs/launch/checklist.md` | release checklist reference |

## UI 资产来源

| 目标目录 | 原始来源 | 属性 |
|---|---|---|
| `ui/assets/approved/` | `prototypes/tianshu-agent-os/audit-2026-07-11/*.png` | approved G0 target |
| `ui/assets/historical/audit-2026-07-10-*` | `audit-2026-07-10/*.png` | superseded |
| `ui/assets/historical/prototype-*` | `prototypes/tianshu-agent-os/artifacts/*.png` | intermediate/superseded |
| `ui/assets/references/comparison-*` | prototype QA artifacts | shell/layout comparison only |
| `ui/assets/brand/brand-current-128.png` | `web/public/brand.png` | documentation copy of product Logo |
| `ui/assets/launch-candidate/` | `docs/launch/assets/*`, `image/img.png` | G5 candidate, not shell Logo |

临时素材已在可能被系统清理前固化：

| 目标 | 临时来源 | 属性 |
|---|---|---|
| `ui/assets/references/source-control-composition-figma.png` | `/tmp/tianshu-figma-home.png` | early composition only |
| `ui/assets/references/source-edict-composition-figma.png` | `/tmp/tianshu-figma-task.png` | early composition only |
| `ui/assets/references/production-universe-empty.png` | `/tmp/tianshu-ui-audit/04-universe-empty.png` | old production semantics |
| `ui/assets/references/user-approved-frozen-shell-annotated.png` | user clipboard `11a4297a...png` | authoritative shell annotation |
| `ui/assets/historical/production-edict-list-before-g3.png` | user clipboard `a342e42d...png` | old production state |
| `ui/assets/negative/system-trust-card-remove.png` | user clipboard `b7c0d02b...png` | explicit anti-requirement |

## 本目录新增文档

`README.md`、`STATUS.md`、`SOURCE-OF-TRUTH.md`、`DEVELOPMENT-HANDOFF.md`、
`PRODUCT-ARCHITECTURE.md`、`IMPLEMENTATION-GUIDE.md`、`RISK-REGISTER.md`、
`VERIFICATION.md`、`SOURCE-MAP.md` 和 `ui/README.md` 是本次交接整理新增内容，
用于把分散快照变成 coding agent 可执行的入口层。
