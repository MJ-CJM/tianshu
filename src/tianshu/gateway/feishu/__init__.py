"""Feishu (Lark) 机器人接入。

``FeishuBot`` 惰性导出（PEP 562）：这个包里只有机器人本体需要 ``lark_oapi``（feishu
extra），而 ``settings`` 是纯 dataclass、属核心配置视图。此前 ``FeishuBot`` 直接住在
``__init__`` 里并在模块级 import 了一串依赖 lark 的子模块，于是核心发行物
(``tianshu[cli]``) 里 ``from tianshu.gateway.feishu.settings import ...`` 也会连坐——
``GET /api/tongzheng/channels/feishu`` 直接 ModuleNotFoundError 500。

机器人实现见 ``bot.py``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tianshu.gateway.feishu.bot import FeishuBot

__all__ = ["FeishuBot"]


def __getattr__(name: str) -> Any:
    if name == "FeishuBot":
        from tianshu.gateway.feishu.bot import FeishuBot

        return FeishuBot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
