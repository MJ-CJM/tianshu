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
from tianshu.gateway.core.outbound import OutboundEventBase
from tianshu.gateway.telegram.markdown_v2 import (
    format_message,
    strip_mdv2,
    truncate_message,
)
from tianshu.gateway.telegram.settings import TelegramSettings
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


class TelegramOutbound(OutboundEventBase):
    def __init__(
        self,
        *,
        settings: TelegramSettings,
        storage: Storage,
        event_bus: EventBus,
        instance_id: str = "telegram-default",
    ) -> None:
        self._bot: Bot | None = None
        super().__init__(
            settings=settings,
            storage=storage,
            event_bus=event_bus,
            instance_id=instance_id,
            chunk_max_len=_SAFE_CHUNK,
        )

    def rebuild_client(self) -> None:
        """构造/重建 Bot（start() 首次调用，或热加载切 token 时），**不重订阅 EventBus**。"""
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
        self,
        chat_id: str,
        message_id: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
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
                chat_id=_to_chat_id(chat_id),
                message_id=int(message_id),
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
        self,
        chat_id: str,
        text: str,
        *,
        parse: bool = True,
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

    # --- core hooks：事件编排已上移 OutboundEventBase，这里只留 telegram 差异 ---

    def _anchored_chats(self, edict_id: str) -> list[str]:
        return self._storage.list_telegram_chats_anchored_to(
            edict_id, instance_id=self._instance_id
        )

    async def _deliver_chunk(self, chat_id: str, chunk: str, idx: int, total: int) -> None:
        if total > 1 and idx > 0:
            chunk = f"## 续 {idx + 1}/{total}\n\n{chunk}"
        await self.send_text(chat_id, chunk)

    async def _clear_thinking(self, memorial_id: str | None, chat_id: str) -> None:
        """清掉 ⏳ 占位消息。"""
        if not memorial_id:
            return
        pending = self._storage.pop_telegram_thinking(memorial_id)
        if pending and pending.get("message_id"):
            await self.delete_message(
                pending.get("chat_id") or chat_id,
                pending["message_id"],
            )
