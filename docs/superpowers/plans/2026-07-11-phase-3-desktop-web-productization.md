# Phase 3 Desktop Web Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已通过 G0 审批的桌面网页方向迁移为正式产品：保留天枢现有品牌壳和四组十四部门，以真实 G1/G2 API 实现首启引导、中枢总览、敕令详情与演化门禁，并以自动化 G3 Gate 和最终用户人工审批证明可用性。

**Architecture:** 保留 React 18、Ant Design、React Router 与 TanStack Query；建立单一 API 错误协议、只读身份上下文、统一页面状态和治理/证据/演化组件。正式页面只消费后端权威的 AuthContext、Governance Contract、RunState、DecisionRequest、Evidence Bundle、readiness 与 promotion gate；浏览器不提交 actor、不本地推断门禁、不把错误折叠为空数据。G3-A 可在 G2 完成前实现壳层与只读组件，G3-B 三张真实页面必须在对应 G2 contract tests 通过后开始，G3-C 对真实 demo stack 执行桌面 E2E/A11y/视觉/性能门禁。

**Tech Stack:** React 18、TypeScript 5.6、Ant Design 5、React Router 6、TanStack Query 5、Axios、Vitest 4、Testing Library、MSW、Playwright、axe-core、FastAPI、Pydantic v2、SQLite、pytest、GitHub Actions。

## Global Constraints

- 只开发和验收桌面 Web；自动测试视口仅为 `1440 × 1024` 与 `1280 × 900`。不得移植 G0 原型中的 mobile drawer、手机断点或移动端导航。
- `web/public/brand.png` 内容不得修改；冻结 SHA-256 为 `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`。
- 顶部品牌壳在三种语言模式下均保留：`天枢`、`成功只有一个——按照自己的方式，去度过人生。`、`彩蛋 / 通用 / English / 实时 / 通政`。状态真值通过圆点、tooltip 与可访问名称表达，不能为保留标签而谎报在线或健康。
- 左侧顺序固定为 `中枢总览`，然后四组十四部门：
  - 敕令：`御书房 / 文书房`
  - 政要：`内阁 / 廷议 / 都察院 / 权印司`
  - 百官：`百官阁 / 文渊阁 / 位面 / 考成`
  - 外朝：`藏兵阁 / 鸿胪寺 / 通政司 / 户部账房`
- 左下角必须同时保留主题切换与侧栏收起/展开；无持久偏好时默认深色，深/浅色与展开/收起均持久化并进入回归测试。
- 中文用户可见治理动作统一为 `裁决 / 自动裁决 / 待裁决 / 等待裁决 / 查看并裁决`；不得出现 `批红 / 朱批 / 司礼监代批`。内部 `Decree` 类型、旧 API alias 和数据库兼容名不强改。
- 视觉方向固定为“墨为骨、朱为睛、纸为气”：暖纸白、烟墨、黛青和低饱和器物色为主体；朱砂只用于裁决、阻断、当前选中与键盘 focus。不得新增霓虹、强渐变、大面积金色、龙纹、宫殿纹样、玻璃拟态、重阴影或夸张动效。
- 标题用宋体体系、正文用黑体体系、数字/ID/金额用等宽字体；正文不小于 13px。交互动画控制在 120–180ms，并尊重 `prefers-reduced-motion`。
- 接口失败不得返回或显示伪造的 `[] / 0 / 未找到`；loading、empty、data、stale、error、permission-denied、service-unavailable 必须是不同状态。
- 三张核心页面不得以 `prototypes/tianshu-agent-os/src/data/mockData.js` 作为生产数据源。该目录只提供已审批的视觉、文案和交互参考。
- G2 的 DecisionRequest、RunState、Evidence Bundle、readiness 和权威演化门禁未通过各自后端 contract tests 时，不得开始对应写操作，也不得把页面标记为“真实”。
- 每个任务严格执行 RED → GREEN → 局部回归 → 阶段回归 → commit。任何 `skip`、`fixme`、只更新 snapshot 或以静态截图代替运行证据都不能使 Gate 通过。
- 最终自动 G3 Gate 通过后仍只标记为 `automation_passed_pending_user_approval`；必须把真实页面交给用户人工审批，用户明确批准后才能标记 G3 `passed`。

## Product Differentiation the UI Must Prove

Phase 3 不用宣传口号代替产品差异；三张核心页面必须让用户直接看见并操作以下闭环：

1. **可治理：** 执行前展示 requested/effective Governance Contract、执行器能力与权限边界；危险动作进入带理由、版本和恢复点的持久裁决。
2. **可验证：** 结果不是一段聊天文本，而是带 canonical digest、产物、变更、检查、策略、成本、环境和独立审计结论的 Evidence Bundle。
3. **持续成长但不失控：** 位面候选必须经过回归、安全、样本、证据、回滚和人工晋升强门；不把任意自改或虚假 Canary 包装成“进化”。
4. **故障后仍可治理：** 中枢和详情显示 RunState、attempt、恢复/不确定状态与 durable decision；不能因 WebSocket 在线就宣称系统可靠。
5. **组织化 Agent OS：** 四组十四部门是长期稳定的信息架构和职责边界，不退化为通用聊天框、低代码画布或泛化“应用/知识/设置”分类。

每个差异点至少有一项后端 contract test、一项页面 test 和一条可演示的 Playwright 证据；没有对应运行证据的宣传语不得进入页面。

## Frozen Route and API Contracts

### Desktop routes

| Route | Product meaning | Compatibility rule |
| --- | --- | --- |
| `/` | legacy entry | `replace` redirect to `/control`；不能产生双历史记录 |
| `/control` | 中枢总览 | 新 canonical 首页 |
| `/approvals` | 御书房 | 保持 canonical，不再用 `/` 高亮 |
| `/edicts/create` | 颁发敕令 | 首次创建前展示 requested/effective contract |
| `/edicts/:edictId` | 敕令详情 | 真实 RunState、Decision、Evidence |
| `/universes` | 位面/演化中心 | 真实权威门禁；不显示虚假 challenger 流量 |
| 其余现有部门路由 | 原十四部门 | 路径和深链保持可用 |

### Required backend contracts

G3 只通过以下稳定接口消费权威数据。若 G1/G2 实现使用了其他内部路径，在 gateway 层增加兼容 alias；不得在每个页面各写一套转换。

| Endpoint | Authority | Required semantics |
| --- | --- | --- |
| `GET /api/auth/me` | G1 AuthContext | 返回 principal id、display name、roles、scopes、runtime mode；只读 |
| `GET /health/ready` | G1/G2 readiness | 200 ready，503 unavailable；带逐项检查和 correlation id，不泄密 |
| `GET /api/onboarding/state` | G3 onboarding aggregate | 是否首启、mock provider、default persona、first governed result 的完成状态 |
| `POST /api/onboarding/mock-provider` | G1 mock provider | 仅 trusted-local/demo 且有配置权限；显式启用，不得静默回退 |
| `POST /api/onboarding/default-persona` | G1 seed service | 幂等创建默认 persona；重复请求返回同一结果 |
| `GET /api/control-center?window=24h` | G2 read model | 运行中、待裁决、预算、Evidence 完整率、异常/恢复及其口径 |
| `GET /api/edicts/{id}/governance-contract` | G1/G2 | requested/effective contract、manifest 与 unsupported controls |
| `GET /api/edicts/{id}/run-state` | G2 | durable state/version、timeline、attempt、restore point |
| `GET /api/decisions?edict_id={id}` | G2 | bounded durable DecisionRequest 列表及 version/expiry/status；可附加 status/kind filters |
| `POST /api/decisions/{id}/resolve` | G2 DecisionService | `{action, reason, expected_version, payload?}`；actor 由服务端产生；CAS 冲突 409 |
| `GET /api/edicts/{id}/evidence` | G2 EvidenceService | schema version、digest、artifacts、checks、policy、cost、environment、auditor |
| `GET /api/evidence/{bundle_id}/download` | G2 ArtifactStore | 下载不可变 close snapshot；保留 digest/content type |
| `POST /api/evidence/{bundle_id}/replay` | G2 application service | body 为 `{idempotency_key}`；创建新的受治理执行，不直接执行 reproduction command |
| `GET /api/evolution/candidates/{id}/gate` | G3/G4 authority | `promotion_allowed`、`blocking_gates`、sample/threshold/baseline/delta/diff/rollback/routing truth |
| `POST /api/evolution/candidates/{id}/decision` | G2 DecisionService | reject/observe/promote/override；reason + expected_version；override 是独立高风险裁决 |

统一错误响应为 `{code, message, correlation_id, details?}`。前端映射固定为：401 → `auth-required`，403 → `permission-denied`，404 → `not-found`，409/412 → `stale`，503 → `service-unavailable`，其余非 2xx → `error`。响应体 `success:false` 同样必须 reject，不能继续渲染 success。

