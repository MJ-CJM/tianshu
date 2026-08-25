"""Owned, identity-safe plugin contribution handles."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, cast

from tianshu.models.system_audit import AppendSystemAuditRequest

logger = logging.getLogger(__name__)

DEFAULT_PLUGIN_OWNER = "plugin:anonymous"

type ContributionKind = Literal["tool", "hook", "channel", "provider", "skill", "command"]


class _SystemAuditSink(Protocol):
    def append_system_audit(self, request: AppendSystemAuditRequest) -> object: ...


class ContributionDisposeStatus(StrEnum):
    """Observable result of one idempotent contribution disposal."""

    DISPOSED = "disposed"
    SKIPPED_STALE = "skipped_stale"
    NOOP = "noop"


@dataclass(frozen=True, slots=True)
class ContributionHandle:
    """One registered capability owned by a trusted source extension."""

    owner: str
    kind: ContributionKind
    name: str
    target: object = field(repr=False, compare=False)
    dispose: Callable[[], ContributionDisposeStatus] = field(repr=False, compare=False)


def record_stale_contribution_dispose(
    storage: object | None,
    *,
    owner: str,
    kind: ContributionKind,
    name: str,
) -> None:
    """Best-effort SystemAudit record for an identity-mismatched disposer."""

    if storage is None or not hasattr(storage, "append_system_audit"):
        return
    actor_digest = hashlib.sha256(owner.encode("utf-8")).hexdigest()
    try:
        cast(_SystemAuditSink, storage).append_system_audit(
            AppendSystemAuditRequest(
                correlation_id=f"contribution:{actor_digest[:32]}",
                actor_digest=actor_digest,
                action="contribution_dispose_stale",
                outcome="failed",
                reason_code="contribution_dispose_stale",
                subject_kind="plugin_contribution",
                subject_digest=hashlib.sha256(f"{kind}:{name}".encode()).hexdigest(),
                metadata={},
            )
        )
    except Exception:
        logger.warning("Failed to persist stale contribution disposal audit")
