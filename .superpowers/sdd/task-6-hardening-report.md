# Closure Task 6 hardening report

Date: 2026-07-19

Branch: `feat_cc_fable_v1`

Base: `5c2062fbee298250b7bc2ee51871d67a9bea9ddb`

## Outcome

The Candidate acceptance path now fails closed instead of accepting caller-authored
summaries. This commit is the source hardening stage only: it deliberately does not run
the full Gate, build release artifacts, or run a real demo. The previous tracked
Candidate JSON and Markdown report were withdrawn because their composite Gate claims
did not satisfy the hardened contract. Historical demo batches remain retained evidence
only and are not accepted as the next Candidate.

No push, tag, publication, container, registry, external-status upgrade, `uv` command,
or `uv.lock` change was performed. Visual approval remains
`user_approval_pending`; publication remains `not_authorized`.

## Implemented hardening

### Final Gate evidence

- Added `scripts/record_lean_preview_gates.py`, a small fixed-command recorder.
- It runs the exact required command set in order, captures combined stdout/stderr
  without synthesizing summaries, hashes each raw log, and writes canonical JSON under
  `docs/cc-fable-v1/evidence/gates/<batch>/`.
- It requires a clean committed source, refuses to overwrite a batch, stops at the first
  non-zero exit, retains the failed raw log, and does not write a passing manifest.
- The checker accepts only the exact command/cwd/environment records, canonical manifest
  hash, matching source commit, complete raw logs, matching log hashes, positive derived
  pass counts, zero failures/required skips, and one Playwright result of exactly
  `41 passed / 0 failed`.
- The ignored legacy `dist/lean-preview-candidate/final-gates.json` summary path and
  `--gate-manifest` assembly option were removed.

### Phase evidence

- Each phase now binds the fixed `gate_source_commit` and actual `report_commit` from the
  hardening brief.
- The checker reads the report bytes from the fixed report commit through the bounded Git
  backend and rejects any difference from the current report.
- Generated phase manifests include both commits; the independent strict verifier checks
  the exact fixed pair rather than accepting an arbitrary well-formed commit string.

### Build provenance

- Candidate assembly requires a tracked provenance path under
  `docs/cc-fable-v1/evidence/`.
- Provenance validation binds the source commit, Python 3.12 patch version,
  `build==1.5.0`, exact sdist/Wheel commands, hashed success logs, exact artifact hashes,
  and the Wheel's source-sdist hash.
- The sdist must have one safe root, regular files/directories only, no unsafe or duplicate
  members, exact bytes for committed `pyproject.toml` and `src/tianshu/**`, no uncommitted
  package source outside generated Web static resources, and a visible
  `src/tianshu/web/static/manifest.json` with no hidden `.vite/manifest.json`.
- The Wheel package/Web payload must exactly equal the sdist package payload and bind its
  own artifact hash.

### Candidate contract and fail-closed repository state

- The model and JSON Schema require `gate_evidence_ref/hash` and
  `build_provenance_ref/hash`.
- Assembly accepts explicit tracked Gate, provenance, and demo paths; validates all three;
  embeds their refs and full-file SHA-256 hashes; and renders them in the report.
- The strict verifier confines and re-hashes both bound artifacts.
- The known composite/unbound Candidate JSON and Markdown report were deleted. README,
  progress, launch, usage, and capability documents now state that no Candidate is
  accepted until the next real final-source Gate, provenance, and new demo complete.

## TDD evidence

Recorder and assembly tests were written before their implementation and failed for the
expected missing script/new assembly interface. Additional RED cases demonstrated that
the prior partial implementation accepted:

- a Wheel with a re-hashed extra package file;
- a substituted Wheel build command;
- Python 3.13 provenance;
- re-hashed uncommitted Python source in both sdist and Wheel;
- an unsafe sdist symlink; and
- substituted fixed phase gate/report commits in the strict verifier.

Each case was made GREEN with a direct validation at the relevant boundary.

## Focused verification

Repository Python 3.12 environment:
`/Users/chenjiamin/tiangong/tianshu/.venv/bin/python` with the worktree `src` on
`PYTHONPATH`.

