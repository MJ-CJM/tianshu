"""Resolve one durable correlation identity across the S3 core tables."""

from __future__ import annotations

import hashlib
import re
import sqlite3

_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


def require_correlation_id(value: str) -> str:
    if not isinstance(value, str) or _CORRELATION_ID_RE.fullmatch(value) is None:
        raise ValueError("correlation_id must be an opaque identifier")
    return value


def correlation_for_memorial(
    connection: sqlite3.Connection,
    memorial_id: str,
    *,
    explicit: str | None = None,
) -> str:
    """Return the root ingress correlation, or an explicit deterministic legacy identity."""
    row = connection.execute(
        """
        SELECT correlation_id
        FROM outbox_events
        WHERE memorial_id=? AND correlation_id IS NOT NULL
        ORDER BY occurred_at, event_id
        LIMIT 1
        """,
        (memorial_id,),
    ).fetchone()
    if row is not None:
        return require_correlation_id(str(row[0]))
    if explicit is not None:
        return require_correlation_id(explicit)
    for table in (
        "decision_requests",
        "run_states",
        "execution_attempts",
        "side_effect_journal",
        "evidence_bundles",
    ):
        row = connection.execute(
            f"SELECT correlation_id FROM {table} "
            "WHERE memorial_id=? AND correlation_id IS NOT NULL LIMIT 1",
            (memorial_id,),
        ).fetchone()
        if row is not None:
            return require_correlation_id(str(row[0]))
    digest = hashlib.sha256(memorial_id.encode("utf-8")).hexdigest()
    return f"legacy:{digest}"


__all__ = ["correlation_for_memorial", "require_correlation_id"]
