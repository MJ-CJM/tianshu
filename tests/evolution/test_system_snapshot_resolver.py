from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateVersionRefV1
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)


def _resolver(source: dict[str, str]) -> SystemSnapshotResolver:
    return SystemSnapshotResolver(
        kernel_facts=lambda: {"version": source["kernel"]},
        executor_digests=lambda: {"z": "6" * 64, "a": "5" * 64},
        skills_digest=lambda: source["skills"],
        personas_digest=lambda: source["personas"],
        policy_rules_digest=lambda: source["policy_rules"],
        provider_profiles_digest=lambda: source["provider_profiles"],
    )


def _assignments() -> tuple[
    RunAssignmentV1,
    LegacyRunAssignmentV1,
    EffectiveEvolutionOverlayV1,
]:
    selected = CandidateVersionRefV1(
        version="2",
        artifact_digest="b" * 64,
        canonical_digest="c" * 64,
    )
    governed = RunAssignmentV1(
        assignment_id="assignment-1",
        memorial_id="memorial-1",
        candidate_id="candidate-1",
        champion_ref=CandidateVersionRefV1(
            version="1",
            artifact_digest="8" * 64,
            canonical_digest="9" * 64,
        ),
        selected_ref=selected,
        routing_version=1,
        bucket=42,
        created_at=datetime.now(UTC),
    )
    legacy = LegacyRunAssignmentV1(
        assignment_id="legacy-1",
        memorial_id="memorial-2",
        created_at=datetime.now(UTC),
    )
    overlay = EffectiveEvolutionOverlayV1(
        assignment_id=governed.assignment_id,
        kind=CandidateKind.SKILL,
        subject_key="skill:research",
        artifact_digest=selected.artifact_digest,
        canonical_digest=selected.canonical_digest,
    )
    return governed, legacy, overlay


def _subject_assignment(
    assignment: RunAssignmentV1,
    overlay: EffectiveEvolutionOverlayV1,
    *,
    assignment_id: str = "subject-assignment-1",
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


def test_resolver_recomputes_sources_and_only_changed_component_moves() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)

    first = resolver.resolve()
    second = resolver.resolve()
    assert first == second
    assert list(first.components) == [
        "kernel",
        "skills",
        "personas",
        "policy_rules",
        "provider_profiles",
        "executor:a",
        "executor:z",
    ]

    source["skills"] = "7" * 64
    changed = resolver.resolve()
    moved = {key for key, value in first.components.items() if changed.components[key] != value}
    assert moved == {"skills"}
    assert changed.digest != first.digest


def test_resolver_adds_only_verified_governed_overlay() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    governed, legacy, overlay = _assignments()

    legacy_snapshot = resolver.resolve_for_run(legacy, None)
    legacy_empty_subject_snapshot = resolver.resolve_for_run(
        legacy,
        None,
        subject_overlays={},
    )
    governed_snapshot = resolver.resolve_for_run(governed, overlay)
    assert canonical_json_bytes(legacy_empty_subject_snapshot) == canonical_json_bytes(
        legacy_snapshot
    )
    assert "evolution_overlay" not in legacy_snapshot.components
    assert governed_snapshot.components["evolution_overlay"] == canonical_sha256(overlay)

    with pytest.raises(ValueError, match="legacy"):
        resolver.resolve_for_run(legacy, overlay)
    with pytest.raises(ValueError, match="requires"):
        resolver.resolve_for_run(governed, None)
    with pytest.raises(ValueError, match="does not match"):
        resolver.resolve_for_run(
            governed,
            overlay.model_copy(update={"assignment_id": "assignment-other"}),
        )


def test_run_snapshot_uses_the_generation_pinned_executor_manifest_snapshot() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    _governed, legacy, _overlay = _assignments()

    snapshot = resolver.resolve_for_run(
        legacy,
        None,
        executor_digests={"a": "7" * 64, "pi": "8" * 64},
    )

    assert snapshot.components["executor:a"] == "7" * 64
    assert snapshot.components["executor:pi"] == "8" * 64
    assert "executor:z" not in snapshot.components


def test_single_subject_shadow_keeps_the_legacy_snapshot_byte_identical() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    governed, _legacy, overlay = _assignments()
    subject = _subject_assignment(governed, overlay)
    assignment_set = _assignment_set(governed.memorial_id, (subject,))

    old_snapshot = resolver.resolve_for_run(governed, overlay)
    shadow_snapshot = resolver.resolve_for_run(
        governed,
        overlay,
        assignment_set=assignment_set,
        subject_overlays={f"{subject.kind.value}:{subject.subject_key}": overlay},
    )

    assert canonical_json_bytes(shadow_snapshot) == canonical_json_bytes(old_snapshot)
    assert "evolution_overlay_set" not in shadow_snapshot.components


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", "candidate-other"),
        ("bucket", 43),
        ("created_at", datetime(2026, 8, 26, tzinfo=UTC)),
    ],
)
def test_single_subject_shadow_mismatch_fails_closed(field: str, value: object) -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    governed, _legacy, overlay = _assignments()
    subject = _subject_assignment(governed, overlay).model_copy(update={field: value})
    assignment_set = _assignment_set(governed.memorial_id, (subject,))

    with pytest.raises(ValueError, match="shadow"):
        resolver.resolve_for_run(governed, overlay, assignment_set=assignment_set)


