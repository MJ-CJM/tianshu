"""Skill curator lifecycle — pure (no-LLM) state transitions.

Drives agent-authored skills through active → stale → archived based on
inactivity, mirroring hermes's automatic transitions. Pinned skills are exempt;
builtin / manually-authored skills are out of scope (only ``created_by=='agent'``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _age_days(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def apply_automatic_transitions(
    metrics_store: Any,
    loader: Any,
    *,
    stale_after_days: int = 30,
    archive_after_days: int = 90,
) -> dict[str, int]:
    """Run pure lifecycle transitions over agent-authored skills.

    Anchor = ``last_used_at``, falling back to ``created_at``. For each
    non-pinned, non-archived agent skill:
      - age > archive_after_days            → archive (move file + mark archived)
      - age > stale_after_days              → state = stale
      - age <= stale_after_days and stale   → reactivate (state = active)

    Returns counts: ``{marked_stale, archived, reactivated, checked}``.
    """
    counts = {"marked_stale": 0, "archived": 0, "reactivated": 0, "checked": 0}
    for m in metrics_store.list_agent_created():
        if m.pinned or m.state == "archived":
            continue
        counts["checked"] += 1
        age = _age_days(m.last_used_at or m.created_at)
        if age is None:
            continue
        if age > archive_after_days:
            if loader.archive_skill(m.skill_name):
                metrics_store.mark_archived(m.skill_name)
                counts["archived"] += 1
            else:
                # File already gone; still converge metrics state.
                metrics_store.set_state(m.skill_name, "archived")
        elif age > stale_after_days:
            if m.state != "stale":
                metrics_store.set_state(m.skill_name, "stale")
                counts["marked_stale"] += 1
        else:
            if m.state == "stale":
                metrics_store.set_state(m.skill_name, "active")
                counts["reactivated"] += 1
    return counts
