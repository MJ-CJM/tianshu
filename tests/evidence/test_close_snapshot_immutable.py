from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.evidence.service import EvidenceImportError, EvidenceServiceError
from tianshu.governance.decision_service import DecisionService
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.evolution_candidate import CandidateKind, CandidateVersionRefV1
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    EvolutionRunEvidenceV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryDecodeError,
)
from tianshu.universe.router import ChallengerRouter

from ._fixtures import NOW, evidence_service, seed_closed_run


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="local:owner",
            kind="local",
            display_name="Local Owner",
            scopes=frozenset({"*"}),
        ),
        source="trusted-local",
        client_kind="web",
        correlation_id="corr-evidence-decision",
    )


def _governed_assignment(
    memorial_id: str,
) -> tuple[RunAssignmentV1, EffectiveEvolutionOverlayV1]:
    selected = CandidateVersionRefV1(
        version="2",
        artifact_digest="b" * 64,
        canonical_digest="c" * 64,
    )
    assignment = RunAssignmentV1(
        assignment_id=f"assignment:{memorial_id}",
        memorial_id=memorial_id,
        candidate_id="candidate-skill",
        champion_ref=CandidateVersionRefV1(
            version="1",
            artifact_digest="8" * 64,
            canonical_digest="9" * 64,
        ),
        selected_ref=selected,
        routing_version=1,
        bucket=42,
        created_at=NOW,
    )
    overlay = EffectiveEvolutionOverlayV1(
        assignment_id=assignment.assignment_id,
        kind=CandidateKind.SKILL,
        subject_key="skill:research",
        artifact_digest=selected.artifact_digest,
        canonical_digest=selected.canonical_digest,
    )
    return assignment, overlay


def _subject_assignment(
    assignment: RunAssignmentV1,
    overlay: EffectiveEvolutionOverlayV1,
    *,
    assignment_id: str = "subject-assignment-skill",
) -> SubjectRunAssignmentV1:
    assert overlay.kind is not None
    assert overlay.subject_key is not None
    return SubjectRunAssignmentV1(
        assignment_id=assignment_id,
        memorial_id=assignment.memorial_id,
        kind=overlay.kind,
        subject_key=overlay.subject_key,
        candidate_id=assignment.candidate_id,
        champion_ref=assignment.champion_ref,
        selected_ref=assignment.selected_ref,
        routing_version=assignment.routing_version,
        bucket=assignment.bucket,
        created_at=assignment.created_at,
    )


def _assignment_set(
    memorial_id: str,
    assignments: tuple[SubjectRunAssignmentV1, ...],
) -> RunAssignmentSetV1:
    material = {
        "memorial_id": memorial_id,
        "assignments": [item.model_dump(mode="json") for item in assignments],
    }
    return RunAssignmentSetV1(
        memorial_id=memorial_id,
        assignments=assignments,
        set_hash=canonical_sha256(material),
    )


def _multi_assignment_set(
    assignment: RunAssignmentV1,
    overlay: EffectiveEvolutionOverlayV1,
    *,
    second_created_at: datetime | None = None,
) -> RunAssignmentSetV1:
    skill = _subject_assignment(assignment, overlay)
    persona = skill.model_copy(
        update={
            "assignment_id": "subject-assignment-persona",
            "kind": CandidateKind.PERSONA,
            "subject_key": "persona:reviewer",
            "candidate_id": "candidate-persona",
            "created_at": second_created_at or skill.created_at,
        }
    )
    assignments = tuple(
        sorted((skill, persona), key=lambda item: (item.kind.value, item.subject_key))
    )
    return _assignment_set(assignment.memorial_id, assignments)


