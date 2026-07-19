# Task 6 process-boundary remediation report

Date: 2026-07-19

Branch: `feat_cc_fable_v1`

Base: `2f8548d02f4e3b37cbbc1a57cc6985aef540b4c1`

## Outcome

The two Lean Preview maintainer recorders now launch commands through the existing
repository-wide low-level process boundary, `AsyncioProcessBackend`. The architecture
allowlist was not expanded, and no dynamic import, configurable command framework, Gate,
build, demo, publication, push, or tag was added or run.

The existing untracked build and failed-Gate evidence under
`docs/cc-fable-v1/evidence/builds/` and `docs/cc-fable-v1/evidence/gates/` was retained
unchanged and excluded from the commit.

## Root cause

`scripts/record_lean_preview_build_provenance.py` and
`scripts/record_lean_preview_gates.py` used `subprocess.run` directly. The exact-site
architecture guard permits process creation only at the established low-level boundary,
so the final backend Gate correctly rejected both recorder launch sites.

## Minimal remediation

- Added `scripts/_trusted_local_process.py`, a small shared synchronous adapter over
  `AsyncioProcessBackend` for these two synchronous maintainer scripts.
- The adapter makes its authority explicit: trusted-local execution, host mode with
  host execution allowed, and unrestricted network semantics.
- It preserves argv, cwd, inherited or supplied environment, and collects stdout,
  stderr, and return code. Recorder logs contain stdout followed by stderr and retain
  their canonical SHA-256 binding.
- Both recorders retain the fixed command set, first-error stop, failed-log retention,
  no-pass-manifest behavior, cwd/environment records, and non-zero exit reporting.

No architecture exemption or generalized runner/configuration surface was introduced.

## TDD evidence

The architecture failure was reproduced first:

- `1 failed` in
  `tests/architecture/test_no_direct_process_launch.py::test_repository_has_only_exact_process_launch_sites`;
- findings were exactly the build recorder's `_run_build -> subprocess.run` and the
  Gate recorder's `record_gate_evidence -> subprocess.run`.

Before production changes, focused behavior tests failed because the shared helper and
recorder boundary function did not exist. The RED tests cover:

- exact argv, cwd, inherited/supplied environment;
- explicit trusted-local/host/unrestricted backend arguments;
- stdout and stderr collection;
- non-zero return-code preservation;
- failed build/Gate log retention; and
- Gate first-error stop with no passing manifest.

After the minimal implementation, the helper, both recorder suites, and the full
architecture guard passed `26 tests / 0 failures / 4 unchanged third-party warnings`.

## Verification

Python 3.12.12 project environment:
`/Users/chenjiamin/tiangong/tianshu-worktree/tianshu/.venv` with the current worktree
`src` on `PYTHONPATH`.

- Candidate evidence regression suites, including both recorders, Candidate evidence,
  Candidate context, strict verifier, and schema parity:
  `162 passed / 0 failed / 4 unchanged third-party warnings`.
- Architecture guard plus helper and both recorder suites:
  `26 passed / 0 failed / 4 unchanged third-party warnings`.
- Ruff check on all touched Python: `All checks passed!`.
- Ruff format check on all touched Python: all 6 files formatted.
- Configured mypy Gate: `Success: no issues found in 132 source files`.
- Shared-helper mypy: `Success: no issues found in 1 source file`.
- `git diff --check`: passed.
- `uv.lock`: zero diff.

## Concerns and retained boundaries

- stdout and stderr are captured independently by the existing backend and appended in
  that order to the single recorder log; bytes within each stream are unchanged and the
  resulting exact log bytes remain hash-bound.
- This source repair does not turn the retained failed Gate batch into passing evidence.
  A later authorized clean-source Gate run must create a new canonical batch.
- No full Gate, real build, exact-Wheel demo, or Candidate assembly was run in this task.