---

## Task 1: Freeze G3 contracts and install the test foundation

**Files:**

- Create: `web/vitest.config.ts`
- Create: `web/src/test/setup.ts`
- Create: `web/src/test/render.tsx`
- Create: `web/src/test/server.ts`
- Create: `web/src/test/fixtures.ts`
- Create: `web/src/contracts/frozenShell.ts`
- Create: `web/src/test/contract/frozenShell.test.ts`
- Create: `web/scripts/record-pre-g3-baseline.mjs`
- Create: `web/performance-baseline.json`
- Modify: `web/vite.config.ts`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/tsconfig.json`

**Interfaces:**

- Consumes the frozen shell and API tables above.
- Produces `renderWithAppProviders()`, a fresh QueryClient per test, MSW lifecycle, jest-dom matchers, and deterministic fixtures shared by component tests.

### 1.1 RED — add tests before dependencies/config exist

- [ ] Add `frozenShell.test.ts` that reads `web/public/brand.png`, verifies the exact SHA-256, and imports/verifies the immutable header/sidebar string arrays from `contracts/frozenShell.ts`.
- [ ] Add a smoke render that needs `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom`, and `msw`.
- [ ] Before any production component change, make the production build emit a Vite manifest and record the current commit, Node/Chrome environment, initial JS bytes, shell LCP and sidebar route-interaction delay in `performance-baseline.json`. The recorder launches `vite preview`, uses Playwright route fixtures for current APIs, and must fail if the page has no LCP/interaction sample; do not type expected values by hand.
- [ ] Run:

```bash
cd web
npm test -- --run src/test/contract/frozenShell.test.ts
```

Expected: FAIL because test environment/helpers and error mapper do not exist.

### 1.2 GREEN — add only the shared test harness

- [ ] Add the Testing Library/MSW dependencies plus `@playwright/test` for the baseline recorder, install desktop Chromium, and add scripts:

```json
{
  "test:run": "vitest run",
  "test:watch": "vitest",
  "test:coverage": "vitest run --coverage",
  "baseline:record": "npm run build && node scripts/record-pre-g3-baseline.mjs"
}
```

- [ ] Configure jsdom, `src/test/setup.ts`, CSS handling, and `restoreMocks: true`; do not make snapshot tests the primary assertion style.
- [ ] Implement deterministic fixture factories for auth, readiness, control center, governance contract, decision, evidence and evolution gate. Factories must allow explicit empty/error/stale variants.
- [ ] Put only the frozen brand/header/navigation strings and logo digest in `contracts/frozenShell.ts`; Task 3 will make the production shell consume this same source.
- [ ] Keep component tests independent: no shared mutable QueryClient, localStorage, MSW handler, fake timer or locale may leak between tests.
- [ ] Run `cd web && npm run baseline:record`; inspect the JSON and commit it before Task 2 changes production UI.

### 1.3 Verify and commit

- [ ] Run `cd web && npm run test:run && npm run typecheck && npm run lint`.
- [ ] Commit:

```bash
git add web/package.json web/package-lock.json web/tsconfig.json web/vitest.config.ts web/vite.config.ts web/src/test web/src/contracts/frozenShell.ts web/scripts/record-pre-g3-baseline.mjs web/performance-baseline.json
git commit -m "test(web): establish the G3 contract harness"
```

---

## Task 2: Implement the restrained New Chinese design foundation

**Files:**

- Modify: `web/src/theme/palette.ts`
- Modify: `web/src/theme/index.ts`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/hooks/useTheme.ts`
- Modify: `web/src/components/common/PageContainer.tsx`
- Create: `web/src/components/common/PageHeader.tsx`
- Create: `web/src/components/common/MetricCard.tsx`
- Create: `web/src/components/common/StatusLegend.tsx`
- Create: `web/src/components/common/designFoundation.test.tsx`
- Create: `web/src/hooks/useTheme.test.tsx`

**Interfaces:**

- Consumes existing `palettes` as the only color source.
- Produces stable typography, spacing, border, surface, focus, motion and density rules for every later page.

### 2.1 RED — encode the approved visual rules

- [ ] Test no stored preference defaults to `dark`, while stored `light` and `dark` survive remount.
- [ ] Test `PageHeader` has one semantic `h1`, optional description, metadata and action slots; heading uses the title token, not inline hard-coded colors.
- [ ] Test `MetricCard` requires `label`, `value`, `definition`, `window`, and `sourceLabel`; a bare unqualified “可信度/置信度” metric cannot be rendered.
- [ ] Test risk, warning and success states always expose visible text/icon in addition to color.
- [ ] Run:

```bash
cd web
npm test -- --run src/components/common/designFoundation.test.tsx src/hooks/useTheme.test.tsx
```

Expected: FAIL because the primitives and dark default are absent.

### 2.2 GREEN — extend the existing palette without a second color system

- [ ] Keep current warm light and smoky dark palette; add only semantic aliases needed by the components (`surface`, `surfaceRaised`, `focusRing`, `decision`, `blocked`) and expose them through `--ts-*` variables.
- [ ] Standardize 8px spacing rhythm, 8/12px radii, one-pixel borders, restrained shadow only in light floating layers, 13px minimum body text and tabular numbers.
- [ ] Keep Ant Design token integration; do not copy the prototype's 2,043-line stylesheet into production.
- [ ] Set default theme to dark only when `tianshu-theme` is absent; preserve explicit user preference.
- [ ] Add reduced-motion CSS that removes translation/scale animations while retaining state change visibility.

### 2.3 Verify and commit

- [ ] Run focused tests, then `cd web && npm run test:run && npm run build`.
- [ ] Commit:

```bash
git add web/src/theme web/src/styles/global.css web/src/hooks/useTheme.ts web/src/hooks/useTheme.test.tsx web/src/components/common
git commit -m "feat(web): establish the restrained Tianshu design foundation"
```

---

## Task 3: Freeze the desktop shell, routes, and fourteen-department navigation

**Files:**

- Create: `web/src/navigation/departments.tsx`
- Create: `web/src/router/AppRoutes.tsx`
- Create: `web/src/hooks/useSidebarState.ts`
- Create: `web/src/components/layout/AppShell.test.tsx`
- Create: `web/src/router/AppRoutes.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/layout/AppHeader.tsx`
- Modify: `web/src/components/layout/LocaleSwitcher.tsx`
- Modify: `web/src/components/common/ConnectionIndicator.tsx`
- Modify: `web/src/components/common/HealthDot.tsx`
- Modify: `web/src/components/layout/AppSidebar.tsx`
- Modify: `web/src/components/layout/AppLayout.module.css`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

- Produces a single `DEPARTMENT_GROUPS` definition consumed by sidebar and contract tests.
- Produces `/control` canonical route, `/` replace redirect, `/approvals` canonical 御书房 route and lazy route boundaries.

### 3.1 RED — prove exact shell fidelity

- [ ] In each locale mode, assert the header contains exact `天枢`, exact quote, and the five visible labels in order: `彩蛋通用English实时通政`.
- [ ] Assert health/connection degradation changes accessible state and dot tone without replacing the frozen `实时`/`通政` labels.
- [ ] Assert the sidebar exposes exactly 15 links: `中枢总览` plus four frozen groups/fourteen departments; `百官图` must not occur.
- [ ] Assert theme and collapse controls stay at bottom, work expanded and collapsed, have accessible names/tooltips, and persist with `tianshu-sidebar-collapsed`.
- [ ] Assert `/` redirects with `replace` to `/control`, `/approvals` remains 御书房, old deep links resolve, and every page except the small control bootstrap is route-level lazy.
- [ ] Run:

```bash
cd web
npm test -- --run src/components/layout/AppShell.test.tsx src/router/AppRoutes.test.tsx
```

Expected: FAIL because `/control`, persistent collapse, frozen cross-locale header and lazy route map do not exist.

### 3.2 GREEN — make the current shell match the approved G0 shell

- [ ] Move only navigation metadata into `departments.tsx`; preserve existing icons and routes.
- [ ] Insert `中枢总览` above, not inside, the department groups. Map 御书房 to `/approvals`; remove the current `/` selected-key alias.
- [ ] Keep `web/public/brand.png` byte-for-byte unchanged. Make the brand lockup navigate to `/control` and give the link a useful accessible name.
- [ ] Keep shell brand marks fixed while page content follows the selected locale. This guarantees the user-requested quote and five labels remain present after switching language.
- [ ] Store sidebar state locally; do not add mobile drawer markup, hamburger buttons or viewports below 1280.
- [ ] Replace synchronous page imports with `React.lazy`, one shared route fallback and a route error boundary that uses the unified state component added in Task 4.

