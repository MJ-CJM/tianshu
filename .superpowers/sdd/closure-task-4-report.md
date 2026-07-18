# Closure Task 4 report: public truth and candidate documentation

Date: 2026-07-18

Branch: `feat_cc_fable_v1`

Starting commit: `04a01c98`

## Outcome

Closure Task 4 aligns the private working branch's public-facing documentation with the verified
Lean Developer Preview Candidate evidence. It does not perform or authorize external publication.

The Chinese positioning is frozen exactly as:

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

The public docs now lead with the demonstrated durable-governance, Evidence, desktop Web, and
Lean Core evolution path. They use `敕令` and `裁决` for user-facing Chinese while preserving
`Edict` and `Decree` as code/API/database compatibility names.

## TDD evidence

The documentation truth/link tests were written before the documentation changes.

- RED: `7 failed, 9 passed, 4 warnings`. The failures were the missing Lean Preview usage guide
  and the old README/support language that lacked the required Ubuntu/Python 3.12,
  host-administrator, installed-runner, status, provenance, brand, and link contracts.
- GREEN: `16 passed, 4 warnings` after the minimal documentation implementation.
- The four warnings are unchanged third-party deprecations from `lark_oapi` and `websockets`.

The tests now require:

- the exact Chinese positioning and natural English positioning;
- source and exact-Wheel local installation with self-created source/build/preview environments;
- Ubuntu + Python 3.12 as the first official target while naming the retained local environment;
- exactly one public installed `tianshu-lean-demo` command and the strict provenance verifier;
- all six truth states: `implemented`, `disabled`, `deferred`, `experimental`,
  `external_pending`, and `user_approval_pending`;
- implemented SystemAudit, MCP ciphertext, durable governance, Evidence Bundle v1, three core
  desktop pages, and bounded Lean Core evolution;
- remote MCP and open stdio MCP `disabled`; official container, PyPI/GHCR, and full G5
  `deferred`; OpenHands, ROI, cost calibration, and full G4 `external_pending`;
- current desktop brand facts and local Markdown link resolution;
- absence of release, one-time effect, sandbox, and unqualified market-uniqueness overclaims.

## Evidence-derived truth

### Installation and retained batch

- Current official local paths: source checkout and the exact Wheel built from that checkout.
- Ubuntu + Python 3.12 is the first official target.
- The retained golden batch was actually verified on `Darwin/arm64/Python 3.12.12`; it is not
  represented as Ubuntu external validation.
- Batch: `20260718T072917Z-b27f525fe4ef`.
- Source: `b27f525fe4eff52a24f0c7769125bc158097e7de`.
- Wheel SHA-256:
  `81ec17b9818e67ac6046fb0e1ab62d13606fcaa5af14141ae4d311179bc10fef`.
- The retained report has `fixture=false`, 13 steps, and every step is `passed`.

The usage guide creates a dedicated build environment, installs a pinned PEP 517 frontend, builds
the Web payload and Wheel with `.build-venv/bin/python -m build --wheel`, requires exactly one
Wheel, installs it outside the source environment with binary dependencies, starts a fresh local
demo profile, runs the one installed console-script command, and invokes the verifier with
mandatory source and Wheel identities.
Neither `uv` nor `uv.lock` was used or changed.

### Desktop Web and brand

- Candidate product surface: local desktop Web only; no mobile implementation or product claim.
- Deep product claims cover Control Center, Edict detail, and Evolution Center. Four groups and
  fourteen department navigation entries remain, but the deferred department-depth work is not
  promoted into the Candidate claim.
- Production asset: `web/public/brand.png`.
- SHA-256: `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`.
- Frozen motto: `成功只有一个——按照自己的方式，去度过人生。`
- Five frozen right-side labels: `彩蛋 / 通用 / English / 实时 / 通政`.
- S4 automation is passed; visual/interaction approval remains `user_approval_pending` and
  VoiceOver remains `external_pending`.

### Security and evolution limits

- The documented runtime boundary is single-host, single-node SQLite. A host administrator can
  read or replace the database, master key, process memory, workspace, and local artifacts and is
  outside the current threat boundary.
- SystemAudit and persisted MCP env/header ciphertext are implemented in their named local scope.
- remote MCP and the Candidate's open stdio MCP surface remain `disabled`; full admission work is
  `deferred`.
- S3 durable governance and Evidence are implemented for managed Native paths and declared
  ledger-tracked effects, not arbitrary external effects or distributed replicas.
