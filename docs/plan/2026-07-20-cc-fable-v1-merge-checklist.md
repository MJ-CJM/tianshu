# cc-fable-v1 合入执行 Checklist

- 日期：2026-07-20
- 对象：`cc-fable/feat_cc_fable_v1`（275 commits，从 `d8631a2` 干净分叉）
- 结论摘要：核心价值真实（持久裁决/崩溃恢复/skill 进化闭环/可安装 wheel/前端产品面），
  但约 20-25% 的 src 代码属仪式化冗余、scripts 4.8k 行 gate 脚本为一次性发布仪式、
  docs 中 2.5MB evidence 回执与过程公文对主干无维护价值。
  策略：**整体 merge（不 squash）+ 减法 commit + 技术债冻结清单**。

---

## 阶段 0：合入前拍板（人工决策，逐项确认）

- [ ] **D1 合并方式**：merge `--no-ff`，不 squash（Gate 报告与 evidence 绑定 commit SHA，squash 即失效）
- [ ] **D2 ADR-0012 术语「朱批/审批 → 裁决」**：接受 / 拒绝（拒绝需改约 35-39 处文案 + terminology 测试）
- [ ] **D3 zh-classic 古风味**：接受术语统一稀释 / 单独为 zh-classic 恢复古风措辞（需同步改 terminology 三语映射）
- [ ] **D4 S4 视觉终审**：亲自跑 web，确认控制中心 `/control`、Edict 详情、进化中心 `/evolution` 三张页面
  （整条证据链只剩此项 `user_approval_pending`）
- [ ] **D5 旧就地审批交互**：确认接受 DecreeModal / PendingToolCallCard 删除，审批统一跳详情页裁决面板

## 阶段 1：merge 操作

- [ ] 新建合入分支：`git checkout -b merge/cc-fable-v1 d8631a2`
- [ ] `git merge --no-ff cc-fable/feat_cc_fable_v1`（分叉点即当前 HEAD，预期零冲突）
- [ ] 后端全量验证：`.venv/bin/python -m pytest`（预期基准：non-slow 约 2900+，full 3800+ passed）
- [ ] 前端验证：`cd web && npm ci && npm run lint && npm run typecheck && npm test && npm run build`
- [ ] wheel 冒烟：`./scripts/build_release.sh` 出包成功且含 web static

## 阶段 2：减法 commit（一次 `chore: post-merge slimming` 完成）

### 2a. 纯删除（低风险，删文件即可）

- [ ] `docs/cc-fable-v1/evidence/lean-preview/` 12 个批次中，仅保留最终批次
  `20260719T083725Z-01da3844dde7` 与 `lean-preview-candidate.json`、`s5-lean-evolution.json`，
  其余历史/失败批次删除（约 2MB JSON 回执）
- [ ] `docs/codex-v1/` 中已被 cc-fable-v1 取代的 `STATUS.md` / `DEVELOPMENT-HANDOFF.md` 移入
  `docs/codex-v1/archive/`（plans/ 与 briefs 保留——是 P2-P4 延期工作的技术事实源）
- [ ] `.superpowers/sdd/` 15 个 tracked 报告：二选一
  - [ ] 方案甲：保留（完整审计链）
  - [ ] 方案乙：关键结论并入 `docs/cc-fable-v1/reports/` 后整目录剔除
- [ ] `docs/codex-v1/ui/assets` 7.2MB 截图：若近期继续 UI 迭代则保留（S4 视觉事实源），否则移出 git
- [ ] ⚠️ `web/e2e/__screenshots__/` 24 张视觉基线**必须保留**（测试资产，勿删）

### 2b. 仪式化脚本归档（低风险）

- [ ] 归档至 `docs/cc-fable-v1/archive-scripts/`（硬编码分支 commit SHA、未接入 CI、一次性发布仪式）：
  - [ ] `scripts/check_lean_preview_candidate.py`（1136 行，PHASE_SPECS 硬编码 10 个 SHA）
  - [ ] `scripts/verify_lean_preview_evidence.py`（956 行）
  - [ ] `scripts/check_s5_lean_evidence.py`（1247 行）
  - [ ] `scripts/check_s3_core_evidence.py`（620 行）
  - [ ] `scripts/record_lean_preview_gates.py`（169 行）
- [ ] 保留（可复用资产）：`scripts/build_release.sh`、`scripts/record_lean_preview_build_provenance.py`、
  `scripts/_trusted_local_process.py`
