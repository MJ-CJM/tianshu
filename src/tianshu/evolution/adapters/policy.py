"""Governance policy candidate source validation."""

from collections.abc import Mapping

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.executor.workspace_policy import validate_workspace_policy
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.governance_contract import RecoveryPolicyV1, WorkspacePolicyV1


class PolicyCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.POLICY

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        workspace_payload = payload.get("workspace")
        recovery_payload = payload.get("recovery")
        if not isinstance(workspace_payload, dict) or not isinstance(recovery_payload, dict):
            raise AdapterError("policy source requires workspace and recovery objects")
        workspace = WorkspacePolicyV1.model_validate(workspace_payload)
        recovery = RecoveryPolicyV1.model_validate(recovery_payload)
        validate_workspace_policy(workspace, recovery)


__all__ = ["PolicyCandidateAdapter"]
