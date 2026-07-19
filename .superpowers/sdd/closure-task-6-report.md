# Closure Task 6 report: Lean Developer Preview Candidate

Date: 2026-07-18

Branch: `feat_cc_fable_v1`

Starting commit: `445b8f1`

Candidate source commit: `ddc8520f9abd3fc5014955c9c4ed4b17eec4aec0`

## Outcome

Closure Task 6 assembled and strictly verified the local Lean Developer Preview
Candidate. Automated scope is `passed`; visual and interaction review remains
`user_approval_pending`; publication remains `not_authorized`.

The task performed no push, tag, release, upload, registry publication, repository
visibility change, container build, OIDC work, mobile work, or deferred work-package
implementation. It did not run `uv` and did not change `uv.lock`.

Two small implementation corrections were committed before candidate evidence:

- `e76a1e5` makes Vite emit the package manifest as visible
  `src/tianshu/web/static/manifest.json`, because setuptools omits the former
  hidden `.vite` directory.
- `ddc8520` updates the Playwright package-manifest assertion to that supported
  visible path.

## Candidate artifacts

- Candidate JSON:
  `docs/cc-fable-v1/evidence/lean-preview-candidate.json`
- Candidate report:
  `docs/cc-fable-v1/reports/lean-developer-preview-candidate.md`
- Golden demo:
  `docs/cc-fable-v1/evidence/lean-preview/20260718T142607Z-ddc8520f9abd/demo-report.json`
- Golden demo content hash:
  `b23e3ea9150287374a98300ee204cc6fa89890543b142f036f0b0c551b4a6f17`
- Candidate content hash:
  `ea692a67f422460ec6a35438d7b80328dca5d7a262447ac4c2559f152285d539`
- sdist SHA-256:
  `4a94cad1cdc0f66baa1ca6fef1cf7b53e587d523d89f3bd5a27f5881c1aaea35`
- Wheel SHA-256:
  `3ff6fe4217daddd6db94e35cefb66970f44b3e9780bcbdbba3a14bfed17bf1c1`

The checker generated five canonical phase manifests under the local candidate
build directory and bound their hashes into the tracked Candidate JSON. Each
manifest hashes the exact historical phase-report bytes and retains the immutable
phase evidence commit. No `external_pending` item is counted as a phase pass.

## Exact sdist-to-Wheel boundary

The source tree was clean at `ddc8520` before either artifact was built. A fresh
Python 3.12 build environment contained pinned `build==1.5.0`, its
`pyproject-hooks==1.2.0` dependency, and `packaging==26.0`. Isolated PEP 517
builds obtained `setuptools==83.0.0` from a local wheel.

The sole sdist was built from the clean repository, inspected to contain one
`tianshu-0.4.2` root, and extracted outside the repository. The sole Wheel was
then built from that extracted sdist, also outside the repository. The final
Wheel contains the visible Vite manifest and exactly matches the current packaged
Web resource tree. Resource/manifest verification passed `13 tests` with four
unchanged third-party deprecation warnings.

No `uv` frontend or `uv.lock` participated in this path.

## Fresh exact-Wheel golden demo

The new non-fixture batch is
`20260718T142607Z-ddc8520f9abd`. It was installed into a fresh Python 3.12
environment with a fresh HOME, database, and workspace outside the repository.
Its import probe resolved `tianshu` from the installed exact Wheel, not the
source tree.

The externally restricted run passed:

- `1 passed, 4 warnings in 573.80s`;
- all 13 governed scenario steps `passed`;
- mandatory source and Wheel identities matched;
- the strict evidence verifier passed;
- descendant processes remained loopback-only;
- SIGTERM shutdown completed cleanly with no ResourceWarning or surviving server;
- SQLite `PRAGMA quick_check` returned `ok`; and
- the package-resource digest was unchanged across the run.

The four warnings are existing third-party deprecations from `lark_oapi` and
`websockets`.

## Final automated Gate truth

### Python static and architecture Gates

- Ruff check: all checks passed.
- Ruff format: 885 files already formatted.
- mypy: no issues in 132 source files.
- import-linter: 483 files, 1754 dependencies, two contracts kept, zero broken.

