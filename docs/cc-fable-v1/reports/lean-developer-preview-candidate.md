# Lean Developer Preview Candidate

- source_commit: `ac51f3e0ecc34525d740078abc0abf31595c0e12`
- automation_status: `passed`
- visual_status: `user_approval_pending`
- publication_status: `not_authorized`
- full G4: `external_pending`
- full G5: `deferred`

## Immutable phase bindings

| Phase | Gate source commit | Report commit | Candidate manifest hash |
|---|---|---|---|
| `s1_g1_5` | `bbf84451a40f8f3450e080c939c82fba52428271` | `8c2303df525b05a69d1a6902c83b06c5fd50102d` | `93803a5d5a2340a24f106dcf2e461cf68e7abe64546093df1710a05c0918ea2f` |
| `s2_lean` | `bbf672e560ecd2c793a1a80d0cc262b41550a4db` | `66e59943b91bc708344c69b895eaa8cfc3298721` | `91a94ed9f5206c4cbb65b5fe7c15b59614ffa9c418b4ae8031a08a2526eb0ace` |
| `s3_core` | `60d3c45b836de44b132dba186e5c9a3672592ea3` | `2eb20d6dfd39b172f438c90aee5eaee507d8a227` | `76a47f1d25123a1898ad736b193badf3630fda1d035a628e8f00ba18b78bf4b1` |
| `s4_automation` | `303787916f1004362c3f250c07a8de179aa0885d` | `303787916f1004362c3f250c07a8de179aa0885d` | `1058c388db5c1706524b8689578a599d87e4f83e5d1dcb01f56ed44ca00d6184` |
| `s5_lean_core` | `f6777b435631ab3d5fa1aeac1a96cdbf2c424774` | `f6777b435631ab3d5fa1aeac1a96cdbf2c424774` | `463502efbe9d784e4b3f8c4970e4b353f4d6a608539808edbc5d8aabe5ff5370` |

Each manifest hashes the exact report bytes in the clean Candidate source; the
historical evidence commit above remains the immutable Gate execution identity.
No `external_pending` item is counted as an automated pass.

## Exact candidate artifacts and demo

- gate_evidence: `docs/cc-fable-v1/evidence/gates/20260719T074326Z-ac51f3e0ecc3/manifest.json`
- gate_evidence_hash: `ed34c71e2efeb13f6c136a1137cd62490f015e3193d11a06c20fe975453b6a54`
- build_provenance: `docs/cc-fable-v1/evidence/builds/20260719T074326Z-ac51f3e0ecc3/provenance.json`
- build_provenance_hash: `b6d20a9176f1c585ff63bb873b955db8ec9c05004eec2d917946e88f4dc901d9`
- demo_report: `docs/cc-fable-v1/evidence/lean-preview/20260719T074326Z-ac51f3e0ecc3/demo-report.json`
- demo_report_hash: `bcff7381b521dc993b62b0dec450075c1774aa6c4dfe72b086e97501c51de377`
- Wheel SHA-256: `5a967cdc833f77d795dcc683d37dc85ae093acff795af7e2f3d8603b6e311695`
- sdist SHA-256: `c5d34485908466f48114ca0bc6adb98c6e1ddd080d12de05ef96123afc537c48`
- build frontend: Python 3.12 with `build==1.5.0`
- Wheel origin: exact sdist extracted and built outside the repository

## Final automated Gates

| Exact command | Exact terminal summary | Passed | Failed | Skipped | Deselected | Warnings |
|---|---|---:|---:|---:|---:|---:|
| `.venv/bin/ruff check src tests` | All checks passed | 1 | 0 | 0 | 0 | 0 |
| `.venv/bin/ruff format --check src tests` | 889 files already formatted | 889 | 0 | 0 | 0 | 0 |
| `.venv/bin/mypy` | Success: no issues found in 132 source files | 132 | 0 | 0 | 0 | 0 |
| `.venv/bin/lint-imports` | Contracts: 2 kept, 0 broken | 2 | 0 | 0 | 0 | 0 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q` | 4411 passed, 2 skipped, 29 deselected, 6 warnings in 942.69s (0:15:42) | 4411 | 0 | 2 | 29 | 6 |
| `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/resources/test_wheel_manifest.py tests/packaging/test_fresh_wheel_demo.py tests/launch/test_lean_preview_fresh_wheel.py -q -s` | 28 passed, 4 warnings in 393.26s (0:06:33) | 28 | 0 | 0 | 0 | 4 |
| `npm ci` | npm ci completed; found 0 vulnerabilities | 1 | 0 | 0 | 0 | 0 |
| `npm run lint` | 35 problems (0 errors, 35 warnings) | 1 | 0 | 0 | 0 | 35 |
| `npm run typecheck` | tsc --noEmit completed | 1 | 0 | 0 | 0 | 0 |
| `npm test -- --run` | Tests  187 passed | 187 | 0 | 0 | 0 | 0 |
| `npm run build` | production build completed | 1 | 0 | 0 | 0 | 1 |
| `npx playwright test` | 41 passed (29.6s) | 41 | 0 | 0 | 0 | 0 |

## User final-approval checklist

- [ ] Review the three desktop pages and interactions; no approval artifact exists yet.
- [ ] Decide whether to authorize any publication action separately.
- [ ] Select any D8 deferred work only through a new approved plan.

No push, tag, release, upload, visibility change, or publication was performed.
