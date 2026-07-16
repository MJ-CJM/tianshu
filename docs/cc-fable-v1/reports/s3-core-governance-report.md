# S3 Core Governance Gate Report

- status: passed
- scope: SQLite single-node durable governance and Evidence only
- entry commit: `7736a1d3255f42ad052962f4e2b49dfe764d2300`
- inherited cleanup commit: `a1ec0a8`
- checked source commit: `8f5b324306b6b49e35ae89d0c22f4ab9a072edb2`
- retained-evidence checker commit: `8f5b324`

This Gate closes the Lean S3 Core boundary: one durable Edict ingress, transactional outbox,
persistent Decision/RunState, attempt lease and fencing, supported side-effect intent/receipt
recovery, bounded continuation recovery, content-addressed artifacts, Evidence Bundle v1, and
durable internal notification delivery. It does not claim PostgreSQL/Kubernetes/multi-replica
semantics, complete OTel/SLO coverage, or delivery by external notification channels.

## Final Gate results

| Gate | Exact result |
| --- | --- |
| Focused S3 fault matrix, including all Evidence tests | `178 passed, 0 skipped, 4 warnings in 6.20s` |
| All notifier tests | `14 passed, 0 skipped, 4 warnings in 0.65s` |
| Ruff check | `All checks passed!` |
| Ruff format | `824 files already formatted` |
| mypy | `Success: no issues found in 125 source files` |
| import-linter | `455 files / 1571 dependencies; 2 contracts kept, 0 broken` |
| Full non-slow | `3804 passed, 2 skipped, 24 deselected, 7 warnings in 613.83s (0:10:13)` |

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
| Claim-classifier remediation | Independent review confirmed that OpenTelemetry/full-OTel, external-channel delivery, multiple replicas, and equivalent Chinese forms remain not claimed even under double negation or alternate word order. | Replaced broad negation-token acceptance with topic detection plus canonical negative-boundary classification in `8f5b324`; those variants remain not claimed, 39 cross-document positive cases are rejected, and current truthful documents remain accepted. |
| Final retained-evidence rerun | `3804 passed, 2 skipped, 24 deselected, 7 warnings in 613.83s` | Passed from clean source `8f5b324`; all seven logs were regenerated and rebound. |

## Machine-checked invariants

The canonical JSON block below is independently checked against live Git state, exact retained
log bytes, and source-file hashes. Each command record names one fixed log under
`reports/s3-core-evidence/`; the checker hashes that file, parses its command-specific terminal
summary and exit trailer, and compares the derived command, source commit, counts, hash, and exit
code with the report. Missing, unretained, tampered, or forged command evidence fails closed.

Bundle integrity, duplicate-effect prevention, stale-fence rejection, and Decision recovery are
not accepted from hand-entered pass booleans. They are derived from the passed exact focused
command plus the required test path and live source hash. Noncanonical JSON is rejected. Full
OTel, external-delivery guarantees, and multi-replica governance remain not claimed. Those
boundaries remain not claimed under OpenTelemetry/full-OTel variants, delivery-before-channel
word order, multiple replicas, double negation, or equivalent Chinese expressions; positive
assertions fail anywhere in this report, the capability matrix, or PROGRESS, while canonical
deferred/unsupported boundaries remain allowed.

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
        "passed": 178,
        "skipped": 0
      },
      "exit_code": 0,
      "id": "focused_fault_matrix",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/focused_fault_matrix.log",
      "log_sha256": "b048021df2d8224d6e9aff9744ed3c4b4499ad22ee60b7a1e88569fdd04cb00d"
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
      "log_sha256": "81faca1239cb2d310623545c2b39e05ce003d5b0bf444a1aef43dce7018c139c"
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
      "log_sha256": "d03a5ce0e955698a5a2a095b815337d66b98142c8fab0ee8894e22b52e4ddecc"
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
      "log_sha256": "5c25c90b51dff970e77bcac50630bc7b446085c5b5a2cf27d426b8be1f36dbb4"
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
      "log_sha256": "5b706a5d0e8a6bfad7269ca0e35b8e953cdb85e5516b57d9b8c005c99f1e8d40"
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
      "log_sha256": "fd2f899afb42b5ba56dbf0694b60eb7d9b09d68f5466e7ddff862a941a54a4d2"
    },
    {
      "command": "env -u VIRTUAL_ENV .venv/bin/python -m pytest -m \"not slow\" -q",
      "counts": {
        "deselected": 24,
        "failed": 0,
        "passed": 3804,
        "skipped": 2
      },
      "exit_code": 0,
      "id": "full_non_slow",
      "log_path": "docs/cc-fable-v1/reports/s3-core-evidence/full_non_slow.log",
      "log_sha256": "4a568bc6440a2c13e93da0d9c970cd6179098f82932443d37e496acf57ee0447"
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
      "source_sha256": "fa0f4959ac811143a54c02398141b1138164e664f3ed6a24c385bbefa6be048b",
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
  "source_commit": "8f5b324306b6b49e35ae89d0c22f4ab9a072edb2",
  "status": "passed"
}
```
<!-- /s3-core-evidence:v1 -->
