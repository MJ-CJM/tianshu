"""ProfileSynthesizer — per-persona growth profile synthesis.

Pipeline (see spec §4.3):
  1. Collect  — DrawerStore + Storage events + SkillMetrics + previous PROFILE
  2. Rule agg — 任务分布 / 健康度 / 退化候选(无 LLM)
  3. LLM      — 擅长领域 + 退化原因(两次独立调用,可并发)
  4. Persist  — atomic write + archive + prune
"""

from __future__ import annotations

import asyncio
import json
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

    class _SkippedError(RuntimeError):
        """Sentinel: synthesis skipped due to lock contention."""
        pass

    def _acquire_lock(self, persona_id: str) -> bool:
        """Try to acquire synthesis lock; returns True if acquired."""
        return self._storage.try_acquire_synthesis_lock(persona_id)

    def _release_lock(self, persona_id: str) -> None:
        """Release synthesis lock for persona."""
        self._storage.release_synthesis_lock(persona_id)

    def _profile_path(self, persona_id: str) -> Path:
        return self._runtime_dir / persona_id / "PROFILE.md"

    async def collect_inputs(
        self, persona_id: str, window_days: int = 14
    ) -> ProfileSynthesisInput:
        """Collect 14-day window data for synthesis.

        Async because DrawerStore.get_drawers is async. Callers (run, API) are
        already async, so this stays natural.
        """
        persona = self._personas.get(persona_id)
        persona_name = persona.name if persona else persona_id
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        since_iso = since.isoformat()

        raw_drawers = (
            await self._drawers.get_drawers(wing=persona_id, limit=200)
            if hasattr(self._drawers, "get_drawers")
            else []
        )
        drawers = tuple(
            d for d in raw_drawers
            if getattr(d, "timestamp", "") >= since_iso
        )
        events = tuple(self._storage.list_persona_events(persona_id, since_iso))
        raw_metrics = self._skill_metrics.list_for_persona(persona_id)
        metrics = tuple(
            ({
                "skill_name": m.skill_name,
                "usage_count": m.usage_count,
                "success_count": m.success_count,
                "failure_count": m.failure_count,
                "status": m.status,
                "last_used_at": m.last_used_at,
            } if hasattr(m, "skill_name") else m)
            for m in raw_metrics
        )

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

    def aggregate_task_distribution(
        self, events: tuple[dict[str, Any], ...], window_days: int
    ) -> dict[str, Any]:
        """Bucket events by event_type, return counts + pct + key samples."""
        from collections import Counter

        counter: Counter[str] = Counter()
        key_events: list[dict] = []
        for e in events:
            counter[e["event_type"]] += 1
            if e["event_type"] in {
                "execution.failed",
                "audit.completed",
                "cost.budget_exceeded",
            }:
                key_events.append(e)
        total = sum(counter.values()) or 1
        buckets = [
            {"type": t, "count": c, "pct": round(c * 100 / total, 1)}
            for t, c in counter.most_common(6)
        ]
        return {
            "buckets": buckets,
            "total": total,
            "window_days": window_days,
            "key_events": key_events[:5],
        }

    def aggregate_health(
        self,
        drawers: tuple[Drawer, ...],
        skill_metrics: tuple[dict[str, Any], ...],
        events_total: int,
        window_days: int,
    ) -> dict[str, Any]:
        """Rule-based health stats: skills status / drawer richness / activity."""
        status_counts = {"healthy": 0, "warning": 0, "retire_suggested": 0}
        for m in skill_metrics:
            s = (m.get("status") or self._infer_skill_status(m)) or "healthy"
            if s in status_counts:
                status_counts[s] += 1
        active_drawers = len(drawers)
        since_iso = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        recent = sum(1 for d in drawers if d.timestamp >= since_iso)
        activity_level = (
            "active" if events_total >= 10 else ("low" if events_total < 3 else "normal")
        )
        return {
            "skills_status": status_counts,
            "active_drawers": active_drawers,
            "drawers_added_window": recent,
            "tasks_in_window": events_total,
            "activity_level": activity_level,
        }

    @staticmethod
    def _infer_skill_status(m: dict[str, Any]) -> str:
        usage = int(m.get("usage_count") or 0)
        success = int(m.get("success_count") or 0)
        fail = int(m.get("failure_count") or 0)
        if usage == 0:
            return "healthy"
        rate = success / max(1, success + fail)
        if rate < 0.4 and usage >= 5:
            return "retire_suggested"
        if rate < 0.7 and usage >= 3:
            return "warning"
        return "healthy"

    def pick_degradation_candidates(
        self, skill_metrics: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        """Find skills trending down. Returns dicts with name/usage/rate."""
        candidates: list[dict] = []
        for m in skill_metrics:
            status = m.get("status") or self._infer_skill_status(m)
            if status in {"warning", "retire_suggested"}:
                candidates.append(
                    {
                        "skill": m.get("skill_name"),
                        "usage_count": m.get("usage_count"),
                        "success_count": m.get("success_count"),
                        "failure_count": m.get("failure_count"),
                        "status": status,
                    }
                )
        return candidates[:5]

    @staticmethod
    def _is_degraded(
        inputs: ProfileSynthesisInput,
        specialties: list[dict],
        degradations: list[dict],
    ) -> bool:
        """Degraded when data was sufficient but LLM returned nothing for both."""
        opinion_count = sum(
            1 for d in inputs.drawers if getattr(d, "category", "") == "O"
        )
        data_sufficient = opinion_count >= 5
        return data_sufficient and not specialties and not degradations

    _SPECIALTIES_SYSTEM = (
        "你是 {persona_name} 的成长档案分析助手。"
        "基于用户提供的记忆片段客观归纳,禁止编造。"
        "数据不足时必须写「数据不足」,不要臆测。"
        "输出严格 JSON,不带任何 markdown 代码块标记。"
    )

    _SPECIALTIES_USER = (
        "以下是近 {window} 天 {persona_name} 的主观经验记忆"
        "(drawer category=O, confidence>0.7):\n\n{drawer_block}\n\n"
        "请归纳 3-8 条「擅长领域」,每条一句 title + 一句 detail。\n"
        "输出 JSON:\n"
        '{{"specialties": [{{"title": "...", "detail": "..."}}]}}'
    )

    _DEGRADATION_USER = (
        "候选退化 skill 列表:\n{cand_block}\n\n"
        "对每个候选,用 1-2 句说明可能的退化原因(基于 usage/失败比)。"
        "不要编造具体案例。\n"
        "输出 JSON:\n"
        '{{"degradations": [{{"skill": "...", "reason": "..."}}]}}'
    )

    async def llm_specialties(
        self, inputs: ProfileSynthesisInput
    ) -> list[dict[str, str]]:
        """Extract specialties from drawer opinions using LLM analysis."""
        opinions = [
            d for d in inputs.drawers
            if getattr(d, "category", "") == "O"
            and getattr(d, "confidence", 0.0) > 0.7
        ][:30]
        if len(opinions) < 5:
            return []
        drawer_block = "\n".join(
            f"- [{d.room}] {d.content[:200]}" for d in opinions
        )
        system = self._SPECIALTIES_SYSTEM.format(persona_name=inputs.persona_name)
        user = self._SPECIALTIES_USER.format(
            window=inputs.data_window_days,
            persona_name=inputs.persona_name,
            drawer_block=drawer_block,
        )
        raw = await self._call_llm_json(system, user)
        return raw.get("specialties", []) if isinstance(raw, dict) else []

    async def llm_degradations(
        self, inputs: ProfileSynthesisInput, candidates: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """Analyze skill degradation reasons using LLM."""
        if not candidates:
            return []
        cand_block = "\n".join(
            f"- {c['skill']} usage={c['usage_count']} "
            f"success={c['success_count']} fail={c['failure_count']} status={c['status']}"
            for c in candidates
        )
        system = self._SPECIALTIES_SYSTEM.format(persona_name=inputs.persona_name)
        user = self._DEGRADATION_USER.format(cand_block=cand_block)
        raw = await self._call_llm_json(system, user)
        return raw.get("degradations", []) if isinstance(raw, dict) else []

    async def _call_llm_json(self, system: str, user: str) -> dict:
        """Invoke LLM with up to 3 attempts (2 retries) for non-JSON output. Returns {} on full failure."""
        last_err: Exception | None = None
        prompt_user = user
        for attempt in range(3):
            try:
                resp = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt_user},
                    ],
                )
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
                prompt_user = (
                    user + "\n\n上次输出不是合法 JSON,严格只输出 JSON 对象,禁止其他字符。"
                )
            except Exception as e:
                last_err = e
                await asyncio.sleep(1)
        logger.warning("LLM json call failed after retries: %s", last_err)
        return {}

    def persist(self, result: ProfileSynthesisResult) -> Path:
        """Atomic write to PROFILE.md; archive previous version; prune to 10.

        Returns final path. Raises on write failures (caller must release lock).
        """
        from tianshu.persona.profile_io import (
            archive_previous,
            atomic_write,
            prune_history,
        )

        path = self._profile_path(result.persona_id)
        prev_version = max(0, result.version - 1)
        if prev_version >= 1:
            archive_previous(path, prev_version)
        atomic_write(path, result.markdown)
        prune_history(path.parent / "profile_history")
        return path

    def detect_conflict(
        self, prev_markdown: str | None, new_auto_section: str
    ) -> bool:
        """True when user-edited auto section diverges >= 30% from previous."""
        from tianshu.persona.profile_renderer import (
            MANUAL_DIFF_CONFLICT_THRESHOLD,
            auto_section_diff_ratio,
        )
        from tianshu.persona.profile_schema import parse_profile

        if not prev_markdown:
            return False
        _, prev_auto, _ = parse_profile(prev_markdown)
        if not prev_auto:
            return False
        return (
            auto_section_diff_ratio(prev_auto, new_auto_section)
            >= MANUAL_DIFF_CONFLICT_THRESHOLD
        )
