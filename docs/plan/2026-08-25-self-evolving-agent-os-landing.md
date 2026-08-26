# 天枢自进化 Agent OS 重构落地方案

> **文档性质**：可直接开工的重构落地方案（最终合成版）。由三份独立视角方案（风险优先 / 最小改动 / 目标纯度）经总架构师合成，合成准则按优先级为：①与当前源码相符；②每步不破坏 fail-closed 不变量、可独立合入与回退；③最少过渡债（对象边界与目标架构一致）；④篇幅完整（分歧处的取舍理由写在各阶段"决策与取舍"小节）。
> **修订记录**：本方案经三路校验（符号核对/可行性/完备性）修订并落盘，日期 2026-08-25。
> **基线**：集成分支 `feat/plugin-v1`（近端合并提交 `a8a03071`，已含 P4b PR #109）；目标态依据 [../design/self-evolving-agent-os/README.md](../design/self-evolving-agent-os/README.md)、[target-architecture.md](../design/self-evolving-agent-os/target-architecture.md)、[domain-and-governance.md](../design/self-evolving-agent-os/domain-and-governance.md)、[migration-roadmap.md](../design/self-evolving-agent-os/migration-roadmap.md)，以及独立评审 [review-and-implementation-plan.md](../design/self-evolving-agent-os/review-and-implementation-plan.md) 与 [architecture-comparison.md](../design/self-evolving-agent-os/architecture-comparison.md)。所有"文件:行"引用均经本轮源码核对（非转引）。
> **迁移编号基线**：P1 已占用 V31 `0031_system_snapshots`；P3 已冻结 V32 `0032_runtime_generations`；P4a 已冻结 V33 `0033_evolution_policies`；P4b 已冻结并由 PR #109 合入 V34 `0034_run_subject_assignments`，集成分支 live tail 为 V34。当前 P5 checkout 已冻结 V35 `0035_executor_candidate_kind`。历史 V25 `0025_persona_allowed_paths` 与 V30 `0030_consultation_rounds` 编号保持不变。
> **流程约定（2026-08-25 用户授权）**：每阶段走 issue → `feat/`|`fix/` 分支 → PR（`Closes #n`），PR 目标统一为集成分支 `feat/plugin-v1`；亲验 `gh pr checks` 全绿后由执行方直接合入，不逐个等待用户确认；全部阶段完成后由用户在 `feat/plugin-v1` 做总体验证，tag 仍由用户操作。跑 Python 一律 `.venv/bin/python`；前端改动后单跑 `cd web && npm run typecheck`（vitest/eslint 不查类型）。

## 实施状态

| 任务 | 状态 | 日期 | 验证 |
|---|---|---|---|
| P0 术语冻结与 Ring 0 守卫 | ✅ 已合入（PR #91） | 2026-08-25 | 4/4 import 契约；46 项架构测试；全量 4746 passed、2 skipped；Web typecheck/test/lint/build 全绿 |
| P1 SystemSnapshotV1 影子双写 | ✅ 已合入（PR #93） | 2026-08-25 | V31 `0031_system_snapshots`；202 项 P1 聚焦测试；全量 4806 passed、2 skipped；Web typecheck/338 tests/lint/build 全绿；真实 Demo 开启态三面摘要对账、关闭态零 binding/artifact |
| P2 ContributionHandle | ✅ 已合入（PR #95） | 2026-08-25 | 82 项 P2 聚焦测试；全量 4830 passed、2 skipped；六类 owned handle、逆序释放、stale 身份保护及 MCP 重新发现/断连/shutdown 回收；原生 live/demo 冒烟、插件面 fail-closed 与 CI 5/5 均绿 |
| P3 Pi 执行器代际与 continuity 固定 | ✅ 已合入（PR #99） | 2026-08-26 | V32、materializer、registry binding、continuity、reconciler、readiness、可观测性与 Web 投影；backend 5096 passed、2 skipped；Web typecheck/339 tests/lint/build 与全部静态门禁通过 |
| P4a EvolutionPolicy 与 per-subject canary 写侧基础 | ✅ 已合入（PR #107，merge `b94d4846`） | 2026-08-26 | V33；严格 CAS、frozen 三闸、repo-level 执法、promote journal guard；目标分支 CI 6/6 全绿 |
| P4b per-subject 运行分配与 UI | ✅ 已合入（Issue #108；PR #109，merge `a8a03071`） | 2026-08-26 | V34；assignment set 持久封存、1..64 原子批写、continuity 选择、运行时深冻结、逐 assignment provenance/digest 复验与 truthful UI；最终后端 5270 passed、2 skipped、24 slow deselected；Web 347 passed，静态检查与生产构建通过 |
| P5 CandidateKind.EXECUTOR 全链路 | 🟢 当前 checkout 实现与本地门禁完成（Issue #110；PR #111，CI pending） | 2026-08-27 | V35；EXECUTOR Candidate、高危 Decision、精确 generation authority、per-subject canary、promote/rollback saga、drift scanner 与 Keqing/Evolution 投影；P5 聚焦 190、迁移/数据 122、Web 350、E2E 32 均通过，静态门禁与生产构建通过 |
| P6 进程级 snapshot 重启与 last-good | ⬜ 未开始 | — | — |
| P7 声明式内容每 run 冻结视图 | ⬜ 未开始 | — | — |
| X1 WS 出站所有权过滤 | ✅ 已合入（PR #89） | 2026-08-25 | 17 项所有权用例；32 项定向测试；全量 4746 passed、2 skipped；CI 5/5 绿 |
| X2 重试判据收敛 | ✅ 已合入（PR #101） | 2026-08-26 | 17 个 FailureReason 值不变；5 类可重试真值表、异常类型映射、canonical failure ledger、generation_retired 与 unknown fail-closed 已覆盖；240 项聚焦回归及静态门禁通过；目标分支 CI 6/6 全绿 |
| X3 allowed_paths 受理校验 | ✅ 已合入（PR #103） | 2026-08-26 | `_insert_edict` 首写前权威校验；内建相对 glob 精确值豁免；非法 fresh 请求五表零写；历史 replay 与 409 优先级保持；101 项聚焦回归及静态门禁通过；目标分支 CI 6/6 全绿 |
| X4 schema 落盘与 CI 门禁 | ✅ 已合入（PR #97） | 2026-08-26 | 9 个 V1 schema 统一登记；105 项聚焦测试；全量 4866 passed、2 skipped；Ruff/format/Mypy/lint-imports 与 Web typecheck/338 tests/lint/build 全绿；合并提交目标分支 CI 6/6 全绿，含严格 Required CI 汇总与 backend/frontend clean-worktree guard |
| X5 路由 scope 表 | ✅ 已合入（PR #105） | 2026-08-26 | 263 条 protected、15 条 public 显式规则覆盖 277 个 route inventory refs；移除 API/method fallback，锁定 OR/AND、docs/WS/MCP/static/readiness/webhook 语义；运行时 first-match 与两类 shadow 负例已守卫；目标分支 CI 6/6 全绿 |

### X5 实施裁决（Issue #104，PR #105）

X5 已将 scope 判定收敛为显式、不可变的路由策略表：263 条 protected 规则和 15 条 public 规则覆盖当前 277 个 HTTP、WebSocket、MCP mount 与静态兼容 route inventory refs，不再保留 `/api/**` 或按 HTTP method 猜测 scope 的 fallback。策略表明确表达 global-read 的 `api OR admin`、workspace apply 的 `api AND workspace:apply`，并分别锁定 docs/OpenAPI、`/api/ws`、MCP 六种方法、SPA/assets 静态面、auth-aware readiness 与动态 webhook 精确 POST 路径的既有语义。

兼容性裁决是：已认证请求命中 protected namespace 但没有注册路由时返回 `404 route_not_allowed`，从而保留既有 router 404 行为，同时保证未知路由不再继承默认 scope；匿名请求仍先经过认证闸门，未登记 WebSocket 仍 fail-closed。覆盖门禁同时按模板与真实运行时 first-match 检查唯一归属，并用“动态 `/api/.../{id}` 抢先遮蔽后置精确 admin 路由”和“public SPA catch-all 抢先遮蔽后置精确 admin 路由”两类负例证明 shadow 能被检出。最终 X5 聚焦矩阵 216 passed，完整 timing-sensitive 切片 26 passed；独立安全复核 APPROVED，并额外完成 184 项聚焦回归、33 项 health 回归及 261 个顶层具体路由的 Starlette 首匹配零错位核对。Ruff、Mypy（141 source files）和四项 import-linter 契约通过；本阶段无数据库迁移，并已通过 PR #105 合入 `feat/plugin-v1`，目标分支 CI 6/6 全绿。

### P4a 实施裁决（Issue #106，PR #107）

P4a 将每个 subject 的进化授权落成显式 `EvolutionPolicyV1` 与 V33 `evolution_policies` 表。可表达 mode 仅为 frozen、manual、canary，`auto` 在类型与数据库 CHECK 两层均不可表达；无行仍按 skill=canary、其余当前 kind=manual 祖父化，但 GET API 对缺行返回 404，不制造 durable 假事实。PUT 使用严格 missing-row CAS：只有“无行 + `expected_version=null`”能首插 version 1，已有行必须精确匹配版本，陈旧的相同内容重试仍冲突，kind 不可变。成功响应固定为 `{data, correlation_id}`，策略行、SystemAudit `evolution_policy_updated` 与同名 outbox event 在同一 UoW 原子提交。

兼容性裁决是：V33 虽已建立 `(kind, subject_key) WHERE lifecycle='canary'` partial unique index，P4a 仍不开放多 subject 并行 canary。旧 `get_routable_candidate()` 在 P4b 前只能解释一个全局 authority，因此 `start_canary` 早拒与 `save_candidate` 唯一 UPDATE 权威共同保留临时全局单 canary backstop；P4b 切换多值读路径时再收窄为 subject 级排他。frozen 在第一笔 artifact 前阻止 propose、在 routing 写入前阻止 start_canary、在 intended journal 前阻止 promote；stage/evaluate 可收口，rollback 始终放行。repo 层同时守住模式、合约 allocation 上限与显式 policy 上限，不能靠绕过服务层规避。

安全裁决是：policy upsert 在同 subject 存在未由相同 command-key completed 行收口的 promote intended/applied journal 时返回冲突。由此 policy 无法在 `intended → adapter.activate → applied/final commit` 窗口中翻转，避免外部已经激活而 durable candidate 因 frozen 防线停留在 CANARY。V33 migration checksum 为 `725e801902e3e8e321a369164d3a5728adb40f96a8c77f2644820a6f69671fc7`，upgrade callback source fingerprint 为 `15aa3bd9527ca0c12be760c8213d029ac554e9ca5b6c7e117ad03c0fd4030d3c`。P4a 已由 PR #107 合入 `feat/plugin-v1`（merge `b94d4846`），目标分支 CI 6/6 全绿。

### P4b 实施裁决（Issue #108，PR #109，已合入）

P4b 已在实现分支完成 per-subject 多值路由。V34 不是可逐行追加的松散集合：每个 Memorial
的完整 assignment set 以 canonical hash 和 size 持久封存，1..64 条通过 batch + SAVEPOINT
原子写入，65 条在零 assignment 写入时拒绝；`sealed_insert`、`no_update`、`no_delete` 与读取时
的 set hash/size 复验共同阻止局部集合冒充完整事实。每条 assignment 还会独立复验 candidate
provenance 与 overlay/payload digest。

continuity 的选择规则已经落定：CANARY 沿用父选择，PROMOTED 选择 `candidate.candidate`，回滚态
选择 base；ARCHIVED 从 V18 lifecycle journal 复核来源，若从 PROMOTED 归档则继续选择 candidate，
否则选择 base，journal 缺失时 fail closed。运行时用 kind-prefixed key 组装 collision-free 多值
映射，对嵌套 payload 深冻结，`always` 按确定顺序注入；单主体兼容访问复用同一次 payload
resolution。Evidence 在 0 条时保持 legacy 的“无 governed assignment artifact”，1 条时保留旧
assignment artifact，N>1 时只挂 assignment-set artifact；SystemSnapshot 的
`evolution_overlay_set` 保存 canonical overlay 集合的 digest，不内嵌 assignment set。

治理 UI 只展示真实能力：Skill availability/source/curator protection 为只读，`pinned` 仅表示
curator protection；用户可用严格 CAS 修改 evolution mode 与 max canary basis points。P4b 没有
实现 enabled 或版本 pin 开关。新增 admin-only `GET /api/evolution/policies` 列表端点后，当前
显式 scope 表为 266 条 protected、15 条 public，覆盖 280 个 route inventory refs。最终本地
门禁为后端 5270 passed、2 skipped、24 slow deselected；Web 347 passed，Ruff、format、Mypy、
import-linter、TypeScript、ESLint（0 error）与生产构建通过。PR 与目标分支 CI 仍待完成，不能把
“分支实现与本地门禁完成”写成已合入。当前 PR 为 #109。

---

## 0. 一页总览

### 0.1 目标（全部落地后成立）

1. 每个受管 Memorial 在**第一个受管副作用前**绑定不可变 `SystemSnapshotV1`（kernel / executor / skills / personas / policy_rules / provider_profiles / evolution_overlay 内容摘要）与 RuntimeGeneration id；Evidence 关闭时多挂一个 system-snapshot artifact，`EvidenceSnapshotV1` schema 零改动，独立重算可对账。
2. 子进程执行器（`keqing:pi` 首条切片）按代切换：**stage → warm → activate → drain**；新 run 取新代、运行中 run 固定旧代、引用归零才 dispose、warm 失败指针不动、保留 last-good。
3. 注册表贡献带 owner/disposer（`ContributionHandle`），可按 owner 逆序整体卸载。
4. Candidate 按 `(kind, subject_key)` 独立 canary（拆掉"全局仅 1 个 canary"，**同 subject 冲突仍 fail-closed**）；每插件一行 `EvolutionPolicy`（frozen/manual/canary，`auto` 不实现且类型级 + DB 级双重不可表达）。
5. `CandidateKind.EXECUTOR` 全链路：版本漂移 → 幂等 PROPOSED 候选 → Gate → per-subject canary → 高危 Decision → `ExecutorPromotionAdapter` 换代 / 回滚 last-good。
6. 进程级 snapshot 重启与 last-good；`GenerationReconciler` 与现有 `EvolutionRollbackReconciler` 组合进同一个后台 reconcile loop（独立锁与职责，不复制六个循环）。
7. 声明式内容每 run 冻结视图（先 skills），SkillsWatcher 不再直接改 active。
8. 治理微内核（Edict / Memorial / Attempt / Decision / Evidence / Effect journal / Promotion Authority）**不进入进化**，且该边界由可执行约束（import-linter + AST 架构测试 + DB 触发器）守住。

首个产品里程碑与 migration-roadmap §1 一致：**完整运行快照 + continuity generation 固定 + last-good 回滚**——不是插件市场。

### 0.2 五条实施原则

| # | 原则 | 落法 |
|---|---|---|
| 1 | **影子先行、开关可退** | 每个新写入面（binding、代际、per-subject 路由、冻结视图）先双写/只记不拒，观测窗口后才翻转 fail-closed；翻转本身是独立一步、有开关。行为回退优先靠"不注入 / 开关关"而非删表 |
| 2 | **只切指针，不改活体** | 一切"换"（执行器版本、skills 内容、进程 snapshot）= 新代 stage→warm→activate 指针原子切换 + last-good 保留；warm 失败指针不动；不做进程内模块 reload |
| 3 | **只加不改，冻结契约零字节** | `RunAssignmentV1` / `LegacyRunAssignmentV1` / `EvidenceSnapshotV1` schema_version / V1–V30 迁移 / Candidate 11 态转移图 / `automatic_promotion_allowed: Literal[False]` 一律不动；新能力走新表 / 新可选字段 / keyword-only 默认参数 / Evidence artifact 通道 |
| 4 | **fail-closed 只收紧粒度、不取消** | 全局单 canary → per-subject 单 canary（同 subject >1 仍抛）；"绑定失败 run 失败"语义原样扩展到 snapshot（`system_snapshot_unavailable`）与 generation（`generation_retired`）；影子期每个宽松点都绑定 strict 开关与翻转阶段归宿，不留"永远宽松"死角 |
| 5 | **治理微内核零进化 + 可执行约束** | governed lifecycle 写继续只经 `PromotionService`（tests/architecture/test_promotion_authority.py:8 AST 守卫）；新增 generation 状态写权威同样上 AST 守卫；每步独立合入、测试全绿、故障注入验收 |

### 0.3 阶段表

| 阶段 | 对应评审 | 目标 | 主要改动 | 迁移 | 工作量（累计） | 完成后用户能看到什么 |
|---|---|---|---|---|---|---|
| **P0** 术语冻结与 Ring 0 守卫 | 评审 §3.6 | ADR/术语/import-linter 就位，后续每步有锚 | ADR-0013/0014、CONTEXT.md 与三语词条、import-linter 定向 forbidden 契约、评审文档 V25→V31 勘误 | 无 | 2 天（2 天） | 文档与 ADR；无 UI 变化 |
| **P1**（PR-1）SystemSnapshotV1 影子双写 | PR-1 | 每个 run 能回答"我用了什么"，零行为变化 | `SystemSnapshotV1` + 4 个 `content_digest()` + Resolver + `system_snapshot_repo` + `bind_runtime` 双写 + Evidence 挂 artifact | **V31** | 4–5 天（≈7 天） | Evidence 下载包里多一个 system-snapshot artifact；（可选）edict 详情/assignment API 显示 digest |
| **P2**（PR-2）ContributionHandle | PR-2 | 六类注册表贡献可归属、可逆序卸载 | `plugins/contribution.py` + Tool/Channel/Skills unregister + PluginApi 返回 handle + `dispose_owner` + dispose 身份校验（Codex A2） | 无 | 1.5–2.5 天（≈9.5 天） | 无 UI 变化；MCP 断连残留工具可被干净清理（隐性修复） |
| **P3**（PR-3a）Pi 执行器代际与 continuity 固定 | PR-3a | stage/warm/activate/drain + attempt lease + durable continuity retention | `RuntimeReleaseV1` + `RuntimeGenerationV1` + `generation_repo` + registry 代际 API + `pi_probe` + 独立 `GenerationReconciler` + 继承规则 + receipt 绝对路径/版本与 EventBus 等待夹具（Codex B8/B9） | **V32** | 1.5–2 周 + 2 天（≈21.5 天） | keqing status 页多"代际"列（active/last-good/活跃 run 数）；换代期间长任务/会话不换 Pi 版本 |
| **P4**（PR-4，**提前**；拆 4a/4b 两 PR）按 subject 独立灰度 + EvolutionPolicy | PR-4 | 拆全局单 canary；每插件一行 frozen/manual/canary | `evolution_policies` 表 + propose/start_canary 执法 + canary partial unique index + `save_candidate` 执法收口（Codex A3）+（4b）`run_subject_assignments` 双写 + `get_routable_candidates` + overlays dict | **V33、V34** | 1.5–2 周 + 2 天 + conftest fixture 解耦先行小 PR ≈1 天（≈34.5 天） | Evolution Center routing 按 subject 分行；policy API 可冻结单个插件的进化（enabled 与进化正交）；两插件可并行灰度 |
| **P5**（PR-3b，**后移**）CandidateKind.EXECUTOR 全链路 | PR-3b | 漂移→候选→Gate→canary→Decision→换代/回滚闭环 | 枚举 + kind CHECK 重建 + Executor 两个 adapter + 漂移巡检 + 前端/CLI 投影 | **V35** | 1.5–2 周（含分支 A 子表连带重建 +2–3 天；≈44.5 天） | 客卿馆 Pi 版本漂移出现治理候选，可全链走到换代与回滚；"代际"列联动 |
| **P6**（PR-5）进程级 snapshot 重启与 last-good | PR-5 | serve 启动校验 snapshot；binding 翻转 fail-closed | `serve --system-snapshot` + strict 模式 + `scope='process'` 指针入 GenerationReconciler | 无 | 3–4 天（≈48.5 天） | `tianshu serve --system-snapshot`；启动漂移审计/strict 拒启提示 last-good |
| **P7** 声明式内容每 run 冻结视图 | 评审"PR-4 之后" | SkillsWatcher 退出 active 直改；每 run 冻结 skills 视图 | `freeze_view` + watcher 只失效缓存 + `frozen_views` 入 runtime context | 无 | 4 天–1 周（≈53.5 天） | 运行中 run 不受 SKILL.md 热改/晋升影响（行为保证，UI 无显） |
| **并行轨** Codex 借鉴独立项 | §8.2 | 堵现存洞 + 基建门禁，不占关键路径 | X1 WS 所有权过滤（**立即**）· X2 重试判据收敛 · X3 allowed_paths 受理校验 · X4 schema 落盘 + CI 门禁（P3 前）· X5 路由 scope 表（P4a 前） | 无 | 8–10 天（可与 P0–P2 同期由第二会话承担） | secure-remote 下 WS 不再越权可见；新契约/新路由漏登记即 CI 红 |

总计 ≈ 10–12 周（并入 Codex 借鉴项后；并行轨若无第二会话再 +8–10 天）。依赖关系：P1→P3→P4→P5→P6；P2 与 P1 可并行；P4a（policy 表）与 P3 可并行开发；P7 依赖 P1（skills digest 已入 snapshot 可做影子比较）与 P4（runtime context 已是多值形态）。

