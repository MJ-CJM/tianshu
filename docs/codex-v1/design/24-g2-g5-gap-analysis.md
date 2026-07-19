# G2–G5 只读差距勘察

> 勘察基线：`feat_codex_phase_1`，观察时 HEAD=`13fbdc7`（G0 已形成 4 个本地提交）。
> 路线图基线：`docs/superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md` 的 Phase 2–5。
> 范围：只读检查后端、存储、正式 Web、G0 原型、测试、CI、容器和发布资料；本文件本身是勘察产物，不修改业务代码。
> 限制：GitNexus 当前只索引了其他两个仓库，未索引 Tianshu；因此本报告以当前 checkout 的源文件、测试和 Git 跟踪事实为准。

## 1. 结论先行

| Gate | 当前真实状态 | 结论 |
|---|---|---|
| G2 Durable Governance & Evidence | 有 SQLite 事件、outer-loop checkpoint、孤儿回收、审计、通知暂存等局部基础；但裁决等待和分发仍是进程内状态，事件总线非持久队列，无统一 UoW/outbox、lease、side-effect journal、Evidence Bundle | **未达 Gate；不能宣称完整重启恢复、一次有效副作用或可独立验证** |
| G3 Desktop Web Productization | G0 原型已有三页视觉参考和真实性测试；正式 Web 保留品牌壳、十四部门、三语言和主题，但 `/control`、Governance/Evidence/Evolution 真实三页及统一数据状态均不存在，页面测试和 Playwright 基础为空 | **视觉方向已定，产品实现未开始；不能录正式产品 Demo** |
| G4 Governed Evolution & Executor Neutrality | Universe、配对评估、代码变体、技能 Guard、FTS 重建和 Keqing CLI 外围约束已有较多零件；但代码明确让 `route_for_memorial()` 永远返回 champion，晋升 API 可绕过统一强门，Keqing 不是 managed，无 OpenHands adapter/兼容套件 | **实验候选系统存在，但“真实自进化闭环”未成立** |
| G5 Open-source Launch & Ecosystem | 已有双语 README、LICENSE/SECURITY/CONTRIBUTING、能力矩阵、launch checklist 和视觉素材；但黄金 Demo、Executor SDK、release workflow、SBOM/attestation、NOTICE、Issue/PR 模板均缺；镜像仍为 root/full-build-tools，仓库还跟踪 `.idea` 与 `web/.vite` | **有发布叙事骨架，无可复现发布系统；不能正式宣发 1.0** |

关键判断：后续不能把“已有某个表/某个 checkpoint/某个测试”直接等价为 Gate 通过。G2 的核心是跨故障点协议；G3 的核心是真实 API 和状态语义；G4 的核心是真实路由、权威门禁和 managed adapter；G5 的核心是可复现制品与外部独立验证。

## 2. 当前事实锚点

### 2.1 G2 相关事实

- `src/tianshu/edict_ops.py` 依次 `save_edict`、`save_memorial`、`event_bus.fire`，三者不在同一 UnitOfWork；中途崩溃会产生部分提交。
- `src/tianshu/bus/event_bus.py` 是进程内 handler 列表与 background task set；`fire()` 只是 `asyncio.create_task()`，不存在 durable outbox、claim/lease、ack 或 replay。
- `src/tianshu/executor/approvals.py` 用 `_pending: dict[str, asyncio.Event]` 和 `_outer_loop_pending` 等内存字典等待裁决；重启后请求、payload、结果和 await 栈均消失。
- `outer_loop_checkpoints` 和 `src/tianshu/executor/checkpoint.py` 已能存部分轮次状态；但 `_state_from_dict()` 明确把 `history=()`，快照也没有 tool proposal、decision request、side-effect cursor、version/CAS。
- `src/tianshu/scheduler/scheduler.py` 有基于 heartbeat 的 orphan sweeper；checkpointed/background 任务只发 `edict.resume`，没有 durable claim、lease owner、attempt 上限和 DLQ。
- `src/tianshu/executor/worker.py` 直接运行 Agent 并在 finally 更新 Memorial；没有 attempt ledger，也没有“intent -> external effect -> receipt -> ack”协议。
- `src/tianshu/models/memorial.py` 有简单 `ArtifactRef(name/type/path/url)` 和 `audit` 字段；没有 ArtifactStore 元数据、canonical bundle、close snapshot、digest、redaction/retention 和 schema validation。
- `src/tianshu/auditor/auditor.py` 有规则/LLM 双层审计，但目前审计结论不会因强制 Evidence 缺失而 fail closed。
- `src/tianshu/observability.py` 只有可选 GenAI LLM span；`src/tianshu/app.py` 没有 tracer flush，也只有固定返回 `{"status":"ok"}` 的 `/health`，无 readiness 依赖检查。
- `src/tianshu/notifier/notifier.py` 的 quiet-hours 暂存只在后续非静默消息到来时懒 flush；外发失败仅记日志，发送后无 attempt/receipt/DLQ，且 `_flush_pending()` 无论成功与否都会删除 pending row。
- `pyproject.toml` 只有一个宽泛 layers contract 和 Telegram→Feishu forbidden contract；新增 application/governance/evidence 边界尚无约束。

