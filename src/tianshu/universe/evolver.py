"""UniverseEvolver（演化）— 从冠军位面变异出候选并据适应度择优。

骨架对齐 SkillCurator：gate(idle + lock) → 采信号 → ONE LLM 变异 →
分支候选位面 → 熔断下线劣质候选 → 晋升推荐（默认人工确认）。

§限制（有意分步）：本轮完成"采信号→提变异意图→分支候选→熔断→晋升推荐/自动晋升"
的闭环骨架，但【不】把变异意图实际改写进候选位面的 SOUL/ROLE/policy 文件——候选先以
"冠军全量拷贝 + 记录 mutation_reason"存在，承接探索流量做对照基线。改写器留作后续增量。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from tianshu.universe.model import UniverseOrigin, UniverseStatus

logger = logging.getLogger(__name__)

_LOCK_KEY = "__universe_evolver__"

_SYSTEM = (
    "你是天枢的「演化」官，负责让宫殿的行为配置随使用越来越贴合主上。"
    "给定冠军位面的行为概要与各位面适应度，你只提出【一处】定向变异，"
    "用以分支出一个候选位面去试验。严禁臆造，只输出 JSON 对象，不带 markdown 代码块标记。"
)

_USER = """\
冠军位面适应度：{champion_fitness}
各候选位面适应度：{challenger_fitness}
冠军行为概要（人格/技能/策略要点）：
{summary}

请提出【一处】可能提升贴合度的定向变异，输出 JSON：
{{"target": "persona:bingbu/ROLE.md | policy | config | skillset",
  "reason": "为何这样改可能更好",
  "name": "候选位面名称（简短中文）"}}
若当前无明确可改之处，输出 {{"target": null, "reason": "...", "name": null}}。"""


@dataclass
class EvolveResult:
    skipped: str | None = None
    created_challenger: str | None = None
    mutation_reason: str | None = None
    retired: list[str] = field(default_factory=list)
    promotion_recommended: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skipped": self.skipped,
            "created_challenger": self.created_challenger,
            "mutation_reason": self.mutation_reason,
            "retired": self.retired,
            "promotion_recommended": self.promotion_recommended,
            "errors": self.errors,
        }


class UniverseEvolver:
    def __init__(
        self,
        llm_client: Any,
        manager: Any,
        storage: Any,
        config_manager: Any,
    ) -> None:
        self._llm = llm_client
        self._mgr = manager
        self._storage = storage
        self._config = config_manager
        self._bus: Any | None = None

    def attach_event_bus(self, bus: Any) -> None:
        self._bus = bus

    def _idle_ok(self, idle_hours: int) -> bool:
        from tianshu.skills.curator import _age_hours
        last = self._storage.last_activity_at()
        age = _age_hours(last)
        return age is None or age >= idle_hours

    async def run(self, trigger_source: str = "manual") -> EvolveResult:
        cfg = self._config.agent_config
        if not getattr(cfg, "parallel_universe_enabled", False):
            return EvolveResult(skipped="disabled")
        if trigger_source != "manual" and not self._idle_ok(getattr(cfg, "universe_evolver_idle_hours", 2)):
            return EvolveResult(skipped="not_idle")
        if not self._storage.try_acquire_synthesis_lock(_LOCK_KEY):
            return EvolveResult(skipped="lock_held")

        result = EvolveResult()
        try:
            champ = self._mgr.champion()
            if not champ:
                return EvolveResult(skipped="no_champion")

            result.retired = self._retire_failing_challengers(cfg)
            result.promotion_recommended = await self._maybe_promote(champ, cfg)

            mutation = await self._propose_mutation(champ)
            if mutation and mutation.get("target"):
                child = self._mgr.branch(
                    champ["id"], mutation.get("name") or "演化候选",
                    origin=UniverseOrigin.MUTATION,
                    mutation_reason=mutation.get("reason"),
                    description=f"target={mutation.get('target')}",
                )
                result.created_challenger = child["id"]
                result.mutation_reason = mutation.get("reason")

            await self._emit("universe.evolved", result.to_dict())
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] run failed")
            result.errors.append(str(e))
            return result
        finally:
            self._storage.release_synthesis_lock(_LOCK_KEY)

    def _retire_failing_challengers(self, cfg: Any) -> list[str]:
        limit = getattr(cfg, "universe_challenger_fail_limit", 5)
        retired: list[str] = []
        for u in self._mgr.list(include_archived=False):
            if u["status"] != UniverseStatus.CHALLENGER.value:
                continue
            stats = self._storage.universe_memorial_stats(u["id"])
            fails = stats["total"] - stats["success"]
            if stats["total"] >= limit and fails >= limit:
                self._mgr.archive(u["id"])
                retired.append(u["id"])
        return retired

    async def _maybe_promote(self, champ: dict, cfg: Any) -> str | None:
        min_samples = getattr(cfg, "universe_min_samples", 20)
        margin = getattr(cfg, "universe_promote_margin", 0.05)
        auto = getattr(cfg, "universe_auto_promote", False)
        champ_score = (champ.get("fitness") or {}).get("score", 0.0)
        best: tuple[str, float] | None = None
        for u in self._mgr.list(include_archived=False):
            if u["status"] != UniverseStatus.CHALLENGER.value:
                continue
            f = u.get("fitness") or {}
            if f.get("samples", 0) < min_samples:
                continue
            score = f.get("score", 0.0)
            if score >= champ_score + margin and (best is None or score > best[1]):
                best = (u["id"], score)
        if not best:
            return None
        winner_id = best[0]
        if auto:
            self._mgr.switch(winner_id)
            await self._emit("universe.promoted", {"universe_id": winner_id, "auto": True})
        else:
            await self._emit("universe.promotion_recommended", {
                "universe_id": winner_id, "score": best[1], "champion_score": champ_score,
            })
        return winner_id

    async def _propose_mutation(self, champ: dict) -> dict:
        challengers = [
            u for u in self._mgr.list(include_archived=False)
            if u["status"] == UniverseStatus.CHALLENGER.value
        ]
        prompt = _USER.format(
            champion_fitness=json.dumps(champ.get("fitness", {}), ensure_ascii=False),
            challenger_fitness=json.dumps(
                [c.get("fitness", {}) for c in challengers], ensure_ascii=False),
            summary=self._champion_summary(champ),
        )
        for _ in range(3):
            try:
                resp = await self._llm.chat(messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ])
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                prompt += "\n\n上次输出非合法 JSON，严格只输出 JSON。"
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
        return {}

    def _champion_summary(self, champ: dict) -> str:
        store = self._mgr._store  # noqa: SLF001
        pdir = store.personas_dir(champ["id"])
        personas = sorted(p.name for p in pdir.glob("*")) if pdir.exists() else []
        return f"人格: {personas}; config: {list(store.read_manifest(champ['id']).keys())}"

    async def _emit(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event
        self._bus.fire(make_event(
            event_type=event_type, edict_id=None, memorial_id=None,
            producer="universe_evolver", payload=payload,
        ))