### 3.3 Verify and commit

- [ ] Run focused tests, full Web tests, typecheck and build.
- [ ] Confirm `shasum -a 256 web/public/brand.png` still prints the frozen digest.
- [ ] Commit:

```bash
git add web/src/App.tsx web/src/navigation web/src/router web/src/hooks/useSidebarState.ts web/src/components/layout web/src/components/common/ConnectionIndicator.tsx web/src/components/common/HealthDot.tsx web/src/i18n/locales
git commit -m "feat(web): freeze the desktop shell and department routes"
```

---

## Task 4: Unify API errors, AuthContext, queries, and page data states

**Files:**

- Create: `web/src/api/errors.ts`
- Create: `web/src/api/response.ts`
- Create: `web/src/api/auth.ts`
- Create: `web/src/test/contract/apiErrors.test.ts`
- Create: `web/src/auth/AuthContext.tsx`
- Create: `web/src/auth/AuthContext.test.tsx`
- Create: `web/src/components/states/PageDataState.tsx`
- Create: `web/src/components/states/PageDataState.test.tsx`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/api/health.ts`
- Modify: `web/src/api/providers.ts`
- Modify: `web/src/api/cost.ts`
- Modify: `web/src/api/memory.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/api/types.ts`
- Test: `tests/gateway/test_auth.py`
- Test: `tests/gateway/test_decision_actor.py`

**Interfaces:**

- Produces `ApiError { kind, status, code, message, correlationId, details }` and `unwrapApiResponse<T>()`.
- Produces read-only `AuthContextValue { principal, roles, scopes, runtimeMode }` from `GET /api/auth/me`.
- Produces `PageDataState` variants: `loading | empty | data | stale | error | permission-denied | service-unavailable`.

### 4.1 RED — reproduce current false-empty and forgeable-actor behavior

- [ ] Test Axios credentials/correlation id propagation, structured `success:false` rejection, and the status mapping table.
- [ ] Test `getSupervisionReports`, providers, cost, memory, policy stats and other page-local wrappers preserve errors instead of returning fallback arrays/zeros.
- [ ] Test each `PageDataState` has a heading, explanation, correlation id when present, retry action when safe, and `role=status`/`role=alert` as appropriate.
- [ ] Test decision create/resolve bodies reject a client-supplied `actor` with 422; the persisted actor must equal request AuthContext.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_auth.py tests/gateway/test_decision_actor.py -q
cd web
npm test -- --run src/test/contract/apiErrors.test.ts src/auth/AuthContext.test.tsx src/components/states/PageDataState.test.tsx
```

Expected: FAIL because API wrappers swallow failures, AuthContext/PageDataState do not exist, and current decision types accept `actor`.

### 4.2 GREEN — one truthful client boundary

- [ ] Configure Axios `withCredentials:true`; parse `X-Correlation-ID` and structured error bodies. Global notification may summarize mutations, but pages must still receive the typed error.
- [ ] Replace local `fetchJson()` wrappers with `apiClient`; use `unwrapApiResponse()` once at the API boundary.
- [ ] Make queries retain previous data as `stale` only when a refresh fails; first-load failures render error/unavailable/permission states.
- [ ] Remove `actor` from `DecreeCreateRequest`, `ToolDecisionRequest` and every Web mutation. AuthContext is used for display/authorization hints only.
- [ ] Add the read-only server identity endpoint or the gateway alias required by the frozen contract; secure-remote and trusted-local must return the same response shape.

### 4.3 Verify and commit

- [ ] Run focused backend/Web tests, then `uv run pytest tests/gateway -q` and `cd web && npm run test:run && npm run typecheck && npm run lint`.
- [ ] Commit:

```bash
git add src/tianshu/gateway/auth.py src/tianshu/models tests/gateway/test_auth.py tests/gateway/test_decision_actor.py web/src/api web/src/auth web/src/components/states web/src/App.tsx
git commit -m "feat(web): make identity and data states explicit"
```

---

## Task 5: Build reusable governance, evidence, and evolution components

**Files:**

- Create: `web/src/components/governance/GovernanceContractCard.tsx`
- Create: `web/src/components/governance/GovernanceContractCard.test.tsx`
- Create: `web/src/components/governance/DecisionPanel.tsx`
- Create: `web/src/components/governance/DecisionPanel.test.tsx`
- Create: `web/src/components/evidence/EvidenceBundlePanel.tsx`
- Create: `web/src/components/evidence/EvidenceBundlePanel.test.tsx`
- Create: `web/src/components/evolution/EvolutionGate.tsx`
- Create: `web/src/components/evolution/EvolutionGate.test.tsx`
- Create: `web/src/api/governance.ts`
- Create: `web/src/api/evidence.ts`
- Create: `web/src/api/evolution.ts`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

```ts
type DecisionAction = "approve" | "reject" | "amend" | "retry" | "cancel" | "observe" | "promote" | "override";

interface DecisionSubmission {
  decisionRequestId: string;
  action: DecisionAction;
  reason: string;
  expectedVersion: number;
  payload?: Record<string, unknown>;
}

interface EvolutionGateView {
  promotionAllowed: boolean;
  blockingGates: Array<{ code: string; label: string; current: number | null; required: number | null; evidenceUri: string | null }>;
  challengerRouting: { enabled: boolean; realTraffic: boolean; samples: number | null };
}
```

- `GovernanceContractCard` renders requested/effective diff and capability truth.
- `DecisionPanel` never infers actor or success; it submits reason + expected version and locks from the returned durable status.
- `EvidenceBundlePanel` separates executor output from independent auditor conclusion.
- `EvolutionGate` treats backend `promotionAllowed` as authoritative.

### 5.1 RED — component state matrices

- [ ] Governance tests cover managed vs contained executor, mandatory mismatch, advisory gap and unsupported control. Contained CLI must not show “逐工具裁决” or “硬成本上限”。
- [ ] Decision tests cover empty trimmed reason, pending submit, durable resolved lock, expired request, 409 stale/CAS refetch, permission denied and retry after network failure.
- [ ] Evidence tests cover artifact/check/policy/cost/environment/auditor sections, missing mandatory evidence, digest/download and governed replay warning.
- [ ] Evolution tests cover `18/50` disabled promotion, blocking reasons, separate override action, reason required, and no gray progress when real routing is false.
- [ ] Run:

```bash
cd web
npm test -- --run src/components/governance/GovernanceContractCard.test.tsx src/components/governance/DecisionPanel.test.tsx src/components/evidence/EvidenceBundlePanel.test.tsx src/components/evolution/EvolutionGate.test.tsx
```

Expected: FAIL because the four components do not exist.

### 5.2 GREEN — implement presentational components only

- [ ] Keep network state in page hooks; components receive typed data and callbacks.
- [ ] Use semantic HTML (`section`, headings, description lists, tables) and visible labels; status must not rely on color.
- [ ] Use朱砂 only for current decision, block, selected state and focus. Ordinary primary navigation/actions use neutral ink treatment.
- [ ] Do not execute reproduction commands in the browser. Replay calls only the governed replay endpoint and navigates to the returned new edict.

### 5.3 Verify and commit

- [ ] Run:

```bash
cd web
npm test -- --run src/components/governance/GovernanceContractCard.test.tsx src/components/governance/DecisionPanel.test.tsx src/components/evidence/EvidenceBundlePanel.test.tsx src/components/evolution/EvolutionGate.test.tsx
npm run test:run
npm run typecheck
npm run lint
```
- [ ] Commit:

```bash
git add web/src/components/governance web/src/components/evidence web/src/components/evolution web/src/api/governance.ts web/src/api/evidence.ts web/src/api/evolution.ts web/src/i18n/locales
git commit -m "feat(web): add governance evidence and evolution primitives"
```

---

## Task 6: Implement first-run onboarding and Governance Contract creation

**Gate:** G1 mock provider, packaged default persona, AuthContext, readiness and Governance Contract preview tests must be green before this task.

**Files:**

- Create: `src/tianshu/gateway/onboarding_api.py`
- Create: `src/tianshu/application/onboarding.py`
- Modify: `src/tianshu/app.py`
- Create: `tests/gateway/test_onboarding.py`
- Create: `tests/integration/test_onboarding_idempotency.py`
- Create: `web/src/api/onboarding.ts`
- Create: `web/src/hooks/useOnboarding.ts`
- Create: `web/src/pages/OnboardingPage.tsx`
- Create: `web/src/pages/OnboardingPage.test.tsx`
- Modify: `web/src/pages/EdictCreatePage.tsx`
- Modify: `web/src/components/edict/EdictForm.tsx`
- Create: `web/src/pages/EdictCreatePage.test.tsx`
- Modify: `web/src/router/AppRoutes.tsx`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

