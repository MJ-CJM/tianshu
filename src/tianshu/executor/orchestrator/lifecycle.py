"""Lifecycle 状态机辅助。

把 EdictRuntime.lifecycle_phase 与 storage 的 update / event emission 解耦封装。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LifecyclePhase = str  # active | paused | winding_down | complete

VALID_PHASES = ("active", "paused", "winding_down", "complete")


@dataclass(frozen=True)
class PhaseTransition:
    edict_id: str
    from_phase: LifecyclePhase
    to_phase: LifecyclePhase
    reason: str


def can_transition(current: LifecyclePhase, target: LifecyclePhase) -> bool:
    """合法转移规则：
    - active <-> paused
    - active -> winding_down
    - winding_down -> complete
    - any -> complete (终态)
    - complete 不可转出
    """
    if current not in VALID_PHASES or target not in VALID_PHASES:
        return False
    if current == target:
        return True  # 幂等
    if current == "complete":
        return False
    if target == "complete":
        return True
    legal = {
        "active": {"paused", "winding_down"},
        "paused": {"active"},
        "winding_down": {},  # 只能进 complete（上面已处理）
    }
    return target in legal.get(current, set())


def apply_transition(
    storage,
    bus,
    edict_id: str,
    memorial_id: str | None,
    current: LifecyclePhase,
    target: LifecyclePhase,
    reason: str,
) -> PhaseTransition | None:
    """执行 phase 转移：DB 更新 + event emit。非法转移记 warning 返 None。"""
    if not can_transition(current, target):
        logger.warning(
            "illegal lifecycle transition for edict %s: %s -> %s (reason=%s)",
            edict_id,
            current,
            target,
            reason,
        )
        return None
    if current == target:
        return None
    storage.update_edict_lifecycle_phase(edict_id, target)
    storage.append_event(
        edict_id,
        memorial_id,
        "edict.lifecycle.changed",
        {"from_phase": current, "to_phase": target, "reason": reason},
    )
    return PhaseTransition(edict_id, current, target, reason)
