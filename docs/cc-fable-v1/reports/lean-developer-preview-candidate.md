# Lean Developer Preview Candidate

- source_commit: `01da3844dde77b5a9e56f346bed9b2605f7bc832`
- automation_status: `passed`
- visual_status: `user_approval_pending`
- publication_status: `not_authorized`
- full G4: `external_pending`
- full G5: `deferred`

## Immutable phase bindings

| Phase | Gate source commit | Report commit | Candidate manifest hash |
|---|---|---|---|
| `s1_g1_5` | `bbf84451a40f8f3450e080c939c82fba52428271` | `8c2303df525b05a69d1a6902c83b06c5fd50102d` | `9a45d52bb57e46ded5d4563fac846aa3540908c52e4e22c933d1db9ba7ab21c3` |
| `s2_lean` | `bbf672e560ecd2c793a1a80d0cc262b41550a4db` | `66e59943b91bc708344c69b895eaa8cfc3298721` | `362173e658cc2755694127a51156863635fecd59207d49a8f62f6f53304f18fd` |
| `s3_core` | `60d3c45b836de44b132dba186e5c9a3672592ea3` | `2eb20d6dfd39b172f438c90aee5eaee507d8a227` | `c3ed88cfddca3724283ba20e204d28a47320dc7172037125177e59e88cfcd9b4` |
| `s4_automation` | `303787916f1004362c3f250c07a8de179aa0885d` | `303787916f1004362c3f250c07a8de179aa0885d` | `8aee1b81ec3dea9410293ab8241b17cc8c8471c643b39a05ae80caaeed50a697` |
| `s5_lean_core` | `f6777b435631ab3d5fa1aeac1a96cdbf2c424774` | `f6777b435631ab3d5fa1aeac1a96cdbf2c424774` | `1c465f5305ddcb391f4b1d8c4d772d48e489970bb7ca1cf1eea138efa19b03ff` |

Each manifest hashes the exact report bytes in the clean Candidate source; the
historical evidence commit above remains the immutable Gate execution identity.
No `external_pending` item is counted as an automated pass.

## Exact candidate artifacts and demo

- gate_evidence: `docs/cc-fable-v1/evidence/gates/20260719T083725Z-01da3844dde7/manifest.json`
- gate_evidence_hash: `1eae63a5f65445642ddad000ca37b903b8d5ac7c0e88f036cb8b2e44e2dae395`
- build_provenance: `docs/cc-fable-v1/evidence/builds/20260719T083725Z-01da3844dde7/provenance.json`
- build_provenance_hash: `9211f412f69c7a2f278a4836f7a54dbaaaaa10c4fe391ba8ddf189c57535ff6e`
- demo_report: `docs/cc-fable-v1/evidence/lean-preview/20260719T083725Z-01da3844dde7/demo-report.json`
- demo_report_hash: `f199ea665c913f4fa84257e1e25fb8856fa34e6804d352be42baa439478e28e5`
- Wheel SHA-256: `bb1c0ca64cc125713863dfe4a927b5f8bc35ec0ff06a7d25b73ad3e121521f76`
- sdist SHA-256: `502bd0d913f897c24d9b8d31c43141b79759e64e339dda065dde8ebc7ab74fea`
- build frontend: Python 3.12 with `build==1.5.0`
- Wheel origin: exact sdist extracted and built outside the repository

## Final automated Gates

| Exact command | Exact terminal summary | Passed | Failed | Skipped | Deselected | Warnings |
|---|---|---:|---:|---:|---:|---:|
| `.venv/bin/ruff check src tests` | All checks passed | 1 | 0 | 0 | 0 | 0 |
| `.venv/bin/ruff format --check src tests` | 889 files already formatted | 889 | 0 | 0 | 0 | 0 |
| `.venv/bin/mypy` | Success: no issues found in 132 source files | 132 | 0 | 0 | 0 | 0 |
| `.venv/bin/lint-imports` | Contracts: 2 kept, 0 broken | 2 | 0 | 0 | 0 | 0 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q` | 4412 passed, 2 skipped, 29 deselected, 4 warnings in 861.64s (0:14:21) | 4412 | 0 | 2 | 29 | 4 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py tests/packaging/test_fresh_wheel_demo.py tests/launch/test_lean_preview_fresh_wheel.py -q -s` | 28 passed, 4 warnings in 326.43s (0:05:26) | 28 | 0 | 0 | 0 | 4 |
| `npm ci` | npm ci completed; found 0 vulnerabilities | 1 | 0 | 0 | 0 | 0 |
| `npm run lint` | 35 problems (0 errors, 35 warnings) | 1 | 0 | 0 | 0 | 35 |
| `npm run typecheck` | tsc --noEmit completed | 1 | 0 | 0 | 0 | 0 |
| `npm test -- --run` | Tests  187 passed | 187 | 0 | 0 | 0 | 0 |
| `npm run build` | production build completed | 1 | 0 | 0 | 0 | 1 |
| `npx playwright test` | 41 passed (35.2s) | 41 | 0 | 0 | 0 | 0 |

## User final-approval checklist

- [ ] Review the three desktop pages and interactions; no approval artifact exists yet.
- [ ] Decide whether to authorize any publication action separately.
- [ ] Select any D8 deferred work only through a new approved plan.

No push, tag, release, upload, visibility change, or publication was performed.
