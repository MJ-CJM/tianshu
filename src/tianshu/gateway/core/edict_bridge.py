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

import logging
from dataclasses import dataclass

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.application.ingress import (
    make_ingress_auth_context,
    requested_contract_for_edict,
)
from tianshu.bus.event_bus import EventBus
from tianshu.executor.executor import Executor
from tianshu.executor.workspace_runtime import WORKSPACE_MAIN_SOURCE_ID
from tianshu.gateway.core.errors import EdictBusyError  # re-export，向后兼容
from tianshu.gateway.core.session_anchor import SessionAnchor
from tianshu.models.common import EdictStatus, TaskStatus
from tianshu.models.edict import Edict, title_from_goal
from tianshu.models.memorial import Memorial
from tianshu.models.principal import (
    AuthenticationSource,
    ClientKind,
    PrincipalKind,
)
from tianshu.storage import Storage


@dataclass(frozen=True)
class EdictBridgeResult:
    """`continue_or_create` / `create_new` 的统一返回，便于 caller 同时拿 memorial_id。"""

    edict_id: str
    memorial_id: str


logger = logging.getLogger(__name__)

# 注：tianshu 的 EdictStatus 仅有 OPEN / COMPLETED / CANCELLED 三态（无 FAILED）。
CLOSED_STATES = {EdictStatus.COMPLETED, EdictStatus.CANCELLED}


def _build_history(edict: Edict, memorials: list[Memorial]) -> list[dict]:
    """与 gateway._helpers._build_history 等价的本地实现。

    避免反向依赖 gateway.api（router 层不应被 gateway/feishu 直接引用）。

    DeepSeek reasoner / 新版 thinking-mode 模型在 multi-turn follow_up 时
    要求 history 中 **每一条** assistant 消息都带 reasoning_content，否则
    返回 400 invalid_request_error（"must be passed back to the API"）。

    后向兼容：reasoning_content 字段是 2026-04-30 才加的，更早的 memorial 没存。
    对那些 assistant 整条跳过（仅保留 user 消息维持上下文），避免 DeepSeek 报错。
    """
    history: list[dict] = []
    for m in memorials:
        if m.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            continue
        instruction = m.instruction or edict.goal
        history.append({"role": "user", "content": instruction})
        if not m.result:
            continue
        if not m.reasoning_content:
            # 老 memorial：assistant 整条跳过，避免触发 thinking-mode 校验
            continue
        history.append(
            {
                "role": "assistant",
                "content": m.result,
                "reasoning_content": m.reasoning_content,
            }
        )
    return history