### 2.2 G3 相关事实

- G0 视觉原型位于 `prototypes/tianshu-agent-os/`，已有 Control Center、Edict Detail、Evolution Center、13 项原型测试和桌面审计截图；数据来自 `src/data/mockData.js`，不是生产 API。
- 正式 `web/src/App.tsx` 仍把 `/` 映射到御书房，没有 `/control`；除 DAG 页外所有重页面均同步进入首屏 bundle。
- `web/src/components/layout/AppHeader.tsx` 已保留现有 Logo、品牌名、标语“成功只有一个——按照自己的方式，去度过人生。”和右上角语言/实时/通政状态；这是应冻结的资产。
- `web/src/components/layout/AppSidebar.tsx` 已有四组十四部门、主题切换和收起/展开；应仅在部门组上方加入“中枢总览”，不能用泛化分类替换部门。
- `web/src/hooks/useTheme.ts` 在没有持久偏好时默认 `light`，与路线图要求的默认深色不一致。
- `web/src/api/client.ts` 只做通知弹窗；无 AuthContext、correlation id、401/403/503 分类、stale/permission/service unavailable 语义。
- 正式 Web 没有 `components/governance/`、`components/evidence/`、`components/states/`、`ControlCenterPage.tsx`、`OnboardingPage.tsx` 或 `EvolutionGate.tsx`。
- `web/src/pages/PersonaDashboardPage.tsx` 1107 行、`PersonaDetailPage.tsx` 1081 行、`EdictDetailPage.tsx` 865 行、`AuditDashboardPage.tsx` 740 行、`UniversePage.tsx` 656 行、`MemoryDashboardPage.tsx` 651 行；均承担多个独立职责，符合按业务区块拆分条件。
- 正式 Web 只有 `i18n/terminology.test.ts` 和 `utils/format.test.ts`；`package.json` 没有 Testing Library、Playwright 或 axe 依赖，也没有 `web/e2e/` 和 `playwright.config.ts`。
- 仍有多套 fetch wrapper（`api/cost.ts`、`api/providers.ts`、`api/memory.ts`、`components/persona/ProfileTab.tsx` 等），且多个 catch/`?? []` 路径会把接口故障显示为空数据。
- 后端没有 control-center aggregate、Evidence Bundle、readiness 或权威 `promotion_allowed + blocking_gates` API。

### 2.3 G4 相关事实

- `src/tianshu/universe/manager.py::route_for_memorial()` 的文档与实现都明确“当前一律归冠军”，`tests/universe/test_routing.py` 也锁定 champion-only；因此 feature flag 的 rollout 百分比不是实际 challenger 流量。
- `src/tianshu/universe/evolver.py` 已有配对评估、delta、最小样本、推荐、行为层可选 auto-promote、代码变体静态/import/test gate；这些是可复用基础。
- `src/tianshu/gateway/universes_api.py` 的 `/{id}/switch` 与 `/{id}/promote-code` 直接调用 manager；没有统一 EvolutionGate、强制 Evidence、回滚点/裁决请求检查，存在绕过权威门禁的写入口。
- `src/tianshu/universe/model.py` 只有 Universe，不存在跨 memory/skill/policy/persona/code 的 `EvolutionCandidate`。
- `src/tianshu/skills/installer.py` 对 zip/目录具备路径穿越、symlink、zip bomb、validator 和 guard；但 `POST /api/skills`、`skill_manage(create)`、reviewer、curator 仍直接调用 `SkillsLoader.create_skill()`，没有统一 install service、provenance、版本、候选/晋升/回滚协议。
- `src/tianshu/executor/keqing/adapter.py` 只是“拼 argv + 解析 JSONL”的 CLI Protocol；`KeqingExecutor` 只能提供独立目录、clean-env、外围 timeout 和事后 best-effort 成本，不能声明 action interception、decision bridge、硬成本上限、durable resume 或 side-effect receipt。
- 没有 `src/tianshu/executor/adapters/openhands.py`，也没有 `tests/compat/executor_adapter/`；Native 与外部执行器未运行同一兼容套件。
- `MemoryManager.sync_index/sync_all_indices` 已能从 Markdown 重建 SQLite/FTS，`POST /api/memory/sync` 也存在；但无 rebuild job 状态/故障证据、无 paired memory ROI benchmark。
- PromptBuilder 只有 skills 30k 字符预算、peer 单份 600 字符、recent logs 2k；SOUL/ROLE/PROFILE/个人/部门/court memory 没有统一分层预算或可观测裁剪结果。
- 成本模块有终态计量、价格表和已观测用量熔断；没有预测区间、校准误差、实际超额量，以及 managed/contained/observed enforcement mode。

