# Closure Task 3 report: exact-Wheel golden demo

Date: 2026-07-18
Branch: `feat_cc_fable_v1`
Starting commit: `f10549765b59a7dd9e4342e48c977737c6702fe2`

## Outcome

Closure Task 3 is complete. One Wheel built after the Web build was installed outside the
repository into a fresh Python 3.12 virtual environment and exercised through the installed server
and installed `tianshu-lean-demo` command. The retained final batch passed all 13 public demo steps
and the strict offline verifier.

The final harness also proved descendant-process external-network denial, fresh HOME/workspace
isolation, clean SIGTERM shutdown, no surviving process group or listening port, SQLite integrity,
and an unchanged packaged-resource digest.

## Final immutable identities

- Wheel source commit:
  `b27f525fe4eff52a24f0c7769125bc158097e7de`
- Wheel SHA-256:
  `81ec17b9818e67ac6046fb0e1ab62d13606fcaa5af14141ae4d311179bc10fef`
- Batch ID: `20260718T072917Z-b27f525fe4ef`
- Environment fingerprint:
  `692e3e262a9b6478793b224c52a5667b5f1ac9ecda52b21b169605ce570590b1`
- Report:
  `docs/cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json`
- Artifacts:
  `docs/cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/artifacts/`

The batch suffix, report source commit, caller-supplied source identity, current source commit at
Wheel build time, and mandatory verifier expectation were all the same identity. Exactly one Wheel
was present for the final test.

## Fresh-install and runtime truth

- Web dependencies were installed with `npm ci`; `npm run build` passed before Wheel construction.
  The build retained its Vite chunk-size advisory and npm reported nine dependency-audit findings;
  neither was hidden or treated as a source-test failure.
- The Wheel was constructed with Python's PEP 517 build frontend after the Web build. `uv` was not
  invoked and `uv.lock` was not touched.
- Installation used one Wheel at a time and `--only-binary=:all:` for dependencies. This avoided a
  non-deterministic source build of a newer `litellm`; pip selected an available binary dependency
  set instead.
- The installed interpreter was Python 3.12. The virtual environment, HOME, database, and Git
  workspace were outside the repository. The HOME/workspace path contained spaces and non-ASCII
  characters (`天枢`, `用户`, and `工作区`).
- `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV` were absent from the runtime environment;
  `PYTHONNOUSERSITE=1` was set. An import probe proved `tianshu` came from the fresh environment's
  `site-packages`, and no import path pointed into the source repository.
- The external network profile was inherited by sandboxed descendants. A descendant socket probe
  proved the denial before the server was launched; only loopback traffic was allowed.
- The installed Web root was served successfully and contained the application root. It did not
  reference Google Fonts.
- The demo provider materialized `DEMO.md` in the fresh Git workspace through the installed public
  path. The run used `fixture=false`.
- The resource catalog digest before and after the run was identical.
- SIGTERM produced Uvicorn's `Application shutdown complete` and `Finished server process`
  markers. The macOS sandbox wrapper can itself return `-SIGTERM` when the process group is
  signalled, so the harness accepts that status only when both clean-shutdown markers exist. It
  still rejects missing markers, SIGKILL, a surviving process group, or a listening port.
- The installed database returned `ok` from `PRAGMA quick_check` after shutdown.
- The server log contained no `ResourceWarning` and no `unclosed` warning.

## Public golden path proved

The final retained batch passed, in order:

1. readiness and authenticated principal discovery;
2. governed edict submission;
3. plan-review decision observation;
4. reason-bound decision resolution;
5. completed Memorial observation;
6. closed Evidence Bundle verification;
7. skill candidate proposal;
8. candidate-specific evidence and gate evaluation;
9. bounded canary start;
10. canary-eligible run submission;
11. real candidate overlay attribution;
12. rollback with zero allocation; and
13. a new post-rollback run with a strict `LegacyRunAssignmentV1`, no effective overlay, and the
    candidate still bound to the rollback receipt as `rolled_back` with zero allocation.

The explicit verifier command, with mandatory expected source commit and Wheel SHA-256, exited 0:

```text
Lean Preview evidence verified: 20260718T072917Z-b27f525fe4ef
```

The earlier all-green batch `20260718T070101Z-2563d42733b2` remains unchanged as historical
evidence. It was superseded because the governance-close review correction changed the Wheel
source after that run.

## Review remediation: final governance evidence closure

Review found that `audit.completed` previously trusted its event verdict and closed any passing
audit, even when the authoritative Memorial was still `NEEDS_REVIEW/pending`. That froze an
immutable snapshot before the human governance decision.

Correction commit `b27f525fe4eff52a24f0c7769125bc158097e7de` now enforces:

- A passed audit creates or retains open evidence but closes it only when the stored Memorial is
  `COMPLETED/not_required` and the stored audit verdict is also `pass`.
- Pending and rejected review states remain open. Direct `close()` calls are governed by the same
  authoritative final-state check and cannot bypass it.
