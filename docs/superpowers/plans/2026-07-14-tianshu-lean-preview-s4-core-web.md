# Tianshu Lean Preview S4 Core Desktop Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved restrained New Chinese desktop shell with real Control Center, Edict Detail, and Evolution Center pages backed by S3/S5 truth, plus first-run onboarding and automated accessibility/visual gates.

**Architecture:** Preserve the current brand shell and fourteen-department navigation, add `/control` as the product entry, and lazy-load heavy routes. A typed API problem model and one `PageDataState` component make loading/empty/stale/error/permission/service-unavailable explicit. Three pages consume named backend read models; no page catches errors and substitutes empty arrays or mock numbers. The Evolution page is implemented against a truthful pre-S5 disabled state, then S5 wires real candidate routing into the same contract.

**Tech Stack:** React 18, TypeScript 5.6, React Router 6, Ant Design 5, TanStack Query, Vitest/jsdom, Testing Library, Playwright Chromium, axe, Vite 6, FastAPI/Pydantic read models.

## Global Constraints

- Require the S3 Core report/checker to pass before real page tasks. Shell/state work may start after S3 read contracts are frozen.
- Normative detailed source is [`docs/codex-v1/plans/13-g3-desktop-web-productization.md`](../../codex-v1/plans/13-g3-desktop-web-productization.md). Execute its Tasks 1–9 and 14–17 as narrowed here; do not execute Tasks 10–13 (full fourteen-department convergence) in D8-A.
- Do not modify `web/public/brand.png`; assert SHA-256 `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`.
- Preserve exact quote, right-side five items, four groups/fourteen departments, theme toggle, and sidebar collapse. Add Control Center above department groups; do not replace department names with generic categories.
- Default theme is dark only when the user has no stored preference. Existing explicit light preference remains respected.
- Use `裁决`; reject `批红 / 朱批 / 司礼监代批` in source/locale truth tests.
- Desktop viewports are 1280×800 and 1440×1024. No mobile CSS, mobile navigation, mobile test, or mobile claim.
- The seven page states are `loading`, `success-empty`, `success-data`, `stale`, `error`, `permission-denied`, `service-unavailable`.
- `automation_passed` and `user_approval_pending` are separate. Automated screenshots do not approve the UI.
- Full VoiceOver and Tasks 10–13 remain deferred. Automated scope includes axe serious/critical=0, keyboard/focus, and 200% zoom for the shell and three core pages.

---

### Task 1: Freeze Web contracts and install only required test dependencies

**Files:**
- Create: `web/src/contracts/api.ts`
- Create: `web/src/test/brandShell.contract.test.tsx`
- Create: `tests/gateway/test_s3_s4_handoff.py`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Interfaces:**
- Consumes: S3 Decision, RunState, Evidence Bundle, SystemAudit, readiness endpoints.
- Produces:

```ts
export type PageDataStatus =
  | "loading" | "success-empty" | "success-data" | "stale"
  | "error" | "permission-denied" | "service-unavailable";

export interface ApiProblem {
  status: number;
  code: string;
  message: string;
  correlationId: string | null;
  retryable: boolean;
}
```

- [ ] **Step 1: Write handoff and shell RED/guard tests**

Backend test imports the named S3 contracts and validates their schemas. Web test asserts the exact logo path/hash fixture, quote, five right-side labels, fourteen department labels, theme control, collapse control, and forbidden governance words absent.

- [ ] **Step 2: Add test dependencies**

Add only `@testing-library/jest-dom`, `@testing-library/user-event`, `@playwright/test`, and `@axe-core/playwright` if absent. Testing Library React/jsdom already exist; do not replace Vitest.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/gateway/test_s3_s4_handoff.py -q
cd web
npm ci
npm test -- --run src/test/brandShell.contract.test.tsx
cd ..
git add web/package.json web/package-lock.json web/src/contracts/api.ts web/src/test/brandShell.contract.test.tsx tests/gateway/test_s3_s4_handoff.py
git commit -m "test: freeze the S3 to desktop Web handoff"
```

### Task 2: Implement the design foundation, shell, routes, and page states

**Files:**
- Create: `web/src/components/states/PageDataState.tsx`
- Create: `web/src/components/states/PageDataState.test.tsx`
- Create: `web/src/components/governance/GovernanceContractCard.tsx`
- Create: `web/src/components/governance/DecisionPanel.tsx`
- Create: `web/src/components/evidence/EvidenceBundlePanel.tsx`
- Create: `web/src/components/evolution/EvolutionGate.tsx`
- Modify: `web/src/theme/palette.ts`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/hooks/useTheme.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/layout/AppHeader.tsx`
- Modify: `web/src/components/layout/AppSidebar.tsx`
- Modify: three locale JSON files