### 2.4 G5 相关事实

- `examples/`、`src/tianshu/sdk/`、`templates/executor-adapter/`、`tests/compat/` 均不存在。
- 只有 `.github/workflows/ci.yml`，包含 backend/frontend 基线；没有 release-smoke、release、CodeQL/dependency scan、history secret scan、SBOM、签名或 attestation job。
- `Dockerfile` 虽有 frontend builder，但运行层基于 Ubuntu、保留 git/curl/wget/jq/zip/unzip/build-essential、以 root 运行，并执行浮动 `pip install ".[cli]"`；不是冻结、最小、非 root、可审计的发行镜像。
- `pyproject.toml` 没有完整作者/URL/classifier/license 元数据；没有清晰的 core/server/all 组合，wheel 对顶层 `personas/`、`templates/persona/` 及非 Python `src/tianshu/skills/builtin/*/SKILL.md` 缺少 package-data/resource 声明。
- 当前仓库仍跟踪 `.idea/**` 与 `web/.vite/**`，正是路线图指定的发行卫生失败项。
- 已有 `LICENSE`、`CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`、双语 README、能力矩阵和 launch checklist；缺 `NOTICE`、third-party license report、`CODE_OF_CONDUCT.md`、Issue templates 与 PR template。
- `CHANGELOG.md` 的历史条目仍含“自动晋升”“自重部署回滚”“批红”等比当前能力矩阵更强/已废弃的表述；发布前需保留历史事实的同时加醒目更正，不能让历史文案覆盖当前能力真相源。

## 3. G2：Durable Governance & Evidence

### 3.1 最小可交付实现

G2 的最小垂直闭环不是“把 asyncio.Event 写进 SQLite”，而是以下 7 个可故障注入、可重放的协议：

1. **单一提交服务**：所有 Web/API/Bot/MCP/Tool 入口构造同一 `EdictApplicationService.submit()` 请求；edict、initial memorial、idempotency record 与 outbox row 同事务提交。
2. **持久 DecisionRequest**：tool/outer-loop/plan review 共用一张版本化请求表；请求先落库，worker 进入可恢复等待态，不依赖原 Python await 栈；resolve 通过 `version + status` CAS，只允许一个决定生效。
3. **版本化 RunState**：在危险动作前保存 messages、tool proposal、iteration、checkpoint、best output、feedback、steer 和 side-effect cursor；恢复器从数据库重建 continuation。
4. **Durable dispatcher**：submitted/orphaned run 由 attempt claim + lease + heartbeat 管理；expired lease 进入重试，超过上限进入 DLQ。
5. **Side-effect journal**：受支持 managed 边界先写 intent，再执行，凭 provider idempotency key/receipt 记录结果，最后 ack；opaque CLI 明确排除零重复保证。
6. **Evidence Bundle**：ArtifactStore 存大结果，Bundle 以 schema v1 聚合 contract/artifact/change/check/decision/cost/environment/auditor/hash/reproduction command；close 时 canonical serialize + hash，形成不可变 snapshot。
7. **系统审计与通知投递**：治理配置变更写 append-only SystemAuditLog；通知使用 delivery outbox/attempt/backoff/DLQ，并显示“不确定结果”，不宣称 exactly once。

### 3.2 精确文件与测试

