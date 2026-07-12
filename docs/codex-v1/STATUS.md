# 当前开发状态

## Snapshot

```yaml
snapshot_date: 2026-07-12
timezone: Asia/Shanghai
branch: feat_codex_phase_1
implementation_base_head: 1b51bcd9
handoff_bundle_commit: resolve from git log subject "docs: add Codex v1 development handoff"
handoff_bundle_tracked: true
worktree_clean: false
current_stage: G1.4b3 / S0 governed apply close
next_slice: re-freeze S0 core after pause, then complete S0.2-S0.5
implementation_paused_by_user: true
full_suite_confirmed_for_latest_tree: false
g1.4b3_report_exists: false
```

## Gate 状态

| Gate/切片 | 状态 | 说明 |
|---|---|---|
| G0 | `passed` | 已提交；产品事实、术语、原型、迁移/备份和启动基线完成 |
| G1.1–G1.4b2 | `passed` | 已提交；详细 commit 与测试快照见 `evidence/progress-snapshot-2026-07-12.md` |
| G1.4b3 / S0.1 | `superseded_snapshot` | 曾完成只读冻结，但统计已因后续局部修复漂移；恢复时必须重做 |
| G1.4b3 / S0.2–S0.5 | `in_progress` | dirty tree；未提交、未终审、未获得最新完整 focused/full 结果 |
| G1.5 | `planned` | 只有 brief、slice、recon |
| G1.6 | `planned` | 只有 brief、slice、recon |
| G2 | `blocked_by_upstream` | 必须等待完整 G1 handoff 与最终 migration prefix |
| G3 | `blocked_by_upstream` | G0 原型通过；正式 Web 必须等待 G2 权威 API |
| G4 | `blocked_by_upstream` | 必须等待 G2 Evidence/RunState 与 G3 真实 UI |
| G5 | `blocked_by_upstream` | 必须等待 G3/G4；外部发布另需用户授权 |

## 当前 dirty code 快照

暂停时的 tracked code/test diff：

```text
17 tracked files changed
+2703 / -104
```

另有 8 个未跟踪的生产/测试文件，共约 4,738 行：

- `src/tianshu/cli/commands/workspace.py`
- `src/tianshu/executor/anchored_fs.py`
- `src/tianshu/executor/workspace_apply.py`
- `src/tianshu/gateway/workspace_api.py`
- `tests/cli/test_workspace_commands.py`
- `tests/executor/test_workspace_apply.py`
- `tests/gateway/test_workspace_apply_api.py`
- `tests/services/test_workspace_governed_apply.py`

Tracked 修改集中于：

- `app.py`、CLI 注册、Auth、capability 和 workspace REST 表面；
- workspace model/repository/migration；
- WorkspaceService、GitBackend；
- storage、executor、gateway、compat 对应测试。

这些数字是文档整理时快照。恢复开发时先重新运行 `git status`、
`git diff --stat` 和 migration ledger 检查，不得直接复用数字。

## 暂停前最新工作

S0 core 自审在既有 governed-apply 实现中发现并按 TDD 局部修复：

1. 发布后非 Git POSIX mode 从 `0600` 漂移到 `0640` 仍可能记录成功 receipt；
2. authority expiry/active lease 未全部纳入原子 DB claim；
3. `close_lease` 可能绕过 source/process lock 并留下 `CLOSED + pending`；
4. V4 migration callback 被改写但 checksum 冻结，V5 不是纯 additive；
5. pre-mutation 失败 receipt 可能把 rollback 误报为 `succeeded`。

证据边界：

- 早期 core owned suite：`339 passed / 2 skipped`，发生在上述全部修复前；
- POSIX mode 修复后的第一次 owned suite：`340 passed / 2 skipped`，但在后续四项修复前 collection，不代表最新树；
- 后续六个 targeted tests 曾报告 `6 passed`；
- 最新完整 owned suite 在用户暂停时被终止，不能标记通过；
- 最新工作树未运行 full `pytest -m "not slow"`；
- `.superpowers/sdd/s0-core-report.md` 尚未生成。

因此任何 agent 都必须把这些改动视为 **WIP、需要重新验证**，不能直接提交或宣布 G1.4b3 完成。

## 已提交锚点

- `1b51bcd9` — rebaselined G0–G5 execution plan
- `302cba3` — G1.4b2 最后一个实现提交
- G0 checkpoint：`edf638a`、`20c4213`、`673fcef`、`13fbdc7`

完整已完成切片 commit 列表见 [progress snapshot](./evidence/progress-snapshot-2026-07-12.md)。

## 恢复前必须完成

1. 确认没有遗留 pytest/ruff/mypy 子进程。
2. 重新冻结 branch、HEAD、tracked/untracked diff 和迁移序列。
3. 审读最新 core diff，确认暂停没有留下半个 edit/stage/commit。
4. 先运行新增 targeted regressions，再运行 active core brief 的 17 文件 suite。
5. 完成 compileall、Ruff、format、mypy、import-linter 和 diff check。
6. 独立 review core；修完 Critical/Important 后才提交 Commit A。
7. 再处理 REST/Auth/CLI/capability Commit B。
8. 由主控制者唯一运行一次 full non-slow Gate，生成 G1.4b3 报告。

## 外部待验证

Docker daemon、Linux/其他架构、真实 OpenHands managed adapter、真实 provider ROI、
100+ 成本 outcome、GitHub OIDC、PyPI/GHCR、VoiceOver、三个独立环境和七日成本窗口
均未完成。不得从本机 fixture 推断为通过。