- [ ] 同步删除测发布脚本本身的测试：`tests/launch/` 中仅针对上述归档脚本的用例
  （逐文件确认，`tests/launch` 共 4073 行，非全删——Lean Preview demo 端到端相关部分随 2d 处置）

### 2c. 仪式性测试放宽（中风险，删测试需同步过 CI）

- [ ] 删除 `tests/test_public_docs_truth.py`（757 行，硬编码版本号 + 锁 9 个文档精确措辞，
  每次 bump/改 README 均需改测试）
- [ ] 删除 `tests/universe/test_documentation_truth.py`（78 行，同类）
- [ ] `web/src/test/brandShell.contract.test.tsx`：删除 `BRAND_SHA256` 字节哈希断言与标语精确措辞断言，
  保留侧栏结构 / locale label（含「彩蛋」）断言
- [ ] `web/src/i18n/terminology.test.ts`：保留三语术语表断言，**删除全源码禁语扫描**部分
  （防回退价值 < 日常迭代摩擦）

### 2d. 代码去仪式化（中风险，需跑测试验证）

- [ ] `src/tianshu/lean_preview_demo.py`（1151 行）移出 `src` 包 → `examples/lean_preview_demo.py`，
  移除 pyproject 中 `tianshu-lean-demo` console script 与打包引用；相应调整 `tests/launch/` 引用
- [ ] 删除被废弃的孤儿 sweep 死路径（`scheduler.py` `_recover_orphan` 中
  「有 durable preparer 则直接 return」的旧分支及其专属测试）
- [ ] `notifier/delivery_outbox.py`（645 行，docstring 自认只保证进程内送达）：
  - [ ] 方案甲（推荐）：评估 wiring 后整层移除，通知回归单层 best-effort
  - [ ] 方案乙：保留但在模块头标注 FROZEN，不再扩展
- [ ] 口径修正：全库文档统一「进化闭环当前仅 SKILL 一种真实生效」，
  CODE/MEMORY/PERSONA/POLICY 标注为 fail-closed 占位（`docs/launch/capability-matrix.md`
  已如实，其他提及「五种候选」处对齐它）——同「廷议 confidence 占位值」教训

## 阶段 3：技术债清单（接受但冻结——不删、不再投入，触发条件到达再重评）

| 项 | 规模 | 冻结理由 | 解冻触发条件 |
|---|---|---|---|
| fencing token 全链路 | ~2000 行 / 11 文件 102 处 | 已织入核心，拆除成本 > 保留成本；lease 层是真需求 | 出现多实例 / 多 worker 部署 |
| HMAC command grant | execution_gateway 内 | 能力纪律有价值，实现超配但无害 | 出现真实进程外插件边界 |
| trusted-local 下不执行的远程 auth 栈 | ~500 行 | secure-remote 可真实开启，超前但非空壳 | 开源后出现远程部署用户 |
| 四个候选 adapter 壳（code/memory/persona/policy） | 24-71 行/个 | fail-closed 断得干净，壳本身很薄 | 某类型出现真实生产者需求 |
| 101 个 V1 后缀模型 | 命名层面 | 改名会造成巨量 churn | 永不（随重构自然消化） |

## 阶段 4：合入后偿还排期（新迭代立项，非本次合入范围）

- [ ] 拆 `executor/workspace_service.py`（单类 61 方法 2200 行 god class）
- [ ] 拆 `executor/approvals.py`（单类 49 方法 1600 行，吃过 11 个 fix，主要维护摩擦点）
- [ ] 拆 `evolution/promotion.py`（1960 行：adapter / service / journal 三块分文件）
- [ ] outbox durable consumer 分级：成本统计、位面画像类降级为 best-effort 本地订阅
- [ ] 其余超 800 行新文件按项目规则逐步归位（git_backend 2033、evidence/service 1323、auth 1094）

## 最终验证

- [ ] `.venv/bin/python -m pytest`（full）全绿
- [ ] `cd web && npm test && npm run build && npx playwright test` 全绿（视觉基线 24 张不变）
- [ ] `ruff check` / `mypy` / `import-linter` 全过
- [ ] `./scripts/build_release.sh` 出包并 fresh-HOME 冒烟（`tianshu doctor` ready）
- [ ] 合入 PR 描述引用本 checklist 与评审结论
