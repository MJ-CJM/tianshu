# Tianshu Agent OS Rebaselined Execution Plan

> **For agentic workers:** If the runtime provides
> `superpowers:subagent-driven-development`, use it to implement this plan one
> reviewed, committed slice at a time. If it is unavailable, continue manually
> with the fully specified TDD → focused verification → independent review →
> commit → ledger workflow below; a private skill is not a product dependency.

**Goal:** Complete the remaining G1 work and G2-G5 so Tianshu truthfully ships as
a governable, verifiable, continuously growing self-evolving Agent OS, with a
production desktop Web experience and reproducible open-source evidence.

**Architecture:** Keep one integration owner and default to one implementation
slice at a time. After S3 freezes the relevant G2 gateway contracts, at most two
explicitly non-conflicting slices with disjoint files may run in parallel; the
integration owner remains unique. Shared contracts, migrations, public entry
points, and authority services are always serialized. Read-only investigation
and independent review may run alongside implementation. Existing detailed
phase plans remain technical references, while this document owns sequencing,
slice boundaries, gate states, and approval authority.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, pytest, React 18,
TypeScript 5.6, Ant Design 5, TanStack Query 5, Vitest, Testing Library,
Playwright, axe-core, Docker/OCI, GitHub Actions.

## Global Constraints

- Preserve completed G0 and G1.1-G1.4b2 behavior and migration checksums.
- Start in the current `feat_codex_phase_1` checkout because the approved
  G1.4b3 work is uncommitted there; do not reset, discard, or overwrite it.
- One slice owns one authority boundary or one vertical product behavior.
- Target at most 800 production lines and 1,500 total changed lines per slice,
  excluding generated artifacts. If a slice exceeds the target or spans two
  unrelated responsibilities, stop and split it before continuing.
- Each slice must show RED, GREEN, focused regression, relevant static checks,
  independent spec and quality review, a commit, and a progress-ledger update.
- Only the integration owner starts broad/full regression. Subagents must not
  duplicate full-suite runs.
- `migrations.py`, `app.py`, CLI registration, public API contracts, and shared
  authority services have one writer at a time.
- Migration numbers are allocated only after the previous Gate is committed:
  read latest version `N`, append `N+1`, and never reuse the stale hard-coded
  versions in older G2/G4 plans.
- Record `implemented`, `focused_verified`, `automation_passed`,
  `external_pending`, `user_approval_pending`, and `passed` as distinct states.
- Local fixtures, explicit demo-provider evidence, CI evidence, and real
  external evidence are never interchangeable.
- Only desktop Web is implemented and accepted. Do not add mobile navigation,
  mobile breakpoints, or a mobile test project.
- Preserve `web/public/brand.png` byte-for-byte with SHA-256
  `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`.
- Preserve the exact header copy `天枢`,
  `成功只有一个——按照自己的方式，去度过人生。`, and
  `彩蛋 / 通用 / English / 实时 / 通政`.
- Preserve `中枢总览`, the four groups and fourteen departments, and the
  bottom theme plus sidebar-collapse controls.
- Chinese governance actions use `裁决`; never introduce `批红`, `朱批`, or
  `司礼监代批`.
- The restrained New Chinese visual direction remains “墨为骨、朱为睛、纸为气”.
  No neon, strong gradients, large gold surfaces, palace/dragon decoration,
  glassmorphism, heavy shadows, or exaggerated motion.
- Production Web never imports prototype `mockData` or falls back to example
  data. Missing/failed APIs render truthful unavailable/error states.
- External publication, repository visibility changes, tags, PyPI/GHCR pushes,
  and public release remain outside this approval and require final user
  authority.

## Evidence and Commit Gate for Every Slice

- [ ] Write the narrow failing contract/regression test and observe the expected
      failure before production code changes.
- [ ] Implement the smallest behavior that satisfies the test.
- [ ] Run the named focused suite and the regressions for the touched authority.
- [ ] Run relevant Ruff/format/mypy/import-boundary or Web
      lint/typecheck/test/build checks.
- [ ] Obtain independent spec-compliance and code-quality verdicts.
- [ ] Fix every Critical and Important finding and re-review.
- [ ] Commit only the slice files with a specific message.
- [ ] Append commit and evidence to `.superpowers/sdd/progress.md`.
- [ ] Confirm no unowned changes or duplicate broad test process exists.

## S0 - Close G1.4b3 Governed Apply

**Goal:** Convert the current reviewed governed-apply implementation into
traceable commits and a complete G1.4b3 Gate without losing user work.

**Technical brief:** `../design/14-g1.4b3-governed-apply-brief.md`

### Task S0.1: Freeze and classify the current change set

- [ ] Record the exact base commit, dirty paths, diff statistics, environment,
      migration prefix, and focused-test inventory.
- [ ] Map each changed file to persistence/domain, anchored filesystem/Git, or
      REST/Auth/CLI/capability ownership.