def test_close_is_canonical_idempotent_immutable_and_independently_verifiable(
    storage, tmp_path
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")

    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)

    assert isinstance(closed, ClosedEvidenceBundleV1)
    assert service.export(closed.bundle_id) == canonical_json_bytes(closed)
    assert service.close(memorial.id, expected_version=opened.version) == closed
    assert service.verify(closed.bundle_id).verified
    assert service.verify_export(service.export(closed.bundle_id)).verified
    schema = json.loads(
        (
            Path(__file__).parents[2] / "docs" / "reference" / "evidence-bundle-v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(service.export(closed.bundle_id)))

    for statement in (
        "UPDATE evidence_bundles SET body_json='{}' WHERE bundle_id=?",
        "DELETE FROM evidence_bundles WHERE bundle_id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            storage._conn.execute(statement, (closed.bundle_id,))  # noqa: SLF001
        storage._conn.rollback()  # noqa: SLF001

    tampered = json.loads(service.export(closed.bundle_id))
    tampered["snapshot"]["cost"]["prompt_tokens"] += 1
    verification = service.verify_export(canonical_json_bytes(tampered))
    assert not verification.verified
    assert "content_hash_mismatch" in verification.reason_codes

    other_root = evidence_service(storage, tmp_path / "other-root")
    with pytest.raises(EvidenceImportError, match="artifact_invalid"):
        other_root.import_bundle(service.export(closed.bundle_id))


def test_two_closers_cannot_create_distinct_snapshots(storage, tmp_path) -> None:
    _, memorial = seed_closed_run(storage)
    first = evidence_service(storage, tmp_path / "artifacts")
    second = evidence_service(storage, tmp_path / "artifacts")
    opened = first.build_open(memorial.id)

    winner = first.close(memorial.id, expected_version=opened.version)
    loser_retry = second.close(memorial.id, expected_version=opened.version)

    assert loser_retry == winner
    rows = storage._conn.execute(  # noqa: SLF001
        "SELECT body_json, content_hash FROM evidence_bundles WHERE memorial_id=?",
        (memorial.id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["body_json"] == canonical_json_bytes(winner).decode()


def test_close_decodes_a_durable_resolution_timestamp_from_sqlite(storage, tmp_path) -> None:
    edict, memorial = seed_closed_run(storage)
    now = datetime(2026, 7, 17, 8, 12, tzinfo=UTC)
    decisions = DecisionService(storage, clock=lambda: now)
    requested = decisions.request(
        RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key="plan:evidence-regression",
            payload={"plan_hash": "a" * 64},
            expires_at=now + timedelta(minutes=10),
        ),
        auth=_auth(),
    )
    decisions.resolve(
        requested.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="reviewed for immutable evidence",
            payload={"schema_version": 1},
            expected_version=requested.version,
        ),
        auth=_auth(),
    )
    service = evidence_service(storage, tmp_path / "artifacts")

    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)

    assert closed.snapshot.decisions[0].resolved_at == now


def test_evidence_does_not_create_a_missing_assignment(storage, tmp_path) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")

    legacy = service.build_open(memorial.id)

    assert not any(
        artifact.media_type
        in {
            "application/vnd.tianshu.evolution.assignment.v1+json",
            "application/vnd.tianshu.evolution.assignment-set.v1+json",
        }
        for artifact in legacy.snapshot.artifacts
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id=?",
            (memorial.id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id=?",
            (memorial.id,),
        ).fetchone()[0]
        == 0
    )


def test_evidence_excludes_an_existing_legacy_unmanaged_marker(storage, tmp_path) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment = ChallengerRouter(storage).assign(memorial.id)
    attributed = service.build_open(memorial.id)
    assert assignment.mode == "legacy_unmanaged"
    assert not any(
        artifact.media_type
        in {
            "application/vnd.tianshu.evolution.assignment.v1+json",
            "application/vnd.tianshu.evolution.assignment-set.v1+json",
        }
        for artifact in attributed.snapshot.artifacts
    )


@pytest.mark.parametrize("with_subject_shadow", [False, True])
def test_evidence_keeps_the_legacy_assignment_artifact_for_zero_or_one_new_row(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    with_subject_shadow: bool,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment, overlay = _governed_assignment(memorial.id)
    subject = _subject_assignment(assignment, overlay)
    assignment_set = _assignment_set(memorial.id, (subject,)) if with_subject_shadow else None
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment",
        lambda _repository, _connection, _memorial_id: (assignment, overlay),
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment_set",
        lambda _repository, _connection, _memorial_id: assignment_set,
    )

    opened = service.build_open(memorial.id)

    assignment_artifacts = tuple(
        artifact
        for artifact in opened.snapshot.artifacts
        if artifact.media_type == "application/vnd.tianshu.evolution.assignment.v1+json"
    )
    assert len(assignment_artifacts) == 1
    assert not any(
        artifact.media_type == "application/vnd.tianshu.evolution.assignment-set.v1+json"
        for artifact in opened.snapshot.artifacts
    )
    expected = EvolutionRunEvidenceV1(
        assignment=assignment,
        overlay=overlay,
        candidate_id=assignment.candidate_id,
        routing_version=assignment.routing_version,
    )
    assert assignment_artifacts[0].digest == canonical_sha256(expected)
    assert service._artifacts.get_bytes(  # noqa: SLF001
        assignment_artifacts[0].digest
    ) == canonical_json_bytes(expected)
    assert assignment_artifacts[0].digest in opened.snapshot.requirements.artifact_digests


def test_evidence_adds_only_the_assignment_set_artifact_for_multiple_new_rows(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment, overlay = _governed_assignment(memorial.id)
    assignment_set = _multi_assignment_set(assignment, overlay)
    legacy = LegacyRunAssignmentV1(
        assignment_id=f"legacy:{memorial.id}",
        memorial_id=memorial.id,
        created_at=NOW,
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment",
        lambda _repository, _connection, _memorial_id: (legacy, None),
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment_set",
        lambda _repository, _connection, _memorial_id: assignment_set,
    )

    opened = service.build_open(memorial.id)

    assert not any(
        artifact.media_type == "application/vnd.tianshu.evolution.assignment.v1+json"
        for artifact in opened.snapshot.artifacts
    )
    assignment_set_artifacts = tuple(
        artifact
        for artifact in opened.snapshot.artifacts
        if artifact.media_type == "application/vnd.tianshu.evolution.assignment-set.v1+json"
    )
    assert len(assignment_set_artifacts) == 1
    assert assignment_set_artifacts[0].digest == canonical_sha256(assignment_set)
    assert service._artifacts.get_bytes(  # noqa: SLF001
        assignment_set_artifacts[0].digest
    ) == canonical_json_bytes(assignment_set)
    assert assignment_set_artifacts[0].digest in opened.snapshot.requirements.artifact_digests


def test_evidence_rejects_a_single_subject_shadow_mismatch(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment, overlay = _governed_assignment(memorial.id)
    subject = _subject_assignment(assignment, overlay).model_copy(update={"bucket": 43})
    assignment_set = _assignment_set(memorial.id, (subject,))
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment",
        lambda _repository, _connection, _memorial_id: (assignment, overlay),
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment_set",
        lambda _repository, _connection, _memorial_id: assignment_set,
    )

    with pytest.raises(EvidenceServiceError, match="single-subject.*shadow"):
        service.build_open(memorial.id)


def test_evidence_rejects_late_rows_in_a_multi_subject_shadow(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment, overlay = _governed_assignment(memorial.id)
    assignment_set = _multi_assignment_set(
        assignment,
        overlay,
        second_created_at=NOW + timedelta(seconds=1),
    )
    legacy = LegacyRunAssignmentV1(
        assignment_id=f"legacy:{memorial.id}",
        memorial_id=memorial.id,
        created_at=NOW,
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment",
        lambda _repository, _connection, _memorial_id: (legacy, None),
    )
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment_set",
        lambda _repository, _connection, _memorial_id: assignment_set,
    )

    with pytest.raises(EvidenceServiceError, match="multi-subject.*shadow"):
        service.build_open(memorial.id)


def test_evidence_does_not_fall_back_when_a_subject_assignment_row_is_corrupt(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    assignment, overlay = _governed_assignment(memorial.id)
    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment",
        lambda _repository, _connection, _memorial_id: (assignment, overlay),
    )

    def _raise_decode_error(
        _repository: EvolutionRepository,
        _connection: sqlite3.Connection,
        _memorial_id: str,
    ) -> RunAssignmentSetV1 | None:
        raise EvolutionRepositoryDecodeError("corrupt subject assignment")

    monkeypatch.setattr(
        EvolutionRepository,
        "get_assignment_set",
        _raise_decode_error,
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match="corrupt"):
        service.build_open(memorial.id)
