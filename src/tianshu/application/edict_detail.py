"""Principal-scoped composition of durable Edict governance and Evidence facts."""

from __future__ import annotations

from tianshu.authz import can_access_submitter
from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.models.decision import DecisionRecordV1
from tianshu.models.edict_detail import (
    EdictDecisionDetailV1,
    EdictDecisionRequestDetailV1,
    EdictDecisionResolutionDetailV1,
    EdictDetailSnapshotV1,
    EdictEvidenceDetailV1,
    EdictRunDetailV1,
    EvidenceArtifactDetailV1,
    EvidenceEnvironmentDetailV1,
    EvidenceExecutorIdentityV1,
)
from tianshu.models.principal import AuthContext
from tianshu.models.run_state import agent_plan_continuation
from tianshu.storage import Storage
from tianshu.storage.edict_repo import get_edict_current
from tianshu.storage.memorial_repo import list_memorials_for_edict_current

_DISCLOSED_DECISION_PAYLOAD_KEYS = frozenset(
    {
        "apply_mode",
        "iteration",
        "permission_boundary",
        "plan_hash",
        "plan_revision_id",
        "restore_point",
        "summary",
        "target_ref",
        "tool_name",
        "verdict",
    }
)


def _decision_detail(record: DecisionRecordV1) -> EdictDecisionDetailV1:
    request = record.request
    resolution = record.resolution
    return EdictDecisionDetailV1(
        request=EdictDecisionRequestDetailV1(
            decision_request_id=request.decision_request_id,
            kind=request.kind,
            edict_id=request.edict_id,
            memorial_id=request.memorial_id,
            payload={
                key: value
                for key, value in request.payload.items()
                if key in _DISCLOSED_DECISION_PAYLOAD_KEYS
            },
            requested_by=request.requested_by,
            expires_at=request.expires_at,
            status=request.status,
            version=request.version,
            created_at=request.created_at,
            updated_at=request.updated_at,
        ),
        resolution=(
            EdictDecisionResolutionDetailV1(
                action=resolution.action,
                reason=resolution.reason,
                actor_principal_id=resolution.actor_principal_id,
                actor_display_name=resolution.actor_display_name,
                resolved_at=resolution.resolved_at,
            )
            if resolution is not None
            else None
        ),
    )


class EdictDetailNotFound(RuntimeError):
    """The Edict is absent or belongs to another principal."""


class EdictDetailUnavailable(RuntimeError):
    """One or more durable detail sources could not be decoded or read."""


class EdictDetailQueryService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def get_snapshot(self, auth: AuthContext, edict_id: str) -> EdictDetailSnapshotV1:
        try:
            with self._storage.unit_of_work() as unit_of_work:
                connection = unit_of_work.connection
                edict = get_edict_current(connection, edict_id)
                if edict is None or not can_access_submitter(auth, edict.submitter):
                    raise EdictDetailNotFound(edict_id)
                memorials = list_memorials_for_edict_current(connection, edict_id)
                run_states = self._storage.run_state_repo.list_for_edict(connection, edict_id)
                decisions = self._storage.decision_repo.list_for_edict(connection, edict_id)
                bundles = self._storage.evidence_repo.list_for_edict_current(
                    connection,
                    edict_id,
                )
                unit_of_work.commit()
        except EdictDetailNotFound:
            raise
        except Exception as exc:
            raise EdictDetailUnavailable("authoritative Edict detail source failed") from exc

        effective_by_memorial = {
            memorial.id: memorial.effective_governance_contract for memorial in memorials
        }
        runs = []
        for state in run_states:
            plan = agent_plan_continuation(state.continuation)
            runs.append(
                EdictRunDetailV1(
                    memorial_id=state.memorial_id,
                    phase=state.phase,
                    version=state.version,
                    checkpoint_present=state.checkpoint_ref is not None,
                    side_effect_cursor=state.side_effect_cursor,
                    pending_decision_id=state.continuation.pending_decision_id,
                    resolved_decision_id=state.continuation.resolved_decision_id,
                    plan_lineage=plan.plan_revisions if plan is not None else (),
                    effective_contract=effective_by_memorial.get(state.memorial_id),
                    updated_at=state.updated_at,
                )
            )

        evidence = []
        for bundle in bundles:
            snapshot = bundle.snapshot
            if isinstance(bundle, ClosedEvidenceBundleV1):
                content_hash = bundle.content_hash
                closed_at = bundle.closed_at
                download_available = True
            else:
                content_hash = None
                closed_at = None
                download_available = False
            evidence.append(
                EdictEvidenceDetailV1(
                    bundle_id=bundle.bundle_id,
                    memorial_id=bundle.memorial_id,
                    status=bundle.status,
                    version=bundle.version,
                    content_hash=content_hash,
                    created_at=bundle.created_at,
                    closed_at=closed_at,
                    download_available=download_available,
                    executor=EvidenceExecutorIdentityV1(
                        adapter_id=snapshot.executor_manifest.adapter_id,
                        display_name=snapshot.executor_manifest.display_name,
                        level=snapshot.executor_manifest.level,
                        manifest_hash=snapshot.executor_manifest_hash,
                    ),
                    artifacts=tuple(
                        EvidenceArtifactDetailV1(
                            digest=artifact.digest,
                            size_bytes=artifact.size_bytes,
                            media_type=artifact.media_type,
                            redaction=artifact.redaction,
                        )
                        for artifact in snapshot.artifacts
                    ),
                    checks=snapshot.checks,
                    decisions=snapshot.decisions,
                    effects=snapshot.effects,
                    cost=snapshot.cost,
                    environment=EvidenceEnvironmentDetailV1(
                        tianshu_version=snapshot.environment.tianshu_version,
                        python_version=snapshot.environment.python_version,
                        platform=snapshot.environment.platform,
                        architecture=snapshot.environment.architecture,
                        dependency_lock_hash=snapshot.environment.dependency_lock_hash,
                        environment_fingerprint=snapshot.environment.environment_fingerprint,
                    ),
                    auditor=snapshot.auditor,
                    requirements=snapshot.requirements,
                )
            )

        return EdictDetailSnapshotV1(
            edict=edict,
            memorials=tuple(memorials),
            runs=tuple(runs),
            decisions=tuple(_decision_detail(decision) for decision in decisions),
            evidence=tuple(evidence),
        )


__all__ = [
    "EdictDetailNotFound",
    "EdictDetailQueryService",
    "EdictDetailUnavailable",
]