| 工作包 | 创建 | 修改 | 必须新增/补强测试 |
|---|---|---|---|
| G2-1 应用事务边界 | `src/tianshu/application/__init__.py`, `application/edicts.py`, `storage/unit_of_work.py`, `storage/outbox_repo.py` | `edict_ops.py`, `bus/event_bus.py`, `storage/migrations.py`, `bootstrap/` 装配；入口 `gateway/edicts_api.py`, `gateway/mcp_server.py`, `gateway/core/edict_bridge.py`, `tools/submit_edict.py`, `tools/schedule_edict.py`, `executor/approvals.py` amend path | `tests/integration/test_edict_idempotency.py`, `test_outbox_recovery.py`, “same key/different hash=409”、所有入口 contract test |
| G2-2 Decision/RunState | `governance/__init__.py`, `governance/decision_service.py`, `models/run_state.py`, `storage/run_state_repo.py` | `executor/approvals.py`, `executor/policy_hook.py`, `executor/orchestrator/loop.py`, `executor/worker.py`, decision API/Bot shared core | `tests/integration/test_decision_restart_recovery.py`, `test_outer_loop_restart_recovery.py`, `test_decision_cas.py`, late/expired decision tests，L0–L3 fault matrix |
| G2-3 Attempt/side effect | `storage/attempt_ledger.py`, `storage/side_effect_journal.py`, dispatcher/reconciler module under `application/` 或 `executor/` | `bus/event_bus.py`, `executor/worker.py`, `scheduler/scheduler.py`, `storage/scheduler_repo.py`, G1 `ExecutionGateway` | `test_claim_lease_recovery.py`, `test_side_effect_idempotency.py`, `test_scheduler_reconcile_dlq.py`；故障点为 effect 前、effect 后 ack 前、lease 过期、重启 |
| G2-4 Planner evidence | `evals/planner_quality.py`（或同职责模型） | `planner/planner.py`, `executor/dag_scheduler.py`, plan persistence/API | `tests/planner/test_plan_amend_metrics.py`, `tests/integration/test_replan_evidence.py`；估时/预算偏差、前后 diff、失败分类 |
| G2-5 Artifact/Evidence | `evidence/__init__.py`, `evidence/models.py`, `evidence/service.py`, `storage/artifact_repo.py`, `gateway/evidence_api.py`, `docs/reference/evidence-bundle-v1.schema.json` | `models/common.py`/`memorial.py`, `auditor/`, `app.py`, storage migration | `tests/evidence/test_bundle.py`, `test_canonical_hash.py`, `test_close_snapshot_immutable.py`, `tests/integration/test_independent_audit_evidence.py`, export/schema round-trip |
| G2-6 System audit/trace/readiness | `auditor/system_log.py`, `storage/system_audit_repo.py`, `gateway/readiness_api.py` | `observability.py`, `app.py`, `gateway/estop_api.py`，凭证/MCP/技能/演化写 API | `tests/audit/test_system_audit_log.py`, `tests/gateway/test_readiness.py`, tracer flush/redaction/correlation chain tests |
| G2-7 Reliable notification | `notifier/delivery_outbox.py`, `notifier/channel_adapter.py`, storage delivery repo | `notifier/notifier.py`, `notifier/channel_registry.py`, `gateway/bot_manager.py` | `tests/notifier/test_delivery_recovery.py`, crash-before/after-send tests、provider idempotency/unknown outcome、silent deadline test、mock adapter plug-in test |
| G2-8 架构门 | 无需新包 | `pyproject.toml` import-linter contracts | `tests/architecture/test_dependency_boundaries.py` 或 import-linter CI；强制 gateway→application→domain/evidence→storage 的单向依赖 |

### 3.3 内部依赖顺序

`G2-1 UoW/outbox` → `G2-2 Decision/RunState` → `G2-3 lease/journal` → `G2-4 planner evidence` → `G2-5 Evidence Bundle` → `G2-6 audit/readiness` 与 `G2-7 notification`。

理由：没有事务化提交不能安全 retry；没有 Decision/RunState 不能恢复 continuation；没有 journal 不能证明不重复有效副作用；Evidence 必须引用上述稳定标识，不能先用松散 JSON 拼页面。

### 3.4 仅本机无法最终证明的验收

- provider 真实 idempotency/receipt 行为需要至少一个真实外部 managed 边界；本机 fake adapter 只能证明协议实现。
- OTel 完整链路需要真实 collector（可本机 Docker 验证），跨部署可观测性仍需外部环境复验。
- “零重复有效副作用”只能对列入兼容矩阵并通过 fault-injection 的边界声明，不能外推到 opaque CLI。

## 4. G3：Desktop Web Productization

### 4.1 最小可交付实现

