"""Telegram 出站：telegram.Bot 客户端 + 事件订阅 → Telegram 消息。

镜像 feishu/outbound.py。关键差异：
- lark client → telegram.Bot；send_card → InlineKeyboardMarkup
- typing reaction → ⏳ 占位消息（thinking），完成时删除
- **channel 路由隔离**：仅投递 edict.metadata.channel == "telegram" 的敕令（+ home_channel 兜底）
- MarkdownV2 发送，解析失败回退纯文本；UTF-16 ≤4096 分片
"""
from __future__ import annotations

import logging

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.markdown_compat import convert_tables_to_lists, split_long
from tianshu.gateway.telegram.markdown_v2 import (
    format_message,
    strip_mdv2,
    truncate_message,
)
from tianshu.gateway.telegram.settings import TelegramSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_TG_MAX = 4096
# 留转义余量：split_long 先按段落粗切，再由 truncate_message 按 UTF-16 精确分片
_SAFE_CHUNK = 3500

# send_card 的载荷：(text, InlineKeyboardMarkup | None)
TelegramCard = tuple[str, "InlineKeyboardMarkup | None"]


def _to_chat_id(chat_id: str) -> int | str:
    """数字 chat_id → int（群为负数）；@username / 其它保持 str。"""
    s = str(chat_id).strip()
    try:
        return int(s)
    except (TypeError, ValueError):
        return s