def test_multi_subject_snapshot_hashes_all_overlays_in_subject_order() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    _governed, legacy, _overlay = _assignments()
    created_at = legacy.created_at
    persona = SubjectRunAssignmentV1(
        assignment_id="subject-assignment-persona",
        memorial_id=legacy.memorial_id,
        kind=CandidateKind.PERSONA,
        subject_key="persona:reviewer",
        candidate_id="candidate-persona",
        champion_ref=CandidateVersionRefV1(
            version="1",
            artifact_digest="a" * 64,
            canonical_digest="b" * 64,
        ),
        selected_ref=CandidateVersionRefV1(
            version="2",
            artifact_digest="c" * 64,
            canonical_digest="d" * 64,
        ),
        routing_version=2,
        bucket=12,
        created_at=created_at,
    )
    skill = SubjectRunAssignmentV1(
        assignment_id="subject-assignment-skill",
        memorial_id=legacy.memorial_id,
        kind=CandidateKind.SKILL,
        subject_key="skill:research",
        candidate_id="candidate-skill",
        champion_ref=CandidateVersionRefV1(
            version="1",
            artifact_digest="1" * 64,
            canonical_digest="2" * 64,
        ),
        selected_ref=CandidateVersionRefV1(
            version="2",
            artifact_digest="3" * 64,
            canonical_digest="4" * 64,
        ),
        routing_version=3,
        bucket=34,
        created_at=created_at,
    )
    assignments = tuple(
        sorted((skill, persona), key=lambda item: (item.kind.value, item.subject_key))
    )
    assignment_set = _assignment_set(legacy.memorial_id, assignments)
    expected_overlays = [
        EffectiveEvolutionOverlayV1(
            assignment_id=item.assignment_id,
            kind=item.kind,
            subject_key=item.subject_key,
            artifact_digest=item.selected_ref.artifact_digest,
            canonical_digest=item.selected_ref.canonical_digest,
        ).model_dump(mode="json", exclude_none=False)
        for item in assignments
    ]
    subject_overlays = {
        f"{item.kind.value}:{item.subject_key}": EffectiveEvolutionOverlayV1(
            assignment_id=item.assignment_id,
            kind=item.kind,
            subject_key=item.subject_key,
            artifact_digest=item.selected_ref.artifact_digest,
            canonical_digest=item.selected_ref.canonical_digest,
        )
        for item in assignments
    }

    snapshot = resolver.resolve_for_run(
        legacy,
        None,
        assignment_set=assignment_set,
        subject_overlays=subject_overlays,
    )

    assert "evolution_overlay" not in snapshot.components
    assert snapshot.components["evolution_overlay_set"] == canonical_sha256(
        {"overlays": expected_overlays}
    )
    reversed_overlay_map = dict(reversed(tuple(subject_overlays.items())))
    reordered_snapshot = resolver.resolve_for_run(
        legacy,
        None,
        assignment_set=assignment_set,
        subject_overlays=reversed_overlay_map,
    )
    assert canonical_json_bytes(reordered_snapshot) == canonical_json_bytes(snapshot)
    with pytest.raises(ValueError, match="requires subject overlays"):
        resolver.resolve_for_run(legacy, None, assignment_set=assignment_set)
    corrupted_overlays = dict(subject_overlays)
    corrupted_key = next(iter(corrupted_overlays))
    corrupted_overlays[corrupted_key] = corrupted_overlays[corrupted_key].model_copy(
        update={"assignment_id": "subject-assignment-corrupt"}
    )
    with pytest.raises(ValueError, match="overlays conflict"):
        resolver.resolve_for_run(
            legacy,
            None,
            assignment_set=assignment_set,
            subject_overlays=corrupted_overlays,
        )
    late_assignments = (
        assignments[0],
        assignments[1].model_copy(update={"created_at": created_at + timedelta(seconds=1)}),
    )
    late_assignment_set = _assignment_set(legacy.memorial_id, late_assignments)
    with pytest.raises(ValueError, match="legacy assignment shadow"):
        resolver.resolve_for_run(
            legacy,
            None,
            assignment_set=late_assignment_set,
            subject_overlays=subject_overlays,
        )


def test_multi_subject_assignment_set_rejects_a_governed_legacy_shadow() -> None:
    source = {
        "kernel": "v1",
        "skills": "1" * 64,
        "personas": "2" * 64,
        "policy_rules": "3" * 64,
        "provider_profiles": "4" * 64,
    }
    resolver = _resolver(source)
    governed, _legacy, overlay = _assignments()
    first = _subject_assignment(governed, overlay)
    second = first.model_copy(
        update={
            "assignment_id": "subject-assignment-2",
            "kind": CandidateKind.PERSONA,
            "subject_key": "persona:reviewer",
        }
    )
    assignments = tuple(
        sorted((first, second), key=lambda item: (item.kind.value, item.subject_key))
    )
    assignment_set = _assignment_set(governed.memorial_id, assignments)

    with pytest.raises(ValueError, match="multi-subject.*shadow"):
        resolver.resolve_for_run(governed, overlay, assignment_set=assignment_set)
