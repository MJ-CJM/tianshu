"""Shared task-ownership authorization policy."""

from __future__ import annotations

from tianshu.models.principal import AuthContext


def has_global_task_access(context: AuthContext) -> bool:
    """Admins may inspect and control tasks across principals."""

    return "admin" in context.principal.scopes


def can_access_submitter(context: AuthContext, submitter: str | None) -> bool:
    """Return whether ``context`` may access a task owned by ``submitter``.

    Historical rows without a submitter stay available to an administrator
    (including the trusted-local owner) but fail closed for ordinary tokens.
    """

    return has_global_task_access(context) or (
        submitter is not None and submitter == context.principal.id
    )


def scoped_submitter(context: AuthContext) -> str | None:
    """Return the storage filter for a principal, or no filter for admins."""

    return None if has_global_task_access(context) else context.principal.id


__all__ = [
    "can_access_submitter",
    "has_global_task_access",
    "scoped_submitter",
]
