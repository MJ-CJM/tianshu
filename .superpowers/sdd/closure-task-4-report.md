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
- source and exact-Wheel local installation with `.venv` PEP 517 tooling;
- Ubuntu + Python 3.12 as the first official target while naming the retained local environment;
- exactly one public installed `tianshu-lean-demo` command and the strict provenance verifier;
- all six truth states: `implemented`, `disabled`, `deferred`, `experimental`,
  `external_pending`, and `user_approval_pending`;
- implemented SystemAudit, MCP ciphertext, durable governance, Evidence Bundle v1, three core
  desktop pages, and bounded Lean Core evolution;
- deferred/disabled remote MCP, open stdio MCP, official container, PyPI/GHCR, OpenHands, ROI,
  cost calibration, full G4, and full G5;
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

The usage guide builds the Web payload, builds the Wheel with
`.venv/bin/python -m build --wheel`, requires exactly one Wheel, installs it outside the source
environment with binary dependencies, starts a fresh local demo profile, runs the one installed
console-script command, and invokes the verifier with mandatory source and Wheel identities.
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
- remote MCP and the Candidate's open stdio MCP surface remain disabled; full admission work is
  deferred.
- S3 durable governance and Evidence are implemented for managed Native paths and declared
  ledger-tracked effects, not arbitrary external effects or distributed replicas.
- The golden skill-candidate gate, real candidate overlay, and rollback are implemented Lean Core
  evidence. OpenHands, executor compatibility, ROI, cost calibration, and complete G4 remain
  `external_pending`; complete G5 remains deferred.

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

- Ubuntu validation, VoiceOver, managed OpenHands, ROI/cost calibration, full G4, and full G5
  remain pending in their named states; this task did not run or relabel them.
- The repository's existing Dockerfile and release helper remain legacy/experimental inputs, not
  official Candidate distribution evidence.
- `publication_status` remains `not_authorized`. No push, tag, release, registry upload, repository
  visibility change, PR/Issue, message, or announcement was performed.
