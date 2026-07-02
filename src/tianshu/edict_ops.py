"""Edict 提交的共享领域操作。"""

from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, Memorial, TaskStatus, make_event
from tianshu.storage import Storage


def submit_new_edict(
    storage: Storage,
    event_bus: EventBus,
    edict: Edict,
    *,
    producer: str,
    extra_payload: dict | None = None,
) -> Memorial:
    """保存 edict + 初始 memorial，fire edict.submitted。调用方自行构造 Edict 与记日志。"""
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED)
    storage.save_memorial(memorial)
    event_bus.fire(make_event(
        "edict.submitted",
        edict_id=edict.id,
        memorial_id=memorial.id,
        producer=producer,
        payload={"goal": edict.goal, **(extra_payload or {})},
    ))
    return memorial
