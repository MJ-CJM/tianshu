# S3 Core Governance Gate Report

- status: passed
- scope: SQLite single-node durable governance and Evidence only
- entry commit: `7736a1d3255f42ad052962f4e2b49dfe764d2300`
- inherited cleanup commit: `a1ec0a8`
- checked source commit: `70d9cb78b2ea9f666195c6a16a3530d053d79a83`
- retained-evidence checker commit: `70d9cb7`

This Gate closes the Lean S3 Core boundary: one durable Edict ingress, transactional outbox,
persistent Decision/RunState, attempt lease and fencing, supported side-effect intent/receipt
recovery, bounded continuation recovery, content-addressed artifacts, Evidence Bundle v1, and
durable internal notification delivery. It does not claim PostgreSQL/Kubernetes/multi-replica
semantics, complete OTel/SLO coverage, or delivery by external notification channels.

## Final Gate results

| Gate | Exact result |
| --- | --- |
| Focused S3 fault matrix, including all Evidence tests | `143 passed, 0 skipped, 4 warnings in 6.22s` |
| All notifier tests | `14 passed, 0 skipped, 4 warnings in 0.66s` |
| Ruff check | `All checks passed!` |
| Ruff format | `824 files already formatted` |
| mypy | `Success: no issues found in 125 source files` |
| import-linter | `455 files / 1571 dependencies; 2 contracts kept, 0 broken` |
| Full non-slow | `3769 passed, 2 skipped, 24 deselected, 7 warnings in 630.10s (0:10:30)` |

The focused matrix executed every required fault source and skipped none. The full-suite skips
are outside the required focused matrix; no skipped item is counted as S3 fault evidence.

## Inherited nine-failure cleanup

Each item was first reproduced from `7736a1d`; the exact nine then passed together as
`9 passed, 4 warnings in 2.96s`.

| # | Root cause | Truth-preserving correction |
| ---: | --- | --- |
| 1 | `WorkspaceService` silently constructed a second production `DecisionService`. | Require composition-root injection of the sole Decision authority; update direct test composition explicitly. |
| 2 | The DAG retry test used the retired scheduler-direct path and omitted the mandatory stable idempotency key. | Prove delegation through managed ingress and prove the scheduler is not rerun directly. |
| 3 | L3 fault 1 reached an approved planner `PLANNING` Agent RunState that the outer-loop decision transition rejected. | Accept that exact undecided planning state and carry its immutable plan continuation into the outer-loop continuation. |
| 4 | L3 fault 3 exposed the same transition gap after a later failure boundary. | Recover the nested plan continuation through planner, evidence, and storage lineage checks after reopen. |
| 5 | The restart test's fixed clock preceded the row's real submission time. | Dispatch at the persisted `available_at` boundary. |
| 6 | Evidence lock hashing inferred repository layout even though `uv.lock` is not a packaged runtime resource. | Record the established unavailable hash identically for source and Wheel runtime. |
| 7 | The test expected a pre-managed root error. | Assert the current mandatory-ingress fail-closed error and retain the no-root-write assertion. |
| 8 | The follow-up test stopped at idempotency admission before the intended absent-Edict lookup. | Supply the mandatory key and assert the resulting `404`. |
| 9 | The integration test wired the retired in-memory scheduler/planner/executor chain. | Exercise managed ingress, claim dispatch, fenced completion, durable outbox, audit, and notification. |

Surrounding verification included `58 passed` across the eight inherited-failure files,
`162 passed, 2 skipped` across workspace/governed-apply suites plus the corrected helper case,
and `76 passed` across continuation, Decision, managed recovery, Evidence, and published-schema
contracts. The Evidence schema needed no change because RunState continuation is not part of the
published Evidence Bundle schema.

## Gate history retained as evidence

