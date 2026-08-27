"""Task-local immutable runtime binding shared across architectural layers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)
from tianshu.models.system_snapshot import SystemSnapshotV1


class _FrozenDict[K, V](dict[K, V]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("EvolutionRuntimeContext payloads are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable  # type: ignore[assignment]
    clear = _immutable
    pop = _immutable  # type: ignore[assignment]
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable  # type: ignore[assignment]
    update = _immutable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


class _FrozenList(list[JsonValue]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("EvolutionRuntimeContext payloads are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable  # type: ignore[assignment]
    __imul__ = _immutable  # type: ignore[assignment]
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return _FrozenList([_freeze_json(nested) for nested in value])
    return value


def _require_payload_digest(
    payload: dict[str, JsonValue],
    overlay: EffectiveEvolutionOverlayV1,
) -> None:
    if canonical_sha256(payload) != overlay.canonical_digest:
        raise ValueError("runtime payload digest does not match its effective overlay")


class EvolutionRuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    assignment: RunAssignmentV1 | None
    overlay: EffectiveEvolutionOverlayV1 | None
    selected_payload: dict[str, JsonValue] | None
    assignments: tuple[SubjectRunAssignmentV1, ...] = ()
    overlays: Mapping[str, EffectiveEvolutionOverlayV1] = Field(default_factory=dict)
    payloads: Mapping[str, dict[str, JsonValue]] = Field(default_factory=dict)
    system_snapshot: SystemSnapshotV1 | None = None

    @model_validator(mode="after")
    def validate_subject_views(self) -> EvolutionRuntimeContext:
        expected_keys = {
            runtime_subject_key(assignment.kind, assignment.subject_key)
            for assignment in self.assignments
        }
        if len(expected_keys) != len(self.assignments):
            raise ValueError("runtime subject assignments must be unique")
        if set(self.overlays) != set(self.payloads):
            raise ValueError("runtime overlay and payload subjects must match")
        if self.assignments:
            if set(self.overlays) != expected_keys:
                raise ValueError("runtime subject assignments and overlays must match")
            for assignment in self.assignments:
                key = runtime_subject_key(assignment.kind, assignment.subject_key)
                overlay = self.overlays[key]
                if (
                    overlay.assignment_id != assignment.assignment_id
                    or overlay.kind is not assignment.kind
                    or overlay.subject_key != assignment.subject_key
                    or overlay.artifact_digest != assignment.selected_ref.artifact_digest
                    or overlay.canonical_digest != assignment.selected_ref.canonical_digest
                ):
                    raise ValueError("runtime subject overlay attribution conflicts")
                _require_payload_digest(self.payloads[key], overlay)
            if len(self.assignments) == 1:
                if self.assignment is None or self.overlay is None or self.selected_payload is None:
                    raise ValueError("single-subject runtime requires compatibility accessors")
                subject = self.assignments[0]
                only_key = runtime_subject_key(subject.kind, subject.subject_key)
                if (
                    self.assignment.memorial_id != subject.memorial_id
                    or self.assignment.candidate_id != subject.candidate_id
                    or self.assignment.champion_ref != subject.champion_ref
                    or self.assignment.selected_ref != subject.selected_ref
                    or self.assignment.routing_version != subject.routing_version
                    or self.assignment.bucket != subject.bucket
                    or self.assignment.created_at != subject.created_at
                    or self.overlay.assignment_id != self.assignment.assignment_id
                    or self.overlay.kind is not subject.kind
                    or self.overlay.subject_key != subject.subject_key
                    or self.overlay.artifact_digest != subject.selected_ref.artifact_digest
                    or self.overlay.canonical_digest != subject.selected_ref.canonical_digest
                    or self.payloads[only_key] != self.selected_payload
                ):
                    raise ValueError("single-subject compatibility accessors must match")
                _require_payload_digest(self.selected_payload, self.overlay)
            elif any(
                value is not None
                for value in (self.assignment, self.overlay, self.selected_payload)
            ):
                raise ValueError("multi-subject runtime has no singular authority")
        else:
            if self.overlays or self.payloads:
                raise ValueError("runtime overlays require durable subject assignments")
            if self.assignment is None or self.overlay is None or self.selected_payload is None:
                raise ValueError("compatibility runtime requires singular attribution")
            if (
                self.overlay.assignment_id != self.assignment.assignment_id
                or self.overlay.kind is None
                or self.overlay.subject_key is None
                or self.overlay.artifact_digest != self.assignment.selected_ref.artifact_digest
                or self.overlay.canonical_digest != self.assignment.selected_ref.canonical_digest
            ):
                raise ValueError("compatibility runtime attribution conflicts")
            _require_payload_digest(self.selected_payload, self.overlay)

        frozen_payloads: _FrozenDict[str, dict[str, JsonValue]] = _FrozenDict(
            {
                key: cast(dict[str, JsonValue], _freeze_json(payload))
                for key, payload in sorted(self.payloads.items())
            }
        )
        object.__setattr__(self, "overlays", _FrozenDict(dict(sorted(self.overlays.items()))))
        object.__setattr__(self, "payloads", frozen_payloads)
        if self.assignments and len(self.assignments) == 1:
            only_key = runtime_subject_key(
                self.assignments[0].kind,
                self.assignments[0].subject_key,
            )
            object.__setattr__(self, "selected_payload", frozen_payloads[only_key])
        elif not self.assignments:
            object.__setattr__(
                self,
                "selected_payload",
                cast(dict[str, JsonValue], _freeze_json(self.selected_payload)),
            )
        return self


def runtime_subject_key(kind: CandidateKind, subject_key: str) -> str:
    """Return the collision-free process-local identity for one governed subject."""

    if not subject_key.strip():
        raise ValueError("runtime subject identity must be non-blank")
    return f"{kind.value}:{subject_key}"


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
def suspend_evolution_runtime() -> Iterator[None]:
    """Temporarily clear any outer governed overlay for a legacy run."""

    token = _CURRENT.set(None)
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
    "runtime_subject_key",
    "suspend_evolution_runtime",
]