- `GET /api/onboarding/state` derives steps from persisted truth; the browser does not keep a separate completion flag.
- Mock provider and default persona operations are idempotent application-service commands.
- Edict preview returns requested/effective contract before submit; submit body contains no actor.

### 6.1 RED — onboarding truth and permission tests

- [ ] Backend tests cover empty DB, already configured install, interrupted resume, repeated requests, secure-remote permission denial, readiness 503 and explicit mock selection.
- [ ] Page tests cover five visible steps: readiness, provider, persona, Governance Contract, first governed result/evidence.
- [ ] Test first-load API error is not interpreted as “fresh install”.
- [ ] Test critical contract fields are always visible: executor/capabilities, permissions, network, source/base workspace, budget, deadline, acceptance and recovery.
- [ ] Test submitted JSON contains neither `actor` nor `submitter` derived from the browser and the response displays requested/effective differences.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_onboarding.py tests/integration/test_onboarding_idempotency.py -q
cd web
npm test -- --run src/pages/OnboardingPage.test.tsx src/pages/EdictCreatePage.test.tsx
```

Expected: FAIL because the onboarding aggregate/page do not exist.

### 6.2 GREEN — resumable desktop onboarding

- [ ] Implement a narrow onboarding service that composes G1 services; do not duplicate provider/persona/edict business logic.
- [ ] In trusted-local fresh install, allow explicit deterministic zero-cost mock provider. In secure-remote, require the appropriate configuration scope.
- [ ] Redirect to `/onboarding` only when the authoritative state says `required`; errors stay errors, and an already-configured install goes to `/control`.
- [ ] After the first governed result, show its real result/evidence link and continue to `/control`.
- [ ] Do not add mobile steps, drawer or responsive variants.

### 6.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/gateway/test_onboarding.py tests/integration/test_onboarding_idempotency.py -q
cd web
npm test -- --run src/pages/OnboardingPage.test.tsx src/pages/EdictCreatePage.test.tsx
npm run test:run
npm run build
```
- [ ] Commit:

```bash
git add src/tianshu/application/onboarding.py src/tianshu/gateway/onboarding_api.py src/tianshu/app.py tests/gateway/test_onboarding.py tests/integration/test_onboarding_idempotency.py web/src/api/onboarding.ts web/src/hooks/useOnboarding.ts web/src/pages/OnboardingPage.tsx web/src/pages/OnboardingPage.test.tsx web/src/pages/EdictCreatePage.tsx web/src/pages/EdictCreatePage.test.tsx web/src/components/edict/EdictForm.tsx web/src/router/AppRoutes.tsx web/src/i18n/locales
git commit -m "feat(web): add truthful first-run onboarding"
```

### G3-A checkpoint

- [ ] Run `uv run pytest tests/gateway/test_auth.py tests/gateway/test_onboarding.py tests/integration/test_onboarding_idempotency.py -q`.
- [ ] Run `cd web && npm run test:run && npm run typecheck && npm run lint && npm run build`.
- [ ] Stop if any shell invariant, false-empty test or onboarding permission test fails.

---

## Task 7: Implement the real Control Center aggregate and `/control` page

**Gate:** G2 RunState, DecisionRequest, Evidence Bundle, cost and readiness repositories must be green. No mock-only substitute is allowed.

**Files:**

- Create: `src/tianshu/models/control_center.py`
- Create: `src/tianshu/application/control_center.py`
- Create: `src/tianshu/gateway/control_center_api.py`
- Modify: `src/tianshu/app.py`
- Create: `tests/application/test_control_center.py`
- Create: `tests/gateway/test_control_center.py`
- Create: `web/src/api/controlCenter.ts`
- Create: `web/src/hooks/useControlCenter.ts`
- Create: `web/src/pages/ControlCenterPage.tsx`
- Create: `web/src/pages/ControlCenterPage.test.tsx`
- Modify: `web/src/router/AppRoutes.tsx`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

- Produces one read-only snapshot with `generated_at`, `window`, `running`, `pending_decisions`, `budget`, `evidence`, `recoveries`, `growth` and `evolution`.
- Evidence completeness is `closed runs with valid mandatory bundle / all closed runs in window`; response includes numerator, denominator, missing count and window.
- Pending items sort by actionable priority: blocked high risk → over-budget → recovery required → expiry time → created time.

### 7.1 RED — prove metric definitions and ordering

- [ ] Backend tests seed durable rows and verify exact counts, denominator-zero semantics (`rate:null`, not 100%), CNY totals, recovery classification and ordering.
- [ ] Verify the endpoint performs bounded aggregate queries and does not load entire event/artifact bodies.
- [ ] Page tests cover loading, true empty, data, stale refresh, permission, unavailable and generic error.
- [ ] Assert visible UI has `办理中 / 待裁决 / 今日预算 / 证据完整率 / 异常与恢复` with definitions/windows and has no `系统可信 / 置信度 / 信心分`.
- [ ] Test `查看并裁决` navigates to the returned edict/decision context and growth/evolution links use real routes.
- [ ] Run:

```bash
uv run pytest tests/application/test_control_center.py tests/gateway/test_control_center.py -q
cd web
npm test -- --run src/pages/ControlCenterPage.test.tsx
```

Expected: FAIL because the read model and `/control` page do not exist.

### 7.2 GREEN — implement the real read model and approved composition

- [ ] Keep aggregate semantics in `application/control_center.py`; router only validates `window=24h` and serializes.
- [ ] Build the production page from `PageHeader`, `MetricCard`, `PageDataState` and restrained bordered sections; use G0 Control Center composition as reference, not its mock data.
- [ ] Show source/time labels and latest refresh. If refresh fails with prior data, mark it stale and expose retry.
- [ ] No direct writes from the dashboard except navigation to governed flows.

### 7.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/application/test_control_center.py tests/gateway/test_control_center.py -q
cd web
npm test -- --run src/pages/ControlCenterPage.test.tsx
npm run test:run
npm run build
```
- [ ] Commit:

```bash
git add src/tianshu/models/control_center.py src/tianshu/application/control_center.py src/tianshu/gateway/control_center_api.py src/tianshu/app.py tests/application/test_control_center.py tests/gateway/test_control_center.py web/src/api/controlCenter.ts web/src/hooks/useControlCenter.ts web/src/pages/ControlCenterPage.tsx web/src/pages/ControlCenterPage.test.tsx web/src/router/AppRoutes.tsx web/src/i18n/locales
git commit -m "feat(web): connect the control center to real system state"
```

---

## Task 8: Rebuild Edict Detail on RunState, durable Decision, and Evidence Bundle

**Gate:** G2 decision restart/CAS, RunState recovery and Evidence Bundle schema tests must be green.

**Files:**

- Modify: `web/src/api/edicts.ts`
- Modify: `web/src/hooks/useEdictDetail.ts`
- Modify: `web/src/pages/EdictDetailPage.tsx`
- Create: `web/src/pages/EdictDetailPage.test.tsx`
- Modify: `web/src/components/decree/PendingToolCallCard.tsx`
- Modify: `web/src/components/decree/DecreeModal.tsx`
- Modify: `web/src/api/decrees.ts`
- Modify: `web/src/hooks/useApprovals.ts`
- Test: `tests/gateway/test_decisions_api.py`
- Test: `tests/gateway/test_evidence_api.py`
- Test: `tests/integration/test_decision_restart_recovery.py`
- Test: `tests/evidence/test_bundle.py`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

- The page joins edict metadata, governance contract, RunState, decisions and Evidence Bundle by stable ids; no page-local inferred run state.
- Decision mutation is `{action, reason, expected_version}` and returns durable status/version/actor/time.
- Evidence download preserves server filename/content type/digest; replay returns a new edict id.

### 8.1 RED — critical decision and evidence behavior

- [ ] Test tabs `总览 / 计划 / 脉络 / 证据 / 变更 / 裁决 / 成本 / 结案` are deep-linkable and preserve selection in URL.
- [ ] Test requested/effective contract diff, capability level and unsupported controls; contained CLI shows only launch/network/workspace/apply boundaries.
- [ ] Test pending high-risk decision shows impact, permission boundary, restore point and required reason.
- [ ] Test blank/whitespace reason never sends; successful response locks actions; navigation away/back reloads durable lock from API; 409 marks stale and refetches; expired request cannot submit.
- [ ] Test executor output and independent auditor conclusion are in separately named regions.
- [ ] Test Evidence missing mandatory items blocks “结案已验证”; download and governed replay use the correct endpoints.
- [ ] Test `PendingToolCallCard` and `DecreeModal` never send hard-coded `actor:"user"` and permanent grants require reason/expiry/impact.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_decisions_api.py tests/gateway/test_evidence_api.py tests/integration/test_decision_restart_recovery.py tests/evidence/test_bundle.py -q
cd web
npm test -- --run src/pages/EdictDetailPage.test.tsx src/components/governance/DecisionPanel.test.tsx src/components/evidence/EvidenceBundlePanel.test.tsx
```

