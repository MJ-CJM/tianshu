"""Public Evidence Bundle v1 contracts with cycle-safe lazy services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tianshu.evidence.models import (
    ArtifactRefV1,
    ClosedEvidenceBundleV1,
    EvidenceBundleV1,
    EvidenceVerificationV1,
)

if TYPE_CHECKING:
    from tianshu.evidence.service import ArtifactStore, EvidenceService


def __getattr__(name: str) -> Any:
    if name in {"ArtifactStore", "EvidenceService"}:
        from tianshu.evidence.service import ArtifactStore, EvidenceService

        return {"ArtifactStore": ArtifactStore, "EvidenceService": EvidenceService}[name]
    raise AttributeError(name)


__all__ = [
    "ArtifactRefV1",
    "ArtifactStore",
    "ClosedEvidenceBundleV1",
    "EvidenceBundleV1",
    "EvidenceService",
    "EvidenceVerificationV1",
]
