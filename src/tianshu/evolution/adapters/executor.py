"""Executor candidate source validation."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1
from tianshu.models.runtime_generation import RuntimeReleaseV1

_SUPPORTED_EXECUTOR_ADAPTERS = frozenset({"keqing:pi"})


class ExecutorCandidateAdapter(BaseCandidateAdapter):
    """Validate a complete content-addressed executor release."""

    kind = CandidateKind.EXECUTOR

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            release = RuntimeReleaseV1.model_validate(payload)
            adapter_id = release.manifest.get("adapter_id")
            if not isinstance(adapter_id, str) or adapter_id not in _SUPPORTED_EXECUTOR_ADAPTERS:
                raise ValueError("unsupported executor adapter")
            if release.scope != f"executor:{adapter_id}":
                raise ValueError("executor release scope does not match its adapter")
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("executor source validation failed") from None
        return release.model_dump(mode="json")

    def require_subject_binding(
        self,
        normalized_payload: Mapping[str, JsonValue],
        *,
        overlay: EffectiveEvolutionOverlayV1,
    ) -> None:
        manifest = normalized_payload.get("manifest")
        adapter_id = manifest.get("adapter_id") if isinstance(manifest, dict) else None
        if not isinstance(adapter_id, str) or overlay.subject_key != f"executor:{adapter_id}":
            raise AdapterError("executor overlay subject does not match release adapter")


__all__ = ["ExecutorCandidateAdapter"]