Expected: FAIL against the current page and client actor payloads.

### 8.2 GREEN — compose the page from durable sources

- [ ] Refactor the 865-line page by business region only: summary/contract, timeline, decision, evidence, cost/close. Do not split tiny one-use render helpers.
- [ ] Use query keys containing edict id and server version; invalidate decisions, run state, evidence and control center after a successful decision/replay.
- [ ] Make feedback a live region and return focus to the decision heading after submit or stale refresh.
- [ ] Keep legacy `/decrees` compatibility inside the API adapter; the visible product language remains `裁决`.

### 8.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/gateway/test_decisions_api.py tests/gateway/test_evidence_api.py tests/integration/test_decision_restart_recovery.py tests/evidence/test_bundle.py -q
cd web
npm test -- --run src/pages/EdictDetailPage.test.tsx src/components/governance/DecisionPanel.test.tsx src/components/evidence/EvidenceBundlePanel.test.tsx
npm run test:run
npm run build
```
- [ ] Commit:

```bash
git add web/src/api/edicts.ts web/src/api/decrees.ts web/src/hooks/useEdictDetail.ts web/src/hooks/useApprovals.ts web/src/pages/EdictDetailPage.tsx web/src/pages/EdictDetailPage.test.tsx web/src/components/decree web/src/i18n/locales tests/gateway/test_decisions_api.py tests/gateway/test_evidence_api.py
git commit -m "feat(web): connect edict detail to durable governance evidence"
```

---

## Task 9: Make the Evolution Center obey one authoritative promotion gate

**Gate:** G2 durable decision/evidence APIs are green. If real challenger routing is not implemented, the response must say so and the UI must not simulate it.

**Files:**

- Create: `src/tianshu/application/evolution_gate.py`
- Create: `src/tianshu/gateway/evolution_api.py`
- Modify: `src/tianshu/gateway/universes_api.py`
- Modify: `src/tianshu/app.py`
- Create: `tests/application/test_evolution_gate.py`
- Create: `tests/gateway/test_evolution_api.py`
- Modify: `web/src/api/universe.ts`
- Modify: `web/src/api/evals.ts`
- Modify: `web/src/pages/UniversePage.tsx`
- Modify: `web/src/pages/EvalsPage.tsx`
- Create: `web/src/pages/UniversePage.test.tsx`
- Create: `web/src/pages/EvalsPage.test.tsx`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`

**Interfaces:**

- `EvolutionGateService` is the single authority used by new endpoints and legacy `/universes/{id}/switch`/`promote-code` writes.
- Gate response includes candidate/source/hypothesis, eval set/version, champion baseline, candidate result, delta, samples/threshold, diff, evidence digests, rollback point, routing truth, version and blocking gates.

### 9.1 RED — reproduce the current bypass

- [ ] Backend test proves a candidate with Canary `18/50` cannot promote through either new or legacy endpoint.
- [ ] Test promotion requires valid Evidence Bundle, rollback point, no mandatory regression, sufficient sample threshold and durable decision where policy requires it.
- [ ] Test `override` never reuses normal promote and always creates a high-risk DecisionRequest with reason.
- [ ] Page tests verify 18/50 disabled, blocking gates visible, real-routing false hides progress language, comparisons state source/baseline/delta, and filter actually changes rendered eval samples.
- [ ] Test API failure is not rendered as “0 candidates” or “all gates passed”.
- [ ] Run:

```bash
uv run pytest tests/application/test_evolution_gate.py tests/gateway/test_evolution_api.py -q
cd web
npm test -- --run src/pages/UniversePage.test.tsx src/pages/EvalsPage.test.tsx src/components/evolution/EvolutionGate.test.tsx
```

Expected: FAIL because current direct endpoints bypass a unified gate.

### 9.2 GREEN — route all writes through the gate

- [ ] Implement the authority once and make legacy endpoints delegate or return a compatible governed response.
- [ ] Use G0 Evolution Center composition, but populate it only with backend fields. Do not hard-code `18/50` outside fixtures.
- [ ] Normal actions: reject candidate, continue observation, promote when allowed. Override appears separately with stronger warning/reason and permission scope.
- [ ] Keep 考成 as its own department page; link its eval run/evidence to the selected candidate without replacing the department IA.

### 9.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/application/test_evolution_gate.py tests/gateway/test_evolution_api.py -q
cd web
npm test -- --run src/pages/UniversePage.test.tsx src/pages/EvalsPage.test.tsx src/components/evolution/EvolutionGate.test.tsx
npm run test:run
npm run build
```
- [ ] Commit:

```bash
git add src/tianshu/application/evolution_gate.py src/tianshu/gateway/evolution_api.py src/tianshu/gateway/universes_api.py src/tianshu/app.py tests/application/test_evolution_gate.py tests/gateway/test_evolution_api.py web/src/api/universe.ts web/src/api/evals.ts web/src/pages/UniversePage.tsx web/src/pages/UniversePage.test.tsx web/src/pages/EvalsPage.tsx web/src/pages/EvalsPage.test.tsx web/src/i18n/locales
git commit -m "feat(web): enforce the authoritative evolution gate"
```

---

## Task 10: Converge the 敕令 department pages

**Files:**

- Modify: `web/src/pages/RoyalStudyPage.tsx`
- Modify: `web/src/components/study/PendingView.tsx`
- Modify: `web/src/components/study/AllEdictsView.tsx`
- Create: `web/src/pages/RoyalStudyPage.test.tsx`
- Modify: `web/src/pages/SchedulerPage.tsx`
- Create: `web/src/pages/SchedulerPage.test.tsx`
- Modify: `web/src/api/scheduler.ts`
- Modify: `web/src/hooks/useScheduler.ts`
- Modify: `src/tianshu/gateway/execution_api.py`
- Modify: `src/tianshu/storage/scheduler_repo.py`
- Create: `tests/gateway/test_scheduler_recovery_api.py`

**Interfaces:**

- 御书房 is one truthful durable decision inbox across plan/tool/outer-loop kinds.
- 文书房 exposes next/last run, missed/recovered state, attempt/max attempts, retry, DLQ and history from G2 scheduler truth.

### 10.1 RED

- [ ] Test 御书房 loading/empty/error/permission/unavailable/stale and cross-kind sorting; each row shows real principal, expiry, reason requirement and affected scope.
- [ ] Test cancel/retry/permanent grant are high-risk writes with explicit reason and server-derived actor.
- [ ] Test 文书房 missed run, recovery, retry and DLQ; a failed list call must not show “暂无任务”.
- [ ] Test retry is idempotent and only available for server-declared retryable states.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_scheduler_recovery_api.py -q
cd web
npm test -- --run src/pages/RoyalStudyPage.test.tsx src/pages/SchedulerPage.test.tsx
```

Expected: FAIL because durable inbox/scheduler recovery states are not implemented.

### 10.2 GREEN

- [ ] Reuse `PageDataState` and `DecisionPanel`; do not create a second approval component.
- [ ] Extend the scheduler read response through existing repository/G2 ledger, then render history without client inference.
- [ ] Preserve `/approvals` and `/scheduler` deep links.