- The golden skill-candidate gate, real candidate overlay, and rollback are implemented Lean Core
  evidence. OpenHands, executor compatibility, ROI, cost calibration, and full G4 remain
  `external_pending`; full G5 remains `deferred`.

## Files

Modified:

- `README.md`
- `README.en.md`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `docs/launch/README.md`
- `docs/launch/capability-matrix.md`
- `docs/launch/checklist.md`
- `docs/launch/demo-storyboards.md`
- `tests/test_public_docs_truth.py`

Created:

- `docs/usage/lean-developer-preview.md`
- `.superpowers/sdd/closure-task-4-report.md`

No production Python, Web UI, mobile surface, migration, dependency, lockfile, or release
configuration was changed.

## Verification

- Documentation truth and local-link Gate:
  `16 passed, 4 warnings`.
- Strict retained evidence verifier:
  `Lean Preview evidence verified: 20260718T072917Z-b27f525fe4ef`.
- `ruff check tests/test_public_docs_truth.py`: passed.
- `ruff format --check tests/test_public_docs_truth.py`: passed.
- All Bash fences in the new usage guide: `bash -n` passed.
- Production brand SHA-256 matched the S1/S4 frozen value.
- `git diff --check`: passed.
- `uv.lock`: zero diff.

## Remaining concerns and authority boundary

- Ubuntu validation and VoiceOver remain `external_pending`. managed OpenHands, ROI, cost
  calibration, and full G4 remain `external_pending`; full G5 remains `deferred`. This task did
  not run or relabel them.
- The repository's existing Dockerfile and release helper remain legacy/experimental inputs, not
  official Candidate distribution evidence.
- `publication_status` remains `not_authorized`. No push, tag, release, registry upload, repository
  visibility change, PR/Issue, message, or announcement was performed.

## Review remediation after `224b07a`

Review found that the first guide revision depended on a repository `.venv` that a fresh checkout
had not created and did not bootstrap the PEP 517 frontend. The verifier also used that undeclared
environment instead of the environment containing the exact installed Wheel. A second finding
identified mixed `external_pending`/`deferred` language that did not assign one truth state to each
named capability.

### Remediation TDD

- RED: `3 failed, 15 passed, 4 warnings`. The tests rejected the undeclared build/verifier
  environment and the mixed full-Gate state language.
- The command simulator then caught two further pre-GREEN defects: quoted build-requirement syntax
  was not parsed by its first matcher, and one guide sentence placed a shared state before the two
  MCP capability names instead of mapping each capability explicitly.
- GREEN: `18 passed, 4 warnings`.

The executable documentation contract now:

1. extracts and joins every Bash fence from the usage guide;
2. executes `bash -n` on the complete command stream;
3. tracks shell-variable assignment before reference;
4. tracks each virtual environment's creation before any executable reference;
5. requires a pinned `build==1.5.0` bootstrap before `.build-venv/bin/python -m build`;
6. records the environment receiving the binary-only exact-Wheel install; and
7. requires both the public runner and strict verifier to use that `.preview-venv` environment.

No product dependency was added. The guide creates `.source-venv`, `.build-venv`, and
`.preview-venv` explicitly. The build environment receives only the pinned frontend needed for
PEP 517 construction; the preview environment receives the exact Wheel and its dependencies.

### Exact status mapping

- remote MCP: `disabled`; its reopening work remains `deferred`.
- open stdio MCP: `disabled`; its exact-grant/executable-binding work remains `deferred`.
- official container, PyPI, GHCR, and full G5: `deferred`.
- OpenHands, executor compatibility, ROI, cost calibration, and full G4: `external_pending`.
- Visual/interaction approval remains `user_approval_pending`; VoiceOver remains
  `external_pending`.

The truth test checks capability-to-state proximity in every Task 4 document and rejects mixed
"one of two states" wording. Presence of all state words alone no longer satisfies the contract.

### Remediation verification

- Documentation truth/link/command simulation: `18 passed, 4 warnings`.
- Strict retained verifier:
  `Lean Preview evidence verified: 20260718T072917Z-b27f525fe4ef`.
- Ruff check and format check: passed.
- Independent usage-guide Bash extraction and `bash -n`: passed.
- `git diff --check`: passed.
- `uv.lock`: zero diff; no `uv` command was run.
- No publication, UI/mobile implementation, migration, dependency, or release action was taken.