class EdictBridge:
    def __init__(
        self,
        *,
        storage: Storage,
        event_bus: EventBus,
        executor: Executor,
        anchor: SessionAnchor,
        channel: str = "feishu",
        instance_id: str = "feishu-default",
        user_meta_key: str = "feishu_user",
        chat_title_prefix: str = "飞书助手对话",
        edict_application_service: EdictApplicationService | None = None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._executor = executor
        self._anchor = anchor
        # 渠道标识：写入 edict.metadata.channel，供出站按 channel 路由隔离
        # （feishu / telegram 等并列通道各自只投递自己的敕令）。默认 feishu 保持向后兼容。
        self._channel = channel
        # 实例标识：写入 edict.metadata.instance_id，供出站按实例路由隔离
        # （同一渠道可跑多个 bot 实例，各自只投递自己的敕令）。默认 feishu-default 向后兼容。
        self._instance_id = instance_id
        self._user_meta_key = user_meta_key
        self._chat_title_prefix = chat_title_prefix
        self._edict_application = edict_application_service or EdictApplicationService(storage)

    async def continue_or_create(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        text: str,
        source_message_id: str | None = None,
    ) -> EdictBridgeResult:
        """主入口。返回 (edict_id, memorial_id)。

        Raises:
            EdictBusyError: 当锚定的活跃 Edict 仍有 active memorial 时。
        """
        current_edict_id = self._anchor.get(chat_id)
        if current_edict_id:
            edict = self._storage.get_edict(current_edict_id)
            if edict and edict.status not in CLOSED_STATES:
                memorials = self._storage.list_memorials_by_edict(edict.id)
                has_active = any(
                    m.status in (TaskStatus.SUBMITTED, TaskStatus.RUNNING) for m in memorials
                )
                if has_active:
                    raise EdictBusyError(f"敕令 #{edict.id[:8]} 仍在处理中，请等待完成后再继续")
                if source_message_id is None or not source_message_id.strip():
                    raise ValueError("channel follow-up requires a stable source_message_id")
                memorial_id = await self._follow_up(
                    edict,
                    text,
                    sender_open_id,
                    memorials,
                    idempotency_key=(f"{self._channel}:{self._instance_id}:{source_message_id}"),
                )
                return EdictBridgeResult(edict_id=edict.id, memorial_id=memorial_id)
            # X1：已结案 → 自动新建（无感）
            logger.info(
                "[feishu/edict] anchor edict %s closed (status=%s), auto-new",
                current_edict_id,
                edict.status if edict else "missing",
            )
        return await self.create_new(
            chat_id=chat_id,
            sender_open_id=sender_open_id,
            goal=text,
            source_message_id=source_message_id,
        )

    async def create_new(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        goal: str,
        source_message_id: str | None = None,
    ) -> EdictBridgeResult:
        """显式新建（来自 /new 或 anchor 已结案后的自动新建）。"""
        title = title_from_goal(goal)
        edict = Edict(
            title=title,
            goal=goal,
            source="channel",
            submitter=f"{self._channel}:{sender_open_id}",
            metadata={
                "channel": self._channel,
                "instance_id": self._instance_id,
                "chat_id": chat_id,
                self._user_meta_key: sender_open_id,
                "workspace_id": WORKSPACE_MAIN_SOURCE_ID,
            },
        )
        instance_namespace = f"{self._channel}:{self._instance_id}"
        correlation_id = f"{instance_namespace}:{source_message_id or edict.id}"
        command = SubmitEdictCommand(
            edict,
            idempotency_key=correlation_id,
            requested_contract=requested_contract_for_edict(edict),
            extra_payload={
                "channel": self._channel,
                "instance_id": self._instance_id,
                "chat_id": chat_id,
            },
        )
        result = self._edict_application.submit(
            command,
            auth=make_ingress_auth_context(
                principal_id=f"{instance_namespace}:{sender_open_id}",
                principal_kind=PrincipalKind.WEBHOOK,
                source=AuthenticationSource.WEBHOOK,
                client_kind=ClientKind.WEBHOOK,
                correlation_id=correlation_id,
            ),
            producer=f"{self._channel}_bot",
            correlation_id=correlation_id,
        )
        self._anchor.set(chat_id, result.edict.id)
        logger.info(
            "[feishu/edict] created edict=%s chat=%s sender=%s",
            result.edict.id,
            chat_id,
            sender_open_id,
        )
        return EdictBridgeResult(
            edict_id=result.edict.id,
            memorial_id=result.memorial.id,
        )

    async def ensure_chat_edict(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        assistant_persona_id: str | None = None,
    ) -> str:
        """确保该 chat 有一个聊天敕令（assistant_chat=true）作为 anchor。

        v2 极简模型：飞书首次接入时自动建 chat 敕令，让纯文本消息能续接。

        Args:
            chat_id: 飞书会话 ID。
            sender_open_id: 发起方 open_id（写入 metadata，便于审计）。
            assistant_persona_id: 通政司配置的助手 persona id；新建敕令时用作
                ``assigned_persona_id``，让 executor 跑用户配的人格。复用旧敕令时
                **不**强制更新 persona（保留旧敕令建立时的 persona，保证对话一致性）。

        优先级：
        1. anchor 已存在 → 直接返回（无论是 chat 还是业务敕令）
        2. anchor 不存在 + 该 chat_id 有 open 状态的 chat 敕令 → 复用 + 设 anchor
        3. 都不存在 → 新建一个 chat 敕令并设 anchor

        **不**创建 SUBMITTED memorial（避免 has_active=True 让 follow_up 抛 EdictBusyError）；
        memorial 由后续用户消息进 continue_or_create 时按需创建。
        """
        existing = self._anchor.get(chat_id)
        if existing:
            return existing

        # 查找该 chat_id 是否有可复用的 open chat 敕令
        # 注意：list_edicts 不带 metadata 过滤，需 Python 端遍历筛
        open_edicts, _total = self._storage.list_edicts(
            status="open",
            limit=200,
            offset=0,
        )
        for e in open_edicts:
            meta = e.metadata or {}
            if meta.get("assistant_chat") and meta.get("chat_id") == chat_id:
                self._anchor.set(chat_id, e.id)
                logger.info(
                    "[feishu/edict] reusing existing chat edict %s for chat=%s",
                    e.id,
                    chat_id,
                )
                return e.id

        # 新建（v2 fix: 用 assistant_persona_id 指派 persona，让 executor 用配置的人格）
        edict = Edict(
            title=f"{self._chat_title_prefix} - {chat_id[:12]}",
            goal="持续对话上下文",
            source="channel",
            submitter=f"{self._channel}:{sender_open_id}",
            assigned_persona_id=assistant_persona_id,
            metadata={
                "channel": self._channel,
                "instance_id": self._instance_id,
                "chat_id": chat_id,
                self._user_meta_key: sender_open_id,
                "assistant_chat": True,
                "workspace_id": WORKSPACE_MAIN_SOURCE_ID,
            },
        )
        self._storage.save_edict(edict)
        # ⚠️ 不创建 SUBMITTED memorial，不 fire edict.submitted
        # —— chat 敕令不需要被 scheduler/executor 拉起；memorial 由 continue_or_create 按需建
        self._anchor.set(chat_id, edict.id)
        logger.info(
            "[feishu/edict] auto-created chat edict %s for chat=%s persona=%s",
            edict.id,
            chat_id,
            assistant_persona_id or "default",
        )
        return edict.id

    async def _follow_up(
        self,
        edict: Edict,
        text: str,
        sender_open_id: str,
        prev_memorials: list[Memorial],
        *,
        idempotency_key: str,
    ) -> str:
        """对应 gateway.api.follow_up_edict 的核心逻辑（无 HTTP 层）。返回 memorial_id。"""
        del prev_memorials
        from tianshu.application.managed_run_ingress import ManagedRunCommand

        ingress = self._executor.managed_run_ingress
        if ingress is None:
            raise RuntimeError("managed run ingress is not configured")
        result = await ingress.start(
            ManagedRunCommand(
                edict_id=edict.id,
                idempotency_key=idempotency_key,
                instruction=text,
                parent_memorial_id=next(
                    (
                        item.id
                        for item in reversed(self._storage.list_memorials_by_edict(edict.id))
                        if item.dag_node_id is None
                    ),
                    None,
                ),
                event_type="followup.submitted",
                event_payload={
                    "instruction": text,
                    "channel": self._channel,
                    self._user_meta_key: sender_open_id,
                },
            )
        )
        logger.info(
            "[feishu/edict] follow_up edict=%s memorial=%s",
            edict.id,
            result.memorial.id,
        )
        return result.memorial.id
