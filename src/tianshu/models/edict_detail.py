"""Disclosure-safe composed read model for the Edict detail Web surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.evidence.models import (
    AuditorConclusionV1,
    CheckEvidenceV1,
    CostEvidenceV1,
    DecisionEvidenceV1,
    EffectEvidenceV1,
    EvidenceRequirementsV1,
)
from tianshu.models.canonical import JsonValue
from tianshu.models.decision import DecisionKind, DecisionStatus
from tianshu.models.edict import Edict
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.memorial import Memorial
from tianshu.models.plan_revision import PlanRevisionV1
from tianshu.models.run_state import RunPhase


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class EdictRunDetailV1(_StrictModel):
    memorial_id: str
    phase: RunPhase
    version: int = Field(ge=1)
    checkpoint_present: bool
    side_effect_cursor: int = Field(ge=0)
    pending_decision_id: str | None
    resolved_decision_id: str | None
    plan_lineage: tuple[PlanRevisionV1, ...]
    effective_contract: EffectiveGovernanceContractV1 | None
    updated_at: datetime


class EdictDecisionRequestDetailV1(_StrictModel):
    decision_request_id: str
    kind: DecisionKind
    edict_id: str
    memorial_id: str
    payload: dict[str, JsonValue]
    requested_by: str
    expires_at: datetime
    status: DecisionStatus
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class EdictDecisionResolutionDetailV1(_StrictModel):
    action: str
    reason: str
    actor_principal_id: str
    actor_display_name: str
    resolved_at: datetime


class EdictDecisionDetailV1(_StrictModel):
    request: EdictDecisionRequestDetailV1
    resolution: EdictDecisionResolutionDetailV1 | None


class EvidenceExecutorIdentityV1(_StrictModel):
    adapter_id: str
    display_name: str
    level: Literal["managed", "contained", "observe-only"]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceArtifactDetailV1(_StrictModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    redaction: str


class EvidenceEnvironmentDetailV1(_StrictModel):
    tianshu_version: str
    python_version: str
    platform: str
    architecture: str
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class EdictEvidenceDetailV1(_StrictModel):
    bundle_id: str
    memorial_id: str
    status: Literal["open", "closed"]
    version: int = Field(ge=1)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    closed_at: datetime | None
    download_available: bool
    executor: EvidenceExecutorIdentityV1
    artifacts: tuple[EvidenceArtifactDetailV1, ...]
    checks: tuple[CheckEvidenceV1, ...]
    decisions: tuple[DecisionEvidenceV1, ...]
    effects: tuple[EffectEvidenceV1, ...]
    cost: CostEvidenceV1
    environment: EvidenceEnvironmentDetailV1
    auditor: AuditorConclusionV1
    requirements: EvidenceRequirementsV1

    @model_validator(mode="after")
    def validate_download_truth(self) -> Self:
        closed = self.status == "closed"
        if closed != self.download_available:
            raise ValueError("download availability must match closed status")
        if closed != (self.content_hash is not None and self.closed_at is not None):
            raise ValueError("closed evidence requires content hash and close time")
        return self


class EdictDetailSnapshotV1(_StrictModel):
    schema_version: Literal[1] = 1
    edict: Edict
    memorials: tuple[Memorial, ...]
    runs: tuple[EdictRunDetailV1, ...]
    decisions: tuple[EdictDecisionDetailV1, ...]
    evidence: tuple[EdictEvidenceDetailV1, ...]


__all__ = [
    "EdictDetailSnapshotV1",
    "EdictDecisionDetailV1",
    "EdictDecisionRequestDetailV1",
    "EdictDecisionResolutionDetailV1",
    "EdictEvidenceDetailV1",
    "EdictRunDetailV1",
    "EvidenceArtifactDetailV1",
    "EvidenceEnvironmentDetailV1",
    "EvidenceExecutorIdentityV1",
]
