"""Telegram 连接层：python-telegram-bot Application（长轮询 / webhook）。

镜像 feishu/connection.py 的双模式。归一化 Update → TelegramMessage/TelegramCallback
后交给 Dispatcher。长轮询为默认（自托管无需公网/TLS）；webhook 暴露 FastAPI router。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tianshu.gateway.telegram.dispatcher import (
    Dispatcher,
    TelegramCallback,
    TelegramMessage,
)
from tianshu.gateway.telegram.settings import TelegramSettings

if TYPE_CHECKING:
    from fastapi import APIRouter

logger = logging.getLogger(__name__)

_GROUP_TYPES = ("group", "supergroup")


class TelegramConnection:
    """ptb Application 封装：构建 / 启动（polling 或 webhook）/ 停止。"""

    def __init__(
        self,
        *,
        settings: TelegramSettings,
        dispatcher: Dispatcher,
    ) -> None:
        self._settings = settings
        self._dispatcher = dispatcher
        self._app: Application | None = None
        self._bot_id: int = 0
        self._bot_username: str = ""

    async def start(self) -> None:
        self._app = (
            Application.builder().token(self._settings.bot_token).build()
        )
        self._app.add_handler(MessageHandler(filters.TEXT, self._on_message))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_error_handler(self._on_error)

        await self._app.initialize()
        me = await self._app.bot.get_me()
        self._bot_id = me.id
        self._bot_username = me.username or ""
        logger.info(
            "[telegram] connected as @%s (id=%s) mode=%s",
            self._bot_username, self._bot_id, self._settings.connection_mode,
        )
        await self._app.start()

        if self._settings.connection_mode == "webhook":
            # webhook 模式：清掉旧 webhook 由 set_webhook 重设（URL 拼接交给运维/部署），
            # 这里仅保证 Application 就绪；实际请求经 router 进 process_update。
            return

        # polling 模式：先删 webhook 注册，再起长轮询
        await self._app.bot.delete_webhook(drop_pending_updates=False)
        await self._app.updater.start_polling(
            timeout=self._settings.poll_timeout,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception:
            logger.exception("[telegram] stop failed")
        finally:
            self._app = None

    # --- webhook router ---

    @property
    def router(self) -> APIRouter:
        """webhook 模式：暴露 FastAPI router（校验 secret → process_update）。"""
        from fastapi import APIRouter, Request, Response

        router = APIRouter()

        @router.post(self._settings.webhook_path)
        async def _telegram_webhook(request: Request) -> Response:
            from tianshu.gateway.telegram.security import verify_webhook_secret

            secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if not verify_webhook_secret(secret_header, self._settings.webhook_secret):
                return Response(status_code=403)
            if self._app is None:
                return Response(status_code=503)
            data = await request.json()
            update = Update.de_json(data, self._app.bot)
            await self._app.process_update(update)
            return Response(status_code=200)

        return router

    async def set_webhook(self, url: str) -> bool:
        """注册 webhook URL（运维侧调用；url 需公网 HTTPS，含 webhook_path）。"""
        if self._app is None:
            return False
        return await self._app.bot.set_webhook(
            url=url,
            secret_token=self._settings.webhook_secret,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )

    # --- 归一化 + 分发 ---

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None or update.effective_user is None:
            return
        text = message.text or ""
        chat = message.chat
        chat_type = chat.type
        directed = self._is_directed(message, chat_type)
        text = self._strip_bot_mention(text)
        tmsg = TelegramMessage(
            update_id=str(update.update_id),
            chat_id=str(chat.id),
            chat_type=chat_type,
            sender_id=str(update.effective_user.id),
            text=text.strip(),
            raw=update.to_dict(),
            message_id=str(message.message_id),
            directed=directed,
        )
        await self._dispatcher.handle_message(tmsg)

    async def _on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if query is None or query.from_user is None:
            return
        msg = query.message
        chat_id = str(msg.chat.id) if msg and msg.chat else ""
        message_id = str(msg.message_id) if msg else ""
        cb = TelegramCallback(
            update_id=str(update.update_id),
            callback_id=query.id,
            chat_id=chat_id,
            sender_id=str(query.from_user.id),
            message_id=message_id,
            data=query.data or "",
        )
        await self._dispatcher.handle_callback(cb)

    def _is_directed(self, message, chat_type: str) -> bool:
        """群里是否指向 bot（@bot 或回复 bot）。私聊恒 True。"""
        if chat_type not in _GROUP_TYPES:
            return True
        reply = getattr(message, "reply_to_message", None)
        if reply and reply.from_user and reply.from_user.id == self._bot_id:
            return True
        text = message.text or ""
        for ent in (message.entities or []):
            if ent.type == "mention":
                mention = text[ent.offset: ent.offset + ent.length]
                if mention.lower() == f"@{self._bot_username}".lower():
                    return True
            elif ent.type == "text_mention" and ent.user and ent.user.id == self._bot_id:
                return True
        return False

    def _strip_bot_mention(self, text: str) -> str:
        """去掉群命令里的 @botusername（如 /list@mybot → /list）。"""
        if not self._bot_username:
            return text
        return text.replace(f"@{self._bot_username}", "").replace(
            f"@{self._bot_username.lower()}", ""
        )

    async def _on_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        err = context.error
        name = type(err).__name__ if err else "?"
        if name == "Conflict":
            logger.error(
                "[telegram] 409 Conflict — 另一进程正在用同一 bot token 轮询。"
                "请确保仅一个天枢实例运行该 bot。"
            )
            return
        logger.warning("[telegram] update error: %s: %s", name, err)
