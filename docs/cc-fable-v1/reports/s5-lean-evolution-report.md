# S5 Lean Core Gate

Status: Lean Core Gate `passed`.

This bounded Gate covers the unified candidate, current fail-closed gate, PromotionService authority, durable challenger assignment/effective overlay, restart-stable 9%-11% distribution contract, and rollback traffic closure.

It does not close complete G4. OpenHands, executor compatibility, ROI, cost calibration/enforcement, and full G4 remain `external_pending`.

## Recomputed evidence

```json
{
  "artifacts": {
    "assignment": {
      "path": "tests/universe/test_challenger_routing.py",
      "sha256": "0de656171ab40686f9b8cfe6ffb0227a64cd861d57c92596817f041751793229"
    },
    "candidate": {
      "path": "tests/evolution/test_candidate_schema.py",
      "sha256": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83"
    },
    "decision": {
      "path": "tests/architecture/test_promotion_authority.py",
      "sha256": "b83425dee563a231dbea56d406bc233499bf2641949fe6714081a8a2afc7cde9"
    },
    "gate": {
      "path": "tests/evolution/test_gate_evaluator.py",
      "sha256": "776720571325061759f5381ea339ec83c88f3b7096568638406cf2cf1cd7010d"
    },
    "promotion": {
      "path": "tests/evolution/test_promotion_fail_closed.py",
      "sha256": "628776f009e504a7f1b1534b2d3c72de197db6fc1afe8a1f61fd31b91fe384a0"
    },
    "rollback": {
      "path": "tests/evolution/test_rollback_fault_matrix.py",
      "sha256": "7a00bc640186c696260970d277c13af5f0704650d4297435c6fd72e9aa7fa8c8"
    }
  },
  "assignment": {
    "arm": "challenger",
    "assignment_hash": "0de656171ab40686f9b8cfe6ffb0227a64cd861d57c92596817f041751793229",
    "assignment_id": "assignment:lean-contract-v1",
    "candidate_id": "candidate:lean-contract-v1",
    "champion_digest": "2e95ab1d9452217133ac00386a4b04448645da0648f1f0b21c7c60af3d48da05",
    "effective_overlay_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83",
    "evidence_candidate_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83",
    "persisted_before_dispatch": true,
    "routing_version": 2,
    "selected_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83"
  },
  "candidate": {
    "automatic_promotion": false,
    "candidate_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83",
    "candidate_id": "candidate:lean-contract-v1",
    "kind": "skill"
  },
  "deferred": {
    "compatibility": "external_pending",
    "cost": "external_pending",
    "full_g4": "external_pending",
    "openhands": "external_pending",
    "roi": "external_pending"
  },
  "distribution": {
    "challenger": 1000,
    "total": 10000
  },
  "distribution_rate": 0.1,
  "gate": {
    "blocking_gates": [],
    "candidate_id": "candidate:lean-contract-v1",
    "evidence_bundle_id": "evidence:s5-lean-contract-v1",
    "evidence_hash": "0de656171ab40686f9b8cfe6ffb0227a64cd861d57c92596817f041751793229",
    "promotion_allowed": true,
    "report_hash": "776720571325061759f5381ea339ec83c88f3b7096568638406cf2cf1cd7010d"
  },
  "gate_name": "Lean Core Gate",
  "gate_status": "passed",
  "promotion": {
    "action": "start_canary",
    "allocation_basis_points": 1000,
    "authority": "PromotionService",
    "candidate_id": "candidate:lean-contract-v1",
    "decision": {
      "risk_tier": "standard",
      "status": "not_required"
    },
    "expected_version": 4,
    "reason": "bounded Lean candidate contract proof",
    "routing_version": 2
  },
  "resumed_assignment": {
    "arm": "challenger",
    "assignment_hash": "0de656171ab40686f9b8cfe6ffb0227a64cd861d57c92596817f041751793229",
    "assignment_id": "assignment:lean-contract-v1",
    "candidate_id": "candidate:lean-contract-v1",
    "champion_digest": "2e95ab1d9452217133ac00386a4b04448645da0648f1f0b21c7c60af3d48da05",
    "effective_overlay_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83",
    "evidence_candidate_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83",
    "persisted_before_dispatch": true,
    "routing_version": 2,
    "selected_digest": "1a7b960522596f46d88056d8b45fd1bfb9707c6436e0f9cadeffa6b0412f9b83"
  },
  "rollback": {
    "allocation_basis_points_after": 0,
    "new_run_arm": "champion",
    "restore_verified": true,
    "routing_version_after": 3,
    "routing_version_before": 2,
    "state": "rolled_back"
  },
  "schema_version": "s5-lean-core-gate-v1"
}
```
