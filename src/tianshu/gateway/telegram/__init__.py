"""Telegram 机器人接入。

``TelegramBot`` 惰性导出（PEP 562）：同 feishu 包——只有机器人本体需要
``python-telegram-bot``（telegram extra），而 ``settings`` 是纯配置视图、属核心。
包 ``__init__`` 若在模块级 import 依赖 telegram 的子模块，核心发行物里
``GET /api/tongzheng/channels/telegram`` 会直接 ModuleNotFoundError 500。

机器人实现见 ``bot.py``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tianshu.gateway.telegram.bot import TelegramBot

__all__ = ["TelegramBot"]


def __getattr__(name: str) -> Any:
    if name == "TelegramBot":
        from tianshu.gateway.telegram.bot import TelegramBot

        return TelegramBot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
