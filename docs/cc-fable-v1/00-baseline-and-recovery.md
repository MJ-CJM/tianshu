# P0 —— 资产回收与基线重建

> 状态：`user_approval_pending`。本文档只定义操作与验收，不授权执行。
> 前置决策：[02 号文档](./02-decisions-for-approval.md) D1（回收策略）、D3（备份方式）。

## 1. 为什么 P0 优先于一切切片

| 事实 | 验证方式（2026-07-12 已做） | 含义 |
|---|---|---|
| `feat_codex_phase_1` 无远程 upstream | `git branch -vv` 无跟踪分支 | 44 个提交只存在于一台机器的一个目录 |
| G1.4b3 WIP 未提交 | `git status` 25 条目；`git diff --stat` = 17 files, +2703/−104 | 约 7,400 行多轮审查过的工作随时可因误操作/磁盘故障丢失 |
| merge-base = `d8631a2` = 当前 main = 当前分支 HEAD | `git merge-base` | 迁移是纯快进，**零冲突** |
| 两处 `docs/codex-v1` 字节级一致 | `diff -rq` 无输出 | 当前 staged 的副本可安全丢弃，用分支里已提交的版本 |
| 两 clone 同一 origin（`MJ-CJM/tianshu`） | `git remote -v` | 本地 remote 直连迁移可行 |

## 2. 设计原则

1. **不重做已审查工作**：44 个提交（约 2 万行）与 7,400 行 WIP 全部回收；
2. **不破坏源现场**：`feat_codex_phase_1` 分支指针不动，dirty tree 固化到独立冻结分支；
3. **每步可验证、可回退**：见第 4 节回退表；
4. **完成后单一现场**：本工作区成为唯一开发现场，源 clone 封存只读（D2）。

## 3. 操作序列

### P0.1 冻结源现场（在 `~/tiangong/tianshu`）

```bash
cd ~/tiangong/tianshu
# a. 确认无遗留验证进程（预期无输出）
ps -axo pid,etime,command | grep -E '[p]ytest|[r]uff|[m]ypy|[l]int-imports'
# b. 记录冻结前指纹（预期 25 / "17 files changed, 2703 insertions(+), 104 deletions(-)"）
git status --porcelain | wc -l
git diff --stat | tail -1
# c. 将 dirty tree（含 8 个 untracked 文件）固化到冻结分支
git checkout -b wip/g1.4b3-freeze
git add -A
git commit -m "wip: freeze G1.4b3 dirty tree for migration (pending S0 close)"
git checkout feat_codex_phase_1
```

说明：冻结提交后源工作树变干净（改动已进 `wip/g1.4b3-freeze`），属预期行为；
WIP 在 P0.6 于新现场原样还原。

### P0.2 离机备份

```bash
cd ~/tiangong/tianshu
git bundle create ~/tianshu-agentos-20260712.bundle --branches --tags
git bundle verify ~/tianshu-agentos-20260712.bundle
```

若 D3 批准推送（推荐，双保险）：

```bash
git push origin feat_codex_phase_1 wip/g1.4b3-freeze
```

### P0.3 迁移到当前工作区

```bash
cd ~/tiangong/tianshu-worktree/tianshu
# a. 处理本地未提交内容
git restore --staged docs/codex-v1 && rm -rf docs/codex-v1
#    （内容与分支内已提交的 7386cf3 字节级一致，快进后自动回来，无信息损失）
git checkout -- .idea/
#    （IDE 本地噪声；docs/cc-fable-v1/ 本包为 untracked，不受影响）
# b. 取回分支
git remote add codex-local /Users/chenjiamin/tiangong/tianshu
git fetch codex-local feat_codex_phase_1 wip/g1.4b3-freeze
# c. 纯快进合并（merge-base = 当前 HEAD，已验证）
git merge --ff-only codex-local/feat_codex_phase_1
# d. 核对
git log --oneline -2      # 预期 HEAD = 7386cf3 "docs: add Codex v1 development handoff"
git rev-list --count main..HEAD   # 预期 44
```

> 执行前重验一次 `git merge-base codex-local/feat_codex_phase_1 main`：
> 若 main 已前进（不再是 `d8631a2`），快进不成立，暂停并重评（见 03 风险表）。

### P0.4 重建运行环境

