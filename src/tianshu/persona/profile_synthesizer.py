"""ProfileSynthesizer — per-persona growth profile synthesis.

Pipeline (see spec §4.3):
  1. Collect  — DrawerStore + Storage events + SkillMetrics + previous PROFILE
  2. Rule agg — 任务分布 / 健康度 / 退化候选(无 LLM)
  3. LLM      — 擅长领域 + 退化原因(两次独立调用,可并发)
  4. Persist  — atomic write + archive + prune
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tianshu.memory.drawer import Drawer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProfileSynthesisInput:
    persona_id: str
    persona_name: str
    data_window_days: int
    drawers: tuple[Drawer, ...]
    recent_events: tuple[dict[str, Any], ...]
    skill_metrics: tuple[dict[str, Any], ...]
    previous_profile_md: str | None


@dataclass(frozen=True)
class ProfileSynthesisResult:
    persona_id: str
    markdown: str
    auto_section: str
    manual_section: str
    version: int
    data_sources: dict[str, int]
    degraded: bool


class ProfileSynthesizer:
    def __init__(
        self,
        llm_client: Any,
        drawer_store: Any,
        storage: Any,
        skill_metrics_store: Any,
        personas_runtime_dir: Path,
        persona_loader: Any,
        model_name: str = "claude-sonnet-4-6",
    ) -> None:
        self._llm = llm_client
        self._drawers = drawer_store
        self._storage = storage
        self._skill_metrics = skill_metrics_store
        self._runtime_dir = Path(personas_runtime_dir).expanduser()
        self._personas = persona_loader
        self._model = model_name

    def _profile_path(self, persona_id: str) -> Path:
        return self._runtime_dir / persona_id / "PROFILE.md"

    def collect_inputs(
        self, persona_id: str, window_days: int = 14
    ) -> ProfileSynthesisInput:
        persona = self._personas.get(persona_id)
        persona_name = persona.name if persona else persona_id
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        since_iso = since.isoformat()

        drawers = tuple(
            self._drawers.search(wing=persona_id, since_iso=since_iso, limit=200)
            if hasattr(self._drawers, "search")
            else []
        )
        events = tuple(self._storage.list_persona_events(persona_id, since_iso))
        metrics = tuple(self._skill_metrics.list_for_persona(persona_id))

        prev_path = self._profile_path(persona_id)
        prev_md = prev_path.read_text(encoding="utf-8") if prev_path.exists() else None

        return ProfileSynthesisInput(
            persona_id=persona_id,
            persona_name=persona_name,
            data_window_days=window_days,
            drawers=drawers,
            recent_events=events,
            skill_metrics=metrics,
            previous_profile_md=prev_md,
        )