### 10.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/gateway/test_scheduler_recovery_api.py -q
cd web
npm test -- --run src/pages/RoyalStudyPage.test.tsx src/pages/SchedulerPage.test.tsx
npm run test:run
npm run build
```

- [ ] Commit:

```bash
git add web/src/pages/RoyalStudyPage.tsx web/src/pages/RoyalStudyPage.test.tsx web/src/pages/SchedulerPage.tsx web/src/pages/SchedulerPage.test.tsx web/src/components/study web/src/api/scheduler.ts web/src/hooks/useScheduler.ts src/tianshu/gateway/execution_api.py src/tianshu/storage/scheduler_repo.py tests/gateway/test_scheduler_recovery_api.py
git commit -m "feat(web): converge the edict department pages"
```

---

## Task 11: Converge the 政要 department pages

**Files:**

- Modify: `web/src/pages/CabinetPage.tsx`
- Create: `web/src/pages/CabinetPage.test.tsx`
- Modify: `web/src/pages/ConsultationPage.tsx`
- Create: `web/src/pages/ConsultationPage.test.tsx`
- Modify: `web/src/pages/AuditDashboardPage.tsx`
- Create: `web/src/pages/AuditDashboardPage.test.tsx`
- Modify: `web/src/pages/SessionRulesPage.tsx`
- Create: `web/src/pages/SessionRulesPage.test.tsx`
- Create: `web/src/features/cabinet/PlanQualityPanel.tsx`
- Create: `web/src/features/audit/SystemAuditPanel.tsx`
- Create: `web/src/features/policy/RuleDiffPanel.tsx`
- Modify: `web/src/api/audit.ts`, `consultations.ts`, `policy.ts`
- Modify: `web/src/hooks/useAudit.ts`, `useConsultation.ts`
- Modify: `src/tianshu/gateway/audit_api.py`
- Create: `tests/gateway/test_system_audit_api.py`
- Create: `tests/gateway/test_session_rule_lifecycle.py`

**Interfaces:**

- 内阁: plan review, complexity, budget/duration estimate, replan reason/diff and quality evidence.
- 廷议: durable stance, conditions, evidence, dissent and synthesis; no uncalibrated confidence score.
- 都察院: task audit, append-only system audit and interface failures are separate datasets/tabs.
- 权印司: scope, expiry, argument fingerprint, diff, reason, actor and revoke; permanent/high-risk rules default to bounded expiry.

### 11.1 RED

- [ ] Add a page/component test per responsibility above, plus all PageDataState variants.
- [ ] Test the required dissent remains visible in 廷议 and no `confidence/可信度` is displayed.
- [ ] Test audit interface failure is not counted as “zero violations”.
- [ ] Test high-risk rule creation without reason/expiry fails; revoke shows effect and recovery.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_system_audit_api.py tests/gateway/test_session_rule_lifecycle.py -q
cd web
npm test -- --run src/pages/CabinetPage.test.tsx src/pages/ConsultationPage.test.tsx src/pages/AuditDashboardPage.test.tsx src/pages/SessionRulesPage.test.tsx
```

Expected: FAIL because the four department responsibility contracts are not complete.

### 11.2 GREEN

- [ ] Split the 740-line audit page only into the three independent panels; keep tab composition in the page.
- [ ] Persist filter/tab state in URL where it is shareable; do not hide critical scope/expiry in expert mode.
- [ ] Reuse G2 SystemAuditLog and planner evidence rather than recomputing from UI events.

