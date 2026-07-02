"""持久化 iteration record 到 outer_loop_iterations 表 + 审计事件到 memorial。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

from ulid import ULID

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator.state import IterationRecord
from tianshu.models.events import make_event
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def persist_iteration(
    storage: Storage,
    edict_id: str,
    record: IterationRecord,
) -> str:
    """写一行 outer_loop_iterations，返回 row id（ULID）。"""
    row_id = str(ULID())
    storage.save_outer_loop_iteration({
        "id": row_id,
        "edict_id": edict_id,
        "iteration": record.iteration,
        "level": record.level,
        "actor_output": record.actor_output,
        "checks_result": json.dumps({
            "all_passed": record.checks_result.all_passed,
            "outcomes": [asdict(o) for o in record.checks_result.outcomes],
        }),
        "critic_result": (
            json.dumps(_critic_result_to_dict(record.critic_result))
            if record.critic_result else None
        ),
        "cost_cny": record.cost_cny,
        "started_at": record.started_at.isoformat(),
        "finished_at": record.finished_at.isoformat(),
    })
    return row_id


def _critic_result_to_dict(cr: object) -> dict:
    """asdict 不能展开 pydantic UsageSummary，手动序列化 critic_result。"""
    d = asdict(cr)
    usage = d.get("usage")
    if usage is not None and hasattr(cr, "usage") and cr.usage is not None:
        # asdict 把 pydantic model 当作单值 attribute；这里替换为 dump 后的 dict
        d["usage"] = cr.usage.model_dump() if hasattr(cr.usage, "model_dump") else None
    return d


async def emit_audit(
    bus: EventBus,
    storage: Storage,
    edict_id: str,
    memorial_id: str | None,
    event_type: str,
    payload: dict,
) -> None:
    """发审计事件（同时落 memorial 事件流 + EventBus 广播）。"""
    if memorial_id:
        storage.append_event(edict_id, memorial_id, event_type, payload)
    try:
        await bus.emit(make_event(
            event_type,
            edict_id=edict_id,
            memorial_id=memorial_id,
            producer="orchestrator",
            payload=payload,
        ))
    except Exception:
        logger.exception("emit_audit %s failed", event_type)
