"""EvolutionUnlock —— 自进化「请旨解锁」(迭代 6「演化 2.0」,ADR-0004)。

自进化出厂默认关:新用户信任建立期不应有后台变异与沙箱评估成本。解法不是文档引导
手动开(钩子哑火),而是**把功能开关做成剧情事件**——行为层积累到阈值(审计通过的
memorial 数)后,系统主动上一道「臣请开启自我演化」奏折,附【将做什么/花多少钱/
如何回滚】白话三段;用户批红(grant)后翻转 parallel_universe_enabled 开启行为层演化。

**代码层演化(自改代码)风险更高,不设自动请旨,永远由用户显式开启**——本模块只管行为层。
check_and_petition 幂等:已开启 / 已有 pending 奏折 / 未达阈值 一律跳过,随 digest cron 周期跑。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

logger = logging.getLogger(__name__)

_KIND = "behavior_evolution"


def _plan_text(count: int) -> str:
    """奏折白话三段:做什么 / 花多少钱 / 如何回滚——对齐系统真实行为,不夸大。"""
    return (
        "臣请开启【行为层自我演化】。\n"
        "· 将做什么:空闲时段从冠军位面变异出候选(仅改官员人格 SOUL/ROLE),"
        "在沙箱配对评估,提升达标才推荐晋升,晋升仍默认由陛下批红。\n"
        "· 花多少钱:每轮一次 LLM 变异 + 一组评估集回归(受 code_variant_eval_budget_cny "
        "预算护栏约束),空闲触发、非空闲不扰。\n"
        "· 如何回滚:候选劣汰自动归档;已晋升位面可一键切回冠军;陛下可随时关闭本开关,"
        f"后台变异即刻停止。\n(触发:已积累 {count} 个审计通过的执行。)"
    )


class EvolutionUnlock:
    def __init__(
        self,
        storage: Any,
        config_manager: Any,
        notifier: Any = None,
        bus: Any = None,
        threshold_memorials: int = 20,
    ) -> None:
        self._storage = storage
        self._config = config_manager
        self._notifier = notifier
        self._bus = bus
        self._threshold = threshold_memorials

    async def check_and_petition(self) -> dict:
        """达阈值且未开启、无 pending 奏折时上一道请旨奏折。幂等、失败安全。"""
        cfg = self._config.agent_config
        if getattr(cfg, "parallel_universe_enabled", False):
            return {"skipped": "already_enabled"}
        if self._storage.get_pending_petition(_KIND):
            return {"skipped": "already_petitioned"}
        count = self._storage.count_successful_memorials()
        if count < self._threshold:
            return {"skipped": "below_threshold", "count": count, "threshold": self._threshold}

        petition_id = str(ULID())
        reason = f"已积累 {count} 个审计通过的执行(阈值 {self._threshold})"
        plan = _plan_text(count)
        self._storage.create_petition(
            petition_id=petition_id,
            kind=_KIND,
            reason=reason,
            plan=plan,
            created_at=datetime.now(UTC).isoformat(),
        )
        await self._announce(petition_id, reason, plan)
        logger.info("[UNLOCK] behavior-evolution petition raised: %s (%s)", petition_id, reason)
        return {"petitioned": petition_id, "count": count}

    def grant(self, petition_id: str) -> dict:
        """批红:翻转 parallel_universe_enabled 开启行为层演化(与设置页开关同一路径)。"""
        p = self._storage.get_petition(petition_id)
        if not p:
            return {"error": "not_found"}
        if p["status"] != "pending":
            return {"skipped": p["status"]}
        self._storage.resolve_petition(petition_id, "granted", datetime.now(UTC).isoformat())
        self._config.update_agent_config(parallel_universe_enabled=True)
        self._fire("universe.evolution_unlocked", {"petition_id": petition_id})
        return {"granted": petition_id}

    def dismiss(self, petition_id: str) -> dict:
        """驳回:关闭奏折不开启;阈值仍在,下一周期不再重复上奏(直到被驳回的这条已终态)。"""
        p = self._storage.get_petition(petition_id)
        if not p:
            return {"error": "not_found"}
        if p["status"] != "pending":
            return {"skipped": p["status"]}
        self._storage.resolve_petition(petition_id, "dismissed", datetime.now(UTC).isoformat())
        return {"dismissed": petition_id}

    async def _announce(self, petition_id: str, reason: str, plan: str) -> None:
        payload = {
            "type": "evolution.petition",
            "petition_id": petition_id,
            "kind": _KIND,
            "reason": reason,
            "plan": plan,
        }
        if self._notifier is not None:
            try:
                await self._notifier.broadcast_ws(payload)
            except Exception:  # noqa: BLE001
                logger.exception("[UNLOCK] broadcast failed")
        self._fire("universe.evolution_petitioned", payload)

    def _fire(self, event_type: str, payload: dict) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event

        self._bus.fire(
            make_event(
                event_type=event_type,
                edict_id=None,
                memorial_id=None,
                producer="evolution_unlock",
                payload=payload,
            )
        )