### 11.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/gateway/test_system_audit_api.py tests/gateway/test_session_rule_lifecycle.py -q
cd web
npm test -- --run src/pages/CabinetPage.test.tsx src/pages/ConsultationPage.test.tsx src/pages/AuditDashboardPage.test.tsx src/pages/SessionRulesPage.test.tsx
npm run test:run
npm run build
```

- [ ] Commit:

```bash
git add web/src/pages/CabinetPage.tsx web/src/pages/CabinetPage.test.tsx web/src/pages/ConsultationPage.tsx web/src/pages/ConsultationPage.test.tsx web/src/pages/AuditDashboardPage.tsx web/src/pages/AuditDashboardPage.test.tsx web/src/pages/SessionRulesPage.tsx web/src/pages/SessionRulesPage.test.tsx web/src/features/cabinet web/src/features/audit web/src/features/policy web/src/api/audit.ts web/src/api/consultations.ts web/src/api/policy.ts web/src/hooks/useAudit.ts web/src/hooks/useConsultation.ts src/tianshu/gateway/audit_api.py tests/gateway/test_system_audit_api.py tests/gateway/test_session_rule_lifecycle.py
git commit -m "feat(web): converge the governance department pages"
```

---

## Task 12: Converge the 百官 department pages

**Files:**

- Modify: `web/src/pages/PersonaDashboardPage.tsx`
- Modify: `web/src/pages/PersonaDetailPage.tsx`
- Create: `web/src/features/personas/PersonaListPanel.tsx`
- Create: `web/src/features/personas/PersonaCapabilityMatrix.tsx`
- Create: `web/src/features/personas/PersonaGrowthPanel.tsx`
- Create: `web/src/pages/PersonaDashboardPage.test.tsx`
- Modify: `web/src/pages/MemoryDashboardPage.tsx`
- Create: `web/src/features/memory/MemorySourcePanel.tsx`
- Create: `web/src/features/memory/MemoryQualityPanel.tsx`
- Create: `web/src/pages/MemoryDashboardPage.test.tsx`
- Modify: `web/src/pages/UniversePage.test.tsx`
- Modify: `web/src/pages/EvalsPage.test.tsx`
- Modify: `web/src/api/personas.ts`, `memory.ts`
- Modify: `web/src/hooks/usePersonas.ts`, `useMemory.ts`

**Interfaces:**

- 百官阁 persona list is API-driven and selectors update immediately after create/delete.
- 文渊阁 shows source, recall, retention, FTS rebuild status, quality samples and token cost with real definitions.
- 位面/考成 reuse the authoritative gate/eval contracts from Task 9.

### 12.1 RED

- [ ] Search current pages for hard-coded persona ids and add tests that introduce an unknown persona, delete an existing persona and verify every selector follows the API list.
- [ ] Test capability matrix distinguishes allowed, denied, unsupported and inherited.
- [ ] Test memory query failure, FTS unavailable and no results as three different states; token/cost figures include source/window.
- [ ] Test destructive persona/memory actions require confirmation, permission and server-derived actor.
- [ ] Run:

```bash
cd web
npm test -- --run src/pages/PersonaDashboardPage.test.tsx src/pages/MemoryDashboardPage.test.tsx src/pages/UniversePage.test.tsx src/pages/EvalsPage.test.tsx
```

Expected: FAIL because dynamic persona synchronization and truthful memory states are incomplete.

### 12.2 GREEN

- [ ] Split the >1,000-line persona pages into the three named business panels; do not mechanically split by line count.
- [ ] Split the 651-line memory page into source/quality panels only where state and tests are independent.
- [ ] Invalidate shared persona queries after mutation so onboarding, 文渊阁, 通政司 and Edict forms update together.

### 12.3 Verify and commit

- [ ] Run:

```bash
cd web
npm test -- --run src/pages/PersonaDashboardPage.test.tsx src/pages/MemoryDashboardPage.test.tsx src/pages/UniversePage.test.tsx src/pages/EvalsPage.test.tsx
npm run test:run
npm run build
```

- [ ] Commit:

```bash
git add web/src/pages/PersonaDashboardPage.tsx web/src/pages/PersonaDashboardPage.test.tsx web/src/pages/PersonaDetailPage.tsx web/src/pages/MemoryDashboardPage.tsx web/src/pages/MemoryDashboardPage.test.tsx web/src/pages/UniversePage.test.tsx web/src/pages/EvalsPage.test.tsx web/src/features/personas web/src/features/memory web/src/api/personas.ts web/src/api/memory.ts web/src/hooks/usePersonas.ts web/src/hooks/useMemory.ts
git commit -m "feat(web): converge the people and growth department pages"
```

---

## Task 13: Converge the 外朝 department pages

**Files:**

- Modify: `web/src/pages/SystemManagementPage.tsx`
- Create: `web/src/pages/SystemManagementPage.test.tsx`
- Modify: `web/src/pages/HongluisiPage.tsx`
- Create: `web/src/pages/HongluisiPage.test.tsx`
- Modify: `web/src/pages/TongzhengPage.tsx`
- Create: `web/src/pages/TongzhengPage.test.tsx`
- Modify: `web/src/pages/CostDashboardPage.tsx`
- Create: `web/src/pages/CostDashboardPage.test.tsx`
- Modify: `web/src/components/system/CreateMCPServerModal.tsx`
- Modify: `web/src/components/config/InstanceManager.tsx`
- Create: `web/src/features/tongzheng/DeliveryAttemptsPanel.tsx`
- Create: `web/src/features/cost/CostFilterBar.tsx`
- Modify: `web/src/api/system.ts`, `mcp.ts`, `hongluisi.ts`, `keqing.ts`, `tongzheng.ts`, `cost.ts`
- Modify: `web/src/hooks/useSystem.ts`, `useMCP.ts`, `useCost.ts`
- Modify: `src/tianshu/gateway/mcp_api.py`, `hongluisi_api.py`, `tongzheng_api.py`, `cost_api.py`
- Modify: `src/tianshu/storage/cost_repo.py`
- Create: `tests/gateway/test_mcp_web_admission.py`
- Create: `tests/gateway/test_notification_delivery_api.py`
- Create: `tests/gateway/test_cost_filter_contract.py`

**Interfaces:**

- 藏兵阁 groups model/tool/skill/MCP/plugin without replacing its department identity.
- MCP stdio creation defaults `enabled:false` and returns allowlist/capability/admission results before activation.
- 鸿胪寺 retains current fetch/search engines and credentials while adding contained/managed capability level and health truth.
- 通政司 exposes delivery level, quiet hours, attempts, retry and DLQ from G2 delivery outbox.
- 户部 uses one `CostFilter {period, from, to, edictId, provider, model}` for summary, trend, records and export.

### 13.1 RED

- [ ] Test model/tool/skill/MCP/plugin tab errors do not collapse into empty tables.
- [ ] Test new stdio MCP is disabled by default, unapproved command/URL is rejected, secret values never return, and admission results are visible.
- [ ] Test 鸿胪寺 does not claim action interception/receipts for contained adapters.
- [ ] Test notification attempts and uncertainty semantics; retry/DLQ are permissioned writes with reason.
- [ ] Test the same cost filter reaches all four endpoints; trend is independent of the current 20-row table page; all CNY uses `¥` and one precision policy.
- [ ] Run:

```bash
uv run pytest tests/gateway/test_mcp_web_admission.py tests/gateway/test_notification_delivery_api.py tests/gateway/test_cost_filter_contract.py -q
cd web
npm test -- --run src/pages/SystemManagementPage.test.tsx src/pages/HongluisiPage.test.tsx src/pages/TongzhengPage.test.tsx src/pages/CostDashboardPage.test.tsx
```

Expected: FAIL because admission, delivery and shared cost-filter contracts are incomplete.

### 13.2 GREEN

- [ ] Refactor API modules onto the unified client and PageDataState.
- [ ] Keep secrets masked and capabilities server-authored. Do not expose raw env/headers in error detail.
- [ ] Make cost query state URL-shareable and reset pagination when filters change.

### 13.3 Verify and commit

- [ ] Run:

```bash
uv run pytest tests/gateway/test_mcp_web_admission.py tests/gateway/test_notification_delivery_api.py tests/gateway/test_cost_filter_contract.py -q
cd web
npm test -- --run src/pages/SystemManagementPage.test.tsx src/pages/HongluisiPage.test.tsx src/pages/TongzhengPage.test.tsx src/pages/CostDashboardPage.test.tsx
npm run test:run
npm run build
```

- [ ] Commit:

```bash
git add web/src/pages/SystemManagementPage.tsx web/src/pages/SystemManagementPage.test.tsx web/src/pages/HongluisiPage.tsx web/src/pages/HongluisiPage.test.tsx web/src/pages/TongzhengPage.tsx web/src/pages/TongzhengPage.test.tsx web/src/pages/CostDashboardPage.tsx web/src/pages/CostDashboardPage.test.tsx web/src/components/system/CreateMCPServerModal.tsx web/src/components/config/InstanceManager.tsx web/src/features/tongzheng web/src/features/cost web/src/api/system.ts web/src/api/mcp.ts web/src/api/hongluisi.ts web/src/api/keqing.ts web/src/api/tongzheng.ts web/src/api/cost.ts web/src/hooks/useSystem.ts web/src/hooks/useMCP.ts web/src/hooks/useCost.ts src/tianshu/gateway/mcp_api.py src/tianshu/gateway/hongluisi_api.py src/tianshu/gateway/tongzheng_api.py src/tianshu/gateway/cost_api.py src/tianshu/storage/cost_repo.py tests/gateway/test_mcp_web_admission.py tests/gateway/test_notification_delivery_api.py tests/gateway/test_cost_filter_contract.py
git commit -m "feat(web): converge the external court department pages"
```

### G3-B checkpoint

- [ ] Run:

```bash
uv run pytest tests/application/test_control_center.py tests/application/test_evolution_gate.py tests/gateway/test_onboarding.py tests/gateway/test_control_center.py tests/gateway/test_decisions_api.py tests/gateway/test_evidence_api.py tests/gateway/test_evolution_api.py tests/gateway/test_scheduler_recovery_api.py tests/gateway/test_system_audit_api.py tests/gateway/test_session_rule_lifecycle.py tests/gateway/test_mcp_web_admission.py tests/gateway/test_notification_delivery_api.py tests/gateway/test_cost_filter_contract.py -q
```
- [ ] Run `cd web && npm run test:run && npm run typecheck && npm run lint && npm run build`.
- [ ] Inspect Web source for false-empty and actor leaks:

```bash
rg -n 'return[[:space:]]+(\[\]|0)' web/src/api web/src/hooks -g '!*.test.ts' -g '!*.test.tsx'
rg -n "actor:[[:space:]]*['\"]user|百官图|批红|朱批|司礼监" web/src -g '!*.test.ts' -g '!*.test.tsx'
```

Expected: no production matches for false-empty, hard-coded actor or forbidden visible names.

---

## Task 14: Add Playwright desktop journeys against fixtures and the real demo stack

**Files:**

- Create: `web/playwright.config.ts`
- Create: `web/e2e/fixtures/api.ts`
- Create: `web/e2e/fixtures/auth.ts`
- Create: `web/e2e/pages/AppShellPage.ts`
- Create: `web/e2e/pages/ControlCenterPage.ts`
- Create: `web/e2e/pages/EdictDetailPage.ts`
- Create: `web/e2e/pages/EvolutionCenterPage.ts`
- Create: `web/e2e/fresh-install.spec.ts`
- Create: `web/e2e/control-center.spec.ts`
- Create: `web/e2e/decision-flow.spec.ts`
- Create: `web/e2e/evolution-gate.spec.ts`
- Create: `scripts/run-web-e2e.sh`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Interfaces:**

- Fixture journeys provide deterministic error/state matrices.
- `fresh-install.spec.ts` runs against a clean real demo backend using a temporary HOME and `TIANSHU_DB_PATH`; it must prove the actual request/response path.
- Playwright projects are desktop Chromium at 1440×1024 and 1280×900 only; no mobile project.

### 14.1 RED — write the four journeys first

- [ ] Control journey: load real `/control`, verify metric definitions/source/window, open the highest-priority pending item.
- [ ] Decision journey: blank reason blocked → reason submit → durable result lock → reload/navigate back remains locked → evidence download metadata → governed replay creates new edict.
- [ ] Evolution journey: 18/50 blocks normal promotion → reasoned observe → no fake routing progress → override remains a separate high-risk flow.
- [ ] Fresh install: empty DB → explicit mock → default persona → contract preview → first governed result/evidence → `/control`.
- [ ] Ban `waitForTimeout`; wait for named response or `data-state=ready`. Capture trace/video/screenshot only on failure/retry.
- [ ] Run `cd web && npx playwright test --list`; then a focused spec. Expected: FAIL because configuration/POM/stack runner are absent.

### 14.2 GREEN — deterministic and real-stack modes

- [ ] Reuse the `@playwright/test` dependency installed in Task 1 and add scripts `e2e`, `e2e:fixture`, `e2e:real`, `e2e:update-snapshots`.
- [ ] `scripts/run-web-e2e.sh` creates a temporary HOME/DB, starts the G1 explicit demo profile on loopback, starts Vite on 7999, waits for `/health/ready`, runs tests, and always terminates only its owned processes.
- [ ] Keep fixture and real tests clearly tagged. Fixture success cannot replace the real fresh-install test.
- [ ] Fail on uncaught page errors, console errors, unexpected 4xx/5xx and unhandled requests.

### 14.3 Verify and commit

- [ ] Run all four journeys twice and `--repeat-each=3` for decision flow; zero flakes/skips.
- [ ] Commit:

```bash
git add web/playwright.config.ts web/e2e web/package.json web/package-lock.json scripts/run-web-e2e.sh
git commit -m "test(web): cover the critical desktop governance journeys"
```

---

## Task 15: Add accessibility, keyboard, zoom, and visual regression gates

**Files:**

- Create: `web/e2e/a11y.spec.ts`
- Create: `web/e2e/keyboard.spec.ts`
- Create: `web/e2e/visual-shell.spec.ts`
- Create: `web/e2e/visual-pages.spec.ts`
- Create: `web/e2e/__screenshots__/`
- Modify: `web/playwright.config.ts`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Interfaces:**

- Uses G0 audit images under `prototypes/tianshu-agent-os/audit-2026-07-11/` as visual art-direction reference.
- Produces deterministic production screenshot baselines for both desktop widths, both themes and both sidebar states.

### 15.1 RED — automate the unresolved G0 accessibility gaps

- [ ] Add axe scans for shell, onboarding, control, edict detail, decision dialog, evolution and each department page; `serious` and `critical` violations must be zero.
- [ ] Keyboard test completes navigation, decision and evolution filter without pointer; focus is visible and dialog close returns focus to the opener.
- [ ] Test status live regions and labels; no state can be communicated by color alone.
- [ ] At desktop 1280, run a 200% browser-zoom/text-scale scenario and assert no horizontal page overflow or unreachable primary action. This is accessibility zoom, not a mobile layout deliverable.
- [ ] Emulate `prefers-reduced-motion: reduce` and assert no transform animation blocks interaction.
- [ ] Add shell visual matrix: `1280/1440 × dark/light × expanded/collapsed`; add three approved-page key states.
- [ ] Run tests before fixes/baselines; expect FAIL and retain diff artifacts.

### 15.2 GREEN — fix semantics and approve baselines internally

- [ ] Add `@axe-core/playwright`; fix labels, heading order, landmarks, focus trap/return, live region and contrast in production components.
- [ ] Compare production shots side-by-side with G0 same viewport/state. Correct spacing, cropping, header density, borders, radii and hierarchy before recording baselines.
- [ ] Keep screenshot animations disabled, time/data fixed and fonts awaited. Do not raise pixel tolerance to hide real regressions.
- [ ] Use textual layout assertions alongside screenshots; a newly recorded image alone is not proof.

### 15.3 Verify and commit

- [ ] Run `npm run e2e:a11y`, keyboard and visual suites twice with no diff/flakes.
- [ ] Commit `test(web): gate desktop accessibility and visual fidelity` including intentional baselines.

---

## Task 16: Enforce lazy loading and measurable performance budgets

**Files:**

- Create: `web/performance-budget.json`
- Create: `web/scripts/check-bundle-budget.mjs`
- Create: `web/e2e/performance.spec.ts`
- Create: `web/src/router/lazyRoutes.test.tsx`
- Modify: `web/vite.config.ts`
- Modify: `web/package.json`

**Interfaces:**

- Production build emits a manifest used to calculate initial JS.
- Budget consumes Task 1's measured pre-G3 baseline and allows at most 10% regression; absolute LCP remains `< 2500ms` in the defined CI demo environment.

### 16.1 RED — make current eager bundle fail visibly

- [ ] Read `performance-baseline.json` from Task 1 and fail if any required environment/commit/initial-JS/LCP/interaction field is absent; derive immutable +10% maxima in `performance-budget.json` rather than re-measuring the already-modified app as “baseline”.
- [ ] Add route tests proving Control Center does not import Edict Detail, Universe, Evals, DAG/@xyflow or large charts before navigation.
- [ ] Add build-manifest check: initial JS ≤ baseline × 1.10 and heavy visualization chunks are not initial dependencies.
- [ ] Add Playwright performance test: five cold runs, median LCP < 2500ms, initial JS within budget, and the same sidebar route-interaction measure within the frozen +10% threshold.
- [ ] Run checks against the current eager App; expected: FAIL on route/bundle boundary.

### 16.2 GREEN — reduce initial work only where measured

- [ ] Keep all heavy routes lazy and split chart/@xyflow dependencies by feature. Do not add speculative memoization.
- [ ] Use skeletons that preserve layout; do not block first content on non-critical growth/evolution panels.
- [ ] Record the resulting metrics as evidence, not as a silently loosened budget. Any budget increase requires an explicit plan amendment.

### 16.3 Verify and commit

- [ ] Run production build, bundle check and performance spec twice.
- [ ] Commit:

```bash
git add web/performance-budget.json web/scripts/check-bundle-budget.mjs web/e2e/performance.spec.ts web/src/router/lazyRoutes.test.tsx web/vite.config.ts web/package.json
git commit -m "perf(web): enforce the G3 desktop performance budget"
```

---

## Task 17: Automate the G3 Gate and CI evidence

**Files:**

- Create: `scripts/g3-web-gate.sh`
- Create: `docs/quality/g3-web-gate.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`

**Interfaces:**

- Produces `artifacts/g3/gate-summary.json`, junit, Playwright HTML, traces/screenshots on failure, axe results, visual diffs and performance report.
- `docs/quality/g3-web-gate.md` records exact commands, environment, actual counts/metrics and status `automation_passed_pending_user_approval` after a green run.

### 17.1 RED — gate must fail if any evidence class is missing

- [ ] Add a gate self-test/dry-run that removes one expected artifact at a time and verifies non-zero exit.
- [ ] Gate sequence:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run lint-imports
uv run pytest -m "not slow" -q
cd web
npm ci
npm run lint
npm run typecheck
npm run test:run
npm run build
npm run bundle:check
npm run e2e
npm run e2e:a11y
npm run e2e:visual
npm run e2e:perf
```

