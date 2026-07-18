# Lean Developer Preview Candidate

- source_commit: `ddc8520f9abd3fc5014955c9c4ed4b17eec4aec0`
- automation_status: `passed`
- visual_status: `user_approval_pending`
- publication_status: `not_authorized`
- full G4: `external_pending`
- full G5: `deferred`

## Immutable phase bindings

| Phase | Immutable evidence commit | Candidate manifest hash |
|---|---|---|
| `s1_g1_5` | `bbf84451a40f8f3450e080c939c82fba52428271` | `cb483fddd03b6e3d48cde2441f58b9eae03bf981ccda8e4ce4a16db102c922f4` |
| `s2_lean` | `bbf672e560ecd2c793a1a80d0cc262b41550a4db` | `4854d9c862d3d5f06d66328438dab6081527be08e1e46b57bceb79496525da38` |
| `s3_core` | `60d3c45b836de44b132dba186e5c9a3672592ea3` | `150e17ae502b59b7ae3d910ac17fc868c2b5ecf82ec83917b41207f799c29d0b` |
| `s4_automation` | `303787916f1004362c3f250c07a8de179aa0885d` | `5758d92a78a14599d2ae51b069aab1fd8e15e2f2fc55c6f874ca2fb07df0892e` |
| `s5_lean_core` | `f6777b435631ab3d5fa1aeac1a96cdbf2c424774` | `a7d4bce4050c9d59e20142b322322696e7c215371200a8a4fda4a63910c06fb9` |

Each manifest hashes the exact report bytes in the clean Candidate source; the
historical evidence commit above remains the immutable Gate execution identity.
No `external_pending` item is counted as an automated pass.

## Exact candidate artifacts and demo

- demo_report: `docs/cc-fable-v1/evidence/lean-preview/20260718T142607Z-ddc8520f9abd/demo-report.json`
- demo_report_hash: `b23e3ea9150287374a98300ee204cc6fa89890543b142f036f0b0c551b4a6f17`
- Wheel SHA-256: `3ff6fe4217daddd6db94e35cefb66970f44b3e9780bcbdbba3a14bfed17bf1c1`
- sdist SHA-256: `4a94cad1cdc0f66baa1ca6fef1cf7b53e587d523d89f3bd5a27f5881c1aaea35`
- build frontend: Python 3.12 with `build==1.5.0`
- Wheel origin: exact sdist extracted and built outside the repository

## Final automated Gates

| Exact command | Exact terminal summary | Passed | Failed | Skipped | Deselected | Warnings |
|---|---|---:|---:|---:|---:|---:|
| `.venv/bin/ruff check src tests` | All checks passed; final Python inputs unchanged | 1 | 0 | 0 | 0 | 0 |
| `.venv/bin/ruff format --check src tests` | 885 files already formatted; final Python inputs unchanged | 885 | 0 | 0 | 0 | 0 |
| `.venv/bin/mypy` | Success: no issues found in 132 source files; final Python inputs unchanged | 132 | 0 | 0 | 0 | 0 |
| `.venv/bin/lint-imports` | 483 files, 1754 dependencies, 2 contracts kept, 0 broken | 2 | 0 | 0 | 0 | 0 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q` | unrestricted e0 4362/2/29/8; ddc backend inputs byte-identical and final-source shards 4337 passed | 4362 | 0 | 2 | 29 | 8 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py tests/packaging/test_fresh_wheel_demo.py tests/launch/test_lean_preview_fresh_wheel.py -q -s` | composite 28: current resource 13, retained equivalent fresh-HOME 10, helper 4, current exact-Wheel golden 1 | 28 | 0 | 0 | 0 | 4 |
| `npm ci` | 465 packages installed, 466 audited, install audit 0 vulnerabilities | 1 | 0 | 0 | 0 | 0 |
| `npm run lint` | 0 errors and 35 retained warnings | 1 | 0 | 0 | 0 | 35 |
| `npm run typecheck` | TypeScript typecheck passed | 1 | 0 | 0 | 0 | 0 |
| `npm test -- --run` | 35 files and 187 tests passed | 187 | 0 | 0 | 0 | 0 |
| `npm run build` | 3720-module production build passed; inherited antd chunk-size advisory retained | 1 | 0 | 0 | 0 | 1 |
| `npx playwright test` | composite 41: unrestricted 40/41, unique fixture fix focused 20/20, final manifest focus 1/1 | 41 | 0 | 0 | 0 | 0 |

## User final-approval checklist

- [ ] Review the three desktop pages and interactions; no approval artifact exists yet.
- [ ] Decide whether to authorize any publication action separately.
- [ ] Select any D8 deferred work only through a new approved plan.

No push, tag, release, upload, visibility change, or publication was performed.
