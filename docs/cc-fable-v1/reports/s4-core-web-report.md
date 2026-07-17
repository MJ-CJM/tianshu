# S4 Core Web Gate Report

Date: 2026-07-17

Branch: `feat_cc_fable_v1`

Implementation base: `6ccf90a`, `81c3125`

Browser-found product fixes: `e69f50a`

Automation/report commit: `test: automate the S4 core desktop Web Gate` (this commit)

## 1. Verdict

```yaml
s4_core_web_automation: automation_passed
core_routes:
  control: automation_passed
  edict_detail: automation_passed
  evolution_disabled: automation_passed
visual_interaction_approval: user_approval_pending
voiceover_manual_audit: external_pending
s4_8_to_s4_11_department_depth: deferred
```

This gate covers the Lean S4 desktop boundary only: the truthful `/control`, authoritative
Edict detail, and pre-S5 disabled `/evolution` routes; real onboarding and Decision/Evidence
journeys; Chromium accessibility; deterministic core visuals; initial-route chunk isolation;
and CI wiring. It does not claim the deferred fourteen-department depth work, VoiceOver, a
cross-browser matrix, wheel/container/publication, OIDC, mobile, or S5 governed evolution.

## 2. Real-stack browser fixture

- Playwright `1.61.1`, Chromium, one worker, `en-US`, UTC.
- FastAPI is launched from this checkout with `.venv/bin/python -m uvicorn` on a random
  loopback port and serves the production Vite output from `src/tianshu/web/static`.
- Every stack receives a temporary HOME, SQLite database, artifact/log/memory/persona/plugin
  directories, `startup_profile=demo`, and `security_mode=trusted-local`; teardown terminates
  the process group and deletes the temporary tree.
- There is no login bypass and no production `mockData` import. Onboarding and Decision
  resolution use public APIs/UI. The deterministic closed-Evidence page fixture uses the
  canonical Storage, Decision repository, RunState models, ArtifactStore, and EvidenceService.
- Visual tests use their own worker-scoped real stack, preventing prior journey tests from
  changing Control counts. Evolution interception is restricted to the named `/api/evolution`
  read contract for the blocked-candidate contract test; the disabled-route test is unmocked.
- Browser console errors, page errors, request failures, and HTTP `>=400` responses fail the
  test. The Edict detail's documented optional DAG lookup is explicitly asserted as a `404`.

## 3. Governed journey and truth checks

The browser suite proves:

1. a fresh HOME opens onboarding, previews requested/effective governance, creates a real
   governed Edict, and navigates directly to its authoritative detail route;
2. a pending `plan_review` Decision rejects an empty reason, resolves with its real expected
   version, persists the reason, closes an Evidence Bundle v1, and downloads JSON whose
   `bundle_id` and `content_hash` equal the authoritative detail response;
3. `/control` is fed by its read contract and production source recursively contains no
   `mockData` import;
4. `/evolution` reports `s5_governed_evolution_not_enabled` before S5, while a named read
   contract can truthfully render a promotion-blocking candidate and evidence gate.

Browser-discovered product defects were fixed separately in `e69f50a`: onboarding navigation
lost a race against the onboarding cache redirect; SQLite Decision timestamps crossed the
Evidence model boundary as strings; dark tertiary/preset-tag contrast was below AA; Ant menu
and Segmented focus affordances were not consistently visible; sidebar/footer and Edict detail
content trapped 200% layouts; and PolicyTimeline nested a Select inside a Collapse button.

## 4. Accessibility result

Each core route has three independent checks:

| Route | axe serious/critical | Keyboard focus | 200% equivalent |
|---|---:|---:|---:|
| Control Center | 0 | pass | pass |
| Edict detail | 0 | pass | pass |
| Evolution disabled | 0 | pass | pass |

Result: `9 passed`. The zoom check halves the CSS viewport from `1280x800` and asserts the
header, sidebar, and sidebar control remain operable and the primary content has no horizontal
trap. This automated evidence does not substitute for VoiceOver; manual assistive-technology
review remains `external_pending`.

## 5. Visual result

The committed matrix is:

- three routes: Control, authoritative Edict detail, disabled Evolution;
- `1280x800` and `1440x1024`;
- dark and light themes;
- expanded and collapsed sidebar.

The visual baseline uses the user-facing `zh-classic` anchor locale. Governed journey and
automated accessibility coverage remain in English so locale-specific presentation evidence
does not replace the independent interaction and accessibility assertions.

Result: `24 passed` on generation and `24 passed` on a fresh-stack comparison run. The full
suite comparison also passes after the governed journey tests. Live timestamps, ULIDs, and
content hashes are masked with the active theme's container color; state, wording, layout,
geometry, theme, viewport, and sidebar presentation remain pixel-compared. The 24 PNG hashes
are committed in `web/e2e/__screenshots__/SHA256SUMS` and verified with
`shasum -a 256 -c`.

The baseline images are automated evidence, not user approval. Final presentation and
interaction status is therefore `user_approval_pending` as required by the S4 exit contract.

## 6. Performance and build evidence

Production Vite build measurements:

| Chunk | Minified | gzip |
|---|---:|---:|
| ControlCenterPage | 5.48 kB | 1.77 kB |
| EvolutionCenterPage | 5.57 kB | 1.81 kB |
| EdictDetailPage | 59.28 kB | 16.18 kB |
| DagBattleMapPage | 187.43 kB | 60.98 kB |
| shared antd | 1,086.34 kB | 337.50 kB |

The real browser network assertion proves the Control initial load fetches its own route chunk
and does not fetch `DagBattleMapPage`, `EdictDetailPage`, `PersonaDashboardPage`, or
`SystemManagementPage`. The inherited Vite warning for the shared Ant Design chunk above
500 kB remains visible and is not represented as optimized by this slice.

## 7. Verification ledger

| Command | Result |
|---|---|
| `npm ci` | pass |
| `npm run lint` | pass, 0 errors / 35 retained warnings |
| `npm run typecheck` | pass |
| `npm test -- --run` | 35 files / 186 tests passed |
| `npm run build` | pass, 3,718 modules transformed |
| `npm run e2e -- --reporter=line` | 39 passed in 30.5 s |
| accessibility subset | 9 passed |
| visual comparison subset | 24 passed |
| `.venv/bin/pytest tests/evidence/test_close_snapshot_immutable.py -q` | 3 passed |
| focused Ruff check/format | pass |
| screenshot SHA-256 verification | 24 passed |

The full E2E count is 39: 9 accessibility checks, 24 visual comparisons, and 6 governed
journey/contract/performance checks. CI now has a separate `web-e2e` job with Node 20, frozen
Python 3.12/all-extras sync, production Web build, Chromium install, and the same E2E command.

## 8. Deferred boundary

- S4.8-S4.11 departmental depth remains deferred; their retained navigation does not expand
  this gate's three-route claim.
- VoiceOver and the complete manual accessibility/cross-browser review remain
  `external_pending`.
- Visual/interaction approval remains `user_approval_pending` until the user reviews the
  committed matrix.
- No wheel, container, public publication, OIDC, mobile, or S5 promotion/routing claim is made.
