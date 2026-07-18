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

## Final Task 2 remediation

- Collection-time verification now rejects a closed bundle from another submitted/completed run;
  a gate whose candidate identity, digest, version, snapshot, or evidence-bundle IDs do not bind to
  the fetched candidate; and canary/post-rollback assignments whose run, candidate, routing receipt,
  overlay assignment, skill subject, or selected digest is spliced. The failed batch is retained,
  the rejecting step is `failed`, every later step is `blocked`, and no adversarial run can report
  all 13 steps passed.
- The verifier enumerates the artifact root before any suffix filtering. Its entry set must be the
  exact 13 canonical names, and every entry must be a regular non-symlink file; extra files of any
  suffix, directories, symlinks, FIFOs/special files, and missing files are rejected.
- The runner has one implementation source at `src/tianshu/lean_preview_demo.py`, uses only public
  HTTP plus the standard library, and is installed as `tianshu-lean-demo`. The legacy source-tree
  script is only a seven-line standard-library `runpy` delegate, and the example uses the installed
  command.

### Final TDD and verification evidence

- Baseline before the final remediation:
  `env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/launch tests/test_public_docs_truth.py -q`
  -> `116 passed`.
- Entrypoint RED before moving the implementation: three contract failures (missing project script,
  missing installed module, and a non-thin wrapper). After the move those three passed.
- Focused invariant RED after the move:
  `... pytest tests/launch/test_lean_preview_entrypoint.py tests/launch/test_lean_preview_runner.py::test_runner_rejects_cross_run_gate_and_assignment_splices_at_collection_time tests/launch/test_lean_preview_verifier.py::test_verifier_rejects_every_unexpected_artifact_root_entry -q`
  -> `17 failed, 3 passed` (13 runner cross-chain splices and four artifact-root entry classes were
  accepted). Focused GREEN -> `20 passed`.
- Full runner/verifier/entrypoint regression -> `69 passed`; full launch plus public-doc truth gate
  -> `136 passed` with only the same four third-party deprecation warnings.
- Wheel verification used `.venv/bin/python`, the repository's in-tree PEP 517 backend, and an
  already-present read-only `setuptools 83.0.0` cache on `PYTHONPATH`; it did not invoke `uv` or
  touch `uv.lock`. The built Wheel contained `tianshu/lean_preview_demo.py`, declared
  `tianshu-lean-demo = tianshu.lean_preview_demo:main`, and passed a fresh isolated Wheel import plus
  `--help` invocation.
- Focused Ruff and format checks passed; `.venv/bin/mypy` reported no issues in 132 source files;
  `git diff --check` passed.
