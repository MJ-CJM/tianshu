# Codex 执行交接 prompt（自进化 Agent OS 落地 + Codex 借鉴并入项）

> **状态：已归档，不得再次作为执行 prompt 使用。** 下方内容保留为 X1–X5 / P0–P7 的历史实施流程与审计记录。
> 历史用法：把下方分隔线之间的整段粘贴给 codex；每次只改「本次执行」一行的任务代号。
> 任务代号一览：`X1`–`X5` 并行轨（规格在 [reference/openai-codex-harness-analysis.md](../reference/openai-codex-harness-analysis.md) §三 对应节 + 方案 §8.2）；`P0`–`P7`（含 `P4a`/`P4b`）主线阶段（规格在 [2026-08-25-self-evolving-agent-os-landing.md](./2026-08-25-self-evolving-agent-os-landing.md) §3）。
> 推荐顺序：`X1` → `P0` → `P1` ∥ `P2` ∥ `X2` ∥ `X3` → `X4` → `P3` → `X5` → `P4a` → `P4b` → `P5` → `P6` → `P7`。
> 分支策略：集成分支 `feat/plugin-v1` 承载整个大需求，所有任务 PR 以它为 base；**全部任务完成并整体验证前，不合 main**。
> **收官状态（2026-08-27）**：X1–X5 与 P0–P7 已全部合入 `feat/plugin-v1`；当前剩余的是用户在集成分支上的总体验证及最终合入 `main`，不是阶段开发未完成。后期用户已授权执行方在任务 PR CI 全绿后直接合入集成分支；仍不得代用户打 tag 或合入 `main`。

---

# 天枢自进化 Agent OS 重构 —— 交接与执行指令

你是本仓库（tianshu，Python 3.12 / FastAPI / SQLite，单机单进程）的实施工程师。你的任务是**按既定落地方案分任务实施**，不是重新设计。方案已经过多视角合成与对抗校验，你的职责是忠实执行、遇到与源码不符时停下汇报，而不是自行改方案。

## 0. 一次只做一个任务

**本次执行的任务：无（X1–X5 与 P0–P7 已收官）**。以下步骤仅保留为历史记录，不得据此重复创建 issue、分支或 PR。

任务分两轨，规格出处不同：

| 轨 | 代号 | 规格出处 | 说明 |
|---|---|---|---|
| 主线 | `P0` `P1` `P2` `P3` `P4a` `P4b` `P5` `P6` `P7` | `docs/plan/2026-08-25-self-evolving-agent-os-landing.md` §3 对应阶段全节（含标 `Codex Xn` 的并入行） | 顺序 P0→P1→P2→P3→P4a→P4b→P5→P6→P7；P2 可与 P1 并行 |
| 并行轨 | `X1` WS 所有权过滤 · `X2` 重试判据收敛 · `X3` allowed_paths 受理校验 · `X4` schema 落盘 + CI 门禁 · `X5` 路由 scope 表 | `docs/reference/openai-codex-harness-analysis.md` §三 的 A1 / B2 / B7 / B3 / B1 节 + 方案 §8.2 的时点约束 | `X1` 立即；`X4` 须在 `P3` 合入前；`X5` 须在 `P4a` 合入前；其余随时 |

每个任务以“PR 已开、CI 已亲验并合入 `feat/plugin-v1`、汇报已发”为终点。用户已授权执行方在 CI 全绿后直接合入集成分支；**不要打 tag，也不要把集成分支合入 `main`**——这两步仍由用户操作。

## 0.1 分支策略（本次大需求独立于 main）