1. **冻结品牌壳**：沿用现有 Logo，不改 `brand.png`；保留标语、右上角“彩蛋 / 通用 / English / 实时 / 通政”、四组十四部门、左下主题与侧栏收起；只在部门组上方加入“中枢总览”。
2. **生产设计系统**：延续“墨为骨、朱为睛、纸为气”，默认深色；朱砂只用于裁决/阻断/关键 focus；不加入龙纹、金边、厚重纹理或浮夸动效。
3. **统一路由与数据协议**：`/control` 为默认页，`/approvals` 为御书房 canonical，旧 `/` 重定向；所有重页面 lazy；统一 API client 映射 Auth/correlation/status。
4. **统一页面状态**：loading、success-empty、success-data、stale、error、permission-denied、service-unavailable 都有独立可测试呈现；禁止 catch 后返回 `[]/0`。
5. **三张真实页面**：中枢总览调用 aggregate API；敕令详情展示 requested/effective contract、Decision 和 Evidence Bundle；演化中心只显示后端权威门禁/真实路由。
6. **首次使用**：空 DB + mock provider + 默认 persona + 第一个受治理结果可从 Web 跑通。
7. **十四部门收敛**：按路线图职责逐页补齐真实状态；只拆超过约 600 行且确有多职责的页面，不机械按行数拆。
8. **质量证据**：Playwright 三旅程、1280/1440 深浅/折叠视觉回归、axe、键盘/focus/200% zoom、性能预算和浏览器 console gate。

### 4.2 精确文件与测试

| 工作包 | 创建 | 修改 | 必须新增/补强测试 |
|---|---|---|---|
| G3-1 UI 基础 | `web/src/components/governance/{GovernanceContractCard,DecisionPanel}.tsx`, `components/evidence/EvidenceBundlePanel.tsx`, `components/states/PageDataState.tsx`, 相应 test | `theme/palette.ts`, `styles/global.css`, `hooks/useTheme.ts`, `App.tsx`, `AppSidebar.tsx`, `api/client.ts`, 三套 locale | 组件状态矩阵 tests；路由重定向/lazy test；默认深色、深浅/折叠持久化 test；品牌壳逐字 contract test |
| G3-2 Onboarding/contract | `web/src/pages/OnboardingPage.tsx`, onboarding API/hooks | `EdictCreatePage.tsx`, `components/edict/EdictForm.tsx` | `EdictCreatePage.test.tsx`, `web/e2e/fresh-install.spec.ts`；actor 不由客户端提交、requested/effective 回显 |
| G3-3 Control Center | `web/src/pages/ControlCenterPage.tsx`, `hooks/useControlCenter.ts`, `src/tianshu/gateway/control_center_api.py`（或清晰同职责名） | `app.py`, storage query/read model | `web/src/pages/ControlCenterPage.test.tsx`, `tests/gateway/test_control_center.py`；真实口径、排序、无“系统可信/置信度” |
| G3-4 Edict/Evidence | 上述 evidence/governance 组件 | `web/src/pages/EdictDetailPage.tsx`, `api/edicts.ts`, G2 evidence endpoint | `EdictDetailPage.test.tsx`；contained executor unsupported controls、审计/执行者分离、下载与受治理 replay |
| G3-5 Evolution Gate | `web/src/components/evolution/EvolutionGate.tsx`, backend `gateway/evolution_api.py` 或扩展 `universes_api.py` | `UniversePage.tsx`, `EvalsPage.tsx`, `api/universe.ts` | `UniversePage.test.tsx`, backend promotion gate tests；未开真实 challenger 时不得显示灰度进度 |
| G3-6 部门收敛 | 按页面真实职责拆 `features/` 或页面子组件，优先 Persona/Audit/Memory/Universe/Edict | 路线图列出的 14 页及 `PendingToolCallCard.tsx`, `DecreeModal.tsx`, MCP create modal、cost hooks/API | 每个高风险写操作至少一项 page/component test；动态 persona、cost filter contract、MCP 默认 disabled、错误非空态 |
| G3-7 浏览器门禁 | `web/playwright.config.ts`, `web/e2e/{control-center,decision-flow,evolution-gate}.spec.ts`, Page Objects/fixtures、visual baselines | `web/package.json`, `vite.config.ts`, `.github/workflows/ci.yml` | 1280/1440 × dark/light × expanded/collapsed；axe serious/critical=0；console error=0；LCP/初始 JS 基线 |

正式 Web 的测试依赖至少需要：`@testing-library/react`、`@testing-library/jest-dom`、`@testing-library/user-event`、`jsdom`、`@playwright/test`、`@axe-core/playwright`。测试环境不能只靠 snapshot，要验证可见内容、键盘与真实请求状态。

### 4.3 内部依赖顺序

