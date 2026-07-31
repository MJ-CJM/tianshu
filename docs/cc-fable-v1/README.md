# 天枢 Agent OS · CC-Fable v1 执行包

> **历史执行与证据包（2026-07-12 起）：** 本目录不再是当前分支的执行权威。
> 当前实现、支持边界、验证排除项和发布状态见
> [当前实现与支持边界](../CURRENT-STATE.md) 与
> [能力事实矩阵](../launch/capability-matrix.md)。`reports/`、`evidence/` 中绑定
> commit/hash/manifest 的内容保持不可变，只证明记录当时的源码与环境。

本目录是在当时工作区（`~/tiangong/tianshu-worktree/tianshu`，分支
`feat_cc_fable_v1`）继续天枢 Agent OS 工程的执行权威。它于 2026-07-12 基于
`docs/codex-v1` 交接包与现场核查建立，并于 2026-07-14 经 D8-A 收敛为
**精简 Developer Preview**：先完成核心竞争力闭环，延期工程另有可续作台账。

产品定位不变：

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

## 为什么需要本包

建立本包时，`docs/codex-v1` 描述的全部实现工作（G0–G1.4b2 的 44 个提交 + G1.4b3 约 7,400 行
未提交 WIP）位于另一个 clone `~/tiangong/tianshu` 的 `feat_codex_phase_1` 分支，
该分支**没有任何远程 upstream**。当时当前工作区是干净的 main 基线，两边已经形成
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

## 当时事实基线（2026-07-14）

- 唯一开发现场已迁移到本工作区与 `feat_cc_fable_v1`；P0、P1 与 S0 均已通过；
- S1.1–S1.5 已全部实现，最新实现基线为 `498b1e4`；
- 当前阶段是 **S1/G1.5 总门禁待执行**：full not-slow、显式 slow Wheel/manifest/
  fresh HOME 黑盒与 G1.5 报告；
- Wheel/sdist、离线 demo、Doctor/readiness 和 CI 构建路径已经形成，继续保留；
- D8-A 已取代 D4-A 作为当前交付范围；完整 G0–G5 仍保留为长期技术路线；
- 当时逐切片状态、测试证据和提交号以 [PROGRESS.md](./PROGRESS.md) 为准；不能替代
  当前工作树回归。

## 历史复盘顺序

1. [PROGRESS.md](./PROGRESS.md) —— 当前执行点与逐切片证据
2. [05-lean-developer-preview-scope.md](./05-lean-developer-preview-scope.md) —— **D8-A 当前交付范围**
3. [Lean Preview 实施计划索引](../superpowers/plans/2026-07-14-tianshu-lean-preview-index.md) —— S1 Gate 至候选收口的可执行计划套件
4. [01-master-plan.md](./01-master-plan.md) —— 完整路线与 D8 当前执行覆盖
5. [06-deferred-work-backlog.md](./06-deferred-work-backlog.md) —— 第一阶段后可直接续作的延期台账
6. [02-decisions-for-approval.md](./02-decisions-for-approval.md) —— D1–D8 裁决记录
7. [00-baseline-and-recovery.md](./00-baseline-and-recovery.md) —— 已完成的 P0 资产回收基线
8. [03-risk-register.md](./03-risk-register.md) —— 风险登记

## 当时审批状态

**当前批准范围：D8-A 精简 Developer Preview**（2026-07-14）。D1–D7 的迁移、
唯一现场、私有备份、执行纪律和权限边界继续有效；D8-A 取代 D4-A 的当前交付范围，
D5 的连续实施只适用于 [05 号文档](./05-lean-developer-preview-scope.md) 定义的
Lean 范围。延期工作按 [06 号台账](./06-deferred-work-backlog.md) 保留，需重新选择
工作包并批准计划后再启动。

公开仓库、tag、PyPI/GHCR、宣发等外部发布动作始终需要另行明确授权，
沿用 `codex-v1/SOURCE-OF-TRUTH.md` 的发布权限边界。