These were run before the two final commits. The only changes from that static
source to `ddc8520` are Web files, so all Python `src` and `tests` inputs are
byte-identical.

### Backend

The unrestricted `e0b5877` full non-slow Gate recorded:
`4362 passed / 2 skipped / 29 deselected / 8 warnings`.

`git diff --name-status e0b5877..ddc8520` contains only:

- `web/e2e/control-center.spec.ts`;
- `web/e2e/fixtures.ts`; and
- `web/vite.config.ts`.

Thus backend source and backend tests are byte-identical to the unrestricted Gate.
The final source was also exercised in managed shards:
`4337 passed / 2 skipped`. The remaining 25 non-slow nodes require DNS, loopback
binding, or process/sandbox capabilities denied by the managed environment; they
are covered by the byte-identical unrestricted result, not relabelled as fresh
managed passes.

### Packaging

The nominal 28-test packaging command is closed by non-overstated composite
evidence:

- current final-source Wheel/resource manifest: 13 passed;
- retained backend-equivalent fresh-HOME black box: 10 passed;
- unchanged clean-shutdown helper matrix: 4 passed; and
- current exact-Wheel, source-bound golden demo: 1 passed.

There were zero failures, zero required skips, and four third-party warnings.

### Desktop Web

- exact offline `npm ci`: 465 packages installed, 466 audited;
- lint: zero errors and 35 retained warnings;
- typecheck: passed;
- unit: 35 files and 187 tests passed;
- production build: 3720 modules passed, with the inherited shared
  `antd` chunk-size advisory; and
- desktop Playwright: 41-test coverage closed by the unrestricted 40/41
  baseline, the unique fixture remediation's focused 20/20 pass, and the final
  package-manifest focused 1/1 pass.

The managed full Playwright rerun could not bind its loopback Web server
(`EPERM`). Therefore this report does not claim a newly executed unrestricted
`41/41` run; it records the exact composite closure above.

### Dependency audits

The current offline npm audit cache reports zero full and zero production
vulnerabilities. Live `npm audit` and `npm audit --omit=dev` could not reach the
registry because DNS is denied. The last registry-backed `e0b5877` record remains
the authoritative live result: one low-severity development-only vulnerability
and zero production vulnerabilities. No dependency was changed in this task.

## Candidate checker

The checker fail-closed rejected the first final-Gate manifest because a formatter
left one trailing newline after otherwise canonical JSON. No candidate output was
accepted from that attempt. After removing that byte, the same checker command
passed and:

- verified the exact new non-fixture demo;
- recomputed sdist, Wheel, screenshot, report, and capability hashes;
- verified exactly 24 screenshot baselines;
- verified no tracked `.idea` paths;
- verified every required D8 deferred-work ID;
- wrote five phase manifests; and
- wrote the strict Candidate JSON and Markdown report.

## Capability and authority boundary

- Candidate product surface: desktop Web only; no mobile implementation or claim.
- Visual/interaction approval: `user_approval_pending`.
- VoiceOver/manual accessibility: `external_pending`.
- OpenHands, executor compatibility, ROI, cost calibration, and full G4:
  `external_pending`.
- Full G5, official container, PyPI, and GHCR: `deferred`.
- remote MCP and Candidate open stdio MCP: `disabled`; reopening remains
  `deferred`.
- publication: `not_authorized`.

## Focused verification

The candidate context, strict verifier, and schema suites passed
`118 tests / 0 failures / 4 third-party warnings`. The new demo was also
reverified independently with mandatory expected source and Wheel identities.
The public-document truth and local-link suite separately passed
`19 tests / 0 failures / 4 third-party warnings`.
Repository diff checks, Candidate artifact hashes, `uv.lock` zero-diff, and final
worktree cleanliness are verified immediately before and after the evidence
commit.

## User final-approval checklist

- [ ] Review the Control Center, Edict detail, and Evolution Center desktop pages.
- [ ] Review keyboard, theme, sidebar, locale, and critical governed interactions.
- [ ] Record visual/interaction approval separately if accepted.
- [ ] Authorize publication separately if desired.
- [ ] Select any D8 deferred work only through a new approved plan.

No unchecked item is silently treated as passed.