**Interfaces:**

```tsx
interface PageDataStateProps<T> {
  status: PageDataStatus;
  data: T | null;
  problem?: ApiProblem | null;
  isEmpty: (data: T) => boolean;
  onRetry?: () => void;
  children: (data: T) => React.ReactNode;
}
```

- [ ] **Step 1: Execute baseline Tasks 2–5 RED component matrices**

Write tests for default-dark/no-preference, stored light preference, header/sidebar exact copy, collapsed controls, `/`→`/control`, `/approvals` canonical route, lazy heavy pages, all seven `PageDataState` states, decision reason required, immutable resolved decision, evidence digest/download, and pre-S5 evolution `not_enabled`.

- [ ] **Step 2: Implement restrained tokens and default theme**

Keep “墨为骨、朱为睛、纸为气”: neutral ink surfaces, low-contrast borders, cinnabar only for decision/block/focus. Do not add gold borders, dragon patterns, heavy paper textures, glow noise, or ornamental motion.

- [ ] **Step 3: Lazy-load all page modules and add Control navigation**

`/control` renders a lazy `ControlCenterPage`; `/` redirects with `<Navigate replace to="/control" />`; `/approvals` remains Royal Study. The Control item appears above group headings. Existing fourteen department items remain unchanged.

- [ ] **Step 4: Implement one error mapping boundary**

`api/client.ts` maps transport/server responses to `ApiProblem`; hooks throw/return that problem. Components never turn 401/403/503 into `[]`, `0`, or a generic toast-only success state.

- [ ] **Step 5: Run and commit**

```bash
cd web
npm test -- --run src/components/states src/components/layout src/App.auth.test.tsx src/test/brandShell.contract.test.tsx
npm run typecheck
npm run lint
cd ..
git add web/src web/package.json web/package-lock.json
git commit -m "feat: add the restrained desktop shell and data states"
```

### Task 3: Add first-run onboarding and governed contract creation

**Files:**
- Create: `web/src/pages/OnboardingPage.tsx`
- Create: `web/src/pages/OnboardingPage.test.tsx`
- Create: `web/src/api/onboarding.ts`
- Modify: `web/src/pages/EdictCreatePage.tsx`
- Modify: `web/src/components/edict/EdictForm.tsx`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: demo readiness, six packaged personas, two builtin skills, requested Governance Contract.
- Produces: first governed Edict request without a client-supplied actor.

- [ ] **Step 1: Write RED first-run tests**

Assert empty fresh HOME shows onboarding, Doctor failures show service-unavailable, demo profile is explicit, six departments/two skills are exact, requested contract is previewed, server-derived actor is absent from request body, and success navigates to the real Edict page.

- [ ] **Step 2: Implement the minimal flow**

Reuse existing `EdictForm`; add only the first-run framing and exact Governance Contract preview. Do not create a second Edict creation form or persist fake onboarding completion.

- [ ] **Step 3: Run and commit**

```bash
cd web
npm test -- --run src/pages/OnboardingPage.test.tsx src/components/edict/EdictForm.governance.test.tsx
npm run typecheck
cd ..
git add web/src/pages/OnboardingPage.tsx web/src/pages/OnboardingPage.test.tsx web/src/api/onboarding.ts web/src/pages/EdictCreatePage.tsx web/src/components/edict/EdictForm.tsx web/src/App.tsx
git commit -m "feat: add governed first-run onboarding"
```

### Task 4: Build the real Control Center read model and page