```bash
cd ~/tiangong/tianshu-worktree/tianshu
uv sync --frozen          # 按 uv.lock 重建 .venv，不改写锁文件
env -u VIRTUAL_ENV .venv/bin/python -VV     # 预期 Python 3.12.x
```

### P0.5 基线健康验证（干净 HEAD，恢复 WIP 之前）

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
env -u VIRTUAL_ENV .venv/bin/python -m ruff check .
env -u VIRTUAL_ENV .venv/bin/python -m ruff format --check .
env -u VIRTUAL_ENV .venv/bin/python -m mypy （按 pyproject 既有范围）
env -u VIRTUAL_ENV .venv/bin/lint-imports
```

预期：与 `codex-v1/evidence/progress-snapshot-2026-07-12.md` 记录的 G1.4b2 基线
量级一致（G1.4b1 时 full not-slow 为 2521 passed / 1 skipped，其后 5 个提交小幅
增减；以实际收集数为准，**不得有失败**）。此步同时验证迁移完整性与新 venv 可用性。

### P0.6 还原 G1.4b3 WIP 为 S0 起点

```bash
git cherry-pick -n codex-local/wip/g1.4b3-freeze
git reset                  # 退回 unstaged，还原 handoff 描述的 dirty tree 形态
# 指纹对齐（预期 25 / +2703/−104，8 个 untracked 路径见 codex-v1/STATUS.md）
git status --porcelain | wc -l
git diff --stat | tail -1
git ls-files --others --exclude-standard
```

此后即处于 `codex-v1/DEVELOPMENT-HANDOFF.md` 定义的 S0 恢复入口
（分支名从 `feat_codex_phase_1` 映射为 `feat_cc_fable_v1`，其余检查项照用）。

### P0.7 封存源 clone

```bash
cat > ~/tiangong/tianshu/FROZEN-2026-07-12.md <<'EOF'
此 clone 已于 2026-07-12 封存。Agent OS 唯一开发现场：
~/tiangong/tianshu-worktree/tianshu（分支 feat_cc_fable_v1）。
本目录仅作应急副本，禁止在此提交新改动。
EOF
```

### P0.8 台账初始化

- 在 [PROGRESS.md](./PROGRESS.md) 记录 P0 各步证据（指纹、bundle 路径与 verify
  结果、基线测试数字、WIP 还原对齐结果）；
- `docs/codex-v1` 内的历史文档**不修改**（快照属性）；分支/现场差异由本包
  README 的取代关系声明覆盖；
- 提交本包：`docs: add cc-fable-v1 execution package`（P0 完成后的第一个提交，
  独立于 S0 的 Commit A/B）。

## 4. 回退表

| 步骤失败 | 回退动作 | 损失 |
|---|---|---|
| P0.1 冻结中断 | `git checkout feat_codex_phase_1 && git branch -D wip/g1.4b3-freeze`（commit 未成功时 dirty tree 仍在工作树） | 无 |
| P0.3 快进被拒 | `git reset --hard d8631a2`（当前分支本就等于 main） | 无 |
| P0.5 基线红 | **停止**，不进 P0.6；先在源 clone 相同 HEAD 复跑判定是"迁移损耗"还是"历史遗留"，记录后再决策 | 无（只读验证） |
| P0.6 还原异常 | `git checkout -- . && git clean -fd -- src tests`（freeze commit 仍在，可重试） | 无 |

## 5. P0 出口条件（全部满足才进入 S0）

- [ ] bundle 备份存在且 `verify` 通过（D3 批准推送时：push 成功）；
- [ ] 当前工作区 HEAD = `7386cf3`，`git rev-list --count main..HEAD` = 44；
- [ ] `.venv` 重建完成，干净 HEAD 上 full not-slow 全绿 + 四项静态门禁通过；
- [ ] WIP 还原后指纹对齐 `codex-v1/STATUS.md`（25 条目 / 17 files +2703/−104 / 8 untracked）；
- [ ] 源 clone 封存声明就位；
- [ ] PROGRESS.md 已记录全部证据，本包已提交。

## 6. 被否决的备选（最终以 D1 裁决为准）

- **B. 只迁文档、代码重做**：丢弃约 2.7 万行经多轮独立审查的工作，无任何收益；
- **C. 回源 clone 继续开发**：保留双现场漂移风险，且当前会话、工具链与后续
  评审都在本工作区，来回切换成本更高。