- [ ] Scan for placeholders, missing registration, migration rewrites, leaked
      secrets, unsafe paths, and unowned files.

### Task S0.2: Close persistence and immutable authority bindings

- [ ] Verify the additive V5 migration, models, repository transitions,
      token/principal binding, receipts, terminal states, and backup upgrade.
- [ ] Fix any discovered behavior test-first, run storage/migration/service
      focused tests, review, and commit the persistence boundary.

### Task S0.3: Close anchored filesystem, Git, apply, and rollback

- [ ] Verify root-anchored operations, symlink/TOCTOU rejection, publication
      identity, object/ref/index fingerprints, mode preservation, CAS, journal,
      synchronous rollback, and postimage ownership.
- [ ] Fix any discovered behavior test-first, run apply/Git/workspace focused
      tests, review, and commit the execution boundary.

### Task S0.4: Close REST, Auth, CLI, and capability truth

- [ ] Verify status/changes/decision/apply routes, server-derived actor,
      `workspace:apply`, token non-disclosure, CLI input/output/exit codes, and
      exact adapter evidence.
- [ ] Fix any discovered behavior test-first, run REST/Auth/CLI/capability
      focused tests, review, and commit the public surface.

### Task S0.5: Run the single final G1.4b3 Gate

- [ ] Run all named G1.4b3 focused suites and project static gates.
- [ ] Run one complete `pytest -m "not slow"` from the project Python and save
      its complete exit evidence.
- [ ] Complete independent security/spec/quality review with Critical and
      Important findings equal to zero.
- [ ] Write the G1.4b3 report, update the ledger, and confirm a clean worktree.

## S1 - G1.5 Self-contained Wheel and Offline Experience

**Detailed brief:** `../design/15-g1.5-wheel-demo-doctor-brief.md`

- [ ] **S1.1:** Package immutable resources and inspect the exact wheel manifest.
- [ ] **S1.2:** Add writable overlay precedence and the idempotent six-persona
      data migration without overwriting user customizations.
- [ ] **S1.3:** Add the explicit deterministic zero-network demo provider at the
      common dispatch boundary, with no live-provider fallback.
- [ ] **S1.4:** Add a read-only structured Doctor plus distinct liveness and
      readiness contracts with safe redaction.
- [ ] **S1.5:** Build once and run the exact wheel from a repo-external fresh HOME,
      complete one governed demo Edict, and verify clean shutdown.

## S2 - G1.6 Public-safe Security and Release Baseline

**Detailed brief:** `../design/19-g1.6-security-release-brief.md`

- [ ] **S2.1:** Append immutable tamper-evident SystemAudit storage.
- [ ] **S2.2:** Add scoped audit read/export and transactional security-event
      coverage with allowlisted metadata.
- [ ] **S2.3:** Migrate MCP secrets to ciphertext, test key rotation/corruption,
      and preserve explicit legacy-sensitive recovery semantics.
- [ ] **S2.4:** Enforce remote MCP HTTPS/SSRF/DNS/redirect/proxy policy and deny
      secure-remote egress without a real enforcement boundary.
- [ ] **S2.5:** Enforce short-lived stdio admission grants, approved tool sets,
      complete drift binding, and process-tree lifecycle receipts.
- [ ] **S2.6:** Build the exact-wheel non-root minimal container and daemon-backed
      smoke when available.
- [ ] **S2.7:** Add CI, SBOM, scanning, threat model, release dry-run, and the full
      G1 Developer Preview Gate.

## S3 - G2 Durable Governance and Evidence

**Detailed reference:**
`./12-g2-durable-governance-evidence.md`

- [ ] **S3.1:** Freeze the complete G1 handoff and allocate migrations from the
      actual latest version.
- [ ] **S3.2:** Add atomic Edict Application Service and Unit of Work.
- [ ] **S3.3:** Add the durable outbox and route every submission ingress through
      persisted envelopes.
- [ ] **S3.4:** Add persistent DecisionRequest and versioned RunState.
- [ ] **S3.5:** Route REST, WS, CLI, Bot, and MCP decision surfaces through the
      durable authority.
- [ ] **S3.6:** Add attempts, leases, fencing, reconciliation, and DLQ.
- [ ] **S3.7:** Add supported side-effect intent and receipt journal semantics.
- [ ] **S3.8:** Resume Agent and outer-loop work from durable continuations.
- [ ] **S3.9:** Persist planner revisions, replan reasons, and quality evidence.
- [ ] **S3.10:** Add content-addressed ArtifactStore.
- [ ] **S3.11:** Close immutable Evidence Bundle v1.
- [ ] **S3.12:** Extend audit/OTel/readiness and durable notification delivery.
- [ ] **S3.13:** Run the distributed fault matrix and final G2 Gate.

## S4 - G3 Production Desktop Web

**Detailed reference:**
`./13-g3-desktop-web-productization.md`

- [ ] **S4.1:** Freeze Web contracts, brand hash/copy, fixtures, and test baseline.
- [ ] **S4.2:** Implement the restrained New Chinese design system and frozen
      desktop shell with default dark mode, persisted theme/sidebar, and lazy
      routes.
