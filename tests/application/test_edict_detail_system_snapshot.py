"""Disclosure-safe SystemSnapshot projection on Edict detail."""

from __future__ import annotations

from tests.evidence._fixtures import evidence_service, seed_closed_run

from tianshu.application.edict_detail import EdictDetailQueryService
from tianshu.models.canonical import canonical_sha256
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository

_MEDIA_TYPE = "application/vnd.tianshu.system-snapshot.v1+json"


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:owner",
            kind="human",
            display_name="Owner",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="corr-edict-snapshot",
    )


def _snapshot() -> SystemSnapshotV1:
    components = {"kernel": "d" * 64}
    return SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )


def _insert_binding(storage, memorial_id: str, snapshot: SystemSnapshotV1) -> None:
    with storage.unit_of_work() as unit_of_work:
        SystemSnapshotRepository().insert_binding(
            unit_of_work.connection,
            memorial_id=memorial_id,
            attempt_id="attempt-detail",
            snapshot=snapshot,
        )
        unit_of_work.commit()


def test_detail_projects_snapshot_digest_not_artifact_digest(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(
        storage,
        submitter="user:owner",
        correlation_id="corr-edict-snapshot",
    )
    snapshot = _snapshot()
    _insert_binding(storage, memorial.id, snapshot)
    bundles = evidence_service(storage, tmp_path / "artifacts")
    opened = bundles.build_open(memorial.id)
    closed = bundles.close(memorial.id, expected_version=opened.version)
    [artifact] = [item for item in closed.snapshot.artifacts if item.media_type == _MEDIA_TYPE]

    detail = EdictDetailQueryService(storage).get_snapshot(_auth(), edict.id)

    assert detail.evidence[0].system_snapshot_digest == snapshot.digest
    assert artifact.digest != snapshot.digest
    assert detail.evidence[0].system_snapshot_digest != artifact.digest


def test_detail_requires_evidence_media_type_before_projecting_binding(
    storage,
    tmp_path,
) -> None:
    edict, memorial = seed_closed_run(
        storage,
        submitter="user:owner",
        correlation_id="corr-edict-snapshot",
    )
    bundles = evidence_service(storage, tmp_path / "artifacts")
    opened = bundles.build_open(memorial.id)
    bundles.close(memorial.id, expected_version=opened.version)
    _insert_binding(storage, memorial.id, _snapshot())

    detail = EdictDetailQueryService(storage).get_snapshot(_auth(), edict.id)

    assert detail.evidence[0].system_snapshot_digest is None
