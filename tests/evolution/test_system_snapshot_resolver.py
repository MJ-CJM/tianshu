from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateVersionRefV1
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
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
    governed_snapshot = resolver.resolve_for_run(governed, overlay)
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