- [ ] **S4.3:** Unify API errors, AuthContext, React Query behavior, and truthful
      page-state components.
- [ ] **S4.4:** Add governance/evidence/evolution components and resumable
      first-run onboarding through real backend APIs.
- [ ] **S4.5:** Add the real Control Center aggregate and `/control` page.
- [ ] **S4.6:** Rebuild Edict Detail around Governance Contract, RunState,
      durable Decision, Evidence Bundle, download, and governed replay.
- [ ] **S4.7:** Add the authoritative Evolution Center; show real routing as
      disabled until G4 proves it.
- [ ] **S4.8:** Converge 御书房 and 文书房 on durable decision/recovery truth.
- [ ] **S4.9:** Converge 内阁、廷议、都察院、权印司 on planner/audit/policy truth.
- [ ] **S4.10:** Converge 百官阁、文渊阁、位面、考成 on dynamic
      persona/memory/evidence truth.
- [ ] **S4.11:** Converge 藏兵阁、鸿胪寺、通政司、户部账房 on
      capability/delivery/cost truth.
- [ ] **S4.12:** Run real-stack Playwright, A11y, keyboard, zoom, visual,
      performance, and CI gates; record `user_approval_pending` together with
      the immutable `automation_passed` evidence.

## S5 - G4 Governed Evolution and Executor Neutrality

**Detailed reference:**
`./14-g4-governed-evolution-executors.md`

- [ ] **S5.1:** Add unified immutable candidates and five domain adapters.
- [ ] **S5.2:** Route all skill installation channels through one guarded supply
      chain and candidate staging boundary.
- [ ] **S5.3:** Add the evidence-bound fail-closed GateEvaluator and read API.
- [ ] **S5.4:** Make PromotionService the sole canary/promote/rollback authority.
- [ ] **S5.5:** Persist real challenger assignments and effective overlays, prove
      distribution/restart/rollback behavior.
- [ ] **S5.6:** Freeze the executor compatibility suite and Native/OpenHands
      adapter boundary without overstating fake evidence.
- [ ] **S5.7:** Collect real pinned OpenHands managed evidence; remain
      `external_pending` when unavailable.
- [ ] **S5.8:** Add observable FTS rebuild, prompt-layer budgets, and paired
      memory/profile ROI evidence.
- [ ] **S5.9:** Add calibrated cost intervals and honest managed/contained/
      observed budget enforcement evidence.
- [ ] **S5.10:** Run the complete G4-A/B/C truth Gate.

## S6 - G5 Open-source Launch and Ecosystem

**Detailed reference:**
`./15-g5-open-source-launch.md`

- [ ] **S6.1:** Freeze launch schemas and evidence-state transitions.
- [ ] **S6.2:** Publish the stable Executor SDK facade, adapter template, and
      compatibility kit.
- [ ] **S6.3:** Add a public-API demo runner and immutable evidence verifier.
- [ ] **S6.4:** Implement the `leave-it-running` golden demo and danger set.
- [ ] **S6.5:** Implement the governed skill-evolution golden demo and danger set.
- [ ] **S6.6:** Implement the same-contract Native/OpenHands/contained comparison
      demo and danger set.
- [ ] **S6.7:** Build reproducible core/server/all profiles and the exact-wheel
      non-root container.
- [ ] **S6.8:** Add SBOM, NOTICE/licenses, provenance, release workflows,
      repository hygiene, and community surfaces.
- [ ] **S6.9:** Collect three independent external environments, real managed
      demo, real cost window, VoiceOver/user evidence, and prepare the final
      release candidate without publishing it.

## Sequencing and Approval

1. S0, S1, S2, and S3 shared authority work is serial.
2. After S3 freezes the relevant G2 gateway contracts, at most one S4.1-S4.4
   presentation/foundation slice may overlap with one non-conflicting later G2
   slice. The two slices must have disjoint files and named owners; the single
   integration owner serializes shared-contract changes and integration. Real
   pages wait for their named backend contracts.
3. S5 begins only after G2 is passed and G3 automation is green.
4. S6 begins only after G4 public contracts and truth state are frozen.
5. G3 real pages remain `user_approval_pending` until the final consolidated
   user review requested by the approved continuous-execution instruction.
6. G5 publication remains `user_approval_pending`; do not make external public
   state changes without a new explicit instruction.

## Final Acceptance

- [ ] Every completed local slice has a reviewed commit and durable ledger entry.
- [ ] Required local and CI Gates are green on the final candidate commit.
- [ ] External-unavailable evidence remains explicitly pending, never fabricated.
- [ ] Desktop 1280/1440 dark/light expanded/collapsed product evidence is ready.
- [ ] Governance, Evidence, Evolution, executor, cost, and release truth are
      mutually consistent in code, UI, docs, and capability matrices.
- [ ] Final candidate is presented to the user for one consolidated approval.