class TelegramOutbound:
    def __init__(
        self,
        *,
        settings: TelegramSettings,
        storage: Storage,
        event_bus: EventBus,
        instance_id: str = "telegram-default",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._event_bus = event_bus
        self._instance_id = instance_id
        self._bot: Bot | None = None
        # 保存订阅引用：EventBus.off 用 `is` 比对，bound method 每次取属性都是新对象。
        self._sub_completed = self._on_execution_completed
        self._sub_failed = self._on_execution_failed

    def start(self) -> None:
        """构造 Bot + 注册 EventBus 订阅（仅 TelegramBot.start 调用一次）。"""
        self._bot = Bot(self._settings.bot_token)
        self._event_bus.on(
            "execution.completed", self._sub_completed, priority=200
        )
        self._event_bus.on(
            "execution.failed", self._sub_failed, priority=200
        )

    def stop(self) -> None:
        """取消 EventBus 订阅（实例停止时调用，避免已停实例继续投递）。"""
        self._event_bus.off("execution.completed", self._sub_completed)
        self._event_bus.off("execution.failed", self._sub_failed)

    def rebuild_client(self) -> None:
        """仅重建 Bot（热加载切 token），**不重订阅 EventBus**。"""
        self._bot = Bot(self._settings.bot_token)

    @property
    def bot(self) -> Bot | None:
        return self._bot

    # --- 公共 API ---

    async def send_text(self, chat_id: str, content: str) -> str | None:
        """发送文本（MarkdownV2，失败回退纯文本）。UTF-16 ≤4096 分片。返回首片 message_id。"""
        if self._bot is None or not chat_id or not content:
            return None
        formatted = format_message(content)
        chunks = truncate_message(formatted, _TG_MAX)
        first_id: str | None = None
        for chunk in chunks:
            mid = await self._send_one(chat_id, chunk)
            if first_id is None:
                first_id = mid
        return first_id

    async def send_card(self, chat_id: str, card: TelegramCard) -> str | None:
        """发送带 inline keyboard 的消息。card = (text, InlineKeyboardMarkup|None)。"""
        if self._bot is None or not chat_id:
            return None
        text, keyboard = card
        formatted = format_message(text)
        # 卡片文本一般较短，不分片；仍做一次截断保护
        if len(formatted.encode("utf-16-le")) // 2 > _TG_MAX:
            formatted = truncate_message(formatted, _TG_MAX)[0]
        try:
            msg = await self._bot.send_message(
                chat_id=_to_chat_id(chat_id),
                text=formatted,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return str(msg.message_id)
        except BadRequest as exc:
            if "parse" in str(exc).lower():
                try:
                    msg = await self._bot.send_message(
                        chat_id=_to_chat_id(chat_id),
                        text=strip_mdv2(formatted),
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                    return str(msg.message_id)
                except Exception:
                    logger.exception("[telegram/outbound] send_card plain fallback failed")
                    return None
            logger.warning("[telegram/outbound] send_card failed: %s", exc)
            return None
        except Exception:
            logger.exception("[telegram/outbound] send_card crashed")
            return None

    async def edit_message(
        self, chat_id: str, message_id: str,
        text: str, reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        if self._bot is None:
            return False
        try:
            await self._bot.edit_message_text(
                chat_id=_to_chat_id(chat_id),
                message_id=int(message_id),
                text=format_message(text),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True
        except BadRequest as exc:
            # "message is not modified" 等视为成功；解析错回退纯文本
            low = str(exc).lower()
            if "not modified" in low:
                return True
            if "parse" in low:
                try:
                    await self._bot.edit_message_text(
                        chat_id=_to_chat_id(chat_id),
                        message_id=int(message_id),
                        text=strip_mdv2(format_message(text)),
                        reply_markup=reply_markup,
                    )
                    return True
                except Exception:
                    logger.exception("[telegram/outbound] edit plain fallback failed")
                    return False
            logger.warning("[telegram/outbound] edit failed: %s", exc)
            return False
        except Exception:
            logger.exception("[telegram/outbound] edit crashed")
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if self._bot is None or not message_id:
            return False
        try:
            await self._bot.delete_message(
                chat_id=_to_chat_id(chat_id), message_id=int(message_id),
            )
            return True
        except Exception:
            # 删除失败非致命（消息可能已被删 / 超 48h）
            logger.debug("[telegram/outbound] delete_message non-fatal failure")
            return False

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        """应答 callback_query（消除按钮 loading 圈，可选弹窗文本）。非致命。"""
        if self._bot is None or not callback_id:
            return
        try:
            await self._bot.answer_callback_query(callback_id, text=text or None)
        except Exception:
            logger.debug("[telegram/outbound] answer_callback non-fatal failure")

    async def send_thinking(self, chat_id: str) -> str | None:
        """发 ⏳ 占位消息表示「正在思考」，返回其 message_id（由分支登记到 thinking 表）。"""
        return await self._send_one(chat_id, "⏳ 思考中…", parse=False)

    # --- 内部发送 ---

    async def _send_one(
        self, chat_id: str, text: str, *, parse: bool = True,
    ) -> str | None:
        if self._bot is None:
            logger.warning("[telegram/outbound] bot not initialized, skipping send")
            return None
        try:
            msg = await self._bot.send_message(
                chat_id=_to_chat_id(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2 if parse else None,
                disable_web_page_preview=True,
            )
            return str(msg.message_id)
        except BadRequest as exc:
            if parse and "parse" in str(exc).lower():
                # MarkdownV2 解析失败 → 纯文本回退
                try:
                    msg = await self._bot.send_message(
                        chat_id=_to_chat_id(chat_id),
                        text=strip_mdv2(text),
                        disable_web_page_preview=True,
                    )
                    return str(msg.message_id)
                except Exception:
                    logger.exception("[telegram/outbound] plain fallback failed")
                    return None
            logger.warning("[telegram/outbound] send failed: %s", exc)
            return None
        except Exception:
            logger.exception("[telegram/outbound] send crashed")
            return None

    # --- 事件订阅 handlers ---

    async def _on_execution_completed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        memorial = (
            self._storage.get_memorial(event.memorial_id)
            if event.memorial_id
            else None
        )
        delivery = (memorial.final_output or memorial.result) if memorial else None

        # 先清掉 ⏳ 占位
        await self._clear_thinking(event.memorial_id, chat_id)
        if not delivery:
            return

        body = convert_tables_to_lists(delivery)
        chunks = split_long(body, max_len=_SAFE_CHUNK)
        for idx, chunk in enumerate(chunks):
            if len(chunks) > 1 and idx > 0:
                chunk = f"## 续 {idx + 1}/{len(chunks)}\n\n{chunk}"
            await self.send_text(chat_id, chunk)

    async def _on_execution_failed(self, event: EventEnvelope) -> None:
        chat_id = self._lookup_chat_id(event)
        if not chat_id:
            return
        await self._clear_thinking(event.memorial_id, chat_id)
        reason = (event.payload or {}).get("error", "未知错误")
        await self.send_text(chat_id, f"❌ 执行失败：{reason}")

    async def _clear_thinking(self, memorial_id: str | None, chat_id: str) -> None:
        if not memorial_id:
            return
        pending = self._storage.pop_telegram_thinking(memorial_id)
        if pending and pending.get("message_id"):
            await self.delete_message(
                pending.get("chat_id") or chat_id, pending["message_id"],
            )

    def _lookup_chat_id(self, event: EventEnvelope) -> str | None:
        """反查回执目标 chat_id；实例路由隔离：仅处理本实例敕令 + home 兜底。

        Fallback：
          1. edict.metadata.chat_id（telegram 原生发起）
          2. anchor 反查（web 创建 + /select 切过去）
          3. settings.home_channel（无 channel 的 cron 敕令兜底）
        """
        if not event.edict_id:
            return self._settings.home_channel or None
        edict = self._storage.get_edict(event.edict_id)
        if not edict:
            return self._settings.home_channel or None
        # 实例路由隔离：非本实例的敕令不由本 outbound 投递，避免交叉发送。
        # 存量敕令无 instance_id → 回退 {channel}-default；cron 等无 channel 敕令
        # （inst=None）仍走 home_channel 兜底，不被实例守卫拦截。
        inst = (edict.metadata or {}).get("instance_id")
        if inst is None:
            ch = (edict.metadata or {}).get("channel")
            inst = f"{ch}-default" if ch else None
        if inst is not None and inst != self._instance_id:
            return None  # 非本实例的敕令，不投递
        chat_id = (edict.metadata or {}).get("chat_id")
        if chat_id:
            return chat_id
        anchored = self._storage.list_telegram_chats_anchored_to(
            event.edict_id, instance_id=self._instance_id
        )
        if anchored:
            return anchored[0]
        return self._settings.home_channel or None