> **2026-08-25 增补**：[Codex harness 借鉴分析](../reference/openai-codex-harness-analysis.md)的裁决已并入本方案——4 条写进 P2/P3/P4a 规格（关键路径 +4–5 天），5 条走独立并行轨，其余等主线收官后单开迭代。逐条见 [§8](#8-codex-harness-借鉴并入项2026-08-25-裁决)。

**与评审 PR 顺序的唯一结构性调整**：评审顺序是 PR-3b（EXECUTOR）→ PR-4（per-subject）；本方案对调为 P4（per-subject）→ P5（EXECUTOR）。理由：现路由 `get_routable_candidate` 对 >1 canary 直接抛 `multiple canary routing authorities`（[../../src/tianshu/storage/evolution_repo.py](../../src/tianshu/storage/evolution_repo.py):270-278），评审顺序下 EXECUTOR 灰度会挤占全局唯一 canary 槽（Pi 灰度期间任何 skill 不能灰度），且要先为"挤占语义"写一批行为测试、PR-4 再全部重写（test_promotion_fail_closed.py 1433 行 + test_rollback_fault_matrix.py 跨文件 import 其私有 fixture，连坐两次）。对调后 EXECUTOR 候选一出生就在 per-subject 世界，行为测试只重写一次，不留过渡语义。PR-3a（代际机械本身）不依赖 canary，仍在 P3 先行。连带地，迁移顺序为 V33 `0033_evolution_policies`、V34 `0034_run_subject_assignments`（两表 kind CHECK **从建表起即含 `'executor'`**——DB 超集无害，免二次重建）、V35 `0035_executor_candidate_kind`。

---

## 1. 目标态回顾（只列会落到代码上的对象、表、状态机与不变量）

### 1.1 领域对象 / 代码对象

| 对象 | 状态 | 形态与落点 |
|---|---|---|
| `SystemSnapshotV1` | **新增**（P1） | frozen strict pydantic（`src/tianshu/models/system_snapshot.py` 新建，config 仿 evidence/models.py:70 的 frozen+extra=forbid+strict）：`schema_version: Literal[1]` + `components: dict[str, str]`（组件 id → 64hex 内容摘要，key 白名单）+ `digest = canonical_sha256(components)` 自校验。吸收目标文档的 SystemSnapshot / PluginSetSnapshot / PluginRelease（评审 §3.1） |
| `SystemSnapshotResolver` | **新增**（P1） | `src/tianshu/evolution/system_snapshot.py`：装配期收集组件 digest；`resolve_base()` / `resolve_for_run(...)` 追加 `evolution_overlay` |
| `RuntimeReleaseV1` | **新增**（P3） | frozen strict pydantic（`src/tianshu/models/runtime_generation.py`）：完整 executor manifest、CLI/version source、解析后绝对 binary path、binary/package digest、single/session argv shape、Pi wire version、materializer id/version；`release_digest` 是全部 canonical material 的内容摘要，可被多个朝复用，重启时逐字段复核 |
| `RuntimeGenerationV1` | **新增**（P3） | frozen pydantic（同文件）：`generation_id`（`rg-`+uuid4，**运行实例身份，非内容摘要**）/ `scope` / `release_digest`（引用不可变发布）/ `state` 七态 / `version`（CAS）；模块级冻结常规转移图 + `validate_generation_transition()`，另有仅 repository 可调用的 last-good rollback 特权边。**refcount 不入模型/表**（见 P3 决策） |
| `ContributionHandle` | **新增**（P2） | `src/tianshu/plugins/contribution.py`：`@dataclass(frozen=True, slots=True)`，`owner / kind / name / target / dispose: Callable[[], ContributionDisposeStatus]`；dispose 三态为 `disposed / skipped_stale / noop`，kind Literal 与 `PluginManifest.type` 六值同形（plugins/manifest.py:16） |
| `EvolutionPolicyV1` | **新增**（P4a） | `src/tianshu/models/evolution_policy.py`：`subject_key / kind / mode ∈ {frozen,manual,canary} / max_canary_basis_points(0..1000) / version(CAS) / updated_at`；`auto` 不进枚举，类型级不可表达 |
| `SubjectRunAssignmentV1` / `RunAssignmentSetV1` | **新增**（P4b） | `src/tianshu/models/run_assignment.py` 内追加；per-subject 分流归因；单条旧行是退化形式 |
| `FrozenSkillsView` / `FrozenContentViews` | **新增**（P7） | `src/tianshu/skills/loader.py` / `evolution/runtime_context.py`；每 run 冻结的技能只读视图 |
| `CandidateKind` | **修改**（P5） | 加 `EXECUTOR = "executor"`（models/evolution_candidate.py:16-21 现有五值）；DB 端 kind CHECK 冻结在 V18 DDL，须 V35 临时表重建扩枚举；同文件新增 `HIGH_RISK_PROMOTION_KINDS = frozenset({CODE, EXECUTOR})` |
| `EvolutionRuntimeContext` | **修改** | P1 加 `system_snapshot: SystemSnapshotV1 \| None = None`；P4b 加 `overlays / payloads` dict（单数字段保留为退化访问器）；P7 加 `frozen_views`（runtime_context.py:15） |
| `PreparedExecutor` | **修改**（P3） | 加 `generation_id: str \| None = None` 与固定 delegate/bundle 引用；`bind_run` 只透传——DAG 子 run 天然继承根的代，不新增 lease |
| `GenerationController` / `ExecutorAdapterRegistry` | **新增 / 修改**（P3） | Controller 独占 stage/warm/activate/rollback/recovery 编排；Registry 只保管 materialized bundle、同锁 selection 与 attempt_id lease。`replace` 保留为无 generation 的 native/测试 seam |
| `GenerationReconciler` | **新增并组合**（P3/P6） | 保持 `EvolutionRollbackReconciler` 原类与锁不变；新 reconciler 使用独立锁，二者由既有 `reconcile_control_planes()` 顺序驱动，因此仍只有一个后台扫描循环，generation 故障不改变 candidate rollback fault matrix |
| `ExecutorCandidateAdapter` / `ExecutorPromotionAdapter` | **新增**（P5） | `src/tianshu/evolution/adapters/executor.py` / `executor_promotion.py`；分别接 `BaseCandidateAdapter`（adapters/base.py:96）与 `_Adapter` 协议（promotion.py:234-237） |
| `PromotionService` | **修改（局部，不拆职责）** | start_canary 排他改 per-subject + policy 执法（promotion.py:784/826-827）；高危 Decision 判据泛化（:935-943、1054-1062）；lifecycle 写权威地位、鉴权、journal 语义零改动 |
| `ChallengerRouter` | **修改（局部）** | `bind_runtime` 加 keyword-only `attempt_id` 与 binding 双写；`assign_current` 内部多 subject 扩展（**12 个调用点签名与返回类型不变**） |
| `SkillsWatcher` | **修改**（P7） | debounce 后只 `invalidate_cache()`，不再 `load_all()` 直改 active；可选 `on_change` 回调 |
| `RunAssignmentV1` / `LegacyRunAssignmentV1` / `EffectiveEvolutionOverlayV1` / `EvolutionRunEvidenceV1` | **现有，禁改** | 旧行 decode 是 canonical 往返 + hash + 列冗余三重逐字节复核（evolution_repo.py:322-381），任何加字段都会炸存量行 |
| `EvidenceSnapshotV1` 及 evidence 全部模型 | **现有，禁改** | extra=forbid + 发布 schema 逐字节锁定（tests/evidence/test_schema_contract.py:31-36）；扩展只走 artifact 通道 |
| `EvolutionContractV1.automatic_promotion_allowed: Literal[False]` | **现有，禁改** | models/evolution_candidate.py:173 |
| Candidate 11 态生命周期与冻结转移图 | **现有，禁改** | EXECUTOR 复用同一图，不加新态（evolution_candidate.py:34-91） |
| `AgentSession` / `PluginSetSpec` / `EvaluationCampaign` | **不引入** | 评审 §3.1：AgentSession 待 ADR；PluginSetSpec 第一阶段就是 `wire_*` 装配代码本身；EvaluationCampaign = 既有 GateEvaluator + universe/eval_harness |

### 1.2 新表（全部尾部追加迁移 V31–V35）

| 表 | 迁移 | 关键约束 | 可变性 |
|---|---|---|---|
| `system_snapshots` | V31 | `snapshot_digest` PK（严格 64hex 小写 CHECK）、`schema_version` CHECK=1、`components_json` CHECK(json object) | no_replace + no_update + no_delete 触发器（内容寻址，永不改） |
| `run_system_bindings` | V31 | `PRIMARY KEY(memorial_id, attempt_id)`、`snapshot_digest` FK→system_snapshots RESTRICT、`generation_ids_json` CHECK(json array) DEFAULT `'[]'`（**建表即带此列**） | snapshot 启用时的可选 shadow；P3 只把它作为 V31 fallback，若与 exact generation marker 同在则必须一致；no_replace + no_update；允许普通 DELETE、不 FK memorials（与敕令删除清理对齐；不可变典制副本在 Evidence artifact——见 §7 决策 5） |
| `runtime_generation_releases` | V32 | `release_digest` PK、`scope`、canonical `release_json`、schema/materializer 版本与时间戳；`UNIQUE(release_digest, scope)` 供 generation 复合 FK | insert-once；no-replace/no-update/no-delete |
| `runtime_generations` | V32 | `generation_id` PK、`scope`、`release_digest` 复合 FK、`state` CHECK 七态、`version>0`（CAS）、部分唯一索引 `(scope) WHERE state='active'` | identity/material 不可变；仅 state/version/timestamps 可 CAS |
| `runtime_generation_journal` | V32 | 严格 64hex `journal_id` / `entry_hash`、from/to 枚举、自校验 canonical entry、FK RESTRICT、`UNIQUE(generation_id, generation_version)` | no-replace + no-update + no-delete |
| `generation_pointers` | V32 | `scope` PK、非空 active/last-good、version CAS；两条 `(generation_id, scope)` 复合 FK 禁 dangling/cross-scope | scope 不可变；no-replace/no-delete；其余仅 CAS |
| `run_generation_bindings` | V32 | `PRIMARY KEY(memorial_id, attempt_id)`、`state IN ('bound','unresolved')`；`bound` 必须保存 canonical generation id array（含显式 `[]`），`unresolved` 必须为 NULL | P3 独立 exact-attempt 代际权威；no_replace + no_update；允许随 Edict 物理删除清理 |
| `evolution_policies` | V33 | `subject_key` PK、`kind` CHECK（**含 'executor'** 超集）、`mode` CHECK IN('frozen','manual','canary')（无 'auto'）、`max_canary_basis_points` 0..1000（mode='canary' 时须 1..1000）、`version>0`（CAS） | 可变（无触发器，仿 evolution_routing_allocations） |
| `run_subject_assignments` | V34 | `assignment_id` PK、`UNIQUE(memorial_id, kind, subject_key)`、`kind` CHECK（含 'executor'）、assignment_json+hash、持久 `assignment_set_hash/size`；set size 1..64 | sealed_insert + no_update；no_delete 仅 `WHEN candidate_id IS NOT NULL` |
| `evolution_candidates`（重建） | V35 | V20 临时表模式扩 kind CHECK 含 `'executor'`；temp 名登记 `_RESERVED_TEMP_TABLES`（migrations.py:728） | 与现状一致 |

### 1.3 状态机

| 状态机 | 状态 | 内容 |
|---|---|---|
| Candidate lifecycle | **现有，冻结不动** | 11 态 + `LEGAL_LIFECYCLE_TRANSITIONS`（evolution_candidate.py:34-91）；EXECUTOR 复用同一图 |
| RuntimeGeneration | **新增** | 常规边为 `staged→warming→ready→active→draining→disposed`，旁路 `failed`（staged/warming/ready 失败进入；指针不动）；`failed`/`disposed` 终态。仅 `rollback_to_last_good()` 可执行受限 `draining→active`，普通 transition API 不表达该边。目标文档的 RESOLVED/VERIFIED/QUARANTINED 按评审 §3.2 折叠：resolve/verify 是 stage 前置校验，quarantine 并入 failed |
| EvolutionPolicy.mode | **新增**（非状态机，一列枚举） | frozen / manual / canary；改动走 CAS（version 列） |

### 1.4 不变量（现有 → 目标的守恒与收紧；每阶段验收对照）

| 不变量 | 状态 | 守卫方式 |
|---|---|---|
| 每受管 root Memorial 在插入 Memorial 的同一 UoW 内落 assignment（12 个 `assign_current` 调用点） | 现有，不动 | tests/universe/test_challenger_routing*.py |
| 每 Memorial 首个受管副作用前绑定 SystemSnapshot（binding 行 + runtime context） | **新增**（P1 影子、P6 严格） | 新测试 + SystemAudit `system_snapshot_drift` / `system_snapshot_binding_failed` |
| assignment/binding persist-once + IntegrityError→重读等值即幂等；永不重分桶（HMAC identity schema_version=2 冻结） | 现有 → 新表沿用 | evolution_repo.py:414-423 模式复制到三个新 repo |
| governed run 的 overlay/binding 解析失败在严格期让 run FAILED，绝不静默回退 live | 现有，扩展 | run_dispatcher.py:253-271；新异常必须继承 `EvolutionRuntimeUnavailable` |
| 全局单 canary → **per-(kind, subject_key) 单 canary**，同 subject >1 仍 fail-closed | **修改（收紧粒度）**（P4b） | `get_routable_candidates` 组内 >1 抛 conflict；start_canary 写侧同 subject 排他 SQL |
| routing 行与 candidate.routing 双写逐字段一致（五路互检） | 现有，按 subject 复制不弱化 | evolution_repo.py:283-297 / promotion.py:1536-1560 |
| 新代 warm 失败 active/last-good 指针不动；旧代无 exact-attempt/OPEN-continuity 引用且不是 last-good 才 dispose；不 break-before-make | **新增**（P3） | 原子故障注入测试 + DB 部分唯一索引 + pointer/retention 重查兜底 |
| 同一连续交互不混用两个 generation：conversation/深度 Edict 固定、cron root 每次选择、DAG/基础设施重试继承 root | **新增**（P3，ADR-0014） | continuity 测试；代已 disposed → fail-closed `generation_retired` |
| governed lifecycle 只由 PromotionService 写 | 现有 | tests/architecture/test_promotion_authority.py:8（AST 扫描） |
| generation 状态/指针写权威仅限 generation_repo / ExecutorAdapterRegistry / GenerationReconciler / 启动序列 | **新增**（P3） | 新增 tests/architecture/test_generation_authority.py（AST 扫描，仿上） |
| `automatic_promotion_allowed: Literal[False]`；`auto` 模式不可表达 | 现有，不动 + **新表同构** | 类型级 Literal + V33 mode CHECK 无 'auto'（DB 级） |
| 证据链不可变（closed bundle / artifact / journal 触发器）；删除降级归档 | 现有 | migrations.py:3149-3177、3574-3621 |
| 迁移 append-only、双冻结（SQL checksum + callback 源码指纹） | 现有 | migration_ledger.py:347；tests/storage/test_migration_callback_freeze.py:197 |
| EXECUTOR/CODE 晋升永远需要已决议高危 Decision（常量地板，policy 只能加严不能放宽） | **新增**（P5） | `HIGH_RISK_PROMOTION_KINDS` frozenset + 既有 durable 预检链 |

---

## 2. 当前实现基线（与本方案相关的现有文件、符号、契约、seam、锁定测试）

### 2.1 存储与迁移

| 事实 | 位置（文件:行） |
|---|---|
| live 迁移尾是 V30 `0030_consultation_rounds`；版本常量链式 +1（`_CONSULTATION_ROUNDS_VERSION = _CONSULTATION_SYNTHESIZER_VERSION + 1`）；注册点唯一 = `MIGRATIONS` 元组尾部追加 | src/tianshu/storage/migrations.py:4160-4161、4239 |
| checksum 公式 = sha256(name + "\n" + 空白归一化语句序列)，只覆盖 SQL 不覆盖 Python callback | migrations.py:49-53、3989-3998 |
| callback 源码指纹冻结：新迁移必登记 `_FROZEN_UPGRADE_FINGERPRINTS`（sha256(dedent(getsource(upgrade)))），登记前须在 docs/cc-fable-v1/PROGRESS.md 记录裁决 | tests/storage/test_migration_callback_freeze.py:25、135、197 |
| 严格新表模板（V18）：CREATE TABLE 无 IF NOT EXISTS + upgrade 内 sqlite_master 逐对象等价比对（幂等且防漂移）+ 不可变触发器 | migrations.py:3429-3661（`_governed_evolution_candidates_upgrade` :3637） |
| 加列模板 V24（:3975）；回放安全加列模板 V29（PRAGMA table_info 逐列判存在，:4122）；删列/改 CHECK 唯一途径 = 临时表重建 V20（temp 名登记 `_RESERVED_TEMP_TABLES` :728）；修订冻结对象唯一途径 = 追加迁移 DROP+重建 V22 并把"完整重放实况"注入形状测试 | migrations.py:3821-3883、3917-3947；tests/evolution/test_evolution_migration_schema.py:292-305 |
| adopt 基线 `_matches_canonical_schema` 对 :memory: 重放全部 MIGRATIONS——新迁移 upgrade 必须在空库、无外部 env 下可跑通 | migrations.py:4321-4358 |
| per-version 测试模板：`MIGRATIONS[:N-1]` 切片建库→哨兵行→apply==(N,)→效果断言 + 前序 (version,name,checksum) 三元组冻结 | tests/storage/test_durable_schema_v24.py:14-63 |
| 每条迁移独立 BEGIN IMMEDIATE 事务；callback 只拿受限 `MigrationConnection`（禁事务控制）；提交前 `foreign_key_check` + `quick_check` | src/tianshu/storage/migration_ledger.py:135-172、356-431 |
| 应用层写事务统一 `SqliteUnitOfWork`：BEGIN IMMEDIATE、禁嵌套、异常回滚 | src/tianshu/storage/unit_of_work.py:16-70 |
| V20 在有存量 key 的库上需要 `TIANSHU_SECRET_MASTER_KEY`——全量 apply 的新测试要处理该 env；per-version 切片测试用空库则天然规避 | migrations.py:3807-3819 |
| evolution 六表 repo：无状态、方法显式传 connection；读路径逐字节复核 fail-closed | src/tianshu/storage/evolution_repo.py:90、242 |

### 2.2 演化核心

| 事实 | 位置 |
|---|---|
| 「全局仅 1 canary」实现处：`get_routable_candidate` 全表查 `lifecycle='canary'`，>1 行抛 `multiple canary routing authorities`；候选与 routing 行五路互检 | evolution_repo.py:270-298（抛点 :278，互检 :283-297） |
| `start_canary` 写侧只查"该 candidate 自身无 routing 行"，无 subject 排他 | src/tianshu/evolution/promotion.py:784、826-827 |
| 候选 CAS + 核心字段不可变 + 冻结转移图；CODE 晋升需已决议高危 Decision：`_require_high_risk_code_promotion_decision` + `_CodePromotionDecisionBindingV1`（四元组绑定校验）+ promote 前 durable 预检 | evolution_repo.py:48、183-211、214-239、531-598；promotion.py:935-943、1054-1062 |
| 晋升适配器协议 `_Adapter(activate/rollback)` + duck-typed `rollback_guard/verify_rollback/rollback_is_idempotent`；P5 前非 SKILL 全部 `UnavailablePromotionAdapter`。当前 P5 checkout 由 `wiring_executor` 始终覆盖 EXECUTOR 为 `ExecutorPromotionAdapter`，开关仅阻止新的前向效果并保留 recovery/rollback/reconcile | promotion.py；src/tianshu/bootstrap/wiring_skills.py；src/tianshu/bootstrap/wiring_executor.py |
| `SkillPromotionAdapter`：flock + marker fencing + renameatx_np/renameat2 原子交换 + preflight——Executor 晋升适配器的参考实现（但其直调 staging 私有方法是已知反例，勿复制） | promotion.py:61-131、258-344、526-528 |
| promote/rollback 各跨 3 个 UoW，副作用在事务外；journal 三段（intended→applied→completed）+ UNIQUE(command_key,status) 保证 crash 后重放；rollback 先断流（置零 allocation + ROLLBACK_PENDING）再恢复 | promotion.py:896-1000、989-995、1103-1214、1136-1214 |
| `EvolutionRollbackReconciler`：Lock 串行、只驱动 PromotionService、`reconcile_once()`/`readiness_probe()`——GenerationReconciler 的扩展母体；挂载点 `reconcile_control_planes`（RunReconciler.before_scan 周期驱动） | src/tianshu/evolution/reconciler.py:13-46（:28、:45）；src/tianshu/bootstrap/wiring_scheduler.py:220-230 |
| GateEvaluator 恒要求全部 8 门（`contract.required_gates` 被忽略）——已知语义分叉，本方案不动 | src/tianshu/evolution/gates.py:39 |
| 五域 staging adapter：`_ADAPTER_TYPES` kind→adapter 映射 + `CandidateLiveAuthorities.for_kind`；`_normalize_domain` 是子类唯一必须实现的钩子；media_type 按 `{kind.value}` f-string 自动扩展；candidate_id 命令确定性哈希、propose 幂等、零 artifact 孤儿 | src/tianshu/evolution/candidate_service.py:59-84、110、129、155-176、236-252；adapters/base.py:96-127 |

### 2.3 运行生命周期与分流

| 事实 | 位置 |
|---|---|
| 12 个 `assign_current` 调用点全部在"插入 Memorial 的同一 UoW 内" | src/tianshu/application/edicts.py:111,135；managed_run_ingress.py:153,174,246,308,413,516；scheduled_runs.py:151,222,365,393 |
| 执行期唯一绑定点：`RunDispatcher._execute` 中 `with self._challenger_router.bind_runtime(authority.memorial_id):`（claim 后、runner task 创建前）；`AttemptAuthority` 携带 attempt_id | src/tianshu/application/run_dispatcher.py:225、32 |
| bind 失败→attempt FAILED（`run_assignment_unavailable` / `candidate_overlay_unavailable`），绝不无绑定执行；裸异常会被误标，新异常必须继承 `EvolutionRuntimeUnavailable` | run_dispatcher.py:253-271 |
| `bind_runtime` 自开 UoW、commit 于 yield 前；legacy 分支 yield None；governed 经 ContextVar 设置 runtime context | src/tianshu/universe/router.py:188-223（UoW :190-211，legacy :212-214） |
| assignment persist-once；`_assignment_id` = `'assignment:'+sha256(memorial_id)`（身份级、不可改派生）；分桶 HMAC identity schema_version=2 冻结（含 allocation_seed_id，天然 per-candidate 独立） | router.py:119-121、240-242、53-68 |
| 无 canary 也写 legacy 占位行（重试路由稳定 + V22 敕令删除清理动机） | evolution_repo.py:384-423；migrations.py:3903-3915 |
| `ManagedRunCommand` 幂等指纹含全部字段——加字段=旧 replay 冲突；follow-up busy 检查与 parent 选择在 :118-141 | src/tianshu/application/managed_run_ingress.py:79-101、118-141 |
| attempt 只属根 Memorial；DAG 子节点无独立 attempt/assignment；基础设施重试 = 同 memorial 新 attempt 行 | src/tianshu/storage/attempt_ledger.py:104-137、164-167、300-305 |
| ContextVar 只在宿主进程内传播；子进程经 create_subprocess_exec 收不到——跨进程只能走 env/argv | src/tianshu/evolution/runtime_context.py:23；src/tianshu/executor/execution_gateway/process_backend.py:294 |

### 2.4 执行器与客卿

| 事实 | 位置 |
|---|---|
| `ExecutorAdapterRegistry`：register 冲突 raise（:94）/ replace 静默覆盖（:103）/ prepare 是所有 run 取执行器的唯一咽喉（:116）；`PreparedExecutor` frozen run handle（:30）+ `bind_run` DAG 派生通道（:35；worker.py:86 消费） | src/tianshu/executor/adapters/__init__.py |
| `_bind_verified_effective` 拒 manifest（id/version/content_hash）与 probe semantic 漂移——换代必须整代保留旧 manifest/adapter 实例直到旧 run drain 完 | adapters/__init__.py:158-176 |
| `ExecutorCapabilityManifestV1` 已有 `manifest_id/manifest_version/adapter_id/level/capabilities` 与 canonical `content_hash`——executor 组件摘要现成 | src/tianshu/executor/capabilities.py:52-98（content_hash 继承自 models/governance_contract.py:69 CanonicalContractModel） |
| 「pi」一名双执行器：单发 `PiAdapter`（grant argv 校验用）与会话 `PiSessionAdapter`/`KeqingSessionExecutor` 并列——换代必须同代切换 | src/tianshu/executor/executor.py:106-146（:118-122）；keqing/session_executor.py:56 |
| Pi 兼容基线仍含 `PINNED_PI_VERSION="0.83.0"`、RPC `VERIFIED_SESSION_VERSION=3`；P3 warm 的接受条件是合法 session header 加 `get_session_stats` 成功 response，不依赖一次任务执行后的 `agent_settled` / `agent_end` 事件 | src/tianshu/executor/keqing/pi_wire.py；src/tianshu/executor/keqing/pi_probe.py |
| 全部 Keqing 执行在准备阶段解析并记录绝对 executable 与版本来源；Pi generation 另把完整受管 package、binary/package digest 和绝对 binary path 固定进 release，每次 pinned 执行前复验，不再让 follow-up 因 PATH 漂移静默换二进制 | src/tianshu/executor/keqing/versions.py；src/tianshu/executor/keqing/generation.py；src/tianshu/executor/execution_gateway/process_backend.py |
| run 结束点五处：单发 executor.py:1275-1284 / outer_loop :931-946 / DAG 根 :476-499 / DAG 节点 worker.py:166-168 / managed `_exit_cleanup` run_dispatcher.py:275-283——release 插入位，须幂等（双 finally 可能重复触发）；per-run 资源释放范式 `_revoke_run_token` | session_executor.py:245-261 |
| 版本探测 `_detect_installed_version` 只读 package.json 不 spawn（进程启动守卫 + GET 幂等）；warm 探针必须走 ExecutionGateway + grant | src/tianshu/gateway/keqing_api.py:44-63；tests/architecture/test_no_direct_process_launch.py |
| grant 双墙：铸造（grants.py:566-584）+ spawn 前（gateway.py:548-557）共用模块级 `is_canonical_adapter_argv`（keqing/adapter.py:288） |
| 取消是 task 级联，adapter.cancel 客卿恒 False——drain 以 memorial 终态为准 | src/tianshu/executor/adapters/protocol.py:167-174 |

### 2.5 注册表与插件门面（P2 当前事实）

| 事实 | 位置 |
|---|---|
| `ToolRegistry.register` 以 keyword-only `owner="kernel"` 归属，默认 `on_conflict="replace"` 保持覆盖兼容；插件路径显式 `error` 并抛结构化 `ToolRegistryConflict`；`unregister` 同时清工具、owner、disabled 与 receipt lookup。execute 执行序不变量 estop→disabled→persona ACL→workspace→tier→winding_down→schema 不变 | src/tianshu/tools/registry.py |
| `HookRegistry` 六类中唯一自带 unregister（identity 过滤）；BEFORE_TOOL_CALL fail-secure 不可弱化；per-type timeout 已有 | src/tianshu/kernel/hooks.py:41、78-80、86-125、121-138 |
| `ChannelRegistry.unregister` 同时清 channel、限流配置与发送窗口；`ProviderManager.unregister` 已存在（demo_mode 拒绝返回 False） | src/tianshu/notifier/channel_registry.py；providers/manager.py:282-286 |
| `SkillsLoader._injected_skills` 是正式初始化字段；注册与注销均完整 `invalidate_cache()`，清 L1、L2 metadata/stat 与 content digest；`for_workspace_overlay` 复制注入视图 | src/tianshu/skills/loader.py |
| `SkillsWatcher` debounce 后 `invalidate_cache()` + `load_all()` 直改 active；与晋升适配器对同一目录并发无锁 | skills/loader.py:815-885（:879-885）；promotion.py:530-544 |
| `PluginApi.register_*` 六类均返回 owned `ContributionHandle`；默认 owner 为 `plugin:anonymous`，`dispose_owner` 逆序释放并返回 `(disposed, skipped_stale)`；插件面仍 fail-closed：投影恒 manifest_only、install/activate 恒 501 | src/tianshu/plugins/api.py；gateway/providers_api.py:154-197 |

### 2.6 证据与制品

| 事实 | 位置 |
|---|---|
| 一 Memorial 恒一 bundle（bundle_id = 'evidence:'+sha256(memorial_id)[:32] + UNIQUE）；`_snapshot_current` 在 open 与 close 各跑一次、close 整体覆盖（"最后一个 attempt 赢"）——artifact 挂入必须在其内且 payload 确定性 | src/tianshu/evidence/service.py:477-479、852、1052、1192；migrations.py:3121 |
| 新增 required artifact 的官方通道 = assignment artifact 块（:911-928）：`get_assignment` → `put_bytes_current`（自定义 vnd media_type）→ `artifact_by_digest` + `required_artifact_digests`；自动流入 requirements/_missing/_require_complete/verify/export/import 全泛型覆盖 | evidence/service.py:911-928、946-951、1070-1145、1252-1303 |
| legacy/缺 assignment run 整块跳过、不凭空建记录——binding 缺失沿同分流，存量数据可关闭 | evidence/service.py:912-914；tests/evidence/test_close_snapshot_immutable.py:132-160 |
| `is_referenced_current` 引用探测表清单封闭——bind 时提前写 artifact 字节有 GC 竞态误删窗口 | src/tianshu/storage/artifact_repo.py:68-86 |
| `_lock_hash` 恒 '0'*64 占位、tianshu_version 硬编码 '0.5.2'（发版隐形连带）；`EvidenceSnapshotV1` 已含 effective_contract_hash / executor_manifest_hash / environment 指纹——SystemSnapshot 大半组件已在证据里 | evidence/service.py:641-644、656、660；evidence/models.py:266-295 |

### 2.7 内容源与位面

| 事实 | 位置 |
|---|---|
| skills 优先级 builtin<user<workspace<injected<runtime overlay；`_l2_stats` 用 mtime/size 非内容哈希 | skills/loader.py:283-310、473-482 |
| canonical 指纹先例：`canonical_sha256`（models/canonical.py:74）、`eval_set_fingerprint`（universe/eval_harness.py:297） |
| 两个"policy"不同物：tools/policy_rules（运行时工具权限规则，纯代码，`build_default_rules` policy_rules/__init__.py:25）≠ evolution/adapters/policy.py（治理契约层）——snapshot 组件 key 命名必须区分（取 `policy_rules`） |
| provider 双注册表：ProviderManager（providers 表）与 ModelProviderRegistry（model_providers 表 + key 三态 api_key_ref） | providers/manager.py:181；providers/registry.py:41-217 |
| Universe legacy 轨（evolver 直改快照文件绕过 CandidateService）保持现状不扩展；switch/rollback/promote-code 已封死 `raise RuntimeError("promotion_service_required")` | universe/evolver.py:228-247；mutator.py:53-74；manager.py:166-183 |
| demo profile 双特判保留：bucket 恒 0（wiring_skills.py:57-63）、provider demo 短路 |

### 2.8 网关 / 前端 / CLI / 架构测试

| 事实 | 位置 |
|---|---|
| 双信封并存：evolution/control 用 `{data, correlation_id}`（evolution_api.py:50-53）；keqing/plugins 用 ApiResponse（keqing_api.py:41）——新演化端点跟 correlation_id 风格，不可混用 |
| run assignment 端点 GET /api/evolution/runs/{memorial_id}/assignment（evolution_api.py:56-95）——snapshot/generation 投影落点；keqing status 每 backend 一行（keqing_api.py:90-124 + web/src/pages/KeqingManagementPage.tsx:107-158）——代际列落点 |
| 前端手写镜像三处同步点：web/src/api/evolution.ts:15（kind union）、web/src/api/types.ts:440（KeqingBackendStatus）/:678（PluginInfo）、src/tianshu/models/evolution_view.py:42；i18n 三份 locale（en/zh-modern/zh-classic，zh-classic「彩蛋」label 不动） |
| 架构守卫：test_promotion_authority.py:8（AST 扫 governed lifecycle 写点）、test_evolution_composition.py:13-56（装配顺序 + 全系统单一 ChallengerRouter）、test_no_direct_process_launch.py |
| import-linter 三组 layers（pyproject.toml:161-176）：`gateway:executor:scheduler:bootstrap:universe`、`storage:secrets:memory:persona:skills`、`kernel:models:config:bus`——**`tianshu.evolution / plugins / application / evidence` 不在任何层** |
| CLI：serve 经 env 回灌重建 Settings（cli/commands/serve.py:38-41）；CLI 测试须 `env -u FORCE_COLOR`；ADR 编号从 0013 起（0002/0006 被占，索引已用到 0012，docs/adr/README.md） |

### 2.9 锁定测试总表（改动会先撞哪里）

| 测试 | 锁定内容 | 受影响阶段 |
|---|---|---|
| tests/storage/test_migration_callback_freeze.py | 30 条迁移 callback 指纹集合断言 | P1/P3/P4/P5 每条新迁移必改（先记 PROGRESS.md 裁决） |
| tests/storage/test_durable_schema_v14.py:64 | 版本序列连续 1..N | 尾部追加自动通过 |
| tests/evolution/test_evolution_migration_schema.py | 六表逐列/FK/UNIQUE/对象 SQL 全文/不可变触发器 | P5（V35 重建实况注入，V22 先例 :292-305） |
| tests/evolution/test_s4_s5_handoff.py:41 | "S5 恰好追加一条含全部未来表的 live 迁移"迁移布局断言 | **P1 起每条新迁移都要核对/更新** |
| tests/evolution/test_promotion_fail_closed.py（1433 行）+ test_rollback_fault_matrix.py（:13-20 跨文件 import 前者私有 fixture） | 晋升 fail-closed 全矩阵；单 canary 排他语义 | P4b（per-subject 重写，两文件连坐）、P5（EXECUTOR Decision 矩阵扩展） |
| tests/evolution/test_candidate_adapters.py:52-167 | `_sources()`/`ADAPTERS` 两张 kind 表 + 6 处 `parametrize("kind", tuple(CandidateKind))`（:237,268,281,295,690,804） | P5（加 EXECUTOR 条目自动展开全矩阵） |
| tests/evolution/test_candidate_schema.py:245-282、319-464 | lifecycle 参数化自 LEGAL_LIFECYCLE_TRANSITIONS 派生；CODE Decision 绑定矩阵 | P5 |
| tests/executor/test_executor_workspace_lifecycle.py:166,354,1242 | `replace` 作 E2E seam | P3（保留 replace，不 stage 时行为不变） |
| tests/compat/test_executor_capabilities.py | registry API 向后兼容 + bind_effective 漂移拒绝 | P3（旧代整代保留 manifest 实例，不红） |
| tests/gateway/test_keqing_status.py | 状态端点字段、gateway_enabled 恒 False | P3/P5（只加字段） |
| tests/gateway/test_plugin_manifest_api.py | 插件面 fail-closed（501/manifest_only 投影） | P2/P4（不得打开动态加载） |
| tests/architecture/test_promotion_authority.py:8 | lifecycle 写点 AST 扫描 | P3/P5 新模块不得直写 |
| tests/evidence/test_schema_contract.py:31-36 | 发布 schema 逐字节锁定 | P1（不改模型即不红） |
| tests/universe/test_challenger_routing*.py + tests/evolution/test_routing_distribution.py | 分桶算法/persist-once/回滚原子性/≈10% 分布 | P1（bind 签名 keyword 兼容）、P4b（多 subject 扩写；fresh-root singleton 的旧表投影与旧 artifact 逐字节保持） |
| tests/application/test_managed_run_ingress.py:198 | 精确 replay 优先于 busy 检查 | P3（继承不进幂等指纹，不红） |
| web：evolution.test.ts / KeqingManagementPage.test.tsx / e2e evolution-gate.spec.ts | 前端契约、15s 轮询、S5 空态、具名读契约注入模式 | P3/P4/P5；改完必单跑 `npm run typecheck` |

---

## 3. 分阶段实施

> **通用约定（适用每个阶段，不再重复）**：
> - 每条新迁移的固定四件套：①migrations.py 尾部追加链式版本常量 + `_*_STATEMENTS` + checksum + upgrade callback（V18 严格幂等范式），MIGRATIONS 元组尾部登记（migrations.py:4239）；②tests/storage/test_migration_callback_freeze.py:25 登记 upgrade 源码指纹，登记前按该测试 docstring 在 docs/cc-fable-v1/PROGRESS.md 记录裁决；③新建 `tests/storage/test_durable_schema_vNN.py`（v24 模板：`MIGRATIONS[:N-1]` 切片建库→哨兵行→apply==(N,)→效果断言 + 前序 (version,name,checksum) 冻结）；④核对并同步 tests/evolution/test_s4_s5_handoff.py:41 的迁移布局断言。upgrade 必须在空 :memory: 库、无外部 env 下可回放（adopt 基线 migrations.py:4321-4358）；无明文机密不入 `_SENSITIVE_MIGRATION_NAMES`；无临时表不入 `_RESERVED_TEMP_TABLES`（有则必入）。
> - 全量验证：`.venv/bin/python -m pytest tests/`（全量 apply 用例注意 V20 的 `TIANSHU_SECRET_MASTER_KEY` 既有约束）+ mypy + ruff + `lint-imports`；前端改动后 `cd web && npm run typecheck`；CI 红先按三处已知偶发（outbox scheduler ×2 + process group ×1）三步判定（diff 碰不碰得到 / 本地复跑 / CI 耗时）。
> - 每阶段一个（或两个）PR：issue → feat 分支 → PR（`Closes #n`）→ `gh pr checks` 亲验 → 汇报；合并与 tag 由用户操作。迁移号一旦在 issue 里分配即冻结，并行分支不得撞号。

### 阶段 P0：术语冻结与 Ring 0 可执行约束（≈2 天）

**目标**：目标术语成为 canonical；"治理微内核不进进化"从文档纪律升格为可执行约束；评审文档版本号勘误。零行为变更。

**前置依赖**：无。

**改动清单**（除 pyproject 外全部文档）：

| 动作 | 文件 | 内容 |
|---|---|---|
| 新增 | docs/adr/0013-generation-based-rollout.md | ADR-0013：热更新采用代际并存 + drain，不做进程内模块 reload；治理微内核不由普通 Evolution 自动修改；`auto` 模式不实现；三分边界：`SystemSnapshot`＝内容身份（content-addressed digest）、`RuntimeGeneration`＝运行实例身份（`rg-`+uuid）、attempt generation marker＝关联事实（P3 落为 `run_generation_bindings`），三者不复用主键、不塞进 `RunAssignmentV1`。V31 `run_system_bindings` 保留 snapshot shadow/V31 fallback。编号从 0013 起（0002/0006 被内部占用，索引已用到 0012） |
| 新增 | docs/adr/0014-memorial-system-snapshot-binding.md | ADR-0014：每个 Memorial 在第一个受管副作用前绑定 SystemSnapshot 与（有代时）generation ids；continuity 固定规则四条（conversation/深度 Edict 固定、scheduled root 每次选择、DAG 子节点与基础设施重试继承 root、**canary 选择随 continuity 固定——follow-up root Memorial 继承 parent root 的 assignment 选择（selected_ref/candidate_id/bucket），不重新分桶**）；AgentSession 不引入；影子期豁免（binding 写失败只记审计）的范围与 P6 翻转条件明写；发版改 tianshu_version 会改变 snapshot digest 属预期行为；组件清单注明 `prompts` key 预留（§6 延期表） |
| 修改 | docs/adr/README.md | 索引表登记 0013/0014 |
| 修改 | CONTEXT.md | 按既有格式（**中文 (English)**: 定义 + _Avoid_，仿客卿条目）新增三词条并直接采用已拍板 canonical 词：`SystemSnapshot`=「典制」、`RuntimeGeneration`=「朝」、`EvolutionPolicy`=「进化策略」；显式注明典制不是位面快照、影子快照或 Restore Point |
| 修改 | web/src/i18n/locales/{en,zh-modern,zh-classic}.json、terminology.test.ts | 三语新增同构领域词条并用精确值测试锁定；zh-classic 的「彩蛋」label 保持不变 |
| 修改 | pyproject.toml:161-176 | import-linter：完整新 layer 被九类存量反向依赖击穿，因此落两条零豁免 forbidden 契约：`application/evolution/evidence/plugins` 不得依赖 `gateway/bootstrap/scheduler`；`application/evolution/evidence` 不得依赖 `plugins`。存量反向边登记 ADR-0013，P7 前清零 |
| 修改 | docs/design/self-evolving-agent-os/review-and-implementation-plan.md、architecture-comparison.md、migration-roadmap.md 等 | 版本号勘误 V25–V28 → V31–V35；评审 §5 自列的 7 条最小修订一并做（Phase 2/3 对调、§2.2 补"单一全局 canary→按 subject 路由"行、6 Reconciler→1、7 ADR→2、恢复 docs/impl/plugins/README.md 3 行转发页、source-map §7 三条文档漂移） |
| 修改 | docs/plan/2026-08-25-self-evolving-agent-os-landing.md | 回填 P0/X1 实施状态，把 §7 从开放问题改为已拍板决策，并同步所有交叉引用 |

**数据迁移**：无。**兼容策略与开关**：无行为变更；新 import-linter 契约本身零豁免，完整分层尚未覆盖的存量反向边在 ADR-0013 登记并于 P7 前清零。

**测试清单**：`lint-imports` 全绿；`.venv/bin/python -m pytest tests/architecture/` 全绿（现有 test_promotion_authority / test_evolution_composition / test_no_direct_process_launch 即 Phase 0 要求的 characterization 锁，不新增）；Web `typecheck` 与全量 test 全绿；CI 全绿。

**验收 checklist**：
- [x] ADR-0013/0014 合入并登记索引；CONTEXT.md 三词条落条目且无"快照"三义混用
- [x] 三份 locale key 同构并锁定「典制 / 朝 / 进化策略」；zh-classic「彩蛋」不变
- [x] 全仓再无"新迁移 V25"口径（grep docs/design/self-evolving-agent-os）
- [x] lint-imports 全绿（含新契约或例外清单）；全量测试零变化
- [x] docs/plan 方案文档含每阶段验收

**回退方式**：revert 文档与 pyproject commit，无数据面。**工作量**：≈2 天。

**决策与取舍**：①import-linter 补层时点——目标纯度案放 P0、风险优先案推迟到 P2 后独立小 PR、最小改动案挂 PR-2：采纳 P0 落地（静态 lint 零行为风险，且带"过不了退 forbidden 契约"的降级路径，可执行约束越早越好）；②ADR 数量三案一致（2 个，评审 §3.6 拍板），其余五项 deferred；③docs/plan 落盘文件名统一为 `2026-08-25-self-evolving-agent-os-landing.md`（两案日期占位不一，取当前日期）。

---

### 阶段 P1（PR-1）：SystemSnapshotV1 影子双写（V31，≈4–5 天）

**目标**：每个新受管 run 在 `bind_runtime`（第一个受管副作用前的全系统唯一执行期绑定点，run_dispatcher.py:225）把"实际使用的系统内容"摘要持久绑定到 (memorial_id, attempt_id)；Evidence 关闭时挂 system-snapshot artifact 进 required 集合。**影子模式：零 active 行为变化**——binding 写入失败只记审计，绝不让 run 失败；drift 只记不拒。

**前置依赖**：P0（ADR-0014 语义已定）。

#### 改动清单

**新增文件**

| 文件 | 内容 |
|---|---|
| src/tianshu/models/system_snapshot.py | `class SystemSnapshotV1(_StrictModel)`（frozen+extra=forbid+strict，仿 evidence/models.py:70）。字段表：<br>· `schema_version: Literal[1] = 1`<br>· `components: dict[str, str]` — key 白名单：`kernel` / `executor:<adapter_id>`（每 adapter 一条）/ `skills` / `personas` / `policy_rules` / `provider_profiles` / `evolution_overlay`（仅 governed assignment 存在，legacy 省略该 key）；key 白名单另预留 `prompts`（prompt/harness 模板内容摘要，本轮不填——见 §6 延期表，加 key 不改 schema_version）；value 匹配 `^[0-9a-f]{64}$`；组件数上界 ≤64（防 evidence body_json 4MB CHECK）<br>· `digest: str` — `^[0-9a-f]{64}$`<br>`model_validator(mode="after")`：`digest == canonical_sha256(self.components)`（models/canonical.py:74）+ key 前缀白名单校验 |
| src/tianshu/evolution/system_snapshot.py | `class SystemSnapshotResolver:`<br>`def __init__(self, *, kernel_facts: Callable[[], dict[str, str]], executor_digests: Callable[[], dict[str, str]], skills_digest: Callable[[], str], personas_digest: Callable[[], str], policy_rules_digest: Callable[[], str], provider_profiles_digest: Callable[[], str]) -> None`<br>`def resolve_base(self) -> dict[str, str]`（不含 evolution_overlay；组件 digest 进程内按内容源失效缓存）<br>`def resolve(self) -> SystemSnapshotV1`<br>`def resolve_for_run(self, assignment: RunAssignmentV1 | LegacyRunAssignmentV1, overlay: EffectiveEvolutionOverlayV1 | None) -> SystemSnapshotV1` — governed 时追加 `evolution_overlay = overlay_digest`（sha256(EffectiveEvolutionOverlayV1 canonical)，与 evolution_repo.py:373-381 同算法），legacy 省略该 key。<br>kernel = `canonical_sha256({"tianshu_version": ..., "dependency_lock_hash": dependency_lock_hash()})`；executor = 每个已注册 adapter 的 `manifest.content_hash`（capabilities.py:52-98 已是 canonical 摘要，content_hash 继承自 models/governance_contract.py:69 CanonicalContractModel；**不用 probe.semantic_id**——它不含 CLI 版本且被回放钉死） |
| src/tianshu/storage/system_snapshot_repo.py | `class SystemSnapshotRepository`（无构造参数、方法显式传 connection，仿 evolution_repo.py:242 形态）：<br>`def insert_snapshot(self, connection, snapshot: SystemSnapshotV1) -> None` — 预读后普通 INSERT；IntegrityError 后重读，逐字段等值才视为幂等，不等值抛错（不用 `INSERT OR REPLACE` / `INSERT OR IGNORE`）<br>`def insert_binding(self, connection, *, memorial_id: str, attempt_id: str, snapshot: SystemSnapshotV1, generation_ids: tuple[str, ...] = ()) -> SystemBindingWriteResult` — SAVEPOINT 内原子 insert-once；IntegrityError → 重读逐字段等值即幂等返回，不等值抛 `EvolutionAssignmentConflict`（仿 evolution_repo.py:414-423/464-469）；同事务比对同 memorial 上一行，snapshot_digest 不同 → SystemAudit + durable outbox 记 `system_snapshot_drift`（只记不拒）；`try_insert_binding(...)` 是 bind_runtime 唯一影子豁免入口，失败原子回滚数据段并 best-effort 记 `system_snapshot_binding_failed`<br>`def get_last_binding(self, connection, memorial_id: str) -> SystemBinding | None` — 按 created_at, attempt_id 取最后一行（Evidence"最后一个 attempt 赢"语义）；解码复核 digest == canonical_sha256(components)（读 fail-closed）<br>`def get_binding(self, connection, *, memorial_id: str, attempt_id: str) -> SystemBinding | None` — P3 起供 continuity 继承读 generation_ids |
| src/tianshu/bootstrap/wiring_snapshot.py | `def wire_system_snapshot(app, settings) -> None`：从 app.state（skills / persona_loader / model_registry / executor adapter registry / policy_rules）组装组件源，构造 Resolver 挂 `app.state.system_snapshot_resolver`。在 app.py lifespan `wire_hook_registrations` 之后调用——ChallengerRouter 装配早于各内容源（test_evolution_composition 顺序守卫），故 router 侧的 resolver 引用必须**晚绑定**（注入 `Callable[[], SystemSnapshotResolver | None]`，bind 时取用） |

**修改文件**

| 文件:位置 | 前 → 后 |
|---|---|
| src/tianshu/storage/migrations.py:4239 前 | 追加 V31 块：`_SYSTEM_SNAPSHOTS_VERSION = _CONSULTATION_ROUNDS_VERSION + 1`、`_SYSTEM_SNAPSHOTS_STATEMENTS`（DDL 见下）、checksum、`_system_snapshots_upgrade`（V18 严格幂等范式：表已存在时 sqlite_master 逐对象等价比对，migrations.py:3637 同款）；MIGRATIONS 尾部追加条目 |
| src/tianshu/evidence/service.py:641 | `_lock_hash` 提为模块级 `def dependency_lock_hash() -> str`（返回值不变，仍 '0'*64 占位），`EvidenceService._lock_hash` 与 `_environment` 改为委托——resolver 的 kernel 组件与 evidence 环境指纹共用一处，避免假漂移（'0.5.2' 版本字面量 :656/:660 同源复用） |
| src/tianshu/skills/loader.py | 新增 `def content_digest(self) -> str`：对全部搜索目录的 SKILL.md 与资源文件做 `{relpath: sha256(bytes)}` 的 canonical_sha256；结果缓存于实例，`invalidate_cache` 与 `save_skill` 清缓存；独立 walk，不动 `_l2_stats`（mtime/size 留作性能层）、不改优先级链 |
| src/tianshu/persona/loader.py | 新增 `def content_digest(self) -> str`：DB 在编官员行（排序序列化，排除时间戳噪声字段）+ runtime_dir 下各 persona 的 SOUL.md/ROLE.md 内容哈希合并 canonical hash |
| src/tianshu/tools/policy_rules/__init__.py | 新增 `def ruleset_digest() -> str`：`canonical_sha256({r.rule_id: r.priority for r in build_default_rules()})`（:25 之后）——key 名定 `policy_rules`，与 evolution/adapters/policy.py 治理契约明确区分 |
| src/tianshu/providers/registry.py | 新增 `ModelProviderRegistry.content_digest(self) -> str`：BUILTIN_PROFILES（asdict 排序）+ `list_model_providers()` 行（**排除任何 key 明文；api_key_ref 三态字符串保留；排除 updated_at 等时间戳**）canonical hash |
| src/tianshu/universe/router.py:188 | `def bind_runtime(self, memorial_id: str) -> Iterator[EvolutionRuntimeContext | None]` → `def bind_runtime(self, memorial_id: str, *, attempt_id: str | None = None) -> Iterator[EvolutionRuntimeContext | None]`；构造器（:82-98）增 keyword-only `snapshot_resolver: Callable[[], SystemSnapshotResolver | None] | None = None`。在现有 UoW（:190-211，commit 前）内：resolver() 与 attempt_id 均非 None 时 resolve_for_run → insert_snapshot → insert_binding（generation_ids 影子期恒 `()`）；**legacy 分支（:212-214）同样写 binding**（省略 overlay key）；写入全程 try/except Exception：失败经 SystemAudit 记 `system_snapshot_binding_failed`，不抛——**全方案唯一影子豁免点**（strict 翻转在 P6）；成功后 snapshot 放进 RunBindingContextV1（governed 同时进 EvolutionRuntimeContext.system_snapshot）——governed 与 legacy 两分支都设置 RunBindingContextV1 |
| src/tianshu/evolution/runtime_context.py:15 | `EvolutionRuntimeContext` 加字段 `system_snapshot: SystemSnapshotV1 | None = None`；另增设第二个轻量 ContextVar 与 frozen 模型 `RunBindingContextV1 { memorial_id, attempt_id, system_snapshot: SystemSnapshotV1 | None, generation_ids: tuple[str, ...] }` 及读取器 `current_run_binding()`——legacy 分支 bind_runtime 直接 yield None、不设 EvolutionRuntimeContext（router.py:200-214；且 EvolutionRuntimeContext strict+frozen、assignment 必填，legacy 无法构造），执行期读 binding/pinned 必须走这条 governed/legacy 通吃的通道 |
| src/tianshu/application/run_dispatcher.py:225 | `bind_runtime(authority.memorial_id)` → `bind_runtime(authority.memorial_id, attempt_id=authority.attempt_id)`（AttemptAuthority 定义 :32，attempt_id 就在手边） |
| src/tianshu/evidence/service.py:911-928 之后、:939 排序之前（`_snapshot_current` 内） | 完全复刻 assignment artifact 块：`binding = SystemSnapshotRepository().get_last_binding(connection, memorial_id)`；非 None → `payload = {"snapshot": snapshot.model_dump(mode="json"), "generation_ids": list(generation_ids)}`（**首版即含 generation_ids key，形状一次定死**——open/close 各算一次 digest，二次变形会漂移）→ `put_bytes_current(connection, canonical_json_bytes(payload), media_type="application/vnd.tianshu.system-snapshot.v1+json", redaction="safe")` → `artifact_by_digest[ref.digest] = ref; required_artifact_digests.add(ref.digest)`。binding 为 None（legacy/影子失败/存量）→ **整块跳过**（仿 :912-914 分流，保证老数据可关闭）。artifact 字节**只在此处写**（open/close 各一次，内容寻址幂等去重），binding 表只存 digest——规避 `is_referenced_current` 引用清单封闭（artifact_repo.py:68-86）的 GC 竞态 |
| src/tianshu/config.py | `TianshuSettings` 加 `system_snapshot_enabled: bool = True`（env `TIANSHU_SYSTEM_SNAPSHOT_ENABLED`；False = wiring 不注入 resolver，一键停写）与 `system_snapshot_strict: bool = False`（env `TIANSHU_SNAPSHOT_STRICT`；P1 仅占位登记，P6 获得完整语义） |
| src/tianshu/gateway/evolution_api.py:56-95（可选，随本 PR） | run assignment 响应 data 加键 `"system_snapshot": {digest, components, generation_ids} | null`（读 binding；owner-or-admin 语义不变） |
| src/tianshu/models/edict_detail.py:94 +application/edict_detail.py:140-179 + web/src/api/edicts.ts:170 | `EdictEvidenceDetailV1` 加 `system_snapshot_digest: str | None = None`；Evidence 中存在 system-snapshot media type 时，从对应 Memorial 的最后一条 binding 投影 `SystemSnapshotV1.digest`（不能误用包含 `generation_ids` 的 ArtifactRef digest）；前端镜像可选字段；gateway（edicts_api.py:582）model_dump 透传免改 |

#### 数据迁移（V31 `0031_system_snapshots`，V18 严格模板）

```sql
CREATE TABLE system_snapshots (
  snapshot_digest TEXT PRIMARY KEY
      CHECK (length(snapshot_digest) = 64
             AND snapshot_digest NOT GLOB '*[^0-9a-f]*'),
  schema_version  INTEGER NOT NULL CHECK (schema_version = 1),
  components_json TEXT NOT NULL
      CHECK (json_valid(components_json) AND json_type(components_json) = 'object'),
  first_seen_at   TEXT NOT NULL CHECK (length(trim(first_seen_at)) > 0)
);
CREATE TABLE run_system_bindings (
  memorial_id         TEXT NOT NULL CHECK (length(trim(memorial_id)) BETWEEN 1 AND 256),
  attempt_id          TEXT NOT NULL CHECK (length(trim(attempt_id)) BETWEEN 1 AND 256),
  snapshot_digest     TEXT NOT NULL REFERENCES system_snapshots(snapshot_digest) ON DELETE RESTRICT,
  generation_ids_json TEXT NOT NULL DEFAULT '[]'
      CHECK (json_valid(generation_ids_json) AND json_type(generation_ids_json) = 'array'),
  created_at          TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
  PRIMARY KEY (memorial_id, attempt_id)
);
-- 触发器：system_snapshots_no_replace / no_update / no_delete；
-- run_system_bindings_no_replace / no_update。no_replace 防 recursive_triggers=OFF 时
-- INSERT OR REPLACE 绕过不可变约束。
-- bindings 不建 no_delete、不建 memorials FK：与敕令删除清理路径对齐（V22 动机 migrations.py:3903-3915）；
-- 不可变权威副本在 Evidence artifact（closed bundle 触发器冻结）。见 §7 决策 5。
```

存量数据：不回填（存量 Memorial 无 binding，Evidence 侧跳过分流兜底）。fresh-schema 与迁移前缀测试必须同步登记 V31：`test_migration_preserves_data.py` 的完整 ledger、post-baseline 表和不可变触发器集合，`test_mcp_secret_migration.py` 的完整 ledger，以及 `test_s4_s5_handoff.py` 的 live-tail 表集合；`test_evolution_migration_schema.py` 另建 V31 对象锁定段，不污染冻结的 V18 注册表。

#### 兼容策略与开关

- `TIANSHU_SYSTEM_SNAPSHOT_ENABLED=0` → wiring 不注入 resolver → bind_runtime 零写入、Evidence 因 binding 缺失自动跳过 artifact 块——**关掉后与今日逐字节同行为**。
- 影子豁免仅 bind_runtime 一处 try/except，有专用审计码、ADR-0014 记录、P6 翻转。
- 不改 `RunAssignmentV1` / `EvidenceSnapshotV1` schema_version / ManagedRunCommand 幂等指纹（binding 在 bind 时写，不进 ingress command）。
- DAG 子节点不建 binding 行（继承 root，与"子节点无 attempt/assignment"现状一致）；dedup/replay 分支不补写 binding——binding 是执行期事实，只在 bind_runtime 落，崩溃窗自愈由下一次 attempt 的 bind 完成。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/storage/test_durable_schema_v31.py（新） | `MIGRATIONS[:30]` 建库→哨兵 memorial→apply `MIGRATIONS[:31]`==(31,)→两表形状/FK/CHECK/五个不可变触发器拒 UPDATE/REPLACE 与（snapshots 表）DELETE、binding 普通 DELETE 仍允许→前序 V1-V30 三元组冻结 |
| tests/storage/test_migration_callback_freeze.py（改） | 登记 `0031_system_snapshots` 指纹（先记 PROGRESS.md 裁决） |
| tests/models/test_system_snapshot.py（新） | digest 自校验拒错值；非法 key/非 hex value/超上界拒；键序无关 digest 稳定 |
| tests/evolution/test_system_snapshot_resolver.py（新） | 同进程两次 resolve digest 相同；改一个 SKILL.md 后 digest 变且**只有 skills 组件变**；改 provider 行后只有 provider_profiles 变；legacy 无 evolution_overlay key |
| tests/universe/test_snapshot_binding.py（新） | binding 在 bind_runtime 的 UoW 内、yield 前落库并随事务原子回滚（复用 test_challenger_routing.py:754 回滚矩阵模式）；governed 与 legacy 两路都写；同 (memorial,attempt) 重复 bind 等值幂等/不等值冲突；resolver=None 零写入；**故障注入**：insert 抛错→run 照常执行 + 审计含 `system_snapshot_binding_failed`；两 attempt 不同 snapshot→两行 + drift 审计 |
| tests/evidence/test_system_snapshot_artifact.py（新，基于 tests/evidence/_fixtures.seed_closed_run + 固定 clock） | 有 binding：artifact 进 required、close 成功、verify/export/import 泛型通过、篡改字节→artifact_invalid；无 binding：跳过且可关闭（对照 test_close_snapshot_immutable.py:132-160 legacy 先例）；open/close 两次重算 digest 一致 |
| tests/gateway/test_evolution_view.py（改）+ tests/application/test_edict_detail_system_snapshot.py（新） | assignment 端点返回 system_snapshot 且 owner 404 语义不变；Edict 详情只在 artifact 存在时投影内容 digest |
| 存量影响 | bind_runtime 新参 keyword-only 带默认——tests/universe/test_challenger_routing*.py、tests/application/test_run_dispatcher_lifecycle.py 现有调用不破（fake router 需接受 attempt_id）；tests/evidence/test_schema_contract.py 不红（未改模型）；test_s4_s5_handoff.py:41 布局断言按新尾更新 |

#### 验收 checklist

- [x] V31 两张表与五个不可变/no-replace 触发器齐；上表测试全绿；mypy / ruff / lint-imports 绿
- [x] 每个开启态新受管 attempt 有 binding 行；Evidence artifact 的 snapshot 与 binding 等值
- [x] drift 只记不拒；同 memorial 两 attempt snapshot 不同 → 两行 + SystemAudit 一条
- [x] 真实 Demo：提交 Edict → assignment、Edict 详情与 required Evidence artifact 三面摘要一致，artifact 字节摘要复核通过
- [x] `TIANSHU_SYSTEM_SNAPSHOT_ENABLED=false` 独立 Demo 仍完成，assignment 字段为 null、Evidence 无该 artifact、两表计数均为 0；默认启用态全量 4806 passed / 2 skipped

**回退方式**：开关置 0（运行时回退）或 revert PR（代码回退）；V31 两表无消费者时留存无害——迁移 append-only，不回滚迁移本身。

**工作量**：≈4–5 天。

**决策与取舍**（三案分歧与选择）：
1. **binding repo 落点**：风险优先案放 evolution_repo.py（同居模块）；最小改动/目标纯度案新建 system_snapshot_repo.py。**采纳新文件**——不碰六表锁定测试（test_evolution_migration_schema 全文锁），改动面更小且边界与目标架构一致。
2. **artifact 字节写入时机**：三案一致（只在 `_snapshot_current` 写、binding 表只存 digest），采纳；备选"扩 `is_referenced_current` 探测清单"记为否决（扩清单要动 queries 元组且引入新引用语义）。
3. **影子期 generation_ids**：目标纯度案写合成 `("rg-legacy-process",)`（对应 roadmap Phase 1 的 legacy/default identity）；另两案写 `()`。**采纳 `()`**——合成身份是无对应行的假数据，P3 后需要解释性清理，违反最少过渡债；roadmap 对 legacy identity 的"仅证明进程归属"诉求由 kernel 组件（tianshu_version+lock hash）已覆盖。
4. **写失败语义**：三案一致影子非致命 + 审计；开关形态合成为"enabled（停写总闸）+ strict（P6 翻转）"双开关，启动时校验拒绝 strict=1 且 enabled=0 的矛盾组合。
5. **attempt_id 为 None**（非 dispatcher 调用方）：目标纯度案用哨兵 `"attempt:unbound"`；**采纳最小改动案**——resolver 与 attempt_id 均非 None 才写，不造哨兵假数据。
6. **`_lock_hash` 提为模块级共享**：三案一致，采纳（发版连带 '0.5.2' 字面量集中度提高）。

---

### 阶段 P2（PR-2）：ContributionHandle（≈1.5–2.5 天，可与 P1 并行）

**目标**：六类注册表贡献可归属（owner）、可逆序卸载（dispose_owner）；修掉 `register_command` hasattr 挂属性与 Tool/Channel/Skills 无卸载原语（MCP 断连工具残留这类现实 bug 的根因）。**不引入 generation 概念，不动执行器**（评审 §3.3：与代际无关，现在就能做）。

**前置依赖**：无。

#### 改动清单

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 新增 | src/tianshu/plugins/contribution.py | `@dataclass(frozen=True, slots=True) class ContributionHandle`：`owner / kind / name / target / dispose`；`dispose()` 返回 `ContributionDisposeStatus.DISPOSED / SKIPPED_STALE / NOOP`，可观测且幂等（kind Literal 与 PluginManifest.type 六值同形，plugins/manifest.py:16） |
| 修改 | src/tianshu/tools/registry.py | `register(...)` 新增 keyword-only `owner: str = "kernel"` 与 `on_conflict: Literal["error", "replace"] = "replace"`；**默认 replace 保持现状语义**，插件路径显式 `error`；同名冲突抛 `ToolRegistryConflict(name, existing_owner)`。新增 `unregister(name, *, owner=None, target=None) -> bool`，身份条件不匹配时不删除；成功时同时清 `_tools`、receipt lookup、disabled 与 owner |
| 修改 | src/tianshu/notifier/channel_registry.py:25 | 新增 `def unregister(self, name: str) -> bool`：pop `_channels` / `_rate_limits` / `_send_log` |
| 修改 | src/tianshu/skills/loader.py:276 | `_injected_skills` 转正为 `__init__` 字段（`for_workspace_overlay` 的 hasattr 复制路径同步简化）；`register_skill` 与新增 `unregister_skill` 都调用完整 `invalidate_cache()`，同时清 L1、L2 metadata/stat 与 content digest 缓存 |
| 修改 | src/tianshu/plugins/api.py | 每个 `register_*` 加 keyword-only `owner: str = "plugin:anonymous"` 并返回 `ContributionHandle`；依赖为 None 时返回**不跟踪**的 no-op handle + warning；`register_tool` 显式传 `on_conflict="error"`；`_commands` 转正为 `__init__` 字段；六类 dispose 闭包统一做身份校验与幂等清账；`dispose_owner(owner) -> tuple[int, int]` 按注册逆序释放并返回 `(disposed, skipped_stale)` |
| 不动 | src/tianshu/executor/adapters/__init__.py:103 `replace` | 只加 docstring 弃用注释（"P3 起由代际 API 接管，仅供装配/测试"），不改行为——3 处 E2E seam（test_executor_workspace_lifecycle.py:166/354/1242）与 `Executor.set_agent`（executor.py:148）依赖它 |
| 新增不变量（Codex A2） | src/tianshu/plugins/contribution.py + 各 dispose 闭包 | `ContributionHandle` 记录注册瞬间的被注册对象引用（`target: object`）；dispose 摘除前做**身份校验**——in-memory 注册表（tool / channel / skill / command）要求当前对象与 owner 均匹配；hook 由 PluginApi 为每次 contribution 建唯一 wrapper，再复用 HookRegistry 的 handler identity 注销，避免同一原 handler 被两个 owner 注册时相互误删；provider 落 SQLite、无内存槽位，只做 owner/current-handle 记账比对。旧身份不匹配时跳过摘除，返回 `SKIPPED_STALE` 并尽力写 SystemAudit `contribution_dispose_stale`。`dispose_owner` 返回 `(disposed, skipped_stale)`；dispose 底层异常时保留失败及未处理 handles，允许后续重试。**动机**：默认覆盖式注册后，旧 handle 不能静默摘掉新对象或丢失仍在运行的贡献账本 |
| 修改 | src/tianshu/tools/mcp/client.py | 每次工具重新发现发布 `on_tools_changed`；连接离开 connected 生命周期时发布 `on_tools_unavailable`，让断连与重连先撤回旧工具集合 |
| 修改 | src/tianshu/tools/mcp/manager.py | 按 session 保存 `owner="mcp:<server>"` 的 tool handles；重新发现、断连、重连与 shutdown 逆序回收旧集合；首次启动保留 manager 兜底登记；stale handle 身份不匹配时保留后来同名替换；一批工具中途校验失败时逆序回滚本批已注册 handles，禁止不可追踪残留 |
| 修改 | src/tianshu/gateway/mcp_api.py | restart helper 只调用 manager 的 shutdown/start 生命周期，不再直接删除 `ToolRegistry._tools` 私有状态 |
| 修改 | src/tianshu/models/system_audit.py | 登记 `contribution_dispose_stale` 允许事件；身份不匹配时尽力留痕，审计写失败不阻断清理 |

**数据迁移**：无。

**兼容策略与开关**：所有新参 keyword-only 带默认值——全部现有调用点（含 tests/tools/test_registry_and_builtins.py:84-92,122-131 三参签名）零改动通过；插件面 fail-closed（501 + manifest_only）不动——ContributionHandle 不是动态加载入口。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/test_plugin_api.py（扩展，评审点名文件） | 注册六类各一 → `dispose_owner` → 各注册表恢复原状（ToolRegistry 定义为 None、hook 链空、channel 还原、injected 无残留、_commands 无残留）；on_conflict="error" 重名给结构化诊断（异常含 name + existing_owner）；部分注册表缺席（None 依赖）时 dispose 不炸 |
| tests/test_plugin_api.py（循环压测） | 100 次「register 六类→dispose_owner」循环后，六个注册表内部结构（_tools/_owners/_channels/_injected_skills/_commands/hook 链）与初始状态逐项相等（contribution 泄漏断言——评审 Phase 2 退出条件『连续 100 次…无 contribution 泄漏』的循环化） |
| tests/test_plugin_api.py（Codex A2） | 注册 tool `x`（owner=A）→ 同名 replace 注册（owner=B）→ dispose_owner(A)：`x` 仍是 B 的对象、返回 skipped_stale=1、SystemAudit 含 `contribution_dispose_stale`；provider 类只比对 owner |
| tests/tools/test_registry_and_builtins.py（不改断言，补用例） | 既有三参调用通过；默认 replace 兼容、结构化冲突；unregister 后 execute 报 unknown tool，owner/disabled/receipt lookup 全清 |
| tests/notifier/test_channel_registry_unregister.py（新） | unregister 后 send_all 不再触达；限流窗口清理 |
| tests/tools/mcp/test_manager.py + test_reconnect.py | 实际 fixture 子进程启动/关闭、每次重新发现、断连/重连均撤回旧工具；shutdown 不摘后来同名替换；API restart 不再手删私有字典 |
| 回归 | wire_persona submit_edict 覆盖注册与 MCP 重连路径全量跑不红；tests/tools/test_persona_acl_enforcement.py、tests/security/test_mcp_lean_admission.py、tests/gateway/test_plugin_manifest_api.py 原样绿 |

#### 验收 checklist

- [x] dispose_owner 逆序卸载且注册表状态与注册前逐项相等；连续 100 次六类注册/释放零 contribution 泄漏
- [x] create_app 双 profile（live/demo）原生启动冒烟绿；插件面 fail-closed 测试不红
- [x] MCP 实际 fixture 子进程启动、重新发现、断连/重连与 shutdown 均自动验证旧工具撤回
- [x] （Codex A2）覆盖式重注册后旧 handle dispose 不摘新对象，且留 `contribution_dispose_stale` 审计

**回退方式**：revert PR；无 schema migration、无新增持久模型，可能追加的既有 SystemAudit 历史记录无需删除，也不会形成新 schema 依赖。**工作量**：≈1.5–2.5 天（含 Codex A2）。

**决策与取舍**：①`on_conflict` 缺省值——目标纯度案取 "error"（收紧，但要求全仓 register 调用点清点且有未知覆盖点炸启动的风险）；风险优先/最小改动案取 "replace"（现状零改动）。**采纳 "replace" 缺省 + 插件路径显式 "error"**——优先级②（每步不破坏）压过③（纯度）；全局收紧为 error 列入延期项（附全仓清点清单后再做）。②`ExecutorAdapterRegistry.replace` 三案一致不在本阶段动（评审原建议"改组合或删除"被否决——它是 E2E seam）。③HookRegistry 核心签名不改（owner 记账留在 PluginApi 层），三案一致。

---

### 阶段 P3（PR-3a）：Pi 执行器代际与 continuity 固定（V32，≈1.5–2 周 + 2 天）

**目标**：`keqing:pi` 换代 = stage → warm → activate 原子切换：新 root 取 active 代、运行中 attempt 固定其 reserve 的代、follow-up 继承 root 的代（代已 failed/disposed 或 material 不可用 → fail-closed `generation_retired`）、cron 每次 fire 取当时 active、DAG 子节点与基础设施重试继承 root、warm 失败指针不动、exact-attempt 与 OPEN-continuity 引用均释放且不是 last-good 后才 dispose。每个新 attempt 都用独立 `run_generation_bindings` insert-once 固定代际选择；snapshot 关闭时也写显式 `bound []`，不依赖 P1 shadow 是否存在。`GenerationReconciler` 与 `EvolutionRollbackReconciler` 独立实现并由同一个 control-plane tick 组合驱动。本阶段**不接 Promotion，也不提供生产 stage/activate API 或 CLI**；治理写入口在 P5。

**前置依赖**：P1（SystemSnapshot 与可选 `run_system_bindings` shadow；EvolutionRuntimeContext 已扩展）。P3 自己在 V32 新增独立 exact-attempt generation authority，不把 P1 shadow 升格为代际权威。P2 非硬依赖。

#### 改动清单

**新增文件**

| 文件 | 内容 |
|---|---|
| src/tianshu/models/runtime_generation.py | `RuntimeReleaseV1` 保存可重建的完整 canonical material：scope、完整 manifest/hash、CLI version/source、解析后的绝对 binary path、binary/package digest、single/session argv shape、Pi wire version、materializer id/version；`release_digest` 为其内容摘要。`RuntimeGenerationV1` 保存 `rg-` 运行实例身份、scope、release_digest、七态、CAS version 与时间戳。常规图含 `staged→{warming,failed}`、`warming→{ready,failed}`、`ready→{active,failed}`、`active→draining→disposed`；`draining→active` 仅由 repository 的 last-good rollback 专用验证器表达。**refcount 不在模型/表中** |
| src/tianshu/storage/generation_repo.py | 无状态、caller-owned transaction 的唯一 SQL 写权威：`insert_release` / `insert_staged` / scope-aware `get_generation` / `list_by_scope` / `get_pointer` / pre-activation transition；`activate(scope,target,expected_pointer_version)` 在一笔事务内 old active→draining、new ready→active、最后 pointer CAS；`rollback_to_last_good` 是唯一允许 draining→active 的入口；`dispose_if_unreferenced` 在同一写事务重查 active/last-good、durable attempt 和 OPEN conversation retention；读取 release/journal 时逐字节复核 canonical JSON/hash/链 |
| src/tianshu/executor/keqing/generation.py | `PiGenerationBundle` 同时拥有固定到同一绝对 binary/release 的 `PiAdapter`、`PiSessionAdapter`、单发与 session delegate；`PiReleaseMaterializer` 只按持久 material 构造/复核 bundle，重启不得重新 `which()` 静默替换 |
| src/tianshu/executor/keqing/pi_probe.py | `async def verify_pi_rpc_contract(execution_gateway: ExecutionGateway, *, workspace_root: Path, binary_path: str | None = None, timeout_seconds: float = 30.0) -> tuple[bool, str | None]`：经 `issue_keqing_command_grant` + `gateway.start` spawn `pi --mode rpc --no-session`（**不得绕网关直 spawn**——test_no_direct_process_launch 管制，allowlist 不扩）；校验首行 session header `version == VERIFIED_SESSION_VERSION`，发送副作用为零的 `get_session_stats` 并要求同 command id 的成功 response + object data；拒绝/失败帧显式失败，不等待 `agent_settled` / `agent_end` 这类任务执行事件。返回 `(ok, reason)`，所有路径清理 stdin/handle |
| src/tianshu/executor/keqing/versions.py | `def detect_installed_version(binary: str) -> str | None`：`_detect_installed_version` 从 gateway/keqing_api.py:44 下移至此（离线读 package.json 不 spawn），keqing_api 改 import 薄委托——executor 侧与状态页共用同一版本探测来源，供 release_digest 计算复用 |

**修改文件**

| 文件:符号 | 前 → 后 |
|---|---|
| src/tianshu/storage/migrations.py | 追加 V32 `0032_runtime_generations`（V18 严格模板，DDL 见下）：四张 release/generation/journal/pointer 表 + 一张 `run_generation_bindings`，共五表；迁移按 system-copy / native-empty / ambiguous-Pi-unresolved 三分回填；四件套照通用约定 |
| src/tianshu/executor/adapters/__init__.py | Registry 只管理 generation-owned materialized bundles 与锁内 selection，不写数据库状态机。`reserve_binding(attempt_id, pinned_ids)` 在同一锁快照返回按 scope 排序的 IDs、bundles 与 manifest digest override；内部 lease 为 `attempt_id → {scope:generation_id}`，等值重入幂等、异值重入拒绝；`release(attempt_id)` 幂等。`generation_ids=()` 时现有 adapter dict/replace seam 逐字节兼容 |
| src/tianshu/executor/generation_controller.py | 唯一编排层：组合 repository、materializer、warm probe 与 registry，实现内部 `stage/warm/activate/rollback/recover`；外部副作用不在 SQLite 事务内，commit 后才发布 registry 视图。P3 不把这些方法暴露为 HTTP/CLI |
| src/tianshu/executor/executor.py | `_prepare_runtime_executor` 只从 `current_run_binding()` 按 `scope='executor:keqing:pi'` 取 pinned bundle；DAG/outer/single 都消费同一个 `PreparedExecutor`。Executor 内不 acquire/release generation，避免多 finally 双重所有权 |
| src/tianshu/application/run_dispatcher.py | attempt 外层 `finally` 是唯一权威 `release(authority.attempt_id)` 点；generation binding/material 错误映射稳定 fail-closed code，且仍走既有 attempt terminalization/projection cleanup |
| src/tianshu/application/managed_run_ingress.py | 精确 replay 顺序与命令指纹不改；只为同一事务刚创建的 attempt 预绑定 exact generation marker，终态/claimed/suspended 历史 replay 不反向补写。follow-up 的 parent 选择仍由现有 ingress 决定，Router 随后按 parent 最新 root marker 继承并验证 |
| src/tianshu/universe/router.py bind_runtime | 在自己的 UoW/attempt 边界调用 controller：同 Memorial retry 优先复制上一 attempt marker；新 follow-up 继承 parent 最新 root；新 scheduled/root 只选本次所需 scope 的 active。selection、bundle、manifest override 来自同一锁快照；`run_generation_bindings` 对非空与空 tuple 都严格 insert-once，任何失败在 yield/副作用前拒绝。snapshot 启用时另写可选 `run_system_bindings` shadow；两者同在必须一致 |
| src/tianshu/evolution/reconciler.py + bootstrap | 新建独立 `GenerationReconciler`，不继承/改写 rollback reconciler。既有 `reconcile_control_planes()` 每 tick 顺序调用二者，各自持锁。启动按 release material 重建 active/draining/last-good；P3 中无授权的 staged/warming/ready 一律置 failed；回收前在同一写事务重查 exact-attempt refs、每个 OPEN Edict 最新 root continuity retention 与 active/last-good roots。readiness 在一把锁内返回原子 `(ready,error_codes)`；活动/保留代材料故障让 `/health/ready` 返回 503，仅待清理旧代保持 200/degraded |
| src/tianshu/executor/keqing/pi_adapter.py:201 | `build_session_argv(self, *, session_dir=None, model=None, resume=False)` → 增 keyword-only `binary_path: str | None = None`（代 materialize 时 `shutil.which("pi")` 固化绝对路径进 argv[0] 与 release_digest——防同一代先后 spawn 到不同二进制；`_is_canonical_pi_session` :161 已兼容绝对路径）。**单发 PiAdapter 与会话 PiSessionAdapter/KeqingSessionExecutor 必须同代切换**：stage 的单元 = (单发实例, 会话实例, binary_path) 三元组整体登记（"pi 一名双执行器"，executor.py:118-122）；首版定死"代际不改 argv 形状，只固化 binary_path"，`is_canonical_adapter_argv` 无需代际化 |
| src/tianshu/gateway/keqing_api.py:90-124 | status 行加 `"generation": {"id", "state", "active_runs", "last_good_id"} | None`（非代际 scope 为 None；active_runs 取内存账本）；web/src/api/types.ts:440 KeqingBackendStatus 加同名字段；KeqingManagementPage.tsx:107-158 加「代际」列；i18n 三份 locale 补 key |
| src/tianshu/config.py | P3 不新增未被生产消费者使用的 stage 开关，避免形成隐藏写入口；`executor_generation_enabled` 随 P5 Promotion Authority 一并引入。无 pointer 行就是 P3 的 legacy 隐式单代开关 |
| 新增 | tests/architecture/test_generation_authority.py | AST 扫描（仿 test_promotion_authority.py:8）：`'active'/'draining'/'disposed'` 等 generation 状态与 generation_pointers 写点仅限 generation_repo / ExecutorAdapterRegistry / GenerationReconciler / 启动序列 |
| 修改（Codex B8） | src/tianshu/executor/execution_gateway/policy_models.py:318 `ExecutionReceipt` + src/tianshu/executor/keqing/executor.py:97 | receipt 新增两个带默认值的可选字段 `executable_version: str \| None = None`、`executable_version_source: Literal["package_json","pinned","unverified"] = "unverified"`（schema_version 保持 "1"，纯追加）；准备阶段对**全部** keqing backend（不只 pi）用 `shutil.which` + `Path.resolve(strict=True)` 把 argv[0] 换成绝对路径再交网关，gateway.py:294 现有的 `executable=request.command_argv[0]` 自然记绝对路径；版本来源复用上表 versions.py；为 claude-code/codex/opencode 补 `PINNED_*_VERSION` 常量（对齐 PINNED_PI_VERSION），**drift 只上报不拦截**、读不到就是 unverified。不动 EvidenceSnapshotV1、不动 parse_stream。「按版本区间 ExecutionDenied」延期到跑满一个 canary 周期有漂移数据后再议（§6） |
| 原子切代注记 | `GenerationRepository.activate` | 新 bundle 先在事务外 materialize/warm；同一 `BEGIN IMMEDIATE` 内必须**先撤旧 active→draining，再立新 ready→active，最后 CAS pointer**。事务外只见完整旧态或新态；“先立新 active”会违反部分唯一索引，“先切 pointer”会制造不一致 |
| 新增（Codex B9，本阶段首个 commit） | tests/support/waiting.py | `EventProbe` / `await_event` 在 EventBus 挂临时订阅并缓冲不匹配事件，配 `drain()` / `seen_types()` 断言「没有多余事件」。**硬规则：EventBus 等待必须带事件类型和谓词，timeout 只作失败上界不作同步原语**；非 EventBus 并发测试使用 `asyncio.Event` / thread barrier。本阶段新增测试均不以 `sleep` 充当同步原语 |

#### 数据迁移（V32 `0032_runtime_generations`，V18 严格模板）

```sql
CREATE TABLE runtime_generation_releases (
  release_digest TEXT NOT NULL PRIMARY KEY CHECK(
    length(release_digest)=64 AND release_digest NOT GLOB '*[^0-9a-f]*'),
  schema_version INTEGER NOT NULL CHECK(schema_version=1),
  scope TEXT NOT NULL CHECK(length(trim(scope)) BETWEEN 1 AND 256),
  release_json TEXT NOT NULL CHECK(json_valid(release_json) AND json_type(release_json)='object'),
  first_seen_at TEXT NOT NULL,
  UNIQUE(release_digest, scope)
);
CREATE TABLE runtime_generations (
  generation_id  TEXT NOT NULL PRIMARY KEY CHECK(
    length(generation_id)=35 AND substr(generation_id,1,3)='rg-'
    AND substr(generation_id,4) NOT GLOB '*[^0-9a-f]*'),
  schema_version INTEGER NOT NULL CHECK(schema_version=1),
  scope          TEXT NOT NULL CHECK(length(trim(scope)) BETWEEN 1 AND 256),
  release_digest TEXT NOT NULL,
  state          TEXT NOT NULL CHECK(state IN ('staged','warming','ready','active','draining','disposed','failed')),
  version        INTEGER NOT NULL CHECK(version > 0),
  created_at     TEXT NOT NULL,
  activated_at   TEXT,
  updated_at     TEXT NOT NULL,
  UNIQUE(scope, generation_id),
  FOREIGN KEY(release_digest, scope)
    REFERENCES runtime_generation_releases(release_digest, scope) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX idx_runtime_generations_active ON runtime_generations(scope) WHERE state = 'active';
CREATE TABLE runtime_generation_journal (
  journal_id         TEXT NOT NULL PRIMARY KEY CHECK(
    length(journal_id)=64 AND journal_id NOT GLOB '*[^0-9a-f]*'),
  generation_id      TEXT NOT NULL REFERENCES runtime_generations(generation_id) ON DELETE RESTRICT,
  generation_version INTEGER NOT NULL CHECK(generation_version > 0),
  from_state         TEXT CHECK(from_state IS NULL OR from_state IN ('staged','warming','ready','active','draining','disposed','failed')),
  to_state           TEXT NOT NULL CHECK(to_state IN ('staged','warming','ready','active','draining','disposed','failed')),
  entry_json         TEXT NOT NULL CHECK(json_valid(entry_json) AND json_type(entry_json)='object'),
  entry_hash         TEXT NOT NULL CHECK(
    length(entry_hash)=64 AND entry_hash NOT GLOB '*[^0-9a-f]*'),
  created_at         TEXT NOT NULL,
  UNIQUE(generation_id, generation_version),
  CHECK((generation_version=1 AND from_state IS NULL AND to_state='staged')
     OR (generation_version>1 AND from_state IS NOT NULL))
);
CREATE TABLE generation_pointers (
  scope                   TEXT NOT NULL PRIMARY KEY,
  active_generation_id    TEXT NOT NULL,
  last_good_generation_id TEXT NOT NULL,
  version                 INTEGER NOT NULL CHECK(version > 0),
  updated_at              TEXT NOT NULL,
  FOREIGN KEY(scope, active_generation_id)
    REFERENCES runtime_generations(scope, generation_id) ON DELETE RESTRICT,
  FOREIGN KEY(scope, last_good_generation_id)
    REFERENCES runtime_generations(scope, generation_id) ON DELETE RESTRICT
);
CREATE TABLE run_generation_bindings (
  memorial_id        TEXT NOT NULL CHECK(length(trim(memorial_id)) BETWEEN 1 AND 256),
  attempt_id         TEXT NOT NULL CHECK(length(trim(attempt_id)) BETWEEN 1 AND 256),
  state              TEXT NOT NULL CHECK(state IN ('bound','unresolved')),
  generation_ids_json TEXT CHECK(
    (state='bound' AND json_valid(generation_ids_json)
                   AND json_type(generation_ids_json)='array')
    OR (state='unresolved' AND generation_ids_json IS NULL)
  ),
  created_at         TEXT NOT NULL CHECK(length(trim(created_at)) > 0),
  PRIMARY KEY(memorial_id, attempt_id)
);
-- release: no_replace/no_update/no_delete
-- generation: no_replace + identity/material immutable
-- journal: no_replace/no_update/no_delete
-- pointers: no_replace/no_delete + scope immutable；其余仅 CAS
-- run_generation_bindings: no_replace/no_update；允许随 Edict 物理删除清理
```

迁移回填覆盖 V32 升级时已有的全部 attempt，并只写可证明的历史事实：有
`run_system_bindings` 时逐 attempt 复制 generation ids；可由 requested contract 与 runtime override
证明为非 Pi 时写 `bound []`；历史 Pi 或契约/override 不能可靠解码时写 `unresolved`。`unresolved`
不会猜测 active，也不会静默退回 static；continuity 与 retention 读取会失败关闭。

#### 兼容策略与开关

- 未显式 stage 任何代 → registry 隐式单代模式，prepare 不带 generation_id、`replace`/`set_agent`/测试 seam 全部不变——现有 test_executor_capabilities / test_executor_workspace_lifecycle 全绿即证明。
- follow-up/retry 优先继承 parent/source attempt 的 exact marker；只有 V31 历史数据尚无 marker 时才读 `run_system_bindings.generation_ids_json` fallback。显式 `bound []` 是确定选择，不会因后来出现 active Pi 而改选。
- generation tuple 只含本次实际需要的 scope，按 `(scope,generation_id)` canonical 排序；拒绝重复 ID、重复 scope、未知 ID、跨 scope、failed/disposed 或不可 materialize 项。
- 每个新 attempt 都必须得到 durable `run_generation_bindings`，空 tuple 也以 `bound []` 落盘。默认 `system_snapshot_enabled=true` 时，scheduled/run-now fresh fire 还必须在推进 cursor 的同一事务内得到 P1 snapshot shadow，resolver 暂坏或 shadow write 失败则整 fire 回滚；显式关闭 snapshot 时仅 `run_system_bindings`/`system_snapshots` 保持零写入，exact generation marker 仍写。任何非空 tuple 的 selection、marker、snapshot override 或 materialization 失败都在首个受管副作用前 fail closed。
- manifest/probe 钉死回放不破：整代保留旧 manifest/adapter 实例直到 drain 完（`_bind_verified_effective` 漂移拒绝不会命中旧 run）。
- ContextVar 不跨子进程：代/快照信息进子进程只能走 env/argv——本阶段不注入（列延期项），binary_path 固化已保证子进程二进制正确。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/storage/test_durable_schema_v32.py（新）+ freeze 登记 | 五表 exact shape；release/journal/pointer no-replace；attempt marker no-replace/no-update；复合 scope FK；部分唯一 active；V31→V32 三分回填、exact precreate replay、partial/drift fail-closed 与前序 checksum/fingerprint 冻结 |
| tests/models/test_runtime_generation.py（新） | release canonical/hash/material 完整性；常规转移逐边；普通 API 拒绝 draining→active；last-good 专用边；version/时间约束 |
| tests/storage/test_generation_repo.py（新） | activate/rollback 每个 SQL 故障点完整回滚；两连接只见旧/新完整态；pointer CAS、坏新代不成为 last-good；release/journal 解码复核；多 scope 校验 |
| tests/executor/test_generation_registry.py（新） | 同锁 selection/bundle/manifest；attempt reservation 等值幂等、异值拒绝、ABA 与双 release；Pi single/session 同代；restart materialize 不重新 `which()`；无 generation 与现行为一致 |
| tests/executor/test_generation_fault_matrix.py（新，故障注入） | warm 坏帧/超时不动指针；warm 前完整包复验且在线程池执行；100 次 stage/warm/activate/rollback/drain 循环中，旧 exact-attempt 每轮真实执行固定的 fake delegate；binary/package/manifest 漂移重启 fail closed |
| tests/executor/keqing/test_pi_probe.py（新） | header + `get_session_stats` 成功 response 通过；header version 漂移、response 拒绝、坏 JSON、超时与 cleanup 失败均收口；必须经 gateway+grant（直 spawn 被架构测试拒） |
| tests/application/test_generation_continuity.py（新） | follow-up 继承；exact retry 新 attempt 同代且无 ABA；每个 OPEN Edict 最新 root 阻止 continuity gap，Edict 结案后释放；cron/interval 每个 fresh fire 在触发事务内预绑定当时 active 且不继承上次 fire；空代也必须持久化，resolver/影子写失败不推进 cursor；终态 replay 不补写历史，只有未启动 CLAIMABLE replay 可补缺失 binding；DAG 子节点复用 root；非空多 scope 任一坏项时零 binding、零部分 lease |
| tests/evolution/test_generation_reconciler.py（新） | active/last-good/OPEN continuity 永不普通 dispose；无引用 draining 收敛；staged/warming/ready 重启置 failed；活动或保留代材料缺失让 required readiness 503，纯 cleanup pending 只 degraded；readiness tuple 原子采样；与 rollback reconciler 顺序组合且锁独立 |
| tests/architecture/test_generation_authority.py（新） | AST 扫描入 CI |
| tests/executor/keqing/test_executable_resolution.py（新，Codex B8） | 四个 backend argv[0] 均为绝对路径；PATH 上不存在 → 明确错误而非裸名透传；receipt 含 version 与 source；读不到版本 = unverified 且不拒绝 |
| 存量影响 | test_executor_workspace_lifecycle.py（replace seam）不 stage 时不红；test_evolution_composition.py 若断言 reconciler 类型需同步（保留 alias 可免改）；tests/compat/test_executor_capabilities.py 不改；tests/gateway/test_keqing_status.py 只加字段断言（gateway_enabled 恒 False 等既有锁不动） |

#### 验收 checklist

- [x] 故障注入矩阵全绿；"连续 100 次 Pi 换代无 run 混代"脚本化验证
- [x] warm 失败 active/last-good 不变；旧任务完成后旧代 disposed；follow-up/长任务全程单代；cron 每 fire 取 active
- [x] 内部 controller/repository 的 stage→warm→activate→rollback→drain 集成矩阵全绿；P3 不伪造生产写入口
- [x] live/demo 真实启动在无 generation 行时行为与当前一致；status generation 为 null；无新增 stage/activate HTTP 或 CLI
- [x] `system_snapshot_enabled=false` 完整 lifespan 下 scheduled/run-now 继续成功，`system_snapshots` / `run_system_bindings` 零写，但每个新 attempt 的 `run_generation_bindings=bound []` 可证明
- [x] 不 stage 时全量测试与主干等价；test_promotion_authority + test_generation_authority AST 绿
- [x] （Codex B8/B9）ExecutionReceipt 记绝对路径 + 版本；本阶段新增测试零基于时间的 `sleep` 同步

> P5 仍须独立证明：snapshot 显式关闭时，executor stage/activate 在配置期 fail closed；该约束不由 P3 预先伪造生产写入口。

**回退方式**：不 stage 新代（隐式单代模式即现状），GenerationReconciler 的 generation 分支由空表天然短路；极端 revert 代码，V32 表闲置无害。

**工作量**：≈1.5–2 周 + 2 天（Codex B8/B9）。

**决策与取舍**：
1. **开关与生产入口**：P3 不引入未被 Promotion Authority 消费的 stage flag/API/CLI；无 pointer 行就是隐式单代模式。P5 才增加 deny-by-default 的晋升写入口与开关。
2. **refcount 与 continuity**：不持久化整数 refcount；V32 的 `run_generation_bindings` 不是第二个 session/refcount 账本，而是每个 attempt 必需的 insert-once 代际权威。进程内 lease 只按 exact `attempt_id`；durable roots 从非终态 `execution_attempts + run_generation_bindings`、每个 OPEN Edict 最新 root marker 与 pointer active/last-good 推导；`run_system_bindings` 仅作 snapshot shadow/V31 fallback，两者同在必须一致。cron/interval 的新 fire 是独立 fresh root，在 scheduler 事务推进 cursor 前预绑定当时 active，不从上次 fire 继承；保留 OPEN Edict 的最新 root 只用于封住其潜在 follow-up 创建窗口。
3. **journal 表**：最小改动案省略；另两案含。**采纳含 journal**（不可变触发器）——generation 切换是治理相关事实，与项目"证据文化"一致，且 reconciler crash 后重放需要它。
4. **"绑定即固定"接线**（bind 时写 active 代 → prepare 按 pinned 取）：三案殊途同归，采纳最小改动案的 `pinned_generation_id` 参数化表述（最明确）；bind 与 prepare 间切代由 pinned 化解，不需要 mismatch 审计兜底（风险优先案的 `generation_binding_mismatch` 降级为可选观测项）。
5. **双执行器同代**：三案一致（单发 + 会话三元组整体 stage）；首版 argv 形状不变、只固化 binary_path（grant 双墙 `is_canonical_adapter_argv` 无需代际化），形状要变时再按代分发（延期项）。
6. **版本探测下移** versions.py：两案一致，采纳（GET /keqing/status 保持零副作用与只读守卫）。
7. **release 独立成表**：代际状态机仍采用四张 release/generation/journal/pointer 表，而非把 `release_json` 嵌入 generation；再加独立 attempt marker 后，V32 合计五表。前者只多一次 join，却保持“不可变发布内容 / 可变运行实例”边界，支持跨重启精确物化与 P5 候选按 digest 复用。本表仅是宿主执行器 release，不冒充完整生态 `PluginRelease`。

---

### 阶段 P4（PR-4，拆 4a/4b 两个 PR）：按 subject 独立灰度 + EvolutionPolicy（V33/V34，≈1.5–2 周）

**目标**：拆掉「全局仅 1 个 canary」——不同 `(kind, subject_key)` 各自 canary、各自 sticky，**同 subject 冲突仍 fail-closed**；每插件（subject）一行 EvolutionPolicy：frozen / manual / canary（auto 不实现）。fresh root 的 0/1 canary 场景保留旧 `run_evolution_assignments` 投影，其中 singleton 的旧 governed assignment artifact 也逐字节不变；V34 影子行和 assignment API 的 `assignment_set` 字段属于有意新增，不能把整个 API/数据库状态称为逐字节一致。4a（policy 表 + 写侧执法，纯加法）与 4b（读路径多 subject，全方案唯一重写既有行为测试预期的一步）分两个 PR 隔离爆炸半径。

**阶段边界补充**：P4a 只建立 `(kind, subject_key)` partial unique index、policy 执法与未来 per-subject 写侧基础，不在旧单值读者仍生效时开放第二个 subject 同时进入 CANARY。`EvolutionRepository.get_routable_candidate()` 在 P4b 前仍是全局单值权威，因此 P4a 在 `save_candidate` 与 `start_canary` 保留临时全局单 canary 写侧 backstop；P4b 切换到 `get_routable_candidates()` 后才把该 backstop 收窄为同 `(kind, subject_key)` 排他。

**前置依赖**：P1（binding/overlay digest 语义）。与 P3 无硬依赖，可并行开发、先后合入均可。

#### P4a 改动清单（EvolutionPolicy，≈4–5 天，含 Codex A3）

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 修改 | src/tianshu/storage/migrations.py | V33 `0033_evolution_policies`（DDL 见下）；四件套照通用约定 |
| 新增 | src/tianshu/models/evolution_policy.py | `class EvolutionPolicyV1(_StrictModel)` 字段表：`subject_key: str`（1..512 trim）/ `kind: CandidateKind` / `mode: Literal["frozen","manual","canary"]` / `max_canary_basis_points: int`（ge=0, le=1000，不越过 EvolutionContractV1 的 10% 上限；model_validator：mode=='canary' 时必须 1..1000，与 V33 CHECK 同构——对照 evolution_candidate.py:172 契约层禁 0）/ `version: int`（ge=1）/ `updated_at: datetime` |
| 新增 | src/tianshu/storage/evolution_policy_repo.py | `class EvolutionPolicyRepository`（无状态）：`get_policy` 缺行返回 `None`，祖父化缺省只供执行路径计算，GET API 不合成虚拟行；`upsert_policy` 使用严格 missing-row CAS——仅无行且 expected 为 `None` 时首插 version 1，无行+非空 expected、已有行+空 expected、版本不精确匹配均冲突，相同内容 stale retry 也不例外；kind 是不可变身份，UPDATE SQL 不修改 kind；模块级 `default_mode_for` 保持 **SKILL 无行=canary，其余当前 kind 无行=manual** |
| 修改 | src/tianshu/evolution/candidate_service.py:178 | `propose` 开头：`mode == 'frozen'` → `CandidateServiceError("subject_frozen")`（在任何 artifact 物化之前，零副作用拒绝；用户可继续**使用**该插件——enabled 与进化授权正交） |
| 修改 | src/tianshu/evolution/promotion.py:784 | `start_canary` 前置追加：① 有效 mode ≠ `canary` → `policy_forbids_canary`；② allocation ≤ contract 与显式 policy 两层上限；③ 同 `(kind, subject_key)` 冲突 → `subject_canary_exists`；④ **P4a 临时全局 backstop**：任一其他 canary 存在即 `global_canary_exists`。两次检查和候选更新同在 `BEGIN IMMEDIATE` UoW；P4b 才删除全局检查并保留 subject SELECT 与 V33 index |
| 修改 | src/tianshu/evolution/promotion.py:935-943（promote durable 预检链同段） | **第三个执法点**：completed 命令重放优先；尚无 intended 时读取有效 mode，frozen → `subject_frozen`。policy upsert 同时拒绝同 subject 未被相同 command-key completed 收口的 promote intended/applied journal，封住外部 activate 窗口；rollback 不受 policy 限制，stage/evaluate 可收口但不得取得流量或晋升 |
| 新增 | src/tianshu/gateway/evolution_api.py 端点 | admin-only `GET/PUT /api/evolution/policies/{subject_key}`；PUT body 明确包含 `kind / mode / max_canary_basis_points / expected_version`，GET 缺行 404；成功响应固定 `{data, correlation_id}`，不增加 `success`。策略行、SystemAudit `evolution_policy_updated` 与同名 outbox event 在同一 UoW 提交，任一写失败整体回滚；**不复活** plugins install/activate 501 |
| 修改（Codex A3-a） | src/tianshu/storage/migrations.py V33 同块 | 在 `0033_evolution_policies` 迁移块追加 `CREATE UNIQUE INDEX idx_evolution_candidates_subject_canary ON evolution_candidates(kind, subject_key) WHERE lifecycle='canary'`（定位为 **P4b per-subject 灰度的第三道墙**，全局唯一仍由 evolution_repo.py:270-278 读侧 + 上表③写侧 SELECT 共守）。同 PR 必带：① tests/evolution/test_evolution_migration_schema.py 的 `_declared_v18_objects` / `_unique_column_sets` 登记新索引（否则锁定测试必红）；② `save_candidate` 的 IntegrityError 捕成 `EvolutionRepositoryConflict("subject_canary_exists")`，promotion 层映 409；③ 迁移前存量体检 `SELECT kind, subject_key, count(*) FROM evolution_candidates WHERE lifecycle='canary' GROUP BY 1,2 HAVING count(*)>1` |
| 修改（Codex A3-b） | src/tianshu/storage/evolution_repo.py:545 `save_candidate` | 策略执法**下沉到唯一 UPDATE 路径**：CANARY/PROMOTED 目标统一守 kind/mode/frozen；CANARY 额外守 contract cap、显式 policy cap、subject 唯一与 P4a 临时全局唯一。该写前防线覆盖绕过 PromotionService 的现有及未来写点，也不误伤 stage/evaluate/rollback；partial unique index 的 SQLite 冲突精确映为 `subject_canary_exists`。入口检查保留为早拒，不再是唯一防线 |

**V33 DDL**（V18 严格模板；可变表无触发器，仿 evolution_routing_allocations）：

```sql
CREATE TABLE evolution_policies (
  subject_key             TEXT PRIMARY KEY CHECK(length(trim(subject_key)) BETWEEN 1 AND 512),
  kind                    TEXT NOT NULL CHECK(kind IN ('memory','skill','policy','persona','code','executor')),
  mode                    TEXT NOT NULL CHECK(mode IN ('frozen','manual','canary')),   -- 'auto' 不可表达
  max_canary_basis_points INTEGER NOT NULL
      CHECK(max_canary_basis_points BETWEEN 0 AND 1000
            AND (mode <> 'canary' OR max_canary_basis_points BETWEEN 1 AND 1000)),  -- 无 DEFAULT 0：mode='canary' 时必须显式 1..1000，『0=禁止』不与『未配置』共用一个值
  version                 INTEGER NOT NULL CHECK(version > 0),
  updated_at              TEXT NOT NULL
);

-- Codex A3-a：P4b per-subject 排他的 DB 墙（同一迁移块）
CREATE UNIQUE INDEX idx_evolution_candidates_subject_canary
  ON evolution_candidates(kind, subject_key) WHERE lifecycle='canary';
```

> kind CHECK **从 V33 起即含 'executor'**：枚举 P5 才加值，DB 先行超集无害（无人写入该值），免二次重建——最少过渡债。

#### P4b 改动清单（按 subject 灰度，前置 conftest fixture 解耦先行小 PR ≈1 天 + 本体 ≈7–8 天）

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 修改 | src/tianshu/storage/migrations.py | V34 `0034_run_subject_assignments`（DDL 见下） |
| 修改 | src/tianshu/storage/evolution_repo.py:270 | 新增 `def get_routable_candidates(self, connection) -> tuple[EvolutionCandidateV1, ...]`：SELECT lifecycle='canary' 全表 → 按 (kind, subject_key) 分组，**组内 >1 抛 `EvolutionRepositoryConflict("multiple canary routing authorities for subject")`**（fail-closed 粒度收窄不取消）；每个候选保留与 evolution_routing_allocations 行的五路等值互检（:283-297 **逐候选复制，不能只改 WHERE**）。旧 `get_routable_candidate` 保留薄封装（内部调新函数，多组时抛原错误；唯一路由调用方迁走后仅供旧读者/测试）。新增 `def insert_subject_assignment(self, connection, assignment: SubjectRunAssignmentV1, overlay: EffectiveEvolutionOverlayV1) -> SubjectRunAssignmentV1`（IntegrityError→重读等值幂等）与 `def get_assignment_set(self, connection, memorial_id: str) -> RunAssignmentSetV1 | None`（逐行三重校验照 get_assignment :300-382 模式）；`get_assignment` 原样保留（存量行 + 回退路径） |
| 修改 | src/tianshu/models/run_assignment.py | 追加 `SubjectRunAssignmentV1` 与 `RunAssignmentSetV1`；set 由 canonical 排序后的 1..64 条 assignment 构成并带 `set_hash`。**RunAssignmentV1 / LegacyRunAssignmentV1 一字不改** |
| 修改 | src/tianshu/universe/router.py:109 assign_current | `get_routable_candidates()` 成为权威多值读者。对没有继承源的 fresh root：0 canary 只写旧表 legacy；1 canary 写旧表精确兼容投影并向 V34 写 singleton set；N>1 写旧表 legacy + V34 完整 set。follow-up 可在当前 0 canary 时继续继承父 V34 set，不能按当前 canary 数推断其 shape。每 subject 独立分桶，所有 payload 与 provenance 先验后在调用方事务内以 batch + SAVEPOINT 原子落库；64 条可写，65 条在任何 assignment 写入前拒绝。`_subject_assignment_id` 固定为 `'assignment:' + sha256(f"{memorial_id}\0{kind.value}\0{subject_key}".encode()).hexdigest()` |
| 修改 | src/tianshu/application/managed_run_ingress.py（followup 分支）+ src/tianshu/universe/router.py assign_current | **continuity 固定语义**：CANARY 父 assignment 复制原选择；PROMOTED follow-up 选择 `candidate.candidate`；ROLLBACK_PENDING、ROLLED_BACK 等回滚态选择 base；ARCHIVED 复核当前 version 的 V18 lifecycle journal，from PROMOTED 时选 candidate，否则选 base，journal 缺失 fail closed。选择完成后仍按新 Memorial 写独立、封存的 assignment set；不得因新 memorial_id 重新抽桶 |
| 修改 | src/tianshu/universe/router.py:188 bind_runtime | 读侧：新表有 governed 行 → 组装 overlays/payloads dict；否则回退旧表单行（存量 run 与回退路径）。每 subject 独立 resolve payload，任一失败仍整体 `EvolutionRuntimeUnavailable`（fail-closed 不弱化，run_dispatcher.py:253-271 语义） |
| 修改 | src/tianshu/evolution/runtime_context.py:15 | 加 `overlays` / `payloads` 多值视图，key 固定为 `f"{kind.value}:{subject_key}"`，避免不同 kind 同名碰撞。每条 assignment 在 bind 时重验 candidate provenance 与 overlay/payload digest，随后对 map 及嵌套值深冻结；`always` 注入按确定顺序执行。单数访问器仅在 set size=1 时返回值，N>1 返回 `None`；singleton 兼容路径复用同一次 payload resolution，不重复调用 resolver |
| 修改 | src/tianshu/skills/loader.py:32 | `_runtime_skill_overlay` 改为遍历 overlays 中 kind=='skill' 条目（按 `skill:{name}` 定向；absent 隐藏语义 :45-46 保留）；单 overlay 时行为等价；多 skill overlay 并行生效 |
| 修改 | src/tianshu/evolution/promotion.py rollback | rollback 断流（置零 allocation + ROLLBACK_PENDING，:1136-1214）只影响本 subject 的 routing 行——多 subject 下逐 subject 独立 |
| 修改 | Evidence（evidence/service.py:911-928 块内） | 无 V34 set 的历史 legacy 行 → 不挂 governed assignment artifact；singleton set → 旧 assignment artifact 照旧；multi set → 只挂 `application/vnd.tianshu.evolution.assignment-set.v1+json`（payload=RunAssignmentSetV1 canonical dump）进 required，不同时挂旧 assignment artifact |
| 修改 | src/tianshu/application/evolution_view.py:174 + models/evolution_view.py + web | `_routing_summary` 按 subject 聚合；Evolution Center 展示 routing subject 与只读 Skill availability/source/curator protection，并允许编辑 evolution mode 与 max canary basis points（严格 CAS）。`SkillInfo.pinned` 表示 curator protection，**不是版本 pin**；P4b 不提供 enabled 或版本锁定开关 |
| 修改 | src/tianshu/config.py | 加 `evolution_routing_enabled: bool = True`（env `TIANSHU_EVOLUTION_ROUTING_ENABLED`）作为全局 kill switch。路由顺序是 existing replay → continuity inheritance → fresh-root kill switch：False 阻止 fresh root 新选 challenger，已持久化 assignment 及由其派生的 follow-up continuity 仍按原选择 sticky，不会被重分桶。API routing 投影标记 disabled，`EvolutionRollbackReconciler.readiness_probe()` 返回 false；整体 `/health/ready` 将可选的 `evolution.rollback` 检查显示为 degraded，若无其他 required failure 则 HTTP 200，这是为了避免关闭可选自进化时摘除仍可服务业务的实例。启动时尽力写 `evolution_routing_disabled` audit/outbox，审计失败不阻止关闸 |

**V34 DDL**（V18 严格模板 + V22 触发器语义）：

```sql
CREATE TABLE run_subject_assignments (
  assignment_id     TEXT PRIMARY KEY,          -- sha256 identity includes memorial_id, kind and subject_key
  memorial_id       TEXT NOT NULL REFERENCES memorials(id) ON DELETE RESTRICT,
  kind              TEXT NOT NULL CHECK(kind IN ('memory','skill','policy','persona','code','executor')),
  subject_key       TEXT NOT NULL CHECK(length(trim(subject_key)) BETWEEN 1 AND 512),
  candidate_id      TEXT REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
  routing_version   INTEGER NOT NULL CHECK(routing_version > 0),
  bucket            INTEGER NOT NULL CHECK(bucket BETWEEN 0 AND 9999),
  champion_ref_json TEXT NOT NULL,
  selected_ref_json TEXT NOT NULL,
  overlay_digest    TEXT NOT NULL CHECK(length(overlay_digest) = 64),
  assignment_json   TEXT NOT NULL,
  assignment_hash   TEXT NOT NULL CHECK(length(assignment_hash) = 64),
  assignment_set_hash TEXT NOT NULL CHECK(length(assignment_set_hash) = 64 AND assignment_set_hash NOT GLOB '*[^0-9a-f]*'),
  assignment_set_size INTEGER NOT NULL CHECK(assignment_set_size BETWEEN 1 AND 64),
  created_at        TEXT NOT NULL,
  UNIQUE (memorial_id, kind, subject_key)
);
-- sealed_insert：同一 memorial 的所有行必须声明同一 set hash/size，且行数不得超过 size。
-- no_update 无条件；no_delete 仅 WHEN OLD.candidate_id IS NOT NULL（V22 语义，legacy 占位可清理）。
```

V34 owned objects 冻结为 table + `run_subject_assignments_sealed_insert` +
`run_subject_assignments_no_update` + `run_subject_assignments_no_delete`。迁移 checksum 为
`2ef0237b22f47310bf1f5d48d20c0262998bba960f1c9418687e54860dd2172f`，upgrade callback
fingerprint 为 `121909d74e49a0263e893327f0caf38f2915e322bd2028a099d4c5b8bde6f180`。

不回填策略行（无行走 default_mode_for 祖父化）；不迁移旧 assignment 行（旧表照读、逐字节保留）。

#### 兼容策略与开关

- **P4a**：无策略行仍按 skill=canary、其余当前 kind=manual 计算有效 mode，保证存量 skill 流程不因迁移自动改变；GET API 对缺行仍返回 404。V33 提前建立 per-subject partial unique index，但旧读侧仍是全局单值，因此 `start_canary` 与 `save_candidate` 暂时保留全局单 canary backstop。policy PUT 使用严格 CAS，kind 不可变；同 subject 有 unresolved promote intended/applied 时拒绝更新。
- **P4b**：切换多值读路径后才移除 P4a 的全局 backstop，保留同 subject SELECT、partial unique index 与 repo-level policy 执法三道墙。fresh root 的 0/1 canary 保留旧表投影；存量行照读，legacy 占位行继续写。assignment API 新增 nullable `assignment_set`，不属于逐字节兼容面。
- **禁止不安全代码回退**：V34 一旦应用，不得部署不认识 V34 的纯 V33/P4a 二进制。先关闭
  `evolution_routing_enabled` 并重启，停止新分配；排空或完成 active attempts 与 OPEN
  continuities；仅针对 active CANARY authorities 走正常 promote/rollback 命令，把全局 active
  canary 数降到旧读者至多可解释的 1 个、最好为 0，并完成 pending rollback；不得为了代码
  回退把已经 PROMOTED 的合法 subject 强退到 base。保留 V34 migration declaration、checksum、
  callback、表和 ledger，仅部署保留 V34 schema/reader 的行为兼容构建。assignment 行留作审计，
  恢复开放必须使用 P4b-compatible reader。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/storage/test_durable_schema_v33.py / v34.py（新）+ freeze 登记 ×2 | 形状；mode CHECK 拒 'auto'；kind CHECK 收 'executor' 拒未知值；UNIQUE(memorial_id,kind,subject_key)；条件 no_delete 语义；前序冻结 |
| tests/evolution/test_evolution_policy_repository.py + tests/gateway/test_evolution_policy_api.py（新） | 缺省 skill=canary/其余=manual；strict CAS 五格矩阵、kind immutable、promote journal guard；admin-only GET/PUT、缺行 404、精确信封，以及 policy/audit/outbox 同 UoW 原子提交 |
| tests/evolution/test_evolution_policy_enforcement.py（新，含 Codex A3） | frozen 在零 artifact 副作用处拒 propose；manual/frozen 拒 start_canary；allocation 取 contract/policy 双上限；CANARY 后 frozen 拒 promote、rollback 放行；绕过 PromotionService 直调 `save_candidate` 仍受 mode/allocation/subject 与 P4a 全局唯一约束；迁移形状测试锁定 partial index 与 409 映射 |
| tests/universe/test_multi_subject_routing.py（新） | 两 subject（skill:foo + skill:bar 或 skill+persona）并行 canary：各自分桶独立命中（统计断言仿 tests/evolution/test_routing_distribution.py）、各自 sticky、重启/重试不重分桶；同 subject 双 canary → conflict；**影子等值**：单 canary 时旧表行与主干产物逐字节相同、新表行与旧行内容等值；新表行随事务原子回滚（仿 test_challenger_routing.py:754）；rollback 单 subject 置零不动他 subject |
| tests/application/test_generation_continuity.py（扩，P3 建） | 同一 conversation 连续 N 次 follow-up 的 selected_ref 恒定（canary 选择随 continuity sticky）；canary 结束（promote/rollback）后 follow-up 收敛 champion |
| 改写存量（4b） | test_promotion_fail_closed.py：排他用例改 per-subject + 新增"不同 subject 可并存"正例（**test_rollback_fault_matrix.py:13-20 import 其私有 fixture，两文件连坐同步**——建议先行小 PR 把 `_contract/_candidate/_service` 提为共享 conftest helper）；tests/evolution/test_routing_distribution.py 扩多 subject 分布；test_s4_s5_handoff.py 布局断言更新 |
| 回归 | tests/universe/test_challenger_routing*.py 单 canary 用例全绿（旧路径未动）；test_gate_evaluator / test_candidate_adapters / test_candidate_schema 不变；demo profile bucket=0 特判保留（wiring_skills.py:57-63）；`evolution_routing_secret` 空 + 有 canary 仍 fail-closed（router.py:58-59） |
| web | evolution.test.ts subject_key 解析；新 e2e（仿 evolution-gate.spec.ts 具名读契约注入）多 subject 快照渲染；`npm run typecheck` |

#### 验收 checklist

- [ ] 单 canary 影子等值断言通过（"可独立回退"的证明）
- [ ] `skill:foo` 与 `skill:bar`（P5 后加 `executor:keqing:pi`）并行 canary，各自 sticky、各自 rollback、互不干扰
- [ ] 用户可保持某插件 enabled 同时 frozen（frozen 后 propose 409、现役运行不受影响）——验收标准"单个插件可保持 enabled 同时 frozen"达成
- [ ] 同 subject 冲突 fail-closed 未弱化；`Literal[False]` 未动；auto 双重不可表达（类型 + CHECK）
- [x] （Codex A3）partial unique index 已入 V33 且锁定测试已登记；直调 `save_candidate` 绕过服务层的回归测试证明 repo-level policy/mode/allocation/唯一性防线生效
- [ ] bind 热路径成本测量：N subject 时候选读次数线性有界（N≤snapshot 组件上界 64）
- [ ] 关闸（`evolution_routing_enabled=0`）后 fresh root 不新选 challenger，follow-up 仍继承已持久化 continuity；开闸后恢复且存量 assignment 不重分桶。内部 Evolution probe=false，整体 health 在无其他 required failure 时为 degraded/HTTP 200（全局 kill switch 验收）

**回退方式**：4a 可删除显式 policy 行回到祖父化缺省。4b 必须按上节运行手册先关路由、
排空 active attempts/OPEN continuities，仅将 active CANARY authorities 经正常 promote/rollback
收敛到全局至多 1 个、最好为 0，并完成 pending rollback；不强退已 PROMOTED subject。随后
切到仍识别 V34 的行为兼容构建；不得把已迁移数据库交给纯 V33/P4a 二进制，也不得删除 V34
表、触发器或 migration ledger。

**工作量**：4a ≈4–5 天（含 Codex A3）+ 4b 前置「conftest fixture 解耦」先行小 PR ≈1 天 + 4b 本体 ≈7–8 天。

**决策与取舍**：
1. **P4 提前到 P5（EXECUTOR）之前**：目标纯度案的顺序（理由见 §0.3）；风险优先/最小改动案沿评审顺序。采纳提前——行为测试只重写一次、EXECUTOR 生在 per-subject 世界，是"最少过渡债"最大的一笔。
2. **4a/4b 拆分**：最小改动案首创，风险优先案单 PR。采纳拆分——4a 纯加法独立可合，4b 是唯一重写行为测试的阶段，隔离爆炸半径。
3. **缺省祖父化 vs 全量 manual（deny-by-default）**：风险优先案祖父化（skill=canary）；最小改动案文字自相矛盾（"无行=manual=现状"与"mode≠canary 拒"冲突，弃）；目标纯度案全量 manual + 一次性摩擦（存量 skill 需先建策略行）。**已拍板采纳祖父化**——优先级②（存量行为逐字节不变、test_promotion_fail_closed 全家不红）压过③；deny-by-default 的收紧永远可由治理面显式写行完成，方向单调（§7 决策 2）。
4. **manual 语义**：风险优先案"manual=要求已决议 Decision 放行"；目标纯度案"manual=一律拒 canary，Decision 放行延期"。**采纳后者（manual=『已决议 Decision 放行』延期）**——StartCanaryCommand 虽已有 decision_request_id 字段（promotion.py:165-168，幂等指纹经 _request_hash 已覆盖该字段），但 start_canary 路径目前不校验该 Decision 与候选的绑定（require 逻辑仅在 CODE promote 路径），补上等值于把高危 Decision 四元组绑定校验泛化到 canary 命令，需单独设计与测试（+2 天），且祖父化下存量流程不受影响；高危地板由 P5 的 `HIGH_RISK_PROMOTION_KINDS` 常量承担。§6 对应行同步修订。
5. **多 subject 存储策略**：三案三样——风险优先案"新表全量双写、旧表 0/1 canary 逐字节保留、>1 canary 旧表 legacy 占位、读侧新表优先回退旧表"；最小改动案"主行（字典序最小 subject）留旧表 + extras 新表（旧读路径零改动）"；目标纯度案"governed 全入新表、旧表恒 legacy marker"。**采纳风险优先案**——最小改动案的"主 subject"是人为的不对称语义（哪个 subject 是主完全任意，正是要还的过渡债）；目标纯度案改变单 canary 的旧表产物（evidence/view 读路径全要动，丢掉逐字节回退证明）。双写按 §7 决策 3 保留到多 subject 稳定运行一个 minor 版本后再评估退役。
6. **`get_routable_candidate` 旧函数去留**：目标纯度案删除；风险优先案保留薄封装。采纳保留薄封装（多组时抛原错误）——存量测试/读者兼容，成本一个函数；合一延期。
7. **policy 变更的 Decision 绑定降级为有意取舍**：评审 §6 落点原表述为『PR-4 policy 表只允许 Decision 改』；本方案降级为 admin PUT + CAS + 同 UoW SystemAudit `evolution_policy_updated`（可追溯性守住），Decision 绑定延期（与 manual 放行同属一个命令面设计，见 §6 延期表）。

---

### 阶段 P5（PR-3b，后移）：CandidateKind.EXECUTOR 全链路（V35，≈1.5–2 周）

**目标**：Pi 版本漂移不再只显示"待兼容验证"，而是产生 `kind=EXECUTOR`、`subject_key=executor:keqing:pi` 的治理候选，走现有八门 Gate → per-subject canary → **高危 Decision** → `ExecutorPromotionAdapter`（canary 前 stage+warm 并建立 durable authority，promote 只 activate 已授权 READY 代）换代 / 回滚 last-good 的完整闭环。落地即为 per-subject 世界（P4 已就位）。

**前置依赖**：P3（代际机械）、P4（per-subject 路由 + policy；EXECUTOR 缺省 manual 生而严格）。

#### 改动清单

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 修改 | src/tianshu/models/evolution_candidate.py:16-21 | `CandidateKind` 加 `EXECUTOR = "executor"`；同文件新增 `HIGH_RISK_PROMOTION_KINDS: frozenset[CandidateKind] = frozenset({CandidateKind.CODE, CandidateKind.EXECUTOR})`——**代码级地板**：这两类晋升永远要已决议高危 Decision；policy 只能在此之外加严其他 kind（权限只能收窄公理） |
| 修改 | src/tianshu/storage/evolution_repo.py:48、183-211、531-598 | `_require_high_risk_code_promotion_decision` 泛化：判据 `candidate.kind is CandidateKind.CODE` → `candidate.kind in HIGH_RISK_PROMOTION_KINDS`（函数与 Decision 四元组绑定校验逻辑不动）；`_CodePromotionDecisionBindingV1` 改名 `PromotionDecisionBindingV1`（保留旧名 alias）；save_candidate 内判据同步；**公开方法 `require_code_promotion_decision`（evolution_repo.py:245-256）的 kind 守卫同步改为 `candidate.kind not in HIGH_RISK_PROMOTION_KINDS`**（现为 `if candidate.kind is not CandidateKind.CODE: raise ValueError`，promotion.py:936 经此公开方法调用——只改私有函数与 save_candidate 判据的话，EXECUTOR 晋升会在 :255 被 ValueError 拒掉；方法可顺势更名 `require_high_risk_promotion_decision`，保留旧名别名），promotion.py:936 调用点判据同改 |
| 修改 | src/tianshu/evolution/promotion.py:935-943、1054-1062 | 两处调用侧判据同步泛化 |
| 修改 | src/tianshu/storage/migrations.py | V35 `0035_executor_candidate_kind`：kind CHECK 冻结在 V18 DDL（migrations.py:3434），SQLite 无 ALTER CHECK——V20 临时表模式重建 `evolution_candidates`：`CREATE TABLE _evolution_candidates_v35`（新 DDL 仅 kind CHECK 扩为 `IN ('memory','skill','policy','persona','code','executor')`，lifecycle CHECK/UNIQUE(kind,subject_key,candidate_id)/索引逐字节保持 V18 原文）→ INSERT SELECT 逐列拷贝（迁移内断言行数一致）→ DROP → RENAME → 重建索引；temp 名登记 `_RESERVED_TEMP_TABLES`（migrations.py:728）；结尾靠引擎 `foreign_key_check` 兜底。**FK 前提（已核实，非 spike 待定项）**：本仓迁移连接 FK 恒 ON——storage/_base.py:87 在 init_db 对迁移所用连接执行 `PRAGMA foreign_keys=ON`，随后 :103 在同一连接上 `run_migrations(conn)`（PRAGMA 在 BEGIN IMMEDIATE 之前生效）；adopt 基线重放（migrations.py:4334）、per-version 测试模板（tests/storage/test_durable_schema_v24.py:16）、tests/evolution/test_s4_s5_handoff.py:49 全部显式 ON。migrations.py:3478/3496/3524/3540/3557 共五处子表 FK `REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT`；SQLite 语义：FK ON 时 DROP TABLE 先做隐式 DELETE，被 RESTRICT 子行引用即失败——**V20 式裸 DROP+RENAME 在有真实候选数据的库上必然失败，分支 A（连带重建子表）是既定事实而非预案**。V35 按 §7 决策 4 采用分支 A：按依赖序连带重建六张子表，全部临时名登记 `_RESERVED_TEMP_TABLES`，结尾 `foreign_key_check` 兜底；工作量 +2–3 天计入 P5 基线（P5 合计 ≈1.5–2 周） |
| 新增 | src/tianshu/storage/migrations.py、storage/executor_generation_authority_repo.py | V35 同批增加 candidate-generation authority 当前表与不可变 journal：每次 canary epoch 精确绑定 `candidate_id + candidate_version + release_digest + generation_id + scope + promotion_journal_id`，同一 candidate 同时至多一个有效授权、同一 generation 只属一个授权，授权/撤销用 CAS 并留 journal。有效 authority（CANARY/PROMOTED/ROLLBACK_PENDING，或可盲重放的 pending start_canary 效果）是 READY generation 的 retention root；候选回 READY、REJECTED、ROLLED_BACK、ARCHIVED 或 command 终止即撤销，允许下一 canary epoch 建新代。无映射、摘要不符、歧义或已撤销的 READY 重启一律 FAILED，不放宽 P3 的默认恢复规则 |
| 新增 | src/tianshu/evolution/adapters/executor.py | `class ExecutorCandidateAdapter(BaseCandidateAdapter)`（staging 侧）：`kind = CandidateKind.EXECUTOR`；`def _normalize_domain(self, payload) -> dict[str, JsonValue]` 校验发行包内包 schema `{adapter_id: Literal 白名单('keqing:pi' 等), release_version: str, binary_path: str, package_digest: 64hex, manifest: {...}}`（仿 `_SkillPackageV1` adapters/skill.py:32）；`def require_subject_binding(...)` 强制 `subject_key == f"executor:{adapter_id}"`（仿 skill.py:96-104） |
| 新增 | src/tianshu/evolution/adapters/executor_promotion.py | `class ExecutorPromotionAdapter`（晋升侧，实现 promotion.py:234 `_Adapter` 协议）只调用 `GenerationController` 与 authority repository，Registry 仍只保管 bundle/lease：start-canary 效果以 promotion pending journal 为命令权威，`controller.stage(release) → await controller.warm(generation_id) → CAS authorize(candidate,generation)`，不改 active pointer；重复命令必须返回同一授权，warm 失败候选仍 READY、指针/last-good 不动且无有效授权。`activate(candidate)` 读取并复核精确授权与 READY material 后只执行 `controller.activate(mapped_generation_id)`，禁止二次 stage/warm；收据与 journal 同时带 generation_id/release_digest。`rollback` 走 `controller.rollback(scope)` 后撤销 challenger authority；`verify_rollback` 校验 pointer 已回 last-good、candidate 代 failed/disposed 且 authority 已撤销。`rollback_is_idempotent = True`，不复制 SkillPromotionAdapter 直调 staging 私有方法的耦合 |
| 修改 | src/tianshu/evolution/candidate_service.py:59-84 | `CandidateLiveAuthorities` 加 `executor_root: Path` 并入 `for_kind`；`_ADAPTER_TYPES` 注册 ExecutorCandidateAdapter；media_type `application/vnd.tianshu.evolution.executor+json` 随 `{kind.value}` f-string（:129）自动成立 |
| 修改 | src/tianshu/bootstrap/wiring_executor.py、src/tianshu/config.py | 始终为 `CandidateKind.EXECUTOR` 装配 `ExecutorPromotionAdapter(..., evolution_enabled=settings.executor_generation_enabled)`；开关关时新的 start-canary/promote 在新 journal/effect 前返回 409，但 existing generation recovery、已发生效果收口、rollback 与 pending rollback reconcile 保留。配置模型拒绝 `executor_generation_enabled=true && system_snapshot_enabled=false`，因此 P1 零写入兼容态不存在可达的生产 stage/activate 路径 |
| 新增 | src/tianshu/evolution/executor_drift.py | `class ExecutorDriftScanner:` `def __init__(self, *, candidate_service, versions: Callable[[str], str | None], pinned: Mapping[str, str], interval_seconds: int = 3600)`；`def scan_once(self) -> int`：`detect_installed_version`（P3 versions.py）vs `PINNED_PI_VERSION`（pi_wire.py:18）；漂移 → `candidate_service.propose(kind=EXECUTOR, subject_key="executor:keqing:pi", source_channel=SYSTEM, ...)`，**幂等键含 installed_version**（candidate_id 命令确定性哈希天然去重，candidate_service.py:155-176、236-241）；内置限流（每小时至多一次实际检查）。挂 `reconcile_control_planes`（wiring_scheduler.py:220）低频驱动——**不放 GET /keqing/status**（进程启动守卫 + GET 零副作用）；frozen policy 下 propose 被 P4a 执法点拒绝——用户冻结插件即冻结漂移提案，语义自洽 |
| 修改 | src/tianshu/config.py | 加 `executor_drift_scan_enabled: bool = False`（env，deny-by-default） |
| 修改 | 投影四处 | src/tianshu/models/evolution_view.py:42 Literal 加 "executor"；web/src/api/evolution.ts:15 union 加 "executor"（**最易漏的前端手写白名单**）；gateway/keqing_api.py drift 行加 candidate 链接字段 + KeqingManagementPage.tsx:129 drift Tag 链到候选详情；i18n 三份 locale（zh-classic「彩蛋」不动） |
| 新增（可选） | src/tianshu/cli/commands/keqing.py | `@app.command("status")`：走 HTTP client（对齐 plugin.py:22 惯例，不直连 Storage），表列 backend/installed/pinned/generation/last_good |

#### canary 消费链（EXECUTOR canary 的运行期效果——不能只在 promote 才换代）

CANARY 生命周期内被分流到 challenger 的 run 必须真的跑到新版本 Pi，否则 executor canary 空转：challenger 与 champion 同跑旧代，灰度期收集不到新版本任何证据（P3 的 bind_runtime generation_ids 三分来源——继承/同 memorial 上一 binding/active 指针——没有 canary 分支，不会指向候选对应的新代；migration-roadmap Phase 3 明确『Pi 新旧版本 side-by-side；只有新的 root assignment 或命中 Canary 的新 continuity scope 使用新版本，已有 continuity 不换代』）：

1. `start_canary`（EXECUTOR kind）效果段先 `GenerationController.stage + warm`，再以 pending promotion journal 建立精确 durable authority（**不 activate，指针不动**）；warm/authorize 失败 → start_canary 失败，候选留 READY且无半授权；
2. `bind_runtime` 对 kind='executor' 且 selected_ref=candidate 的 subject assignment，只能按 exact authority 找到 READY generation 并写入 `binding.generation_ids`；映射缺失/摘要不符/歧义全部 fail closed，champion run 仍取 active pointer；
3. authority 是 restart 与 retention 的必要条件：P5 recovery 只重建仍获授权的 READY，P3 的 abandoned READY 仍失败化；rollback/退回 READY/拒绝/归档先断流并撤销 authority，新 run 全部回 active，旧 exact-attempt 排空后候选代再 failed/disposed；
4. promote 只复核并 activate 映射的 READY generation（免二次 stage/warm），因此收据、SystemSnapshot、Candidate 与实际二进制共享同一个 generation_id/release_digest 真相。

测试补：tests/evolution/test_executor_promotion.py 增『executor canary 期间 challenger run 用新代、champion run 用旧代（binding generation_ids 断言）；rollback 后新 run 零新代』。

**数据迁移**：V35（上表）；存量候选行全量保留（逐列拷贝 + 行数断言）。形状测试同步：test_evolution_migration_schema.py `_declared_v18_objects`（:292-305）按 V22 先例注入重建后实况 SQL；`_V18_TABLE_COLUMNS`（:97-230）kind CHECK 文本更新。

**兼容策略与开关**：V35 重建后旧代码读它完全兼容（旧枚举值集合是子集，零数据变换）；开关关时 EXECUTOR 候选仍可 propose/stage/gate，但新的 start-canary/promote 在新 journal/effect 前返回 409 `executor_generation_unavailable`。适配器继续装配，既有代 recovery、已发生效果的幂等收口、rollback 与 pending rollback reconcile 不被 kill switch 关闭——**有意的 fail-closed 与可恢复并存**。EXECUTOR 晋升永远需要已决议高危 Decision（常量地板，先于任何 policy 配置生效）；漂移巡检独立开关默认关。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/storage/test_durable_schema_v35.py（新）+ freeze 登记 | V34 库插候选哨兵 + 六张子表哨兵行 → apply V35 → 数据逐列保留、行数一致、FK 图不变（对照 test_evolution_migration_schema.py:358 FK 锁）、`PRAGMA foreign_key_check` 空、kind CHECK 收 'executor' 拒未知值、`_RESERVED_TEMP_TABLES` 无残留（adopt 拒绝残留语义）、前序冻结；**连接保持 `PRAGMA foreign_keys=ON` 并带六张子表哨兵行复现生产条件**（这正是暴露裸 DROP 必炸的条件） |
| tests/evolution/test_evolution_migration_schema.py（大改） | 实况注入 + 锁表更新 + partial/drift 拒绝语义保持 |
| tests/evolution/test_candidate_adapters.py（改） | `_sources()`/`ADAPTERS` 加 EXECUTOR 条目 → 六处 parametrize 自动展开（propose/stage 幂等、错 adapter fail-closed、artifact 零孤儿全矩阵免费覆盖） |
| tests/evolution/test_candidate_schema.py（改） | lifecycle 参数化自动覆盖新 kind；CODE Decision 绑定矩阵（:319-464）经 PromotionDecisionBindingV1 改名路径 + EXECUTOR 参数化复制 |
| tests/evolution/test_executor_promotion.py（新，仿 test_promotion_fail_closed 的 fixture :69-120） | 无 Decision 永不晋升；start-canary 建唯一 exact authority，重复/崩溃重放不产第二代；promote 只 activate 已映射 READY 且收据/journal 带同一 generation_id；warm/authorize 失败零半状态且指针/last-good 不动；rollback 回 last-good并撤权，verify/reconciler 从 pending 盲重放安全；无/错/歧义映射与 `system_snapshot_enabled=false` 全部 fail closed |
| tests/evolution/test_executor_drift_scanner.py（新） | 漂移 → 恰一 PROPOSED（幂等键）；同 installed_version 重扫零新候选；开关关零副作用；GET /keqing/status 前后无副作用；frozen policy 拒绝且零 artifact 副作用 |
| 修改 | tests/gateway/test_keqing_status.py（加字段不动既有锁）；web evolution.test.ts kind union / KeqingManagementPage.test.tsx；`npm run test && npm run typecheck` |

#### 验收 checklist

- [ ] demo 栈端到端：人为改 installed 版本 → 巡检产候选 → Gate → canary（与 skill canary 并行互不影响，P4 成果直接消费）→ Decision → promote 换代 → rollback 回 last-good
- [ ] EXECUTOR 无已决议高危 Decision 永不晋升；`Literal[False]` 未动
- [ ] rollback 在 `contract.rollback_slo_seconds` 内恢复指针（故障注入计时断言）；warm 失败换代不发生且 run 零感知
- [ ] V35 三路（apply/adopt/形状回放）绿；test_promotion_authority AST 绿（ExecutorPromotionAdapter 不直写 lifecycle）
- [ ] 前端三处 kind 白名单同步 + typecheck 绿

**回退方式**：关 `executor_generation_enabled` → 新的 EXECUTOR start-canary/promote 在新 journal/effect 前返回 409 `executor_generation_unavailable`，但 `ExecutorPromotionAdapter`、existing generation recovery、效果收口、canary/promoted rollback 与 pending rollback reconcile 保留；关 drift 开关 → 零新候选；V35 已合入则枚举、authority journal 与审计数据保留（超集无行为影响）——正是“停止继续进化”不能同时拆掉安全回退路径。

**工作量**：≈1.5–2 周（含分支 A 子表连带重建 +2–3 天）。

**决策与取舍**：
1. **后移到 P4 之后**（目标纯度案）：见 §0.3；连带 V35 是最后一条迁移，且 P4 两表 CHECK 已预含 'executor' 免二次重建。
2. **高危 Decision 门**：三案一致用 `HIGH_RISK_PROMOTION_KINDS` 常量泛化既有 CODE 门（Decision 门是 fail-closed 底线，不等 policy 表）；"由 policy.require_decision 驱动"列延期（目标纯度案的"frozenset=地板、policy=天花板"合流表述写进 ADR-0013）。
3. **漂移→Candidate 不挂 GET /keqing/status**：三案一致（页面刷新不应 propose；GET 幂等 + 进程启动守卫）；挂 reconcile_control_planes（风险优先/最小改动案）而非独立 scheduled job——后者列为可选演进（若用户希望巡检在调度面可见可控）。
4. **FK 重建**：三案原以 spike 先行 + 两分支预案；本轮校验已核实迁移连接 FK 恒 ON（storage/_base.py:87）——按 §7 决策 4 使用分支 A，依赖序连带重建六张子表并计入工期，不采用 `writable_schema`。
5. **ExecutorPromotionAdapter 文件**：独立 `executor_promotion.py`（与 staging 侧 executor.py 分文件，最小改动/目标纯度案）——避免复刻 SkillPromotionAdapter 与 staging 同文件的既有耦合反例。

---

### 阶段 P6（PR-5）：进程级 snapshot 重启与 last-good（≈3–4 天）

**目标**：进程本身进入 snapshot 治理：`tianshu serve --system-snapshot <digest>` 启动校验；漂移默认记审计继续、strict 模式退出并提示 last-good；`scope='process'` 指针由 GenerationReconciler 维护；**binding 写入从影子豁免翻转为 fail-closed（strict 下）**。

**前置依赖**：P1（Resolver + strict 开关占位）、P3（generation_pointers 表与 reconciler）。

#### 改动清单

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 修改 | src/tianshu/cli/commands/serve.py:23 | `def serve(host, port, reload)` 加 `system_snapshot: str | None = typer.Option(None, "--system-snapshot")`；沿既有"回灌 env 再重建 Settings"模式（serve.py:38-41，CLI 不成为绕过校验的第二路径）→ `TIANSHU_SYSTEM_SNAPSHOT_TARGET` |
| 修改 | src/tianshu/config.py | 加 `system_snapshot_target: str | None = None`；`system_snapshot_strict`（P1 已占位）在此获得完整语义；启动校验拒绝 strict=1 且 system_snapshot_enabled=0 的矛盾组合 |
| 修改 | src/tianshu/bootstrap/wiring_snapshot.py（app.py lifespan 装配完成处） | 启动序列：`resolver.resolve()`；目标 digest = CLI 参数 > generation_pointers(scope='process').active 对应 release_digest > 无（首启，记录当前为 active）；不等且 strict → 退出非零，stderr 含 last_good digest 与**差异组件清单**（脱敏，仿 serve.py:43-50 惯例）；不等且非 strict → SystemAudit 记 `system_snapshot_drift` 继续；健康启动后经 GenerationRepository CAS：新建 process 代（release_digest=resolved digest，state=active）、上一 process 代经 recover_on_startup 走两步 CAS：active→draining→disposed（同一 UoW 内两次 save_generation，落两条 journal，from_state 链完整——§1.3 转移图无 active→disposed 直达边，不加边）、pointer.last_good → 上一次干净退出的 digest |
| 修改 | src/tianshu/universe/router.py bind_runtime | **翻转**：`system_snapshot_strict=True` 时 binding resolve/写入失败抛 `SystemSnapshotUnavailable(EvolutionRuntimeUnavailable)`（**必须继承该基类**——run_dispatcher.py:253 按基类归类，裸异常会被误标 run_assignment_unavailable）→ run FAILED `code="system_snapshot_unavailable"`；影子豁免路径仅在非 strict 保留 |
| 修改 | src/tianshu/application/run_dispatcher.py:253-258 | 错误码分类从二元 isinstance 改为三分（现状是二元硬编码 `failure_code = "run_assignment_unavailable" if isinstance(exc, (RunAssignmentUnavailable, LookupError)) else "candidate_overlay_unavailable"`——任何继承 EvolutionRuntimeUnavailable 的新异常都会被标成 candidate_overlay_unavailable，永远不会出现 system_snapshot_unavailable）：RunAssignmentUnavailable/LookupError → `run_assignment_unavailable`；新增 SystemSnapshotUnavailable → `system_snapshot_unavailable`；其余 EvolutionRuntimeUnavailable → `candidate_overlay_unavailable`。SystemSnapshotUnavailable 须直接继承 EvolutionRuntimeUnavailable、**不得继承 RunAssignmentUnavailable**（否则被旧分支吃掉）；tests/application/test_run_dispatcher_lifecycle.py 扩三分类矩阵用例 |
| 修改 | src/tianshu/evolution/reconciler.py | GenerationReconciler 增 `scope='process'` 分支：指针收敛、孤儿 process 代清理、last_good 缺失 readiness 降级告警 |
| 修改 | src/tianshu/application/evolution_view.py | EvolutionCenterSnapshotV1 加 `active_generation: str | None`、`last_good_generation: str | None` 只读字段（不开新端点） |

**数据迁移**：无（复用 V32 表；scope 列无 CHECK 枚举，值域扩 'process' 零迁移）。

**兼容策略与开关**：strict 默认 False——合入后先跑影子观测窗口（`system_snapshot_drift` 与 `system_snapshot_binding_failed` 审计为零），再由用户显式开 strict（不设自动转严）；不传 `--system-snapshot` 且非 strict = 现状行为 + 多一行 process 代记账。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/cli/test_serve_snapshot.py（新，`env -u FORCE_COLOR`） | `--system-snapshot` 回灌 env 生效；strict + 漂移 → 退出码非零且 stderr 含 last-good digest（不泄其他配置）；非 strict 记 drift 继续 |
| tests/evolution/test_process_snapshot.py（新） | 循环 N 次 resolve digest 稳定（100 次脚本 CI 可选 job）；改一个 provider profile 后 digest 变且仅 provider_profiles 组件变；process 指针 CAS 推进与 last_good 轮转；strict 下 binding 写失败 → run FAILED code=system_snapshot_unavailable（继承链断言）；非 strict 同故障 → run 继续 + 审计 |
| tests/evolution/test_generation_reconciler.py（扩） | process scope：崩溃后指针收敛、last_good 缺失 readiness 降级；上一 process 代收尾的 journal 含 draining 中间态（两步 CAS 断言） |
| 回归 | tests/application/test_run_dispatcher_lifecycle.py:87-126（bind 失败完成 attempt）在 strict 下扩新错误码用例 |

**验收 checklist**：
- [ ] 连续 100 次重启 digest 稳定；strict 漂移退出提示可操作（含 last-good 与差异组件）
- [ ] strict 故障注入（改 SKILL.md 后重启）按预期拒启；非 strict 只记不拒
- [ ] 影子观测窗口审计为零后才建议开 strict（写入 docs/plan 运维段）

**回退方式**：关 strict（运行时）；revert PR 安全（process 指针行留存无害）。**工作量**：≈3–4 天。

**决策与取舍**：①strict 翻转集中在本阶段（风险优先案 P6 翻转拍板）而非 P1 就给完整语义（目标纯度案）——影子期需要数据积累，翻转是独立一步；②process 代的 release_digest = base snapshot digest（两案一致）；③`tianshu keqing status` CLI 归 P5 可选项（不在本阶段重复）。

---

### 阶段 P7：声明式内容每 run 冻结视图（≈4 天–1 周）

**目标**：SkillsWatcher 不再直接改 active loader；每个 run 在 bind 时冻结 skills 视图，mid-run 的 SKILL.md 变更/晋升换装对已绑定 run 不可见——消除 watcher 与 SkillPromotionAdapter 对同一 live 目录的无锁并发竞态（loader.py:879-883 vs promotion.py:530-544），顺带消除 get_skill L1 LRU 不校验 mtime 的 gotcha（loader.py:541-544）。persona/prompt 模板/provider 配置的冻结列后续轮次。

**前置依赖**：P1（skills content_digest 已入 snapshot，可做影子比较）；P4（EvolutionRuntimeContext 已是多值形态）。

#### 改动清单

| 动作 | 文件:符号 | 内容 |
|---|---|---|
| 修改 | src/tianshu/skills/loader.py | 新增 `class FrozenSkillsView`（frozen dataclass：`skills: Mapping[str, FrozenSkill]`，FrozenSkill=(digest, content, metadata)）与 `def freeze_view(self) -> FrozenSkillsView`——实现复用 `for_workspace_overlay` 的每-call 隔离模式（:147-164）+ `list_all_metadata` 快照 + injected 复制；`get_skill`/`load_index`/`load_always`/`load_all` 开头查 ContextVar 冻结视图（沿 `_runtime_skill_overlay` :32 同一通道泛化为 `_runtime_frozen_view()`），命中则全程只读该视图；无 view（CLI/工具面等非 run 上下文）走现行为 |
| 修改 | src/tianshu/skills/loader.py:873-885 | `SkillsWatcher._debounced_reload`：新增可选 `on_change: Callable[[list[str]], None] | None = None`；无 on_change 保持旧行为（兼容）；有 on_change 只 `invalidate_cache()` + 回调（不再 `load_all()` 预热 active） |
| 修改 | src/tianshu/evolution/runtime_context.py | 加 `frozen_views: FrozenContentViews | None = None`（`class FrozenContentViews: skills: FrozenSkillsView`——字段化便于后续扩 personas） |
| 修改 | src/tianshu/universe/router.py bind_runtime | 注入 `view_factory: Callable[[], FrozenContentViews] | None`；开关开时 bind 冻结一次，断言 view 的 skills digest == snapshot.components["skills"]（不等记 `skills_view_drift` 审计；影子期只记，翻转消费后重新 resolve 收敛） |
| 修改 | src/tianshu/bootstrap/wiring_skills.py:223 | watcher 装配传 on_change（重算 skills digest 缓存 + 失效）；SkillPromotionAdapter activate 后主动 `loader.invalidate_cache()`（替代 watchdog 竞态依赖） |
| 修改 | src/tianshu/config.py | 加 `frozen_content_views: bool = False`（env `TIANSHU_FROZEN_CONTENT_VIEWS`） |

**数据迁移**：无。

**兼容策略与开关**：默认关 = 现状（watcher 无 on_change 时行为与主干一致，watchdog 缺失静默不启动的现状保留 wiring_skills.py:234-238）；开后先影子（构建视图 + digest 比对，消费仍走 live）观测 `skills_view_drift` 为零，再翻消费（同一开关第二档或拆两个布尔，实现取简单者并在 PR 描述注明）。

#### 测试清单

| 测试文件 | 断言 |
|---|---|
| tests/skills/test_frozen_view.py（新） | bind 后 mid-run 改 SKILL.md：冻结 run 的 get_skill/load_index/`skill_view` 工具（skill_tools.py:345 路径）读旧内容，新 run 读新内容且 snapshot.skills digest 变化；watcher 触发不改运行中视图；晋升 activate 换装期间运行中 run 不读半程树（三方并发竞态回归）；absent overlay 隐藏语义保留；digest 一致性断言 |
| 回归 | tests/skills/test_loader_multifile.py、test_builtin_overlay.py、tests/tools/test_skill_manage_files.py 原样绿（写路径与 builtin copy-on-write 未动）；tests/universe/test_challenger_routing.py:363/461/527（overlay 改变 loader 行为/absent 隐藏/champion 重放冻结 payload）在冻结通道下断言不变 |

**验收 checklist**：
- [ ] 影子期 skills_view_drift 为零后翻转消费；竞态用例（watcher + 晋升换装 + run 执行三方并发）确定性绿
- [ ] 开关关闭行为与 main 一致

**回退方式**：关开关即回现状；revert 无数据面。**工作量**：≈4 天–1 周。

**决策与取舍**：①最小改动案把本阶段整体延期（"skill overlay 的 per-run 冻结已由 runtime overlay 通道成立"）——**否决**：runtime overlay 只覆盖 governed 候选 overlay，不覆盖"mid-run 改 SKILL.md 影响运行中 run"这一真实竞态（watcher vs promotion 无锁并发），且目标态明文要求 watcher 退出 active 直改；②冻结范围只做 skills（三案一致）：skills 是唯一有实际 mid-run 竞态的内容源，personas/prompt/provider 冻结收益/复杂度比低且 MEMORY.md/recent logs 本就应 live；③loader 入口 ContextVar 方案（两案一致）优于改 prompt_builder 签名——prompt_builder 经 load_index/load_always 自动 frozen-aware；④目标纯度案的 `skills_char_budget` 共享可变态顺带修复列为可选项，不作为本阶段验收。

---

## 4. 横切事项

### 4.1 治理不变量守卫（可执行约束清单）

| 守卫 | 动作 | 阶段 |
|---|---|---|
| tests/architecture/test_promotion_authority.py:8（AST 扫 governed lifecycle 写点） | 每阶段 CI 必跑；GenerationReconciler / ExecutorPromotionAdapter / 漂移巡检不得出现 lifecycle 字面量写入（设计上全部经 PromotionService；它们写的 runtime_generations.state 不在扫描集） | P3/P5 |
| tests/architecture/test_generation_authority.py（**新增**，仿上） | AST 扫描：generation 状态与 generation_pointers 写点仅限 generation_repo / ExecutorAdapterRegistry / GenerationReconciler / 启动序列 | P3 |
| tests/architecture/test_evolution_composition.py:13-56 | wire 顺序与单 ChallengerRouter 断言保持；P1 resolver 晚绑定设计使其零改动；GenerationReconciler 换类用 alias 免改 | P1/P3 |
| tests/architecture/test_no_direct_process_launch.py | `verify_pi_rpc_contract` 必须经 ExecutionGateway + grant；allowlist 不扩 | P3 |
| import-linter（pyproject.toml:161-176） | P0 补层 `tianshu.application : tianshu.evolution : tianshu.evidence : tianshu.plugins`（过不了退逐条 forbidden 契约 + ADR 例外清单，P7 前清零）；不为改层而重排既有 import | P0 |
| DB 级不变量 | system_snapshots 的 no_replace/no_update/no_delete；run_system_bindings 与 run_generation_bindings 的 no_replace/no_update；runtime_generation_journal 不可变触发器；`(scope) WHERE state='active'` 部分唯一索引；evolution_policies mode CHECK 无 'auto'；run_subject_assignments V22 同款条件 no_delete | P1/P3/P4 |
| 类型级不变量 | `automatic_promotion_allowed: Literal[False]` 不动；`HIGH_RISK_PROMOTION_KINDS` frozenset 地板；EvolutionPolicyV1.mode Literal 三值 | P4/P5 |
| 迁移纪律 | 每条 V31–V35：checksum + callback 指纹 + PROGRESS.md 裁决 + per-version 切片测试 + 空库回放可通 +（V35）`_RESERVED_TEMP_TABLES`；版本连续性由 test_durable_schema_v14.py:64 自动锁 | 各阶段 |
| tests/architecture/test_route_scope_coverage.py（Codex B1，并行轨） | 每条路由模板恰好被 `_public_route` 与 `route_policy` 之一认领、无 0 命中僵尸规则；**P4a/P5 新增端点前合入**，新路由不登记即红 | 并行轨 → P4a 前 |
| tests/architecture/test_response_contract.py（Codex B5 第二期，并行轨） | 已迁移 router 模块内新增路由必须带参数化 `response_model=ApiResponse[XxxView]`；P4a/P5 新端点从一开始就带具名 View 模型（实测现状：249 个操作仅 2 个有具名响应模型） | 并行轨 → P4a 前 |

### 4.2 API / 前端 / CLI 落点汇总

| 面 | 落点 | 阶段 |
|---|---|---|
| GET /api/evolution/runs/{memorial_id}/assignment | data 加 `system_snapshot: {digest, components, generation_ids} \| null`（evolution_api.py:56-95；`{data, correlation_id}` 信封） | P1 → P3 填 generation_ids |
| GET /api/keqing/status | backend 行加 `generation: {id, state, active_runs, last_good_id} \| null`（keqing_api.py:90-124）；gateway_enabled 恒 False 等断言不动；P5 drift 行加候选链接 | P3 / P5 |
| GET /api/evolution（Evolution Center） | routing summary 加 `subject_key`；快照加 `active_generation`/`last_good_generation` 只读字段 | P4b / P6 |
| GET/PUT /api/evolution/policies/{subject_key} | 新端点（admin scope、CAS、correlation_id 信封）；**不复活** plugins install/activate 501 | P4a |
| Edict 详情 | `EdictEvidenceDetailV1` 加可选 `system_snapshot_digest`（models/edict_detail.py:94 → application/edict_detail.py:140-179 先按 media_type 确认 artifact 存在，再从同 Memorial 最后 binding 取内容 digest → web/src/api/edicts.ts:170；gateway model_dump 透传免改）。ArtifactRef digest 是 `snapshot + generation_ids` 整体字节摘要，不能冒充 SystemSnapshot 内容 digest | P1 |
| 前端手写白名单三处 | web/src/api/evolution.ts:15 kind union、web/src/api/types.ts:440 KeqingBackendStatus、models/evolution_view.py:42——每个含枚举扩展的 PR 列入 checklist；`npm run typecheck` 单独跑 | P3/P4/P5 |
| Web 天工院 | 每 Skill 行展示只读 availability/source/curator protection；可编辑 evolution mode 与 max canary basis points（严格 CAS）。不宣称 enabled 或版本 pin；KeqingManagementPage 加「代际」列 | P4b / P3 |
| CLI | `tianshu serve --system-snapshot`（env 回灌）；`tianshu keqing status`（HTTP client，可选）；CLI 测试 `env -u FORCE_COLOR` | P6 / P5 |
| e2e | 新 spec 按具名读契约注入模式（web/e2e/fixtures.ts helper）；复用 /evolution、/keqing 路由，CORE_ROUTES 不加新路由 | P3–P5 |
| i18n | 三份 locale（en / zh-modern / zh-classic）同步；zh-classic「彩蛋」label 不动 | 各阶段 |
| 响应契约纪律（Codex B5） | 本方案新增端点必须 `response_model=ApiResponse[XxxView]` + frozen View 模型，**不再新增裸 `response_model=ApiResponse`**。assignment 是既有 `{data, correlation_id}` 端点，P1 为它补具名 frozen data/envelope response model 并保持线上 JSON 信封不变；不为满足新端点规则强加 `success` 字段。存量回填与 TS codegen 单开迭代（§8） | P1/P3/P4a/P5 |
| 插件面 | install/activate 501 与 manifest_only 投影全程不动（tests/gateway/test_plugin_manifest_api.py）——本方案不打开任何动态加载 | 全程 |

### 4.3 可观测性

- **SystemAudit 新事件码**（全部走既有 `_append_system_audit_unlocked` + outbox 通道，gates.py:661-696 范式；失败静默降级不阻断执行）：`system_snapshot_binding_failed`（P1 影子降级，strict 后消失）、`system_snapshot_drift`（P1 多 attempt / P6 启动漂移）、`generation_retired`（P3 结构化错误码）、`evolution_policy_updated`（P4a 治理面配置变更）、`evolution_routing_disabled`（P4b 全局 kill switch 关闸）、`skills_view_drift`（P7）；generation 状态转移全量入 `runtime_generation_journal`（不可变）。
- **readiness_probe 聚合**：pending rollback（现有）+ 未 drain 旧代（P3）+ last_good 缺失（P6），被既有 diagnostics/启动探针消费（reconciler.py:45 通道）。
- 漂移与换代事件不新建总线——正确性走 outbox/SystemAudit/journal，EventBus 只作 UI 通知（roadmap §2.2 既定）。
- 部署告警补充：`evolution_routing_secret` 为空且出现 canary 时 allocation_bucket fail-closed（router.py:58-59）——P4 文档与 readiness 提示补上。

### 4.4 文档 / ADR / CONTEXT.md / CURRENT-STATE / 能力矩阵同步（每阶段 PR 必带）

| 文档 | 何时 | 内容 |
|---|---|---|
| docs/adr/0013、0014 + README 索引 | P0 | 见 §3 P0；后续阶段引用不重写 |
| CONTEXT.md + 三份 locale | P0 | 三术语词条直接采用已拍板 canonical 词「典制 / 朝 / 进化策略」，并由 i18n 契约测试锁定 |
| docs/CURRENT-STATE.md | 每条迁移 | 迁移序列 V30 → V31…V35 逐次更新；「当前可用能力」四列表更新自进化/插件/Keqing 行；影子期宽松点作为"明确边界"列出 |
| docs/launch/capability-matrix.md:36/38/40 | P1/P3/P4/P5 | 插件、Lean Core evolution、Keqing 三行按 8 列格式更新 Verified guarantee / non-guarantees / Evidence（链接本方案新增测试路径） |
| docs/cc-fable-v1/PROGRESS.md | 每条迁移 | callback 指纹登记的裁决记录（freeze 测试 docstring 要求） |
| docs/plan/2026-08-25-self-evolving-agent-os-landing.md | 每阶段 | 实施状态表逐阶段勾选 |
| scripts/export_schemas.py `SCHEMA_EXPORTS` | P1/P3/P4a/P4b | `SystemSnapshotV1` / `RuntimeGenerationV1` / `EvolutionPolicyV1` / `SubjectRunAssignmentV1` / `RunAssignmentSetV1` 各自阶段登记落盘 |
| docs/usage/feature-tour.md(+.en) | P3/P4b/P5 | 代际列、策略开关、候选链路的 UI 条目 |
| docs/design/self-evolving-agent-os/* | P0 | 版本号勘误 + 评审 §5 的 7 条最小修订（含恢复 docs/impl/plugins/README.md 3 行转发页） |

### 4.5 发布与 tag

- 每阶段：issue → feat/fix 分支 → PR（`Closes #n`，base=`feat/plugin-v1`）→ `gh pr checks` 亲验 → **执行方直接合入** → 同步集成分支后进入下一阶段；全部阶段完成后用户统一验证，tag 仍由用户操作。迁移号在 issue 预登记占位，并行分支不撞号。
- 版本节奏建议：P1+P2 合入后发 0.5.x patch（P1 为影子归因；P2 为受控生命周期修复，无 UI/API 激活面变化，但会清理失效 MCP 工具）；P3+P4 合入后打 **0.6.0**（代际 + per-subject 灰度成型，主叙事）；P5–P7 进 0.6.x。
- 发版隐形连带：evidence/service.py:656/660 硬编码 '0.5.2'（P1 已把 dependency_lock_hash 与版本字面量集中共享——发版改版本号会改变 snapshot digest，**预期行为**，ADR-0014 明写）；PyPI 发行名 `tianshu-agent-os`，`v*` tag 触发 Trusted Publishing。

---

## 5. 风险与对策

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 影子期豁免被滥用/遗忘，binding 永远"可失败" | 归因数据不可信 | 豁免只有一处（bind_runtime try/except）、专用审计码、ADR-0014 记录翻转条件；P6 验收含"strict 下写失败 run FAILED"测试；观测窗口审计为零是开 strict 的门槛；CURRENT-STATE 列出宽松点 |
| R2 | **V35 重建 evolution_candidates 撞 FK RESTRICT**（六张子表 FK 指向本表；本仓迁移连接 FK 恒 ON——storage/_base.py:87 对迁移所用连接执行 `PRAGMA foreign_keys=ON`，adopt 重放与全部迁移测试模板同样 ON；不可变触发器禁删子行） | 裸 DROP+RENAME 在有子行数据的库上必然失败 | 按 §7 决策 4，V35 依赖序连带重建六张子表（全部临时名登记 `_RESERVED_TEMP_TABLES`，结尾 `foreign_key_check` 兜底，+2–3 天已计入 P5 基线）；单迁移事务原子 + 失败整体回滚（migration_ledger.py:378-431）+ 迁移前在线备份链路 |
| R3 | 旧行 decode fail-closed 被误伤（assignment/binding 任何字段语义漂移） | 存量 run 全部 FAILED | 铁律：RunAssignmentV1/LegacyRunAssignmentV1/EvidenceSnapshotV1 禁改；新数据一律新表；P4b 影子等值测试仅把 fresh-root singleton 的旧表投影与旧 governed artifact 固化为逐字节兼容，V34/API 新字段不在兼容面 |
| R4 | Evidence 因缺 binding 关不上（存量/影子失败/关开关期间的 run） | 治理闭环卡死 | binding 缺失整块跳过（legacy 先例同款）；strict 前不把 binding 设为无条件 required |
| R5 | system-snapshot artifact payload 非确定性导致 open/close digest 漂移 | close 被 required 集合卡死 | payload 只由持久 binding 行确定性推导；形状 P1 一次定死（含 generation_ids key）；测试显式断言 open/close digest 相等 |
| R6 | 引用泄漏、晚释放 ABA 或多个 finally 争夺所有权 | 旧代永不 dispose / 新 attempt 被旧 attempt 误释放 / 提前 dispose | lease 以 exact `attempt_id` 为键；Dispatcher attempt 外层 finally 是唯一 release 点；durable roots 由 execution_attempts + binding + OPEN conversation + active/last-good 推导，回收事务内重查 |
| R7 | manifest/probe 钉死回放与换代冲突（bind_effective 拒漂移） | 旧 run 回放失败 | 旧代整代保留旧 manifest/adapter 实例直到引用归零；probe 语义不随 warm 改变；全程不改参与 content_hash 的测试节点名 |
| R8 | follow-up 代继承与幂等指纹互踩 | 旧 replay 冲突或继承失效 | 继承信息不进 ManagedRunCommand 指纹，从 parent binding 推导；精确 replay 优先于 busy 检查的既有语义有测试锁（test_managed_run_ingress.py:198） |
| R9 | bind 热路径成本：snapshot resolve（全 skills 文件 hash）+ 多 subject N 次候选读 | 派发延迟上升 | 组件 digest 进程内按内容源失效缓存（watcher invalidate 即失效 skills 组件）；per-run 只追加 overlay key；subject 数受组件上界（≤64）约束；基准断言 bind_runtime P95 加量 < 50ms，超了再优化不预先复杂化 |
| R10 | P4b 重写 test_promotion_fail_closed（1433 行）预期，且 test_rollback_fault_matrix 跨文件 import 其私有 fixture 连坐 | 回归面大、易漏 | 4a/4b 拆 PR；fixture 提为共享 conftest helper 的解耦先行小 PR；顺序调整（P4 前 P5 后）保证只重写一次；demo profile 确定性桶做 e2e |
| R11 | 双执行器异代（单发 PiAdapter 校验 vs 会话 PiSessionAdapter 执行）；会话档"每轮重新 spawn 静默拿新二进制"现状偏差 | grant 校验与执行不一致 / 长会话中途换版本 | adapter_factory 一次产出配套两实例、同代 stage/activate；binary_path 固化 + pinned generation 使每轮 spawn 按代取绝对路径（该偏差正是 P3 主修目标，测试锁定）；首版不改 argv 形状，`is_canonical_adapter_argv` 免代际化 |
| R12 | ExecutorPromotionAdapter 同步 activate 内跑异步 warm（事件循环桥接） | 死锁/循环冲突 | loop_runner 注入：sync endpoint 线程池内 `asyncio.run` 独立短命 loop 驱动探针（gateway session 独立实例）；覆盖"promote 并发 + 主 loop 繁忙"用例；不稳则备选 promotion 端点转 async（逃生门记录在案） |
| R13 | snapshot digest 卷入易变无害字段（updated_at、key 明文）造成假漂移或泄密 | drift 噪音 / 安全 | content_digest 只取语义字段（显式排除时间戳与 key 明文，api_key_ref 三态保留）；resolver 测试锁"改 X 只有 X 组件变" |
| R14 | 前端/后端手写白名单漏改（kind union、KeqingBackendStatus） | 运行期 undefined | 每个含枚举扩展的 PR 把三处同步列入 checklist；typecheck 单独跑（vitest/eslint 不查类型） |
| R15 | 把 EventBus/GET 端点当权威（漂移巡检、代切换通知）；三处已知时序敏感偶发测试干扰判断 | 丢事件后状态错 / 误判回归 | 巡检与收敛全部 level-based 挂 reconcile_control_planes；EventBus 只做 UI 通知；CI 红按三步判定（本方案 diff 不触及 outbox scheduler） |

---

## 6. 非目标与延期项

| 项 | 处置 | 理由 |
|---|---|---|
| 进程内 built-in（Tool/Hook/Provider/Channel Python 对象）代际并存与任意模块卸载 | **不做**；进程实现的代际 = P6 的带 drain 优雅重启进 snapshot | 评审 §3.2：单用户/单进程/asyncio/SQLite 下最重且收益最低 |
| `auto` 晋升模式 | **不实现**；类型级 `Literal[False]` + V33 mode CHECK 双重不可表达 | 前置（独立评测/状态回滚/隔离/供应链/kill switch）不齐 |
| 第三方插件动态 import / install / activate | 不做；plugins API 保持 501 + manifest_only；Process/Wasm 隔离、签名/SBOM/TUF 属 Phase 6 | ContributionHandle ≠ 动态加载入口 |
| `AgentSession` 持久对象 | 不引入；continuity 按 Edict 类型三条规则（ADR-0014） | 真实需求出现再 ADR |
| `PluginSetSpec` / `EvaluationCampaign` 数据化 | 不做；前者=`wire_*` 装配代码本身，后者=既有 GateEvaluator + eval_harness | 评审 §3.1 词汇收敛 |
| Legacy Universe evolver 改产 governed Candidate（双轨归一） | 延期为独立轮次；本轮只保证其无生产 active 写权（现状已封死 manager.py:166-183） | 互不干扰；PERSONA 候选承载文件内容是前置 |
| personas / prompt 模板 / provider 配置的每 run 冻结 | 延期（P7 只冻 skills） | skills 是唯一有实际 mid-run 竞态的内容源；MEMORY.md/recent logs 本就应 live |
| `GateEvaluator` 忽略 `contract.required_gates` 的语义分叉 | 不动（维持恒 8 门） | 收紧方向正确；改它牵动全部 gate 测试，收益与本目标无关 |
| manual 模式"Decision 后放行 canary"、`EvolutionPolicy.require_decision` 驱动高危门 | 延期 | StartCanaryCommand 已有 decision_request_id 字段（promotion.py:165-168），但 start_canary 路径不校验该 Decision 与候选的绑定（require 逻辑仅在 CODE promote 路径）——补上等值于把高危 Decision 四元组绑定校验泛化到 canary 命令，需单独设计与测试；常量地板已兜底 |
| policy 变更绑定已决议 Decision（评审 §6 落点原表述『policy 表只允许 Decision 改』） | 延期 | Decision 命令面扩展与 manual 放行同属一个设计；先以 admin scope + CAS + 同 UoW SystemAudit `evolution_policy_updated` 审计守住可追溯性（P4a） |
| prompt/harness 模板内容摘要入 SystemSnapshot 组件 | 延期（components key 白名单预留 `prompts`，加 key 不改 schema_version） | 现由 EvidenceSnapshotV1 的 plan_revision/effective_contract_hash 部分覆盖；模板数字化后补 `prompts` 组件（ADR-0014 组件清单同步注明） |
| ToolRegistry `on_conflict` 缺省收紧为 "error" | 延期（附全仓 register 调用点清点清单后） | P2 决策：默认 replace 保现状 |
| 子进程内感知 snapshot/generation（env 注入 TIANSHU_GENERATION_ID 等） | 延期 | 非不变量所需；binary_path 固化已保证正确性 |
| 客卿二进制按版本区间 `ExecutionDenied`（Codex B8 PR-2） | 延期到 P3 跑满一个 canary 周期、有真实漂移数据后 | 先记录不拦截；`MINIMUM_SUPPORTED_CODEX_VERSION` 在 codex 里也只是 CI 偏斜测试常量而非运行时闸门 |
| activate 前重读 `cli_version` 与 `release_digest` 比对（Codex A2 被砍的第 (2) 条最小形态） | 延期 | warm 已校验活体 session header version；唯一剩余漂移向量 cli_version 的价值等有数据再定；若做则复用 warm 失败路径、指针不动，不引入新事件类型 |
| 真实 dependency_lock_hash | 延期另立 decision | 占位 '0'*64 双方（evidence 与 snapshot）同源即无假漂移 |
| run_evolution_assignments 与 run_subject_assignments 最终合一（退役双写） | 待多 subject 稳定运行一个 minor 版本后评估 | §7 决策 3 |
| Executor 命令面权限对齐（evaluate 仅 owner vs stage 允许 admin 的不对称） | 延期专项 | EXECUTOR 候选由 SYSTEM 巡检产生，先沿既有 SkillInstallService 样板 |
| 多节点 / 微服务拆分 / Wasm host / 插件市场 | 不做 | 目标文档非目标清单原文 |

---

## 7. 已拍板决策（2026-08-25）

1. **中文 canonical 词**：`SystemSnapshot`=「典制」，`RuntimeGeneration`=「朝」，`EvolutionPolicy`=「进化策略」。三词写入 `CONTEXT.md` 与三份 locale；zh-classic 的「彩蛋」label 不改。
2. **EvolutionPolicy 缺省祖父化**：skill 无策略行时维持 `canary`，其余 kind 无策略行时为 `manual`。后续可显式写行收紧，不在 P4a 偷渡存量行为变更。
3. **legacy 单 assignment 双写期限**：P4b 后继续保留旧表 0/1 canary 语义与新表影子双写；多 subject 稳定运行一个 minor 版本后再评估退役，退役另走独立迁移与 ADR。
4. **V35 重建形态**：采用分支 A，按依赖序连带重建六张子表；所有临时表名登记 `_RESERVED_TEMP_TABLES`，结尾执行 `foreign_key_check`。不使用 `PRAGMA writable_schema`，不引入仅 propose/stage 的过渡语义。
5. **attempt binding 删除语义**：`run_system_bindings` 与 `run_generation_bindings` 都使用 no-replace/no-update，允许 DELETE 且不对 memorials 建 FK，与敕令物理删除清理路径对齐；前者的不可变典制副本由已关闭 Evidence artifact 保存，后者是存活 attempt/continuity 的运行权威，不冒充永久 WORM 证据。
6. **Candidate 类型**：引入 `CandidateKind.EXECUTOR`，不复用 `CODE` 加 subject 前缀特判。
7. **策略 UI 与随阶段交付**：P4b 同批交付天工院 truthful policy UI：Skill availability/source/curator protection 只读，evolution mode 与 max canary basis points 可经严格 CAS 修改；不把 `pinned` 误报为版本 pin，也不虚构 enabled 开关。`tianshu keqing status` CLI 和 edict 详情的 `system_snapshot_digest` 投影分别随对应阶段交付。

---

## 8. Codex harness 借鉴并入项（2026-08-25 裁决）

> 来源：[docs/reference/openai-codex-harness-analysis.md](../reference/openai-codex-harness-analysis.md)（13 组测绘 → 3 视角 → 7 组对抗证伪 → 终审）。本节回答「哪些并进 P0–P7、哪些并行、哪些等主线收官」，并已把并入项**逐条写进上文对应阶段的改动清单 / 测试清单 / 验收 checklist**（标 `Codex Xn`），此处只给总表。判据只有一条：**防的是正在写的代码或已存在的洞 → 并入；防的是半年后的漂移 → 并行轨；新接缝/新功能 → 收官后**。

### 8.1 并入现有阶段（改规格，不加阶段；关键路径 +4–5 天）

| 条目 | 并入 | 增量 | 为什么现在 |
|---|---|---|---|
| **A2** dispose 身份校验 | **P2** 已落地 | +0.5 天 | 补 P2 自己拍板的 `on_conflict="replace"` 留下的洞；旧 handle 不会摘掉后来同名替换，并留 stale audit |
| **A3-a** canary partial unique index | **P4a** 的 `0033` 迁移同块 | +1 天（锁定测试登记 + 409 映射 + 体检） | 索引键一次按 (kind, subject_key) 建好，P4b 免二次迁移 |
| **A3-b** policy 执法收口 `save_candidate` | **P4a** | +1 天 | 三个入口检查靠人记得；唯一 UPDATE 路径一处兜住全部现有及未来写点 |
| **B8** 二进制绝对路径 + 版本入 receipt | **P3**（`versions.py` 下移与 pi binary_path 固化本就在 P3） | +1 天 | 与 P3 同 PR 最省；P5 EXECUTOR canary 对比的可归因前提 |
| **B9** EventBus 谓词等待夹具 | **P3** 首个 commit | +1 天 | P3 故障注入矩阵与 P4b 并行灰度都是多步时序，没有它只能 sleep |
| §4 #2 materialize-first + 状态原子切换 | **P3** `activate` 规格注记 | 0 | 新 bundle 先 warm；事务内 old→draining、new→active、最后 pointer CAS，避免第二 active 或指针错配 |
| **B5** 新端点必须参数化 `response_model` | **§4.2** 纪律行 | ≈0（每端点 +10 行 View 模型） | 新增面从一开始就有类型，不再欠债；存量回填不在主线 |
| **B3** 新契约登记 `SCHEMA_EXPORTS` | **§4.4** 文档表行 | 0（B3 已生效） | P1/P3/P4a/P4b 各自登记，避免回填 |

### 8.2 独立并行轨（可交给第二个 codex 会话，与 P0–P2 同期；不占关键路径）

| 代号 | 条目（规格见分析文档 §三对应节） | 成本 | 时点约束 |
|---|---|---|---|
| X1 | **A1** WS 出站所有权过滤 | S | **立即**——已实测确认的越权可见（secure-remote 下任一 api scope PAT 收到全部主体的 `stream.delta`）；`fix/` 分支单独 PR |
| X2 | **B2** 重试判据收敛进 `models/failure.py` | S | 随时 |
| X3 | **B7** 敕令受理期校验 allowed_paths glob | S | 随时 |
| X4 | **B3** schema 落盘 + CI 汇总门禁 + clean-worktree | M | **P3 合入前**（P3 起有 `RuntimeGenerationV1` 要登记；P1 的 `SystemSnapshotV1` 由 B3 PR 自己补登记） |
| X5 | **B1** 路由 scope 显式表 + 覆盖测试 | M | **P4a 合入前**（P4a 新增 policies 端点要登记）；三步走，先对拍再切换 |

并行轨总量 ≈ 8–10 天；若无第二会话，则插在 P2 之后、P3 之前串行做，关键路径再 +8–10 天。

### 8.3 主线收官后单开迭代

| 条目 | 成本 | 为什么不并入 |
|---|---|---|
| **B4** 客卿凭证网关（钥匙不出治理层） | **XL** | 战略上最重要（叙事与实现裂缝最大），但体量决定它是 0.7.0 主线候选而不是顺手活；七处隐藏连带见分析文档。**不阻塞 P5**：EXECUTOR canary 对比两代 Pi 时模型出口相同，结论仍成立——P5 验收里注明「对比在同一模型出口下成立」 |
| **B5** 存量 117 处 `response_model` 回填 + OpenAPI 导出 + TS codegen | L | 先做 codegen 会把 `data?: unknown` 固化成生成物；回填是体力活，不占主线 |
| **B6** 插件坏 manifest 可见 + symlink 校验 | M | 审计可见性缺口，与 P2 无前置关系（原「owner 前置」叙事已被证否） |
| **D1–D4** 位面 GC 窄判据 / WS 有界队列 / events 落库 redact / 错误信封统一 | S–L | 均为 P3 级；D4 是 L 且 69 处裸 detail 要清 |
| hook 事件面上移治理链（Hooks UI 老待办的根源） | L | 新接缝：天枢只有 `on_before_tool_call` 一个事件，codex 有 9 个 + 完整引擎 |
| `ContextFragment` 带 kind + 硬预算收编 | M | `PromptBuilder` 已有预算但是散落魔法数；收编成显式契约 + 总预算断言 |
| 配置键 `Stage::Removed` 注册表 | S | 配置键永不删、只标 removed、加载时忽略并记 `legacy_usage` |
| 「破坏性变更外部契约面」review 清单（HTTP API / SSE+WS 事件 / CLI 参数 / 配置加载 / 续跑） | S | 写进 `.claude/rules/`，可随任意 PR 顺带 |

### 8.4 明确不并入（分析文档 §七 的 11 条）

Starlark 策略 DSL、自建 OS 沙箱、code-mode/V8、`ext` 编译期扩展体系、exec-server 多进程边界与 daemon 自更新、marketplace 分发、HTTPS MITM、源码硬编码白名单与旁路开关、Bazel 双轨与运行时元编程、agent-identity/process-hardening、单文件巨兽。**本方案任何阶段不得以「codex 也这么做」为由引入上述任一项。**
