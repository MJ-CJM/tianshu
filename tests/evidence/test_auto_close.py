from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tianshu.application.edicts import EdictApplicationService
from tianshu.bus.event_bus import EventBus
from tianshu.evidence.models import ClosedEvidenceBundleV1, EvidenceBundleV1
from tianshu.evidence.service import EvidenceServiceError
from tianshu.executor.approvals import ApprovalManager
from tianshu.models import Decree, TaskStatus
from tianshu.models.events import EventEnvelope, make_event

from ._fixtures import evidence_service, seed_closed_run


async def test_passed_audit_event_closes_bound_evidence_idempotently(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    event = make_event(
        "audit.completed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={"verdict": "pass", "reasons": []},
    )

    await service.handle_audit_completed(event)
    await service.handle_audit_completed(event)

    bundles = storage.evidence_repo.list_for_edict(edict.id)
    assert len(bundles) == 1
    assert isinstance(bundles[0], ClosedEvidenceBundleV1)
    assert bundles[0].memorial_id == memorial.id


def _set_review(storage, memorial, *, status: TaskStatus, review_status: str) -> None:
    memorial.status = status
    memorial.review_status = review_status
    storage.update_memorial(memorial)


def _approval_event(edict, memorial, decree, **payload: object) -> EventEnvelope:
    return make_event(
        "decree.approved",
        edict_id=edict.id,
        memorial_id=memorial.id,
        producer="approval_manager",
        payload={"decree_id": decree.id, **payload},
    )


def _bundle_for(storage, edict_id: str) -> EvidenceBundleV1 | ClosedEvidenceBundleV1:
    [bundle] = storage.evidence_repo.list_for_edict(edict_id)
    return bundle


async def test_passed_audit_keeps_pending_human_review_evidence_open(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    service = evidence_service(storage, tmp_path / "artifacts")

    await service.handle_audit_completed(
        make_event(
            "audit.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={"verdict": "pass"},
        )
    )

    assert isinstance(_bundle_for(storage, edict.id), EvidenceBundleV1)
    with pytest.raises(EvidenceServiceError, match="governance"):
        service.close(memorial.id, expected_version=1)


async def test_rejected_review_never_closes_passed_audit_evidence(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    audit = make_event(
        "audit.completed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={"verdict": "pass"},
    )
    await service.handle_audit_completed(audit)
    rejection = Decree(
        id="decree:rejected",
        memorial_id=memorial.id,
        action="reject",
        comment="human review rejected",
    )
    storage.save_decree(rejection)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.FAILED,
        review_status="rejected",
    )

    await service.handle_audit_completed(audit)
    await service.handle_decree_approved(
        make_event(
            "decree.approved",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={"decree_id": rejection.id},
        )
    )

    assert isinstance(_bundle_for(storage, edict.id), EvidenceBundleV1)


@pytest.mark.parametrize("order", ["audit_then_approval", "approval_then_audit"])
async def test_approved_review_closes_with_decree_evidence_under_reorder_and_replay(
    storage,
    tmp_path,
    order: str,
) -> None:
    edict, memorial = seed_closed_run(
        storage,
        correlation_id="correlation:review",
        submitter="user:owner",
    )
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    audit = make_event(
        "audit.completed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={"verdict": "pass"},
    )
    approval = Decree(
        id="decree:approved",
        memorial_id=memorial.id,
        action="approve",
        actor="user:owner",
        comment="human approved final result",
    )
    event = _approval_event(
        edict,
        memorial,
        approval,
        actor="user:owner",
        owner_id="user:owner",
        correlation_id="correlation:review",
    )
    if order == "audit_then_approval":
        await service.handle_audit_completed(audit)
    storage.save_decree(approval)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.COMPLETED,
        review_status="approved",
    )

    await service.handle_decree_approved(event)
    await service.handle_decree_approved(event)
    await service.handle_audit_completed(audit)

    closed = _bundle_for(storage, edict.id)
    assert isinstance(closed, ClosedEvidenceBundleV1)
    assert any(
        decision.decision_request_id == approval.id
        and decision.action == "approve"
        and decision.actor_principal_id == approval.actor
        for decision in closed.snapshot.decisions
    )


async def test_approval_close_is_restart_and_race_idempotent(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    first = evidence_service(storage, tmp_path / "artifacts")
    await first.handle_audit_completed(
        make_event(
            "audit.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={"verdict": "pass"},
        )
    )
    approval = Decree(
        id="decree:restart-approved",
        memorial_id=memorial.id,
        action="approve",
    )
    storage.save_decree(approval)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.COMPLETED,
        review_status="approved",
    )
    restarted_a = evidence_service(storage, tmp_path / "artifacts")
    restarted_b = evidence_service(storage, tmp_path / "artifacts")
    event = _approval_event(edict, memorial, approval)

    await asyncio.gather(
        restarted_a.handle_decree_approved(event),
        restarted_b.handle_decree_approved(event),
    )

    assert isinstance(_bundle_for(storage, edict.id), ClosedEvidenceBundleV1)


async def test_approval_manager_event_closes_through_the_registered_handler(
    storage,
    tmp_path,
) -> None:
    edict, memorial = seed_closed_run(
        storage,
        correlation_id="correlation:manager",
        submitter="user:owner",
    )
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    bus = EventBus()
    bus.on(
        "decree.approved",
        service.handle_decree_approved,
        consumer_name="evidence.decree_approved.v1",
    )
    manager = ApprovalManager(
        bus,
        storage,
        edict_application_service=EdictApplicationService(storage),
    )

    await manager.submit_decree(
        Decree(
            id="decree:manager-approved",
            memorial_id=memorial.id,
            action="approve",
            actor="user:owner",
        )
    )

    assert isinstance(_bundle_for(storage, edict.id), ClosedEvidenceBundleV1)


@pytest.mark.parametrize(
    ("payload", "wrong_edict"),
    [
        ({"owner_id": "user:outsider"}, False),
        ({"correlation_id": "correlation:other"}, False),
        ({"actor": "user:outsider"}, False),
        ({}, True),
    ],
)
async def test_approval_event_rejects_wrong_owner_correlation_actor_and_edict_binding(
    storage,
    tmp_path,
    payload: dict[str, object],
    wrong_edict: bool,
) -> None:
    edict, memorial = seed_closed_run(
        storage,
        correlation_id="correlation:owned",
        submitter="user:owner",
    )
    _set_review(
        storage,
        memorial,
        status=TaskStatus.NEEDS_REVIEW,
        review_status="pending",
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    await service.handle_audit_completed(
        make_event(
            "audit.completed",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={"verdict": "pass"},
        )
    )
    approval = Decree(
        id="decree:owned",
        memorial_id=memorial.id,
        action="approve",
        actor="user:owner",
    )
    storage.save_decree(approval)
    _set_review(
        storage,
        memorial,
        status=TaskStatus.COMPLETED,
        review_status="approved",
    )
    event = EventEnvelope(
        event_type="decree.approved",
        edict_id="edict:other" if wrong_edict else edict.id,
        memorial_id=memorial.id,
        payload={"decree_id": approval.id, **payload},
    )

    await service.handle_decree_approved(event)

    assert isinstance(_bundle_for(storage, edict.id), EvidenceBundleV1)


async def test_approval_event_cannot_splice_another_runs_decree(storage, tmp_path: Path) -> None:
    first_edict, first_memorial = seed_closed_run(
        storage,
        submitter="user:first",
        edict_id="edict:first",
        memorial_id="memorial:first",
    )
    second_edict, second_memorial = seed_closed_run(
        storage,
        submitter="user:second",
        edict_id="edict:second",
        memorial_id="memorial:second",
    )
    service = evidence_service(storage, tmp_path / "artifacts")
    for edict, memorial in (
        (first_edict, first_memorial),
        (second_edict, second_memorial),
    ):
        _set_review(
            storage,
            memorial,
            status=TaskStatus.NEEDS_REVIEW,
            review_status="pending",
        )
        await service.handle_audit_completed(
            make_event(
                "audit.completed",
                edict_id=edict.id,
                memorial_id=memorial.id,
                payload={"verdict": "pass"},
            )
        )
    second_approval = Decree(
        id="decree:second",
        memorial_id=second_memorial.id,
        action="approve",
        actor="user:second",
    )
    storage.save_decree(second_approval)
    _set_review(
        storage,
        first_memorial,
        status=TaskStatus.COMPLETED,
        review_status="approved",
    )

    await service.handle_decree_approved(
        EventEnvelope(
            event_type="decree.approved",
            edict_id=first_edict.id,
            memorial_id=first_memorial.id,
            payload={"decree_id": second_approval.id},
        )
    )

    assert isinstance(_bundle_for(storage, first_edict.id), EvidenceBundleV1)
    assert isinstance(_bundle_for(storage, second_edict.id), EvidenceBundleV1)


def test_production_wiring_subscribes_final_approval_evidence_close() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "tianshu" / "bootstrap" / "wiring_scheduler.py"
    ).read_text(encoding="utf-8")

    assert '"decree.approved"' in source
    assert "evidence_service.handle_decree_approved" in source
    assert 'consumer_name="evidence.decree_approved.v1"' in source