- G3-1 壳/tokens/只读状态组件可在 G2 同步推进。
- G3-2 依赖 G1 mock/fresh-install 和 Governance Contract。
- G3-3/4/5 必须等待 G2 aggregate/evidence/decision/run-state 协议稳定。
- G3-6 在统一 PageDataState/API client 后逐页迁移。
- G3-7 从第一张真实页开始增量建立，最终对三旅程和全壳跑 Gate。

### 4.4 仅本机无法最终证明的验收

- VoiceOver 抽检和“设计 QA 无 P0/P1/P2”需要人工；自动 axe 不能替代屏幕阅读器与用户审批。
- 用户要求最终审批真实页面，因此自动截图与视觉 diff 只能作为候审证据，不能代替用户签字。
- LCP <2.5s 需固定硬件/浏览器/网络环境；本机结果必须记录环境，不能泛化到所有机器。

## 5. G4：Governed Evolution & Executor Neutrality

### 5.1 最小可交付实现

1. **统一候选模型**：`EvolutionCandidate` 包含 kind、source/provenance、base version、candidate version、diff、contract、gates、evidence、routing、rollback 和 lifecycle；memory/skill/policy/persona/code 用 adapter 映射，不能各自另造晋升语义。
2. **统一技能供应链**：API/agent/reviewer/curator/zip/CLI 全部走 `SkillInstallService`；恶意 SKILL.md 不得绕开现有 Installer/Guard；写入先成为候选，公开 Demo 只晋升 skill。
3. **权威 Gate evaluator**：回归、安全、样本、证据、预算、人工 veto 全部返回 `promotion_allowed + blocking_gates`；所有 switch/promote API 必须调用同一 PromotionService，不能直接 manager.switch。
4. **真实 challenger 路由**：每个 run 固化 assignment 和实际 effective overlay；10% 是真实候选行为，不只是标签；run evidence 记录 champion/candidate 和输入版本；回滚立即停止新流量并恢复 champion。
5. **Executor neutrality**：Native 和 OpenHands SDK/Agent Server 使用 G1 同一 adapter protocol、Governance Contract、结构化事件与 Evidence schema；mandatory capability 缺失 fail closed。Keqing Codex/Claude CLI 保持 contained + experimental。
6. **收益/成本证据**：记忆和画像做 paired benchmark；Prompt 各层显式预算与裁剪证据；成本输出预测区间、实际值、超额量和 enforcement mode。

### 5.2 精确文件与测试

| 工作包 | 创建 | 修改 | 必须新增/补强测试 |
|---|---|---|---|
| G4-1 Candidate domain | `models/evolution_candidate.py`, `evolution/{candidate_service,gates,promotion}.py`, `evolution/adapters/{memory,skill,policy,persona,code}.py`, storage repo/migration | `universe/model.py`, `gateway/universes_api.py` | `tests/evolution/test_candidate_schema.py`, `test_gate_evaluator.py`, `test_promotion_fail_closed.py`, rollback/version CAS tests |
| G4-2 Skill supply chain | `skills/install_service.py` | `gateway/skills_api.py`, `skills/installer.py`, `skills/loader.py` write entry points、`tools/skill_tools.py`, `skills/reviewer.py`, `skills/curator.py`，CLI 若新增安装命令 | `tests/skills/test_install_service_security.py`；逐入口恶意路径/内容不能绕过、provenance/version/rollback round-trip |
| G4-3 Challenger routing | `universe/router.py` | `universe/manager.py`, `universe/evolver.py`, executor/prompt/resource resolution、Memorial attribution/storage/API | `tests/universe/test_challenger_routing.py`（统计分布 + 实际 overlay 断言）, `test_promotion_gates.py`, restart-stable assignment、p95 rollback harness |
| G4-4 Managed adapter | `executor/adapters/openhands.py`, `tests/compat/executor_adapter/` fixtures/suite, `docs/usage/executor-adapter.md` | G1 `executor/adapters/protocol.py`, Native adapter, Keqing classification/API | contract tests 同时参数化 Native/OpenHands；mandatory negative dispatch；action/decision/budget/workspace/network/receipt/durable fault tests |
| G4-5 Memory/profile ROI | `tests/evals/memory_recall/` 数据集、paired runner/metrics，rebuild job/status model | `memory/manager.py`, `memory/fts.py`, `gateway/memory_api.py`, `persona/prompt_builder.py`, `persona/profile_synthesizer.py` | 空库/损坏/schema upgrade rebuild；provenance/delete；每层 budget；paired quality/task-result/token-cost benchmark |
| G4-6 Cost governance | `cost/forecast.py`, forecast/enforcement evidence model | `cost/manager.py`, `cost/models.py`, `gateway/cost_api.py`, Evidence Bundle、`web/src/pages/CostDashboardPage.tsx` | `tests/cost/test_forecast_calibration.py`, `tests/integration/test_budget_enforcement_modes.py`；预测区间覆盖、实际超额、三种 mode 文案/证据 |

