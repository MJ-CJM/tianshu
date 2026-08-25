"""SystemSnapshot attribution through the generic Evidence artifact channel."""

from __future__ import annotations

import json

from tianshu.models.canonical import canonical_sha256
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository

from ._fixtures import evidence_service, seed_closed_run

_MEDIA_TYPE = "application/vnd.tianshu.system-snapshot.v1+json"


def _snapshot() -> SystemSnapshotV1:
    components = {"kernel": "a" * 64, "skills": "b" * 64}
    return SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )


def test_bound_snapshot_is_required_stable_and_generically_verifiable(
    storage,
    tmp_path,
) -> None:
    _, memorial = seed_closed_run(storage)
    snapshot = _snapshot()
    with storage.unit_of_work() as unit_of_work:
        SystemSnapshotRepository().insert_binding(
            unit_of_work.connection,
            memorial_id=memorial.id,
            attempt_id="attempt-evidence",
            snapshot=snapshot,
            generation_ids=("generation-1",),
        )
        unit_of_work.commit()

    artifact_root = tmp_path / "artifacts"
    service = evidence_service(storage, artifact_root)
    opened = service.build_open(memorial.id)
    [opened_ref] = [
        artifact for artifact in opened.snapshot.artifacts if artifact.media_type == _MEDIA_TYPE
    ]

    assert opened_ref.digest in opened.snapshot.requirements.artifact_digests
    payload = json.loads(service._artifacts.get_bytes(opened_ref.digest))  # noqa: SLF001
    assert payload == {
        "snapshot": snapshot.model_dump(mode="json"),
        "generation_ids": ["generation-1"],
    }

    closed = service.close(memorial.id, expected_version=opened.version)
    [closed_ref] = [
        artifact for artifact in closed.snapshot.artifacts if artifact.media_type == _MEDIA_TYPE
    ]
    assert closed_ref.digest == opened_ref.digest
    assert service.verify(closed.bundle_id).verified
    exported = service.export(closed.bundle_id)
    assert service.verify_export(exported).verified
    assert service.import_bundle(exported) == closed

    artifact_path = artifact_root / closed_ref.digest[:2] / closed_ref.digest
    artifact_path.write_bytes(b"tampered")
    verification = service.verify(closed.bundle_id)
    assert not verification.verified
    assert f"artifact_invalid:{closed_ref.digest}" in verification.reason_codes


def test_missing_binding_skips_system_snapshot_artifact_and_still_closes(
    storage,
    tmp_path,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")

    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)

    assert not any(artifact.media_type == _MEDIA_TYPE for artifact in closed.snapshot.artifacts)
    assert service.verify(closed.bundle_id).verified
