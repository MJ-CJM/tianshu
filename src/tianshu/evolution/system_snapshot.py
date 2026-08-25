"""Resolve the effective system content identity for a run."""

from __future__ import annotations

from collections.abc import Callable

from tianshu.models.canonical import canonical_sha256
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
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

    def resolve_base(self) -> dict[str, str]:
        components = {
            "kernel": canonical_sha256(dict(self._kernel_facts())),
            "skills": self._skills_digest(),
            "personas": self._personas_digest(),
            "policy_rules": self._policy_rules_digest(),
            "provider_profiles": self._provider_profiles_digest(),
        }
        for adapter_id, digest in sorted(self._executor_digests().items()):
            components[f"executor:{adapter_id}"] = digest
        return components

    def resolve(self) -> SystemSnapshotV1:
        return self._snapshot(self.resolve_base())

    def resolve_for_run(
        self,
        assignment: RunAssignmentV1 | LegacyRunAssignmentV1,
        overlay: EffectiveEvolutionOverlayV1 | None,
    ) -> SystemSnapshotV1:
        components = self.resolve_base()
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
    def _snapshot(components: dict[str, str]) -> SystemSnapshotV1:
        copied = dict(components)
        return SystemSnapshotV1(
            components=copied,
            digest=canonical_sha256(copied),
        )


__all__ = ["SystemSnapshotResolver"]
