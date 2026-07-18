# Closure Task 2 remediation report

Date: 2026-07-18
Base: `1fed71b94cf52ae1080ce23625a84641163513af` on `feat_cc_fable_v1`

## Scope closed

- The runner and verifier now require the golden candidate and both effective overlays to be the
  same `skill` subject, with canary bound to the candidate package refs and post-rollback bound to
  the champion package refs. A fully rehashed, schema-valid `code` candidate/overlay splice is
  rejected.
- Canary and rollback requests retain redacted public request bindings. The runner checks echoed
  batch-derived idempotency keys and the public action/version/receipt relations. The verifier
  derives the exact keys from `report.batch_id`, checks request path/body-hash/expected-version
  relations, and recomputes deterministic completed journal IDs from the authenticated principal.
- `verify_demo_evidence` requires both expected build-identity keyword arguments; omission raises
  `TypeError`, and all non-negative direct callers pass explicit values.
- Demo evidence is accepted only at `<batch-id>/demo-report.json` paired with the sibling
  `<batch-id>/artifacts` directory. The batch ID must match the real batch-root leaf; report,
  batch-root, artifact-root, ancestor-component, leaf-symlink, and escape variants are rejected.

## TDD and verification

- Baseline: `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_lean_preview_runner.py tests/launch/test_lean_preview_verifier.py -q`
  -> `25 passed`.
- Runner RED: `... pytest tests/launch/test_lean_preview_runner.py -q`
  -> `5 failed, 2 passed` for missing principal/request proof, code-candidate acceptance,
  overlay-subject mismatch, and canary/rollback echoed-key mismatch.
- Runner GREEN: same command -> `7 passed`.
- Verifier RED: `... pytest tests/launch/test_lean_preview_verifier.py -q`
  -> `21 failed, 21 passed` (20 reviewer probes plus one caller requiring its actual Wheel hash).
- Verifier GREEN: same command -> `42 passed`.
- Task 1+2 launch gate: `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch/test_lean_preview_schemas.py tests/launch/test_lean_preview_runner.py tests/launch/test_lean_preview_verifier.py -q`
  -> `104 passed`.
- Launch plus public-doc truth regression: `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch tests/test_public_docs_truth.py -q`
  -> `116 passed`.
- `ruff check` on the four Python files -> passed; `ruff format --check` -> four files already
  formatted; `.venv/bin/mypy` -> `Success: no issues found in 132 source files`.
- `git diff --check` -> passed.

The pytest runs retain four pre-existing third-party deprecation warnings from `lark_oapi` and
`websockets`; there are no new project warnings.

## Public journal boundary

The public `/api/auth/me` response, batch-derived idempotency key, receipt fields, and documented
deterministic ID relation are sufficient to verify the completed receipt's `journal_id`. There is
no public endpoint that reads promotion journal entries, so this remediation does **not** claim to
verify the private journal entry body, its stored `request_hash`, or other non-public columns.
