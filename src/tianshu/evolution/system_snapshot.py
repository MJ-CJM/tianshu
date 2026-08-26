"""Resolve the effective system content identity for a run."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from tianshu.models.canonical import canonical_sha256
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)
from tianshu.models.system_snapshot import SystemSnapshotV1


class SystemSnapshotResolver:
    """Collect current source digests without caching the composed snapshot."""

    def __init__(
        self,
        *,
        kernel_facts: Callable[[], dict[str, str]],
        executor_digests: Callable[[], dict[str, str]],
        skills_digest: Callable[[], str],
        personas_digest: Callable[[], str],
        policy_rules_digest: Callable[[], str],
        provider_profiles_digest: Callable[[], str],
    ) -> None:
        self._kernel_facts = kernel_facts
        self._executor_digests = executor_digests
        self._skills_digest = skills_digest
        self._personas_digest = personas_digest
        self._policy_rules_digest = policy_rules_digest
        self._provider_profiles_digest = provider_profiles_digest

    def resolve_base(
        self,
        *,
        executor_digests: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        components = {
            "kernel": canonical_sha256(dict(self._kernel_facts())),
            "skills": self._skills_digest(),
            "personas": self._personas_digest(),
            "policy_rules": self._policy_rules_digest(),
            "provider_profiles": self._provider_profiles_digest(),
        }
        effective_executor_digests = (
            self._executor_digests() if executor_digests is None else executor_digests
        )
        for adapter_id, digest in sorted(effective_executor_digests.items()):
            components[f"executor:{adapter_id}"] = digest
        return components

    def resolve(self) -> SystemSnapshotV1:
        return self._snapshot(self.resolve_base())

    def resolve_for_run(
        self,
        assignment: RunAssignmentV1 | LegacyRunAssignmentV1,
        overlay: EffectiveEvolutionOverlayV1 | None,
        *,
        assignment_set: RunAssignmentSetV1 | None = None,
        subject_overlays: Mapping[str, EffectiveEvolutionOverlayV1] | None = None,
        executor_digests: Mapping[str, str] | None = None,
    ) -> SystemSnapshotV1:
        components = self.resolve_base(executor_digests=executor_digests)
        if assignment_set is None and subject_overlays:
            raise ValueError("subject overlays require an assignment set")
        if assignment_set is not None:
            self._validate_assignment_set_memorial(assignment, assignment_set)
            if len(assignment_set.assignments) == 1:
                self._validate_single_subject_shadow(
                    assignment,
                    overlay,
                    assignment_set.assignments[0],
                    subject_overlays,
                )
            else:
                if (
                    not isinstance(assignment, LegacyRunAssignmentV1)
                    or overlay is not None
                    or any(
                        subject_assignment.created_at != assignment.created_at
                        for subject_assignment in assignment_set.assignments
                    )
                ):
                    raise ValueError(
                        "multi-subject assignment set conflicts with the legacy assignment shadow"
                    )
                overlays = self._validate_multi_subject_overlays(
                    assignment_set,
                    subject_overlays,
                )
                components["evolution_overlay_set"] = canonical_sha256(
                    {
                        "overlays": [
                            item.model_dump(mode="json", exclude_none=False) for item in overlays
                        ]
                    }
                )
                return self._snapshot(components)

        if isinstance(assignment, LegacyRunAssignmentV1):
            if overlay is not None:
                raise ValueError("legacy run assignment cannot have an evolution overlay")
            return self._snapshot(components)

        if overlay is None:
            raise ValueError("governed run assignment requires an evolution overlay")
        if (
            overlay.assignment_id != assignment.assignment_id
            or overlay.artifact_digest != assignment.selected_ref.artifact_digest
            or overlay.canonical_digest != assignment.selected_ref.canonical_digest
        ):
            raise ValueError("evolution overlay does not match the governed assignment")
        components["evolution_overlay"] = canonical_sha256(overlay)
        return self._snapshot(components)

    @staticmethod
    def _validate_assignment_set_memorial(
        assignment: RunAssignmentV1 | LegacyRunAssignmentV1,
        assignment_set: RunAssignmentSetV1,
    ) -> None:
        if assignment_set.memorial_id != assignment.memorial_id:
            raise ValueError("assignment set does not match the legacy assignment shadow")

    @classmethod
    def _validate_single_subject_shadow(
        cls,
        assignment: RunAssignmentV1 | LegacyRunAssignmentV1,
        overlay: EffectiveEvolutionOverlayV1 | None,
        subject_assignment: SubjectRunAssignmentV1,
        subject_overlays: Mapping[str, EffectiveEvolutionOverlayV1] | None,
    ) -> None:
        if not isinstance(assignment, RunAssignmentV1) or overlay is None:
            raise ValueError(
                "single-subject assignment set conflicts with the legacy assignment shadow"
            )
        subject_overlay = cls._overlay_for_subject(subject_assignment)
        if (
            subject_assignment.memorial_id != assignment.memorial_id
            or subject_assignment.candidate_id != assignment.candidate_id
            or subject_assignment.champion_ref != assignment.champion_ref
            or subject_assignment.selected_ref != assignment.selected_ref
            or subject_assignment.routing_version != assignment.routing_version
            or subject_assignment.bucket != assignment.bucket
            or subject_assignment.created_at != assignment.created_at
            or subject_overlay.kind != overlay.kind
            or subject_overlay.subject_key != overlay.subject_key
            or subject_overlay.artifact_digest != overlay.artifact_digest
            or subject_overlay.canonical_digest != overlay.canonical_digest
        ):
            raise ValueError(
                "single-subject assignment set conflicts with the legacy assignment shadow"
            )
        if subject_overlays is not None:
            expected_key = cls._runtime_subject_key(subject_assignment)
            if set(subject_overlays) != {expected_key}:
                raise ValueError("single-subject overlays conflict with the assignment set")
            runtime_overlay = subject_overlays[expected_key]
            if (
                runtime_overlay.assignment_id
                not in {assignment.assignment_id, subject_assignment.assignment_id}
                or runtime_overlay.kind != subject_overlay.kind
                or runtime_overlay.subject_key != subject_overlay.subject_key
                or runtime_overlay.artifact_digest != subject_overlay.artifact_digest
                or runtime_overlay.canonical_digest != subject_overlay.canonical_digest
            ):
                raise ValueError("single-subject overlays conflict with the assignment set")

    @classmethod
    def _validate_multi_subject_overlays(
        cls,
        assignment_set: RunAssignmentSetV1,
        subject_overlays: Mapping[str, EffectiveEvolutionOverlayV1] | None,
    ) -> list[EffectiveEvolutionOverlayV1]:
        if subject_overlays is None:
            raise ValueError("multi-subject assignment set requires subject overlays")
        ordered_assignments = sorted(
            assignment_set.assignments,
            key=lambda item: (item.kind.value, item.subject_key),
        )
        expected_keys = {
            cls._runtime_subject_key(subject_assignment)
            for subject_assignment in ordered_assignments
        }
        if set(subject_overlays) != expected_keys:
            raise ValueError("multi-subject overlays conflict with the assignment set")
        overlays: list[EffectiveEvolutionOverlayV1] = []
        for subject_assignment in ordered_assignments:
            runtime_overlay = subject_overlays[cls._runtime_subject_key(subject_assignment)]
            expected_overlay = cls._overlay_for_subject(subject_assignment)
            if runtime_overlay != expected_overlay:
                raise ValueError("multi-subject overlays conflict with the assignment set")
            overlays.append(runtime_overlay)
        return overlays

    @staticmethod
    def _runtime_subject_key(assignment: SubjectRunAssignmentV1) -> str:
        return f"{assignment.kind.value}:{assignment.subject_key}"

    @staticmethod
    def _overlay_for_subject(
        assignment: SubjectRunAssignmentV1,
    ) -> EffectiveEvolutionOverlayV1:
        return EffectiveEvolutionOverlayV1(
            assignment_id=assignment.assignment_id,
            kind=assignment.kind,
            subject_key=assignment.subject_key,
            artifact_digest=assignment.selected_ref.artifact_digest,
            canonical_digest=assignment.selected_ref.canonical_digest,
        )

    @staticmethod
    def _snapshot(components: dict[str, str]) -> SystemSnapshotV1:
        copied = dict(components)
        return SystemSnapshotV1(
            components=copied,
            digest=canonical_sha256(copied),
        )


__all__ = ["SystemSnapshotResolver"]