- [ ] The gate fails for any skipped/fixme focused G3 test, console error, serious/critical axe issue, unexpected visual diff, bundle/LCP regression or missing real fresh-install journey.
- [ ] Run the gate before CI wiring; expect FAIL until artifact validation is implemented.

### 17.2 GREEN — add a dedicated Web E2E job

- [ ] CI job installs Node, uv/Python and Playwright Chromium with system dependencies; it starts only the loopback demo stack with a temporary DB.
- [ ] Upload reports with retention even on failure, but never turn a failed test into a successful job.
- [ ] Do not add Firefox/WebKit/mobile scope to G3; cross-browser expansion is a separately approved phase.
- [ ] Populate `g3-web-gate.md` from the actual green run; do not copy expected values as results.

### 17.3 Verify and commit

- [ ] Run `./scripts/g3-web-gate.sh` locally to completion and verify every artifact referenced by the summary exists.
- [ ] Commit:

```bash
git add scripts/g3-web-gate.sh docs/quality/g3-web-gate.md .github/workflows/ci.yml .gitignore
git commit -m "ci: enforce the automated G3 desktop web gate"
```

---

## Task 18: Present the real pages for final user approval

**Files:**

- Create: `docs/quality/g3-design-qa.md`
- Use, do not modify: `prototypes/tianshu-agent-os/audit-2026-07-11/`

**Interfaces:**

- Consumes a green automated Gate and the real demo stack.
- Produces the human approval record; until explicit approval, status remains `pending_user_approval`.

### 18.1 Prepare the approval candidate

- [ ] Start the real demo stack and verify `/health/ready` before showing pages.
- [ ] Present these URLs/states to the user in the in-app browser:
  - `/control`: dark/expanded at 1440×1024 and light/collapsed at 1280×900.
  - `/edicts/{real_demo_id}`: contract, pending decision with reason, durable resolved lock, Evidence Bundle.
  - `/universes`: blocked sample gate, comparison/evidence and no-real-routing state.
- [ ] Also show theme switch, sidebar collapse/expand, all 15 navigation entries and the exact header.
- [ ] Perform VoiceOver spot-check: landmarks/headings, sidebar, decision dialog, error/live region and focus return. Record actual observations; axe is not a substitute.
- [ ] Record design QA severity. P0/P1/P2 must be zero; P3 cosmetic notes may remain only with user acceptance.

### 18.2 Stop for explicit approval

- [ ] Write `docs/quality/g3-design-qa.md` with automated evidence links and status `pending_user_approval`.
- [ ] Ask the user to approve or annotate the pages. Do not mark G3 passed and do not record a formal product demo while approval is pending.
- [ ] If the user requests changes, add/revise a failing test first, implement the narrow fix, rerun the complete G3 Gate, regenerate candidate screenshots and return to this checkpoint.

### 18.3 GREEN only after user approval

- [ ] After an explicit approval, update the QA document with approval date, approved viewports/states and any accepted P3 notes.
- [ ] Rerun `./scripts/g3-web-gate.sh` without code changes to prove the approved revision is still green.
- [ ] Commit:

```bash
git add docs/quality/g3-design-qa.md
git commit -m "docs: record final G3 desktop web approval"
```

## Final G3 Acceptance Checklist

- [ ] G0 frozen shell contract is byte/string exact; Logo hash unchanged.
- [ ] Desktop-only scope is respected; no mobile code or test project was added.
- [ ] Default dark, light mode, expanded/collapsed persistence and all visual states pass.
- [ ] `/control`, real Edict Detail and real Evolution Center consume authoritative APIs and expose truthful failure/stale states.
- [ ] Onboarding completes an actual governed mock result from an empty DB without a live key.
- [ ] All four groups/fourteen departments retain identity, route and truthful state handling.
- [ ] Browser never submits actor; high-risk decisions require reason and durable version semantics.
- [ ] Evidence, auditor conclusions, contained executor limits and promotion gates are not overstated.
- [ ] Three critical Playwright journeys plus fresh install pass with console errors = 0.
- [ ] axe serious/critical = 0; keyboard/focus/live-region/200% zoom/reduced-motion checks pass; VoiceOver spot-check recorded.
- [ ] 1280×900 and 1440×1024 have no horizontal overflow or unreachable primary action.
- [ ] Initial JS/LCP/interaction stay within the frozen budget and LCP < 2.5s in the defined environment.
- [ ] Automated G3 Gate has real artifacts and no skipped/fixme evidence gaps.
- [ ] User has explicitly approved the final real pages; only then is G3 `passed`.