**Files:**
- Create: `src/tianshu/models/control_center.py`
- Create: `src/tianshu/gateway/control_center_api.py`
- Create: `tests/gateway/test_control_center.py`
- Create: `web/src/api/control.ts`
- Create: `web/src/hooks/useControlCenter.ts`
- Create: `web/src/pages/ControlCenterPage.tsx`
- Create: `web/src/pages/ControlCenterPage.test.tsx`
- Modify: `src/tianshu/app.py`

**Interfaces:**

```python
class ControlCenterSnapshotV1(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    readiness: str
    active_runs: tuple[ControlRunSummaryV1, ...]
    pending_decisions: tuple[ControlDecisionSummaryV1, ...]
    recent_evidence: tuple[ControlEvidenceSummaryV1, ...]
    evolution_status: Literal["not_enabled", "enabled", "degraded"]
```

- [ ] **Step 1: Write backend RED contract tests**

Assert stable sorting, scoped visibility, real counts from storage, no “system confidence/trust score”, no hidden error-as-zero, and correlation ID.

- [ ] **Step 2: Implement one aggregate query service**

Read existing decision/run/evidence/readiness repositories. Do not issue one HTTP request per card and do not cache a second truth table.

- [ ] **Step 3: Write page RED states and implement**

Cover all seven states, keyboard links, precise empty copy, no mock numbers, and links to real Edict/Decision/Evidence routes.

