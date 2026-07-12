# CC-Fable v1 执行台账

> 规则：每个切片一条记录，包含 commit、测试证据与审查结论。状态枚举沿用
> `codex-v1/SOURCE-OF-TRUTH.md`（证据：`implemented / focused_verified /
> automation_passed / external_pending / user_approval_pending / passed`；
> 排期：`planned / in_progress / blocked_by_upstream / superseded_snapshot / passed`）。
> 旧台账（另一轮工程治理与 Agent OS G0–G1.4b2）见
> `codex-v1/evidence/progress-snapshot-2026-07-12.md`，不在此重复。

## 当前状态

```yaml
package_status: approved_2026-07-12 (D1 附加 P1 复审; D4 完整范围; D5 连续实施; D3 push 已授权)
current_stage: S0 G1.4b3 close (P0 passed, P1 passed)
sole_worksite: ~/tiangong/tianshu-worktree/tianshu (feat_cc_fable_v1)
source_clone: ~/tiangong/tianshu (FROZEN 2026-07-12)
```

## 台账

```
=== P0 资产回收 (2026-07-12) ===
审批: D1=A+P1复审附加条件 / D2=A / D3=A(push已授权) / D4=A完整 / D5=B连续 / D6,D7=默认 (见 02 号文档审批记录)
P0.1: complete (无遗留进程; 冻结前指纹 25 条目 / 17 files +2703/-104 对齐 STATUS.md; freeze commit 08e3742 @ wip/g1.4b3-freeze; 源树冻结后 clean)
P0.2: complete (bundle ~/tianshu-agentos-20260712.bundle 14M, verify "complete history"; push origin feat_codex_phase_1 + wip/g1.4b3-freeze 均 [new branch] 成功)
P0.3: complete (staged docs/codex-v1 副本丢弃[与 7386cf3 字节级一致已验证]; .idea 噪声还原; merge-base 重验=d8631a2 仍成立; ff merge 后 HEAD=7386cf3, main..HEAD=44 commits)
P0.4: complete (uv sync --frozen --python 3.12 --all-extras; Python 3.12.12 对齐 codex 基线; pytest 9.0.3/ruff/lint-imports 就位。备注: 首次 uv sync 默认选 3.14 且缺 dev extra, 已纠正)
P0.5: complete (干净 HEAD full not-slow: 2543 passed / 1 skipped / 1 deselected, 250s——对齐 G1.4b1 基线 2521 + G1.4b2 增量; 15 warnings 记录[coroutine acompletion never awaited 等历史已知类]; 静态门禁四项全绿: ruff check 全过 / format 663 files 零 diff / lint-imports 2 kept / mypy 108 files Success)
P0.6: complete (cherry-pick -n 08e3742 + reset; 指纹 26 条目[25 WIP + 本包目录] / 17 files +2703/-104 / 8 untracked 与 STATUS.md 逐一对齐; git diff --check 干净)
P0.7: complete (FROZEN-2026-07-12.md 已置于源 clone)
P0.8: complete (本包提交, hash 见 git log "docs: add cc-fable-v1 execution package")
=== P0 出口条件核验: 6/6 满足 → P0 passed ===
P1: complete (04-inherited-code-review.md; 总评"可保留, 需按排期局部重构, 不需大改"; 1 CRITICAL[迁移 checksum 不含 callback 源码指纹→并入 S0.2] + 6 IMPORTANT[execution_gateway 2495 行等体量超标→重构候选清单] + 3 MINOR; 新增 P1.R1 切片[S0 后拆 execution_gateway]; "明确不动"清单已锁定)

=== S0 G1.4b3 收口 (开始) ===
S0.1: in_progress (P0.6 即重新冻结: 指纹对齐 STATUS 无漂移; 分支映射 feat_cc_fable_v1/HEAD 7386cf3 已记录)
```