- **集成分支：`feat/plugin-v1`**（已存在，方案与分析文档都在上面）。它是本次大需求的唯一集成点；**在 X1–X5 与 P0–P7 全部完成、整体验证通过之前，任何东西不得合入 `main`**。合 main 的最终 PR 由我在收官时开。
- 每个任务从集成分支起任务分支：`git checkout feat/plugin-v1 && git pull && git checkout -b sea/<代号>-<slug>`（如 `sea/x1-ws-ownership-filter`、`sea/p1-system-snapshot-shadow`；`sea` = self-evolving agent-os）。
- PR 一律 `gh pr create --base feat/plugin-v1`，**禁止以 main 为 base**。我合入集成分支后，你下一个任务重新从最新的 `feat/plugin-v1` 起分支；并行任务开 PR 前先 `git rebase feat/plugin-v1` 解决冲突。
- 迁移号在 issue 里预登记，并行分支不得撞号；两条并行分支都含迁移时，后合入者 rebase 后必须重跑迁移切片测试。
- 集成分支上的每个任务 PR 都要求 CI 全绿——集成分支不是回收站，任何时刻它都应能通过全量验证。

## 1. 先读什么（按顺序，读完再动手）

1. `docs/plan/2026-08-25-self-evolving-agent-os-landing.md` —— **主线唯一执行依据**。先读 §0（总览与原则，含并行轨行）、§1（目标对象/表/不变量）、§2（当前基线与锁定测试）、§3 开头的"通用约定"、§8（Codex 借鉴并入项总表），再精读本次任务对应章节。§4 横切事项、§5 风险、§6 非目标在动手前扫一遍。
2. `docs/reference/openai-codex-harness-analysis.md` —— 并行轨任务的规格来源；主线任务也要读其 §三 中被引用的条目（方案里标 `Codex Xn` 的行会指向它）以及 §七「明确不建议借鉴」。**该文档 §三每条的「验证修订」段列出了已被证伪的做法，禁止回退到原提案**。
3. `docs/design/self-evolving-agent-os/README.md` → `review-and-implementation-plan.md` → `architecture-comparison.md`（目标架构与前后对照，理解"为什么"）。
4. `docs/CURRENT-STATE.md` 的"事实源优先级"一节：源码与测试 > CURRENT-STATE/能力矩阵 > design/impl 文档 > ADR > 历史计划。方案与源码冲突时**以源码为准并停下汇报**。
5. `AGENTS.md`（本仓行为守则：先想后写、最小改动、外科手术式修改、目标驱动验证）。

这些文件都在集成分支 `feat/plugin-v1` 上；若你所在分支看不到它们，说明起点错了，回到 §0.1 重新起分支。

## 2. 已拍板的开放问题（方案 §7，不要再问）

1. 中文 canonical 词：`SystemSnapshot`=「典制」，`RuntimeGeneration`=「朝」（新朝预热 → 登基 active → 逊位 draining → 退位 disposed）；`EvolutionPolicy`=「进化策略」。写入 CONTEXT.md 与 i18n 三份 locale（zh-classic 的「彩蛋」label 不动）。
2. EvolutionPolicy 缺省祖父化：skill 无行 = canary（保持现状），其余 kind 无行 = manual。
3. 旧表双写保留到多 subject 稳定运行一个 minor 版本后再评估退役；本轮不退役。
4. V35 重建 `evolution_candidates` 采用分支 (a)：按依赖序连带重建六张子表，临时表名全部登记 `_RESERVED_TEMP_TABLES`，结尾 `foreign_key_check`。
5. `run_system_bindings`：no_update 触发器 + 允许 DELETE + 不 FK memorials。
6. 引入 `CandidateKind.EXECUTOR` 枚举值（不用 subject 前缀特判）。
7. P4b 同批落天工院每插件一行三开关 UI；`tianshu keqing status` CLI 与 edict 详情 `system_snapshot_digest` 投影随各自阶段顺带交付。
8. **Codex 借鉴项按方案 §8 的三分处置执行**：§8.1 并入项随对应阶段做、§8.2 并行轨按代号单独做、§8.3 一律不做、§8.4 一律不做。

## 3. 铁律（任何任务都不得违反）

