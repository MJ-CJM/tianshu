"""Process-local context for one durably fenced managed attempt."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    """Immutable process-local projection of one durable execution lease."""

    attempt_id: str
    memorial_id: str
    owner_id: str
    fencing_token: int


class ManagedRunSuspended(RuntimeError):
    """Structured unwind after durable suspension; not an execution failure."""


_current_authority: ContextVar[AttemptAuthority | None] = ContextVar(
    "managed_attempt_authority",
    default=None,
)


@contextmanager
def bind_managed_attempt_authority(authority: AttemptAuthority) -> Iterator[None]:
    token: Token[AttemptAuthority | None] = _current_authority.set(authority)
    try:
        yield
    finally:
        _current_authority.reset(token)


def get_managed_attempt_authority() -> AttemptAuthority | None:
    return _current_authority.get()


__all__ = [
    "AttemptAuthority",
    "ManagedRunSuspended",
    "bind_managed_attempt_authority",
    "get_managed_attempt_authority",
]