- `decree.approved` has a dedicated production subscription. Closure requires a stored
  `COMPLETED/approved` Memorial, a stored passing audit, an exact event Edict/Memorial binding, and
  a persisted `approve` Decree for that Memorial.
- Actor, Edict owner, and durable root-correlation values are checked when carried by the event;
  ApprovalManager now emits all three. A wrong owner, actor, correlation, Edict, or cross-run
  Decree is ignored without closing either run.
- Legacy final Decrees are represented through the existing strict `DecisionEvidenceV1` snapshot
  contract. DecisionRequest-backed Decrees are not duplicated.
- Audit/approval delivery order, replay, concurrent handlers, and a new EvidenceService instance
  after restart all converge on the same immutable closed bundle.
- A mid-execution approval cannot close evidence because its authoritative Memorial is not in the
  final approved state.

## Corrections discovered by exact-Wheel RED runs

All failed evidence directories were retained unchanged and committed separately before their
corrections.

| Failed batch | Last passed step | Failing boundary | Correction commit |
|---|---:|---|---|
| `20260718T051136Z-f10549765b59` | 5 | audited Evidence Bundle did not auto-close | `34be90d` |
| `20260718T053315Z-34be90dd3d9a` | 7 | candidate checks were bound to an unavailable source workspace | `42a3c9f` |
| `20260718T054021Z-42a3c9f66acd` | 7 | native gate command could not attest the inherited network policy | `0fddd96` |
| `20260718T060259Z-0fddd9610000` | 7 | demo rubric responses did not satisfy strict gate JSON | `fa39afe` |
| `20260718T061726Z-fa39afed9d2f` | 7 | acceptance outcomes were not persisted for evidence closure | `bbd29b7` |
| `20260718T063829Z-79c3c6fc7afc` | 8 | requested canary allocation exceeded the skill contract maximum | `32a8f1c` |
| `20260718T064349Z-32a8f1cd11c6` | 10 | a single probabilistic canary run selected the champion | `64ef848` |
| `20260718T064950Z-64ef8485b65e` | 12 | runner/verifier expected a governed assignment after rollback | `40d1013` |
| `20260718T065714Z-40d10134d9c0` | 13 | sandbox wrapper returned `-SIGTERM` despite complete server shutdown | `2563d42` |

The routing fix is exact-opt-in and demo-profile-only: it supplies a deterministic bucket for the
golden canary while the live profile retains the production allocation function. The rollback fix
did not change product routing semantics. Existing rollback fault-matrix tests already establish
that new runs after allocation reaches zero are legacy/unmanaged while prior durable assignments
remain frozen.

Two dependency-install attempts failed before an application batch could be created:
`20260718T055847Z-0fddd...` and `20260718T062730Z-bbd...`. Both were source-build network timeouts
while trying to obtain Rust tooling for `maturin`; neither created an evidence directory. The
binary-only fresh-install rule is covered by a committed test.

## Verification summary

- Exact-Wheel fresh-install harness: `5 passed` in 234.25 seconds. This includes four focused
  shutdown-contract cases plus the slow black-box run.
- Independent strict verifier: passed with both expected build identities supplied.
- `tests/launch tests/test_public_docs_truth.py`: `140 passed, 1 skipped` (the slow exact-Wheel case
  skips when its mandatory caller identities are absent).
- Evidence/provider/orchestrator/tool/evolution/gateway focused regression: `157 passed`.
- Governance-close RED: `11 failed, 1 passed`; GREEN after correction: `13 passed` including the
  actual ApprovalManager-to-EventBus close path.
- Wider evidence/auditor/decision/governance regression: `297 passed`; focused evidence/decree
  regression after the EventBus case was added: `32 passed`.
- Runner/verifier focused RED after the legacy-assignment test conversion: `45 failed, 21 passed`;
  focused GREEN after the correction: `66 passed`.
- Shutdown-contract RED: `4 failed`; GREEN: `4 passed`.
- Ruff check and format check passed on all five governance-remediation source/test files and on
  the earlier Closure Task 3 changes.
- Mypy: `Success: no issues found in 132 source files`.
- Import Linter: 483 files / 1,754 dependencies, two contracts kept, zero broken.
- `git diff --check`: passed.

The pytest runs emitted four existing third-party deprecation warnings from `lark_oapi` and
`websockets`. During installed startup, LiteLLM attempted to fetch its public model-cost map; the
network sandbox denied it and LiteLLM logged a warning before falling back to its local backup.
No outbound connection succeeded. No `ResourceWarning` or unclosed-resource warning was observed.

## Scope and release boundaries

- Failed batches and the final verified batch are immutable retained evidence.
- Corrections and evidence were committed separately.
- No private database mutation or private runner/service shortcut was used by the demo.
- No promotion beyond the bounded demo canary occurred; rollback completed with zero allocation.
- No container, OIDC, publish, tag, push, or release operation was performed.
- `uv`, `uv.lock`, and unrelated source files were not changed.
- Scenario-declared external work (`voiceover`, `external_executor`) remains explicitly pending and
  is not represented as complete.