- **禁改对象**（方案 §1.1 标"现有，禁改"）：`RunAssignmentV1` / `LegacyRunAssignmentV1` / `EffectiveEvolutionOverlayV1` / `EvolutionRunEvidenceV1`、`EvidenceSnapshotV1` 及全部 evidence 模型的 schema_version、V1–V30 迁移、Candidate 11 态转移图、`EvolutionContractV1.automatic_promotion_allowed: Literal[False]`、`CapabilityId` 的 13 项 Literal（**不得新增能力位**——会撞冻结夹具与已落库合同）。新能力一律走新表 / 新可选字段 / keyword-only 默认参数 / Evidence artifact 通道。
- **fail-closed 只收紧不取消**：单 canary 排他改为 per-subject 仍排他；bind 失败 run 失败的语义只扩展不弱化；`auto` 模式在类型级与 DB CHECK 两级都不可表达；策略执法是**写前校验**，永远不做写后改写已落库状态。
- **插件面不开动态加载**：`POST /api/plugins/install`、`PUT /api/plugins/{name}/status` 保持 501，投影保持 `manifest_only`。ContributionHandle 不是加载入口。
- **迁移 append-only**：新迁移从 V31 起（0031–0035），编号在 issue 里预登记后冻结；每条迁移四件套（migrations.py 尾部追加 + checksum；`tests/storage/test_migration_callback_freeze.py` 登记 upgrade 源码指纹并先在 `docs/cc-fable-v1/PROGRESS.md` 记录裁决；新建 `tests/storage/test_durable_schema_vNN.py`；同步 `tests/evolution/test_s4_s5_handoff.py` 的迁移布局断言）。新增索引/表必须同步 `tests/evolution/test_evolution_migration_schema.py` 的锁定登记。upgrade 必须在空 `:memory:` 库、无外部 env 下可回放。
- **治理微内核不进进化**：candidate lifecycle 只经 `PromotionService` 写（`tests/architecture/test_promotion_authority.py` AST 守卫）；generation 状态与指针只经方案指定的写权威；不新增绕过 Decision 的路径。
- **只切指针，不改活体**：换代 = stage → warm → activate；warm 失败指针不动；activate **先立新后撤旧**；不做进程内模块 reload。
- **新增端点必须 `response_model=ApiResponse[XxxView]` + frozen View 模型**（方案 §4.2）；不再新增裸 `response_model=ApiResponse`。
- **测试同步原语**：所有等待必须带谓词（`tests/support/waiting.py` 落地后一律用它），timeout 只作失败上界；禁止新增 `sleep` 同步。
- **行号会漂**：方案与分析文档里的 `文件:行` 是 2026-08-24/25 基线，以符号名重新定位、以当前源码为准；若某处源码已与文档描述不符（函数不存在、签名不同、语义相反），**停下汇报，不要自行改方案或猜测**。
- **只做本任务改动清单内的事**：不顺手重构、不改无关格式、不删既有死代码（发现了在汇报里提）。清单外但为通过测试必须的改动，在 PR 描述里单列并说明理由。

## 4. 执行流程

1. `git checkout feat/plugin-v1 && git pull`（集成分支，见 §0.1；**不要从 main 起分支**）。
2. 用 `gh issue create` 开 issue：主线标题 `feat(evolution): P<n> <阶段名>`，并行轨标题 `fix(gateway): X1 WS 出站所有权过滤` 这类按实际类型；正文引用规格文件与章节、列本任务验收 checklist；在 issue 里登记本任务占用的迁移号（如 `V31 0031_system_snapshots`；并行轨无迁移）。
3. `git checkout -b sea/<代号>-<slug>`（如 `sea/x1-ws-ownership-filter`、`sea/p1-system-snapshot-shadow`）。
4. 按规格的**改动清单**逐项实施；**测试清单**逐项落地（新测试文件名按规格）；每个改动清单条目做完即跑相关测试。
5. 任务完成前的全量验证（全部用 `.venv/bin/python`，裸 `python` 指向错误环境）：
   - `.venv/bin/python -m pytest tests/ -q`
   - `.venv/bin/python -m mypy`（按 pyproject 配置的包范围）
   - `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`
   - `.venv/bin/lint-imports`
   - 若改了 `web/`：`cd web && npm run typecheck && npm run test`（vitest/eslint 不查类型，typecheck 必须单跑）
   - 若改了 CLI：CLI 测试用 `env -u FORCE_COLOR` 运行
   - 已知三处时序偶发：outbox scheduler ×2、process group ×1。若红了，先判断你的 diff 是否碰得到、本地复跑是否稳定，不要为它们改测试。
