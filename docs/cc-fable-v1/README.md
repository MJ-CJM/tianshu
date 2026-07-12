# 天枢 Agent OS · CC-Fable v1 执行包

本目录是在**当前工作区**（`~/tiangong/tianshu-worktree/tianshu`，分支
`feat_cc_fable_v1`）继续 G0–G5 工程的完整执行方案，由 Claude（Fable 5）于
2026-07-12 基于 `docs/codex-v1` 交接包与现场核查生成。

产品定位不变：

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

## 为什么需要本包

`docs/codex-v1` 描述的全部实现工作（G0–G1.4b2 的 44 个提交 + G1.4b3 约 7,400 行
未提交 WIP）位于另一个 clone `~/tiangong/tianshu` 的 `feat_codex_phase_1` 分支，
该分支**没有任何远程 upstream**。当前工作区是干净的 main 基线，两边已经形成
"文档在这里、代码在那里"的断裂。本包解决三件事：

1. **P0 资产回收**：把 44 个提交与 WIP 无损迁移到当前工作区，消除单点丢失风险；
2. **执行权威转移**：在新现场重建"顺序、切片、Gate、审批边界"的唯一权威；
3. **范围决策**：把需要用户裁决的事项集中成一份审批清单。

## 与 docs/codex-v1 的关系

本包**引用**而不复制 codex-v1；技术细节仍以 codex-v1 的 briefs/recons 为准。

| codex-v1 内容 | 在本包下的地位 |
|---|---|
| `STATUS.md`、`DEVELOPMENT-HANDOFF.md`、`README.md` 的"当前暂停点" | **被本包取代**。它们描述的分支/现场（`feat_codex_phase_1`）已由 P0 迁移到当前分支；文中恢复步骤经 [00 号文档](./00-baseline-and-recovery.md) 映射后仍有效 |
| `plans/01-rebaselined-execution.md` | 切片划分、Gate、工程纪律**沿用**；执行顺序权威转移到本包 [01-master-plan.md](./01-master-plan.md)（新增 P0，其余顺序一致） |
| `plans/02-s0-g1.4b3-close.md`、`design/13-*`、`14-*`、`evidence/active-s0-core-brief.md` | **沿用**，S0 技术契约 |
| `design/00`、`01`、`15-*` 至 `23-*`（briefs/recons） | **沿用**，各阶段技术事实源 |
| `design/24-g2-g5-gap-analysis.md` | **沿用**，G2–G5 差距与最小交付的权威 |
| `product/`、`ui/`、`quality/` | **沿用**（产品决策、视觉事实源、能力矩阵口径） |
| `SOURCE-OF-TRUTH.md`（权威顺序、状态枚举、迁移号规则、发布权限边界） | **沿用**；其中"本目录最新 STATUS.md"一项改读本包 [PROGRESS.md](./PROGRESS.md) |
| `evidence/` | 历史台账快照，其证据边界声明继续有效 |
| `RISK-REGISTER.md` | 由本包 [03-risk-register.md](./03-risk-register.md) 继承并扩展 |

## 当前事实基线（2026-07-12 现场核查，全部经 git 验证）

- 当前分支 `feat_cc_fable_v1` = main（`d8631a2`），**不含**任何 Agent OS 实现提交。
- 全部已完成实现在 `~/tiangong/tianshu` 的 `feat_codex_phase_1`（HEAD `7386cf3`）：
  44 个提交领先 main，含 G0 审批原型（`prototypes/tianshu-agent-os/`，6,570 行）、
  生产 `web/` 术语与 palette 校准（+2,938/−254）、版本化迁移 v1–v4、G1 全部安全边界。
- `merge-base(feat_codex_phase_1, main) = d8631a2` = 当前 HEAD →
  **迁移是纯快进（fast-forward），无冲突**。
- G1.4b3 约 7,400 行 WIP（17 tracked +2703/−104、8 个 untracked）在该 clone
  未提交；两处 `docs/codex-v1` 内容字节级一致；两 clone 同一 origin。
- 当前工作区无 `.venv`（uv 0.9.27 可用，P0 重建）；codex clone 为 Python 3.12.12。

## 必读顺序

1. [02-decisions-for-approval.md](./02-decisions-for-approval.md) —— 待你裁决的 7 项决策（**先看这个**）
2. [00-baseline-and-recovery.md](./00-baseline-and-recovery.md) —— P0 资产回收与基线重建
3. [01-master-plan.md](./01-master-plan.md) —— P0 + S0–S6 完整执行计划
4. [03-risk-register.md](./03-risk-register.md) —— 风险登记
5. [PROGRESS.md](./PROGRESS.md) —— 执行台账（P0 起启用）

## 审批状态

**2026-07-12 已获用户批准**（裁决全文见
[02-decisions-for-approval.md](./02-decisions-for-approval.md) 审批记录）：
D1 全量迁移 + P1 继承复审附加条件；D3 授权推送私有 origin；D4 完整 G0–G5；
D5 连续实施至 G5（S4 视觉终审、S6 外部发布授权单独保留）。

公开仓库、tag、PyPI/GHCR、宣发等外部发布动作始终需要另行明确授权，
沿用 `codex-v1/SOURCE-OF-TRUTH.md` 的发布权限边界。