### 5.3 内部依赖顺序

`Candidate schema + Gate evaluator` → `SkillInstallService` → `PromotionService` → `真实 router` → `OpenHands managed compatibility` → `memory/profile ROI` 与 `cost mode` → G4 全链证据。

真实 router 必须晚于 G2 RunState/Evidence 和 G3 权威 UI：否则产生了真实候选流量，却没有耐重启归因、阻断 UI 或回滚证据。外部 adapter 必须晚于 G1 ExecutionGateway/WorkspaceService 和 G2 side-effect/evidence 协议。

### 5.4 仅本机无法最终证明的验收

- **Native + 1 external managed** 必须连接真实 OpenHands SDK/Agent Server；mock server 只能验证客户端契约，不能让 capability 位升级为 managed。
- OpenHands action interception、decision bridge、network/workspace control、receipt 和 durable semantics 需要用明确版本、部署方式和故障注入记录；没有真实服务时必须把测试标为 external/未通过，不能用 skip 当成功。
- 10% 分布与 p95 回滚可在本机做定义环境的统计测试，但生产声明需记录样本数、随机策略、机器和存储环境。
- memory/profile “收益”需要固定任务集与 paired baseline；单次主观好评不能构成 G4 证据。

## 6. G5：Open-source Launch & Ecosystem

### 6.1 最小可交付实现

1. **三个黄金 Demo**：每个 Demo 都能一条命令在 mock/frozen 环境重复，产出机器可校验 Evidence Bundle；每个附危险动作负向集。
2. **Executor SDK**：只暴露稳定 protocol、capability builder、event/evidence helpers 和 compatibility runner；模板 adapter 在一天内可改成新 adapter。
3. **可复现制品**：tag 构建相同版本的 wheel、OCI image、SBOM、checksums、attestation 与 release notes；安装使用锁定依赖，镜像非 root。
4. **发行组合**：明确 core/server/all（含 MCP）内容和 smoke；wheel/container fresh install 均含默认 persona、builtin skills、Web。
5. **仓库与社区卫生**：移除跟踪的 IDE/cache/temp；补 NOTICE/license report、行为准则、Issue/PR 模板；history secret scan 和 license gate。
6. **外部验证包**：三位/三个隔离环境的执行记录、版本、失败与修正全部进入 `docs/launch/demo-evidence/`，不手填“已通过”。

### 6.2 精确文件与测试

| 工作包 | 创建 | 修改 | 必须新增/补强测试 |
|---|---|---|---|
| G5-1 黄金 Demo | `examples/leave-it-running/`, `examples/governed-evolution/`, `examples/same-contract-multiple-executors/`, `docs/launch/demo-evidence/`, 共用 runner/schema checker | README/usage docs、mock fixtures | 每 Demo 10-run harness；9/10 无人工修复；10/10 dangerous negative；bundle hash/schema/check reproduction |
| G5-2 SDK/kit | `src/tianshu/sdk/`, `templates/executor-adapter/`, `tests/compat/`, `docs/usage/compatibility-kit.md` | `pyproject.toml`, protocol exports | template smoke、third-party mock adapter compatibility、mandatory capability negative、API stability tests |
| G5-3 Packaging | `.github/workflows/release-smoke.yml`, `.github/workflows/release.yml`, packaging scripts/config，必要的 `src/tianshu/resources/` | `pyproject.toml`, `uv.lock`, `Dockerfile`, `.dockerignore` | wheel clean-venv install、core/server/all imports、resource presence、container non-root/readiness/Web/default persona/skill smoke |
| G5-4 Supply chain | secret/license/SBOM config，`NOTICE`, third-party license report | release workflow、SECURITY/CONTRIBUTING | gitleaks/trufflehog history scan、dependency/code scan、license allowlist、Syft SBOM、provenance attestation/checksum verify |
| G5-5 社区/仓库卫生 | `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`, `CODE_OF_CONDUCT.md` | `.gitignore`, `README.md`, `README.en.md`, `CHANGELOG.md`, `docs/launch/checklist.md` | repo hygiene script 断言不跟踪 `.idea`, `web/.vite`, caches/db/log/temp；双语链接与版本/capability truth tests |

