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
S0 review base: 1acec44 (docs 提交, 排除在 S0 diff 之外)
S0.1: complete (P0.6 即重新冻结: 指纹 25+1 条目 / +2703/-104 / 8 untracked 对齐 STATUS 无漂移; git diff --check 干净; 文件分组按 DEVELOPMENT-HANDOFF Commit A/B 归属确认)
S0.2: in_progress
  - 17 文件 owned suite 首跑: 10 failed / 333 passed / 2 skipped——全部失败根因一致: pre-ledger 恢复路径中 V4 _validate_workspace_foundation_schema 的"精确匹配 V4 reference"遇到 V5 形状表(apply_decisions 多 8 列/触发器被 V5 替换)而 fail closed
  - 裁决: V4 callback 为已提交冻结物, 其"精确匹配"在 V4=最新时正确, V5 加入后暴露 latent 假设——STATUS 所披露"V4 callback 曾被改写"即为被撤销的错误修法; 正确修法为框架层 canonical adopt: pre-ledger 库若与"全部迁移完成后的权威形状"语义等价则整体采纳只补 ledger, 否则逐版重放(V4 只会遇到 ≤V4 形状, 精确匹配依然成立); 对未来 V6+ 自动成立
  - 附带: historical fixtures 失真修正(真实历史库无 workspace 表); V5 present 分支触发器重建幂等性核查; 新增守卫测试(V4 形状库不被 adopt 误吞/adopt 幂等)
  - 修复已委托单写者实现代理(TDD), 验证链: storage 域 → 17 文件 suite → compileall/ruff/format/diff-check
  - 修复①完成 (canonical adopt): migration_ledger.py +56[adopt_migrations/ledger_exists, 事务化+并发安全] / migrations.py +~56[_matches_canonical_schema + run_migrations 分支] / 测试 +67[historical fixtures 失真修正(DROP workspace 8 表)+守卫测试 test_v4_shape_preledger_replays_v5_instead_of_adopt]; FTS 触发器豁免复用 V1 _EXPECTED_OPTIONAL_TRIGGERS 精确匹配机制; V5 触发器 DROP+CREATE 幂等性核查通过(无需改); RED 10 failed→GREEN: storage 域 124 passed, 17 文件 suite 344 passed/2 skipped/0 failed
  - 修复②完成 (callback 指纹守卫, P1 CRITICAL): 新增 tests/storage/test_migration_callback_freeze.py——MIGRATIONS 全部 upgrade callback + 2 个 validate helper 的源码 SHA-256 冻结; 变异验证: V4 callback 插行→精确命中该条目红, 恢复→8 passed
  - 注意: migration_ledger.py 新入 WIP 修改集, Commit A owned 清单追加之(active brief 7 文件清单写于此修复前)
  - 体量豁免裁决: workspace_service.py 2186 行/migrations.py 2156 行超 800 上限——S0 收口不做结构移动(保持已审查代码可追溯), 拆分挂 P1.R1/S1(见 04 号报告清单)
  - 静态门禁 (dirty tree 全项): compileall OK / ruff check 全过 / format 672 files 零 diff(修正 1 个新测试文件) / lint-imports 2 kept / mypy 108 files Success / git diff --check 干净
  - 独立审查: 已派干净上下文审查代理(Commit A 域, spec/安全/质量三维 + adopt 与指纹守卫专项), 结论待回
S0.2+S0.3: complete (Commit A = 29ef814 "feat: add governed workspace apply authority", 15 文件)
  - 独立审查结论: SPEC PASS / SECURITY PASS / QUALITY APPROVED, C/I=0, MINOR×4
  - required checks 逐项证据确认(V5 additive/authority binding/one-time decisions/safe receipts/locks/root-anchored+rollback/symlink+TOCTOU/pending ownership/exact identities/POSIX mode/CAS/failure injection/rollback truth——各有测试实跑绿)
  - STATUS 五项暂停前修复逐项落地确认: ①POSIX mode(_desired_posix_mode+fail-closed 测试) ②expiry 原子 claim(version CAS+V5 触发器) ③close_lease 锁与 pending 保护 ④V4 改写已撤销/V5 additive(指纹+拒绝测试) ⑤rollback 真值(仅实际 restore 成功才记 succeeded)——全部实跑绿
  - MINOR 处置: #1(指纹表缺 adopt 门控)已采纳修复——_matches_canonical_schema+adopt_migrations 入冻结表(10 passed); #2(adopt 不重跑语义校验, 实际不可达)记录不改; #3(PRAGMA f-string 标识符校验, 已实测不可注入且 fail-closed)挂 S1 migrations 拆分顺路; #4(read_worktree_entry 每次新开 AnchoredRoot, 只读预检无失败场景)记录
S0.4: complete (Commit B = a9106aa "feat: expose governed workspace apply surfaces", 12 文件)
  - 独立审查结论: SPEC PASS / SECURITY PASS / QUALITY APPROVED, C/I=0, MINOR×2
  - 专项确认: actor 全链路 server-derived(伪造 actor 字段 422 且不达 service); token 仅存在于一次性签发响应(设计使然), 视图白名单投影/错误固定消息/CLI stdin-only+redaction 多点实测无泄露
  - required checks: 四路由语义+路由面锁定 / workspace:apply 三层强制(中间件+API+service) / CLI 退出码契约+防注入 / capability 精确证据(ENFORCED 升级有测试证据, 限制诚实披露)
  - MINOR 处置: #1(中间件尾斜杠末段匹配为防御纵深冗余, 另两层独立强制且 Starlette 仅规范路径执行 handler, 无可利用场景)记录, S0 报告中说明中间件非唯一 scope 门; #2(_ERRORS 与 service 错误码维护耦合, 已核验超集无缺口+fail-closed 有测试)记录, 共享注册表评估挂 S2 顺路
S0.5: complete (唯一一次 full not-slow Gate: 2678 passed / 2 skipped / 1 deselected / 0 failed, 652.82s——较迁移基线 2543 净增 135 个 G1.4b3 测试; 17 warnings 均历史已知类; 报告 reports/g1.4b3-report.md)
=== S0 收官: G1.4b3 = passed (Commit A 29ef814 + Commit B a9106aa + 报告, clean tree) ===
下一阶段: P1.R1 (execution_gateway 四拆, 纯移动) → S1/G1.5
```
