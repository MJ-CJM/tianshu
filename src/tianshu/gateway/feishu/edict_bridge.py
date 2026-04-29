"""把飞书消息桥接到 tianshu 敕令模型。

决策：
- 默认续接当前 chat 锚定的 Edict（follow_up）
- /new <goal> → 显式新建并更新锚
- 子决策 X1：anchor 指向的 Edict 已结案 → 自动新建（无感）
- 当 anchor 指向的活跃 Edict 仍有 active memorial 时，抛 EdictBusyError 让 caller 提示用户

Follow-up 路径与 gateway.api.follow_up_edict 行为对齐：
- storage.append_event(... "followup.submitted")
- 直接调 executor.execute_edict 启动任务
- 加入 executor.running_tasks 集合
"""
from __future__ import annotations

import asyncio
import logging

from tianshu.bus.event_bus import EventBus
from tianshu.executor.executor import Executor
from tianshu.gateway.feishu.session_anchor import SessionAnchor
from tianshu.models.common import EdictStatus, TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import make_event
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

# 注：tianshu 的 EdictStatus 仅有 OPEN / COMPLETED / CANCELLED 三态（无 FAILED）。
CLOSED_STATES = {EdictStatus.COMPLETED, EdictStatus.CANCELLED}


class EdictBusyError(RuntimeError):
    """敕令仍有 active memorial，无法立即 follow_up。caller 应向用户提示。"""


def _build_history(edict: Edict, memorials: list[Memorial]) -> list[dict]:
    """与 gateway.api._build_history 等价的本地实现。

    避免反向依赖 gateway.api（router 层不应被 gateway/feishu 直接引用）。
    """
    history: list[dict] = []
    for m in memorials:
        if m.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            continue
        instruction = m.instruction or edict.goal
        history.append({"role": "user", "content": instruction})
        if m.result:
            history.append({"role": "assistant", "content": m.result})
    return history


class EdictBridge:
    def __init__(
        self,
        *,
        storage: Storage,
        event_bus: EventBus,
        executor: Executor,
        anchor: SessionAnchor,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._executor = executor
        self._anchor = anchor

    async def continue_or_create(self, *, chat_id: str, sender_open_id: str, text: str) -> str:
        """主入口。返回最终绑定的 edict_id。

        Raises:
            EdictBusyError: 当锚定的活跃 Edict 仍有 active memorial 时。
        """
        current_edict_id = self._anchor.get(chat_id)
        if current_edict_id:
            edict = self._storage.get_edict(current_edict_id)
            if edict and edict.status not in CLOSED_STATES:
                memorials = self._storage.list_memorials_by_edict(edict.id)
                has_active = any(
                    m.status in (TaskStatus.SUBMITTED, TaskStatus.RUNNING)
                    for m in memorials
                )
                if has_active:
                    raise EdictBusyError(
                        f"敕令 #{edict.id[:8]} 仍在处理中，请等待完成后再继续"
                    )
                await self._follow_up(edict, text, sender_open_id, memorials)
                return edict.id
            # X1：已结案 → 自动新建（无感）
            logger.info(
                "[feishu/edict] anchor edict %s closed (status=%s), auto-new",
                current_edict_id, edict.status if edict else "missing",
            )
        return await self.create_new(
            chat_id=chat_id, sender_open_id=sender_open_id, goal=text,
        )

    async def create_new(self, *, chat_id: str, sender_open_id: str, goal: str) -> str:
        """显式新建（来自 /new 或 anchor 已结案后的自动新建）。"""
        title = goal[:20] + ("…" if len(goal) > 20 else "")
        edict = Edict(
            title=title, goal=goal,
            source="channel",
            submitter="emperor",
            metadata={
                "channel": "feishu",
                "chat_id": chat_id,
                "feishu_user": sender_open_id,
            },
        )
        self._storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED,
        )
        self._storage.save_memorial(memorial)
        self._anchor.set(chat_id, edict.id)
        self._event_bus.fire(make_event(
            "edict.submitted",
            edict_id=edict.id, memorial_id=memorial.id,
            producer="feishu_bot",
            payload={"goal": edict.goal, "channel": "feishu", "chat_id": chat_id},
        ))
        logger.info(
            "[feishu/edict] created edict=%s chat=%s sender=%s",
            edict.id, chat_id, sender_open_id,
        )
        return edict.id

    async def ensure_chat_edict(
        self, *, chat_id: str, sender_open_id: str,
    ) -> str:
        """确保该 chat 有一个聊天敕令（assistant_chat=true）作为 anchor。

        v2 极简模型：飞书首次接入时自动建 chat 敕令，让纯文本消息能续接。

        若 anchor 已存在 → 直接返回 anchor edict_id（无论 chat 还是业务敕令）
        若 anchor 不存在 → 创建一个 metadata.assistant_chat=true 敕令并设 anchor
        """
        existing = self._anchor.get(chat_id)
        if existing:
            return existing
        edict = Edict(
            title=f"飞书助手对话 - {chat_id[:12]}",
            goal="持续对话上下文",
            source="channel",
            submitter="emperor",
            metadata={
                "channel": "feishu",
                "chat_id": chat_id,
                "feishu_user": sender_open_id,
                "assistant_chat": True,
            },
        )
        self._storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id, instruction=edict.goal, status=TaskStatus.SUBMITTED,
        )
        self._storage.save_memorial(memorial)
        self._anchor.set(chat_id, edict.id)
        self._event_bus.fire(make_event(
            "edict.submitted",
            edict_id=edict.id, memorial_id=memorial.id,
            producer="feishu_bot",
            payload={"goal": edict.goal, "channel": "feishu", "chat_id": chat_id,
                     "assistant_chat": True},
        ))
        logger.info(
            "[feishu/edict] auto-created chat edict %s for chat=%s",
            edict.id, chat_id,
        )
        return edict.id

    async def _follow_up(
        self,
        edict: Edict,
        text: str,
        sender_open_id: str,
        prev_memorials: list[Memorial],
    ) -> None:
        """对应 gateway.api.follow_up_edict 的核心逻辑（无 HTTP 层）。"""
        history = _build_history(edict, prev_memorials)
        memorial = Memorial(
            edict_id=edict.id, instruction=text, status=TaskStatus.SUBMITTED,
        )
        self._storage.save_memorial(memorial)
        self._storage.append_event(
            edict.id, memorial.id, "followup.submitted",
            {
                "instruction": text,
                "channel": "feishu",
                "feishu_user": sender_open_id,
            },
        )
        task = asyncio.create_task(
            self._executor.execute_edict(
                edict, memorial=memorial, history=history, user_content=text,
            )
        )
        self._executor.running_tasks.add(task)
        task.add_done_callback(self._executor.running_tasks.discard)
        logger.info(
            "[feishu/edict] follow_up edict=%s memorial=%s",
            edict.id, memorial.id,
        )