- [ ] **Step 4: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/gateway/test_control_center.py -q
cd web
npm test -- --run src/pages/ControlCenterPage.test.tsx
npm run typecheck
cd ..
git add src/tianshu/models/control_center.py src/tianshu/gateway/control_center_api.py src/tianshu/app.py tests/gateway/test_control_center.py web/src/api/control.ts web/src/hooks/useControlCenter.ts web/src/pages/ControlCenterPage.tsx web/src/pages/ControlCenterPage.test.tsx
git commit -m "feat: add the real Agent OS Control Center"
```

### Task 5: Rebuild Edict Detail on Decision, RunState, and Evidence

**Files:**
- Create: `web/src/pages/EdictDetailPage.test.tsx`
- Create: `web/src/components/governance/GovernanceContractCard.test.tsx`
- Create: `web/src/components/governance/DecisionPanel.test.tsx`
- Create: `web/src/components/evidence/EvidenceBundlePanel.test.tsx`
- Modify: `web/src/pages/EdictDetailPage.tsx`
- Modify: `web/src/api/edicts.ts`
- Modify: `web/src/hooks/useEdictDetail.ts`
- Modify: S3 gateway endpoints only if the composed read contract lacks a required field

**Interfaces:**
- Consumes: requested/effective Governance Contract, versioned RunState, DecisionRecordV1, EvidenceBundleV1/export endpoint.
- Produces: one page where requested vs effective contract, pending/resolved decision, run status, artifacts/checks, download, and governed replay are distinguishable.

- [ ] **Step 1: Write RED page and component tests**

Assert decision reason is mandatory; version conflict is shown without overwriting; resolved actions are locked; unsupported executor controls are absent; evidence hash/download are real; replay creates a governed request rather than direct re-execution; auditor and executor identities are separate.

- [ ] **Step 2: Implement by extracting focused page sections**

Keep `EdictDetailPage.tsx` as composition. Do not add another decision store in Web state; invalidate TanStack queries after authoritative mutations.

- [ ] **Step 3: Run and commit**

```bash
cd web
npm test -- --run src/pages/EdictDetailPage.test.tsx src/components/governance src/components/evidence
npm run typecheck
cd ..
git add web/src/pages/EdictDetailPage.tsx web/src/api/edicts.ts web/src/hooks/useEdictDetail.ts web/src/components/governance web/src/components/evidence
git commit -m "feat: expose durable decisions and Evidence on Edict detail"
```

### Task 6: Build the authoritative Evolution Center contract and page

**Files:**
- Create: `src/tianshu/models/evolution_view.py`
- Create: `src/tianshu/gateway/evolution_api.py`
- Create: `tests/gateway/test_evolution_view.py`
- Create: `web/src/pages/EvolutionCenterPage.tsx`
- Create: `web/src/pages/EvolutionCenterPage.test.tsx`
- Create: `web/src/api/evolution.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/layout/AppSidebar.tsx`
- Modify: `web/src/components/evolution/EvolutionGate.tsx`

**Interfaces:**

```python
class EvolutionCenterSnapshotV1(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["not_enabled", "enabled", "degraded"]
    reason_code: str
    candidates: tuple[EvolutionCandidateSummaryV1, ...]
    routing: tuple[EvolutionRoutingSummaryV1, ...]
    last_gate_hash: str | None
```

- [ ] **Step 1: Write RED truth tests**

Before S5, endpoint/page must say `not_enabled`, show no canary progress, no promotion button, and no fabricated candidate. With contract fixtures, render blocking gates, evidence hashes, assignment counts, and rollback state exactly.

- [ ] **Step 2: Implement the read contract and page**

Route `/evolution` to the new page; keep `/universes` as the existing department route. The page consumes only `EvolutionCenterSnapshotV1`; S5 later replaces `not_enabled` with real repository data without changing the Web contract shape.

- [ ] **Step 3: Run and commit**

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/gateway/test_evolution_view.py -q
cd web
npm test -- --run src/pages/EvolutionCenterPage.test.tsx src/components/evolution
npm run typecheck
cd ..
git add src/tianshu/models/evolution_view.py src/tianshu/gateway/evolution_api.py src/tianshu/app.py tests/gateway/test_evolution_view.py web/src/pages/EvolutionCenterPage.tsx web/src/pages/EvolutionCenterPage.test.tsx web/src/api/evolution.ts web/src/App.tsx web/src/components/layout/AppSidebar.tsx web/src/components/evolution/EvolutionGate.tsx
git commit -m "feat: add the truthful Evolution Center surface"
```

### Task 7: Add core-page browser, accessibility, visual, and performance Gates

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/fixtures.ts`
- Create: `web/e2e/control-center.spec.ts`
- Create: `web/e2e/decision-flow.spec.ts`
- Create: `web/e2e/evolution-gate.spec.ts`
- Create: `web/e2e/accessibility.spec.ts`
- Create: `web/e2e/visual-core.spec.ts`
- Create: approved baseline images under `web/e2e/__screenshots__/`
- Create: `docs/cc-fable-v1/reports/s4-core-web-report.md`
- Modify: `web/package.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/cc-fable-v1/PROGRESS.md`

**Interfaces:**
- Consumes: real demo stack and three core routes.
- Produces: machine-verifiable `automation_passed`; user visual approval remains separate.

- [ ] **Step 1: Add Playwright scripts and RED journeys**

Journeys: fresh onboarding→governed Edict; pending Decision→reasoned resolution→Evidence download; Evolution disabled/blocked fixture. Every journey fails on browser console error, failed network response without asserted error state, or mockData import.

- [ ] **Step 2: Add axe, keyboard, zoom, and visual matrices**

Run 1280×800 and 1440×1024, dark/light, expanded/collapsed on the three core routes. Axe serious/critical must be zero. Keyboard must reach all actions with visible focus. 200% zoom must not hide header/sidebar controls or horizontal-trap primary content.

- [ ] **Step 3: Enforce lazy/performance budgets**

Set recorded budgets after measuring the current build: initial route JS must exclude DAG/large department chunks; no single new core-page chunk exceeds the documented measured ceiling without an explicit report amendment. Do not invent LCP claims from an uncontrolled machine.

- [ ] **Step 4: Run the automated Gate**

```bash
cd web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright test
cd ..
```

Expected: zero failed tests, axe serious/critical=0, approved screenshot diffs clean.

- [ ] **Step 5: Write report and commit**

Report exact browsers/viewports, commands/counts, screenshot hashes, accessibility results, bundle measurements, `automation_passed`, and `user_approval_pending`. Explicitly list S4.8–S4.11 and VoiceOver as deferred.

```bash
git add web/playwright.config.ts web/e2e web/package.json web/package-lock.json .github/workflows/ci.yml docs/cc-fable-v1/reports/s4-core-web-report.md docs/cc-fable-v1/PROGRESS.md
git commit -m "test: automate the S4 core desktop Web Gate"
```

S5 may start after automation passes even while visual status remains `user_approval_pending`; never record `user_approved` without the user's explicit review.