6. 逐条对照本任务**验收 checklist**，未满足的不得进入下一步；规格里标"demo 栈手工"的验收项，用 `tianshu serve` 起 demo profile 实际操作一遍并把观察结果写进 PR。
7. 提交：conventional commits（`feat:` / `fix:` / `test:` / `docs:`），小步多次；PR 标题同 issue，正文含：改动摘要（按改动清单对照）、迁移说明、测试计划（勾选）、验收 checklist 状态、回退方式、`Closes #<n>`。`git push -u origin sea/<代号>-<slug>` 后 `gh pr create --base feat/plugin-v1`（**base 必须是集成分支**）。
8. `gh pr checks <n> --watch` 亲眼看到全绿；红了先修，修不了说明原因。按后期用户授权，全绿后由执行方合入 `feat/plugin-v1`；**不 tag、不碰 main。**
9. 同步文档（方案 §4.4）：在 `docs/plan/2026-08-25-self-evolving-agent-os-landing.md` 文首增加/更新「实施状态」表（仿 `docs/plan/2026-07-23-keqing-pi-implementation.md` 样式，逐任务记录状态、日期、测试数；并行轨任务也登记）；新迁移时更新 `docs/CURRENT-STATE.md` 迁移序列；能力矩阵与 CONTEXT.md 按规格要求更新；`X4` 合入后，后续每个新增 V1 契约的阶段须登记 `SCHEMA_EXPORTS`。这些随同一 PR 提交。

## 5. 汇报格式（每任务结束一次，直接在对话里给）

```
任务：<代号> <名称>
Issue / PR：#<n> / #<m>（CI：全绿 / 非通过项：…）
改动：<按规格改动清单逐条：✅ 完成 / ⚠️ 偏离（原因）/ ❌ 未做（原因）>
迁移：<版本/名称/指纹已登记/PROGRESS.md 裁决行；无则写"无">
测试：<新增测试文件与数量；全量 pytest 结果；mypy/ruff/lint-imports/typecheck 结果>
验收 checklist：<逐条勾选状态>
与规格不符之处：<源码事实 vs 文档描述，及你采取的处理>
留给我的决定：<需要我拍板的事项，没有就写"无">
下一任务前置：<是否满足；X4/X5 的时点约束是否已达成>
集成分支状态：<本 PR 合入后 feat/plugin-v1 是否仍能全量验证通过；距离整体收官还差哪些代号>
```

## 6. 必须停下来问我的情形

- 源码与规格描述冲突，且无法在不改规格意图的前提下实施；
- 实施需要触碰 §3 任一"禁改对象"或弱化任一 fail-closed 语义；
- 某条迁移无法在空库回放通过，或 V35 重建在带子表数据的库上失败；
- 全量测试有非本 diff 引起、且不属于三处已知偶发的失败；
- 工作量明显超出规格估算两倍以上；
- 分析文档「验证修订」段标为已证伪的做法看起来又成了必要路径。

以上情形先给出你观察到的事实与两三个可选处理，不要自行选择后继续。

现在开始：先按第 1 节顺序读文档，然后用一段话复述本次任务的目标、改动清单要点、验收标准和你打算的实施顺序，我确认后你再动手。

---
