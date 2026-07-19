"""Task-local immutable evolution overlay binding for resource resolvers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict

from tianshu.models.canonical import JsonValue
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1, RunAssignmentV1


class EvolutionRuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    assignment: RunAssignmentV1
    overlay: EffectiveEvolutionOverlayV1
    selected_payload: dict[str, JsonValue]


_CURRENT: ContextVar[EvolutionRuntimeContext | None] = ContextVar(
    "tianshu_evolution_runtime",
    default=None,
)


def current_evolution_runtime() -> EvolutionRuntimeContext | None:
    return _CURRENT.get()


@contextmanager
def bind_evolution_runtime(context: EvolutionRuntimeContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


__all__ = [
    "EvolutionRuntimeContext",
    "bind_evolution_runtime",
    "current_evolution_runtime",
]
