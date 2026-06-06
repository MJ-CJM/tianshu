"""Skill quality metrics — SQLite-backed usage tracking and health scoring."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class SkillMetrics:
    skill_name: str
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    created_at: str | None = None
    created_by: str = "manual"
    source_edict_id: str | None = None
    # Curator lifecycle (修撰)
    state: str = "active"  # active | stale | archived
    pinned: bool = False
    archived_at: str | None = None
    absorbed_into: str | None = None
    # Human-in-the-loop (前景主导)
    human_curated: bool = False
    last_human_action: str | None = None

    @property
    def success_rate(self) -> float | None:
        """Return success rate, or None if insufficient data (< 3 uses)."""
        if self.usage_count < 3:
            return None
        return self.success_count / self.usage_count

    @property
    def status(self) -> str:
        """Return health status: healthy / warning / retire_suggested."""
        rate = self.success_rate
        if rate is None:
            return "healthy"
        if rate < 0.3 and self.usage_count >= 5:
            return "retire_suggested"
        if rate < 0.6:
            return "warning"
        return "healthy"

    def is_dormant(self, dormant_days: int = 90) -> bool:
        """Check if skill hasn't been used in dormant_days."""
        if not self.last_used_at:
            return False
        try:
            last = datetime.fromisoformat(self.last_used_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            delta = datetime.now(UTC) - last
            return delta.days > dormant_days
        except (ValueError, TypeError):
            return False


class SkillMetricsStore:
    """SQLite-backed CRUD for skill_metrics table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, skill_name: str) -> SkillMetrics | None:
        row = self._conn.execute(
            "SELECT * FROM skill_metrics WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_metrics(row)

    def get_all(self) -> list[SkillMetrics]:
        rows = self._conn.execute("SELECT * FROM skill_metrics").fetchall()
        return [self._row_to_metrics(r) for r in rows]

    def ensure_exists(
        self, skill_name: str, created_by: str = "manual", source_edict_id: str | None = None,
    ) -> None:
        """Create metrics row if it doesn't exist."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO skill_metrics (skill_name, created_at, created_by, source_edict_id)
               VALUES (?, ?, ?, ?)""",
            (skill_name, now, created_by, source_edict_id),
        )
        self._conn.commit()

    def increment_usage(self, skill_name: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE skill_metrics
               SET usage_count = usage_count + 1, last_used_at = ?
               WHERE skill_name = ?""",
            (now, skill_name),
        )
        self._conn.commit()

    def increment_success(self, skill_name: str) -> None:
        self._conn.execute(
            "UPDATE skill_metrics SET success_count = success_count + 1 WHERE skill_name = ?",
            (skill_name,),
        )
        self._conn.commit()

    def increment_failure(self, skill_name: str) -> None:
        self._conn.execute(
            "UPDATE skill_metrics SET failure_count = failure_count + 1 WHERE skill_name = ?",
            (skill_name,),
        )
        self._conn.commit()

    def delete(self, skill_name: str) -> None:
        self._conn.execute("DELETE FROM skill_metrics WHERE skill_name = ?", (skill_name,))
        self._conn.commit()

    # --- Curator lifecycle (修撰) ---

    def set_state(self, skill_name: str, state: str) -> None:
        """Set lifecycle state: active | stale | archived."""
        self._conn.execute(
            "UPDATE skill_metrics SET state = ? WHERE skill_name = ?",
            (state, skill_name),
        )
        self._conn.commit()

    def set_pinned(self, skill_name: str, pinned: bool) -> None:
        """Pin/unpin a skill to exempt it from curator transitions."""
        self._conn.execute(
            "UPDATE skill_metrics SET pinned = ? WHERE skill_name = ?",
            (1 if pinned else 0, skill_name),
        )
        self._conn.commit()

    def set_human_curated(self, skill_name: str, curated: bool = True) -> None:
        """Mark a skill as human-edited (golden); curator's auto-iterate skips it."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE skill_metrics SET human_curated = ?, last_human_action = ? WHERE skill_name = ?",
            (1 if curated else 0, now, skill_name),
        )
        self._conn.commit()

    def touch_human_action(self, skill_name: str) -> None:
        """Record a human action timestamp (archive/pin) without changing curated flag."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE skill_metrics SET last_human_action = ? WHERE skill_name = ?",
            (now, skill_name),
        )
        self._conn.commit()

    def mark_archived(self, skill_name: str, into: str | None = None) -> None:
        """Mark a skill archived; `into` records the umbrella it was absorbed into."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE skill_metrics
               SET state = 'archived', archived_at = ?, absorbed_into = ?
               WHERE skill_name = ?""",
            (now, into, skill_name),
        )
        self._conn.commit()

    def list_agent_created(self) -> list[SkillMetrics]:
        """Return metrics for agent-authored skills only (curator scope)."""
        rows = self._conn.execute(
            "SELECT * FROM skill_metrics WHERE created_by = 'agent'"
        ).fetchall()
        return [self._row_to_metrics(r) for r in rows]

    def list_iteration_candidates(
        self, min_success_rate: float, min_usage: int,
    ) -> list[SkillMetrics]:
        """Agent skills eligible for auto-iteration:
        active, not pinned, not human-curated, enough usage, low success rate.
        """
        out: list[SkillMetrics] = []
        for m in self.list_agent_created():
            if m.state != "active" or m.pinned or m.human_curated:
                continue
            if m.usage_count < min_usage:
                continue
            rate = m.success_rate
            if rate is None or rate >= min_success_rate:
                continue
            out.append(m)
        return out

    def list_for_persona(self, persona_id: str) -> list[SkillMetrics]:
        """Return skill metrics filtered by persona's skills_allowed.

        Stub impl: returns all skill metrics (caller can filter further).
        Note: project convention uses `get_all()` here (see above) rather than
        `list_all`; this method wraps it so growth-profile synthesis has a
        persona-scoped entry point without changing existing callers.
        """
        return self.get_all()

    @staticmethod
    def _row_to_metrics(row: sqlite3.Row) -> SkillMetrics:
        keys = row.keys()

        def col(name: str, default: object) -> object:
            return row[name] if name in keys else default

        return SkillMetrics(
            skill_name=row["skill_name"],
            usage_count=row["usage_count"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            source_edict_id=row["source_edict_id"],
            state=col("state", "active"),
            pinned=bool(col("pinned", 0)),
            archived_at=col("archived_at", None),
            absorbed_into=col("absorbed_into", None),
            human_curated=bool(col("human_curated", 0)),
            last_human_action=col("last_human_action", None),
        )