- Focused pytest: `191 passed / 0 failed / 4 warnings`.
  - recorder;
  - Candidate evidence/provenance;
  - Candidate context;
  - schema/model parity;
  - strict evidence verifier;
  - Git backend; and
  - public-document truth and local links.
- Ruff check on all touched Python: `All checks passed!`.
- Ruff format check on all touched Python: all checked files formatted.
- Configured mypy: `Success: no issues found in 132 source files`.
- Recorder-specific mypy: `Success: no issues found in 1 source file`.
- `git diff --check`: passed.
- `uv.lock`: zero diff.

The four warnings are unchanged third-party deprecations from `lark_oapi` and
`websockets`.

## Self-review and next-stage concerns

- No full Gate or real demo was run here, as required. Therefore no replacement
  `lean-preview-candidate.json` or aggregate Candidate Markdown exists in this commit.
- The next stage must commit this source hardening first, run the fixed recorder on that
  clean source, create the fixed build-provenance batch and new source/Wheel-bound demo,
  then call Candidate assembly with those tracked paths.
- Historical demo batches can support historical phase facts only; they must not be passed
  as the new Candidate demo.
- The recorder intentionally has no plugin/config framework. Changing the accepted command
  set requires a source change and review.

## Independent-review hardening addendum

The independent closure review requested changes to five remaining evidence boundaries.
They are resolved in a separate follow-up commit without running the full Gate, building
real release artifacts, or generating a Candidate:

- The packaging Gate recorder now sets and records the exact dynamic `BATCH_ID` and
  `TIANSHU_LEAN_WHEEL_SOURCE_COMMIT` values in addition to unsetting `VIRTUAL_ENV`.
  The checker reconstructs that environment from the manifest identity and rejects a
  missing or substituted value. Packaging's parsed skip count now feeds
  `required_skipped`, so `27 passed, 1 skipped` fails closed.
- Playwright evidence must contain exactly one terminal summary with exactly 41 passed,
  zero failed, and zero skipped. An extra summary or a re-hashed skipped summary is
  rejected.
- Build records now require exact `command`, `cwd`, and zero `exit_code` values. The Wheel
  cwd is the fixed extracted-sdist path
  `dist/lean-preview-candidate/extracted/<sdist-sha256>/<sdist-root>`. Hashed logs that
  contain an explicit `ERROR` or `FAILED` marker are rejected even if they also contain
  `Successfully built`.
- `scripts/record_lean_preview_build_provenance.py` is the minimal fixed generation
  interface for the next build batch. It builds the sdist from the repository root,
  safely extracts it below its SHA-256 directory, builds the Wheel only from that exact
  extracted root with the fixed relative output path, and records canonical hashed logs
  and provenance. It refuses dirty source in its CLI path and does not provide a
  configurable command framework.
- Wheel installable payload is now restricted to `tianshu/**` plus exactly one
  `tianshu-*.dist-info/**` root. The obsolete demo-only dirty-path helper and its test were
  removed; Gate recorder tests now assert every actual argv, cwd, and relevant environment
  mutation.

### Addendum TDD and verification

RED was captured before implementation:

- seven targeted cases failed on the missing dynamic Gate environment, missing
  Playwright/skip constraints, and missing provenance fields;
- the new build-provenance recorder test failed because the fixed interface did not yet
  exist; and
- a re-hashed Wheel case carrying `evil/__init__.py` was present in the RED suite before
  the top-level payload allowlist implementation.

After the direct boundary checks were implemented:

- focused pytest: `199 passed / 0 failed / 4 warnings`;
- Ruff check: `All checks passed!`;
- Ruff format check: all seven touched Python files formatted;
- configured mypy: `Success: no issues found in 132 source files`;
- build-recorder-specific mypy: `Success: no issues found in 1 source file`;
- `git diff --check`: passed; and
- `uv.lock`: zero diff.

The four warnings remain the same third-party `lark_oapi` and `websockets`
deprecations. The build-recorder test uses controlled subprocess substitutes to verify
the actual fixed argv/cwd and resulting provenance; this addendum did not generate or
claim real build provenance.