### 6.3 三个 Demo 的最小真实内容

- `leave-it-running`：Native/managed executor，危险 tool 进入持久 Decision，kill/restart/resolve/continue，不重复受支持副作用；输出 Evidence Bundle、成本和恢复点。
- `governed-evolution`：只用 skill candidate；安装 guard → paired eval → blocking gates → 决定 → 10% real route → evidence → promote/rollback。代码候选只展示被阻断，绝不自动晋升。
- `same-contract-multiple-executors`：同一 requested contract 分别发 Native/OpenHands；显示各自 effective contract、capability 差异和 fail-closed；Keqing 仅作为 contained 对照，不计 managed 数量。

### 6.4 内部依赖顺序

`G3/G4 Gate 全绿` → `SDK/compat kit` → `三个 Demo` → `packaging/container` → `supply-chain/release workflow` → `三外部环境复验` → `最终文档/素材冻结`。

Demo 应先使用冻结版本和机器可校验证据，再录视频；不能先录原型画面后补 API。发布 workflow 最后只组装已经通过 smoke 的内容，不应在 tag 时首次发现 wheel 缺资源或镜像以 root 运行。

### 6.5 仅本机无法最终证明/执行的验收

- **至少三个全新外部环境独立完成 quickstart/Demo**：本机多个临时目录或三个容器不能替代“外部独立”。可先生成验证脚本与空白证据模板，但 Gate 状态必须保持未通过，直到外部记录回收。
- GitHub 仓库 Public、branch protection、About/Topics/social preview、tag/release 发布属于外部状态变更；没有维护者授权不能执行。
- 真实发布签名/attestation 需要 GitHub Actions OIDC 和仓库权限；本地只能验证 workflow/schema/unsigned dry run。
- 7 天真实成本基线、真实桌面 Demo 录制、社区响应窗口都需要时间和维护者参与，不能由一次本机测试伪造。
- 真实 OpenHands managed compatibility 是 G4/G5 共同外部前提；没有它，第三个 Demo 和“Native + 1 external managed”均不能标绿。

## 7. 推荐总依赖图与连续实施切片

```text
G1 contracts/gateway/workspace/fresh install
  -> G2 UoW/outbox
  -> G2 durable Decision + RunState
  -> G2 lease + side-effect journal
  -> G2 Evidence/Audit/Notification
  -> G3 real control/detail/evolution pages
  -> G3 all-department states + Web quality gate
  -> G4 candidate/gates/install service
  -> G4 real challenger routing
  -> G4 OpenHands managed compatibility
  -> G4 memory/cost evidence
  -> G5 SDK + demos
  -> G5 reproducible release + external validation
```

建议连续实施但逐 Gate 自动卡口：

1. **G2-A**：应用服务、UoW/outbox、idempotency。
2. **G2-B**：DecisionRequest/RunState/lease/journal + restart fault matrix。
3. **G2-C**：Evidence/SystemAudit/Notification + G2 gate report。
4. **G3-A**：正式 UI 壳、统一状态、测试基础、Onboarding。
5. **G3-B**：三张真实页及十四部门收敛。
6. **G3-C**：E2E/A11y/visual/performance gate。
7. **G4-A**：统一候选/技能供应链/权威门禁。
8. **G4-B**：真实 challenger/晋升/回滚。
9. **G4-C**：OpenHands managed、记忆 ROI、成本模式。
10. **G5-A**：SDK + 三 Demo。
11. **G5-B**：wheel/container/release/supply chain/community files。
12. **G5-C**：外部验证证据回收与最终候审包。

每个切片都应遵循 TDD：先写会失败的 contract/fault/UI test，再实现；完成后运行局部 test、阶段全集、lint/type/import boundary，并保存真实 Gate 输出。不能用 roadmap 勾选、mock-only 测试或静态截图代替运行证据。

## 8. 最终验收时必须分开的三类结论

1. **本机自动已证明**：明确命令、版本、通过数、Evidence digest、容器/浏览器环境。
2. **本机实现已完成但外部待证明**：真实 OpenHands、三外部环境、GitHub attestation/发布权限、真实成本周期。
3. **需要用户人工审批**：正式页面视觉/交互、VoiceOver 抽检、最终宣发文案与公开动作。

只有三类全部满足相应 Gate，才能把能力矩阵从 Planned/Experimental 提升；尤其不能在 G4/G5 外部项未完成时宣称“自进化闭环已成立”或“正式 1.0 已可发布”。
