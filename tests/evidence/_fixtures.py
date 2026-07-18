from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.executor.capabilities import (
    get_executor_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.models import AuditResult, Edict, EventEnvelope, Memorial, TaskStatus
from tianshu.models.governance_contract import (
    AcceptancePolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.plan_revision import build_plan_revision
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

NOW = datetime(2026, 7, 17, 8, 9, 10, tzinfo=UTC)


def seed_closed_run(
    storage: Storage,
    *,
    acceptance: AcceptancePolicyV1 | None = None,
    side_effect_cursor: int = 0,
    correlation_id: str | None = None,
    submitter: str | None = None,
) -> tuple[Edict, Memorial]:
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="produce independently verifiable evidence"),
        acceptance=acceptance or AcceptancePolicyV1(),
    )
    manifest = get_executor_manifest("native")
    effective = resolve_governance_contract(requested, manifest, probe_host_capabilities())
    edict = Edict(
        id="edict-evidence",
        goal=requested.objective.goal,
        governance_contract=requested,
        submitter=submitter,
    )
    memorial = Memorial(
        id="memorial-evidence",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.COMPLETED,
        completed_at=NOW,
        audit=AuditResult(verdict="pass", rules_checked=1),
        effective_governance_contract=effective,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)

    if correlation_id is not None:
        with storage.unit_of_work() as unit_of_work:
            OutboxRepository().add(
                unit_of_work.connection,
                EventEnvelope(
                    event_id="event-evidence",
                    event_type="edict.submitted",
                    edict_id=edict.id,
                    memorial_id=memorial.id,
                    producer="evidence-test",
                    payload={"correlation_id": correlation_id},
                    timestamp=NOW,
                ),
            )
            unit_of_work.commit()

    plan = {"tasks": [{"task_id": "verify", "description": "verify evidence"}]}
    revision = build_plan_revision(
        plan,
        revision_id="plan-revision-1",
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
        created_at=NOW,
    )
    usage = PersistedUsageSummaryV1(
        prompt_tokens=3,
        completion_tokens=5,
        total_tokens=8,
        cache_read_tokens=1,
        cost_cny=0.02,
        actual_model="demo-model",
        upstream_provider=None,
    )
    continuation = AgentContinuationV1(
        messages=(),
        pending_tool=None,
        iteration=1,
        usage=usage,
        checkpoint_ref=None,
        resolved_decision_id=None,
        side_effect_cursor=side_effect_cursor,
        plan_ref="plan:current",
        plan_hash=revision.plan_hash,
        plan_revision_id=revision.revision_id,
        plan_revisions=(revision,),
        plan_snapshot=plan,
    )
    state = RunStateV1(
        memorial_id=memorial.id,
        edict_id=edict.id,
        phase=RunPhase.COMPLETED,
        continuation=continuation,
        checkpoint_ref=None,
        side_effect_cursor=side_effect_cursor,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    with storage.unit_of_work() as unit_of_work:
        storage.run_state_repo.create(unit_of_work.connection, state)
        unit_of_work.commit()
    return edict, memorial


def evidence_service(storage: Storage, root: Path) -> EvidenceService:
    artifacts = ArtifactStore(
        root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
        clock=lambda: NOW,
    )
    return EvidenceService(storage, artifacts, clock=lambda: NOW)
