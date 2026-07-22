"""Governance policy candidate source validation."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.executor.workspace_policy import validate_workspace_policy
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.governance_contract import RecoveryPolicyV1, WorkspacePolicyV1


class _PolicySourceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: WorkspacePolicyV1
    recovery: RecoveryPolicyV1


class PolicyCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.POLICY

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            source = _PolicySourceV1.model_validate(payload)
            validate_workspace_policy(source.workspace, source.recovery)
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("policy source validation failed") from None
        return source.model_dump(mode="json")


__all__ = ["PolicyCandidateAdapter"]
