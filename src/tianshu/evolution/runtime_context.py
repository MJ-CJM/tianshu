"""Task-local immutable evolution overlay binding for resource resolvers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict

from tianshu.models.canonical import JsonValue
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1, RunAssignmentV1
from tianshu.models.system_snapshot import SystemSnapshotV1


class EvolutionRuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    assignment: RunAssignmentV1
    overlay: EffectiveEvolutionOverlayV1
    selected_payload: dict[str, JsonValue]
    system_snapshot: SystemSnapshotV1 | None = None


class RunBindingContextV1(BaseModel):
    """Task-local binding shared by governed and legacy managed runs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    memorial_id: str
    attempt_id: str
    system_snapshot: SystemSnapshotV1 | None = None
    generation_ids: tuple[str, ...] = ()


_CURRENT: ContextVar[EvolutionRuntimeContext | None] = ContextVar(
    "tianshu_evolution_runtime",
    default=None,
)
_CURRENT_RUN_BINDING: ContextVar[RunBindingContextV1 | None] = ContextVar(
    "tianshu_run_binding",
    default=None,
)


def current_evolution_runtime() -> EvolutionRuntimeContext | None:
    return _CURRENT.get()


def current_run_binding() -> RunBindingContextV1 | None:
    return _CURRENT_RUN_BINDING.get()


@contextmanager
def bind_evolution_runtime(context: EvolutionRuntimeContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def bind_run_binding(context: RunBindingContextV1) -> Iterator[None]:
    token = _CURRENT_RUN_BINDING.set(context)
    try:
        yield
    finally:
        _CURRENT_RUN_BINDING.reset(token)


__all__ = [
    "EvolutionRuntimeContext",
    "RunBindingContextV1",
    "bind_evolution_runtime",
    "bind_run_binding",
    "current_evolution_runtime",
    "current_run_binding",
]