| Attempt | Result | Correction |
| --- | --- | --- |
| Planned focused command | Collection exit 4: two planned filenames had never existed in this tree. | Added a source-existence RED test; bound the two required faults to `test_decision_service_restart_race.py` and `test_managed_production_recovery.py`, which contain the actual durable Decision and outer-loop restart coverage. |
| First full non-slow diagnostic | `1 failed, 182 passed, 24 deselected, 4 warnings in 8.55s` | The checker directly launched Git and was rejected by the process-boundary architecture test. No allowlist exemption was added; narrow named read-only repository-state operations were added to `GitBackend`, and the checker now uses that existing process authority. |
| Initial final full non-slow | `3759 passed, 2 skipped, 24 deselected, 7 warnings in 619.25s` | Passed from clean source `2e66bea`. |
| Retained-evidence remediation | Independent review found self-reported command facts and partial claim scanning. | Bound every result to a fixed retained log and source hash; added canonical JSON plus full report/capability/PROGRESS claim scanning in `70d9cb7`. |
| Remediated final full non-slow | `3769 passed, 2 skipped, 24 deselected, 7 warnings in 630.10s` | Passed from clean source `70d9cb7`; exact output is retained and hashed. |

## Machine-checked invariants

The canonical JSON block below is independently checked against live Git state, exact retained
log bytes, and source-file hashes. Each command record names one fixed log under
`reports/s3-core-evidence/`; the checker hashes that file, parses its command-specific terminal
summary and exit trailer, and compares the derived command, source commit, counts, hash, and exit
code with the report. Missing, unretained, tampered, or forged command evidence fails closed.

Bundle integrity, duplicate-effect prevention, stale-fence rejection, and Decision recovery are
not accepted from hand-entered pass booleans. They are derived from the passed exact focused
command plus the required test path and live source hash. Noncanonical JSON is rejected. Full
OTel, external-delivery guarantees, and multi-replica governance remain not claimed; asserting
them positively anywhere in this report, the capability matrix, or PROGRESS fails the check,
while explicit deferred/unsupported boundaries remain allowed.

## Known limits and deferred work

- Durability is proven for one process topology over one local SQLite database with restart and
  separate-connection tests. PostgreSQL, Kubernetes, and multi-replica coordination are not in
  this Gate.
- Observability here is stable correlation propagation, SystemAudit, readiness, and evidence.
  Complete OTel export, dashboards, and SLOs remain deferred.
- Notification durability ends at the internal delivery record/outbox. Feishu, Telegram, email,
  or other external channel delivery is not guaranteed by this Gate.
- Side-effect recovery is bounded by the declared effect semantics. Untracked external effects
  and opaque CLI internals are not promoted to exactly-once behavior.
- Planner evidence is limited to immutable plan hash, revision reason, lineage, and references;
  the complete planner quality system remains deferred.
- External Keqing CLI adapters remain contained and experimental. This Gate does not add internal
  tool interception, hard cost caps, durable resume, or governed merge for those adapters.
- No container, public registry, signing, tag, publication, or external marketing action was
  performed.

The seven final warnings are retained: four third-party `lark_oapi`/`websockets` deprecations,
one existing unawaited `AsyncMock` warning in a compatibility test, and two subprocess-transport
`PytestUnraisableExceptionWarning` records. They were not suppressed or counted as passes.

## Exit

S3 Core Governance is passed only at the boundary above. S4 may consume the frozen public read
contracts; broader claims remain deferred.

