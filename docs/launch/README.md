# Lean Developer Preview Candidate · Launch Kit

当前版本为 **0.4.2**。本目录是私有工作分支中的候选证据包，不是外部发布材料。

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

`publication_status`: `not_authorized`。不得据此公开仓库、push、tag、release、上传
PyPI/GHCR、发布官方容器或对外宣发。

## 当前证据

| 材料 | 事实 | 状态 |
|---|---|---|
| [能力事实矩阵](capability-matrix.md) | 默认值、支持面、保证、非保证、证据 | `implemented` truth index |
| [Lean Preview 使用指南](../usage/lean-developer-preview.md) | source/exact Wheel、单一黄金 Demo、严格 verifier | `implemented` |
| [最终 Demo 报告](../cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json) | 13 步、`fixture=false`、源码/Wheel/证据绑定 | verified local evidence |
| [桌面 Web 报告](../cc-fable-v1/reports/s4-core-web-report.md) | 三张核心页自动化 | `automation_passed`; `user_approval_pending` |
| [Lean Core evolution 报告](../cc-fable-v1/reports/s5-lean-evolution-report.md) | 技能候选、门禁、分流、回滚 | `implemented`; full G4 `external_pending` |
| [延期路线图](../cc-fable-v1/06-deferred-work-backlog.md) | 恢复条件与验收证据 | `deferred` / `external_pending` |

## 支持边界

- Ubuntu + Python 3.12 是首个正式目标；保留批次实际验证于
  `Darwin/arm64/Python 3.12.12`，不能替代 Ubuntu 外部复验。
- 产品面为 local desktop Web only；无移动端产品承诺。
- 运行边界为单机、single-node SQLite、host-administrator trusted。
- remote MCP 与 open stdio MCP 为 `disabled`；Keqing 为 `experimental`。
- official container、PyPI、GHCR、OpenHands、ROI、cost calibration、full G4、full G5
  均为 `deferred` 或 `external_pending`。

状态词必须保持分离：`implemented`、`disabled`、`deferred`、`experimental`、
`external_pending`、`user_approval_pending`。局部通过、历史计划或截图不能提升状态。
