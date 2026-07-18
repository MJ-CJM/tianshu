from __future__ import annotations

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.models.events import make_event

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