<!-- s3-core-evidence:v1 -->
```json
{
  "commands": [
    {
      "command": "env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/integration/test_edict_idempotency.py tests/integration/test_outbox_recovery.py tests/integration/test_decision_service_restart_race.py tests/integration/test_managed_production_recovery.py tests/integration/test_claim_lease_recovery.py tests/integration/test_side_effect_idempotency.py tests/integration/test_continuation_recovery.py tests/integration/test_replan_evidence.py tests/evidence tests/notifier/test_internal_delivery_recovery.py -q",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 143,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "focused_fault_matrix",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/focused_fault_matrix.log",
      "log_sha256": "6e5355a74ea8b239b7ef5ed5f10329165737ddf21d5a8c5f7f956a4d33c77cc4"
    },
    {
      "command": "env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/notifier -q",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 14,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "notifier_all",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/notifier_all.log",
      "log_sha256": "4a6f06bba5a5b1dfee250d044f25ff959e022b8c66ef05d486aed06af7a6a341"
    },
    {
      "command": ".venv/bin/ruff check src tests",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 1,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "ruff_check",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/ruff_check.log",
      "log_sha256": "1cb886e4569ce39538854a80ade076816224e5c8722f1eef5b7bb6ca1698b142"
    },
    {
      "command": ".venv/bin/ruff format --check src tests",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 824,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "ruff_format_check",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/ruff_format_check.log",
      "log_sha256": "412671fd083a52dc0a31507fceb94461b5be07d27c59cc66aced28dabbe7aba3"
    },
    {
      "command": ".venv/bin/mypy",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 125,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "mypy",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/mypy.log",
      "log_sha256": "762b04e46bbb3f733ed1fa94b9c846e10e94908e21322744a765970c43df4dee"
    },
    {
      "command": ".venv/bin/lint-imports",
      "counts": {
        "deselected": 0,
        "failed": 0,
        "passed": 2,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "import_linter",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/import_linter.log",
      "log_sha256": "a5a31cd65ff4ec1588639528a68fd070941a4ccc1e191af109992a53d6d4ae15"
    },
    {
      "command": "env -u VIRTUAL_ENV .venv/bin/python -m pytest -m \"not slow\" -q",
      "counts": {
        "deselected": 24,
        "failed": 0,
        "passed": 3769,
        "skipped": 2
      },
      "exit_code": 0,
      "id": "full_non_slow",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/full_non_slow.log",
      "log_sha256": "0e0e1a18964494863d606d5fa409fa60a6d7af318e960ab7e57892aa5b977291"
    }
  ],
  "faults": [
    {
      "id": "idempotent_submission",
      "source_sha256": "7e02ea0bd5404aad3db22223047c174216a9f375f72f2e6806c5f96bbc0edbf8",
      "test_path": "tests/integration/test_edict_idempotency.py"
    },
    {
      "id": "committed_outbox_restart",
      "source_sha256": "046e83bace6ef8a0d7609858ad1317e4bb23b57af8647677ee0750d46560f9a0",
      "test_path": "tests/integration/test_outbox_recovery.py"
    },
    {
      "id": "decision_restart_recovery",
      "source_sha256": "c25273a01433b7af34b7031b5bacb1e5120f0c9dad7b170ccbb037948d4b06da",
      "test_path": "tests/integration/test_decision_service_restart_race.py"
    },
    {
      "id": "outer_loop_restart_recovery",
      "source_sha256": "423d8a17b605d3e60046db0053580920b56f0535c6cc952b0909cd5d2c64b4ba",
      "test_path": "tests/integration/test_managed_production_recovery.py"
    },
    {
      "id": "claim_lease_recovery",
      "source_sha256": "e6f34cdb78422f4cc9a5d88b090ded545d897e62373e9106bff6d0f136497e28",
      "test_path": "tests/integration/test_claim_lease_recovery.py"
    },
    {
      "id": "side_effect_idempotency",
      "source_sha256": "1d8047479a2d4ec631fb1c4c03a691cb8fea5cdc61ff2c5db14410c5e27611ea",
      "test_path": "tests/integration/test_side_effect_idempotency.py"
    },
    {
      "id": "continuation_recovery",
      "source_sha256": "87dfd25bd320111394d1631203c80fa80af8edb8232133f0b5f82bf9bd3a7459",
      "test_path": "tests/integration/test_continuation_recovery.py"
    },
    {
      "id": "replan_evidence",
      "source_sha256": "6a8769dbe427aebe3fd0e846785ffcf696bcf7f38055e338165bf56805d05696",
      "test_path": "tests/integration/test_replan_evidence.py"
    },
    {
      "id": "evidence_bundle_integrity",
      "source_sha256": "60a3cbec3afd14041567bbbb3fe21b1e25ab38fc4a9005ebfac9c1409e805ed7",
      "test_path": "tests/evidence"
    },
    {
      "id": "internal_delivery_recovery",
      "source_sha256": "4ac68f7fac9d2c45145c9c4bdfc0e8d059889aac5bb2977a07a2ce7abe3d9a71",
      "test_path": "tests/notifier/test_internal_delivery_recovery.py"
    }
  ],
  "schema_version": "s3-core-gate-v1",
  "scope": {
    "durability": "sqlite_single_node",
    "notification_delivery": "internal_only",
    "observability": "correlation_only",
    "replication": "none"
  },
  "source_commit": "70d9cb78b2ea9f666195c6a16a3530d053d79a83",
  "status": "passed"
}
```
<!-- /s3-core-evidence:v1 -->
