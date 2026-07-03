"""通道无关的共享异常类型，feishu/telegram gateway 分支代码共用。"""

from __future__ import annotations


class EdictBusyError(RuntimeError):
    """敕令仍有 active memorial，无法立即 follow_up。caller 应向用户提示。

    原定义于 gateway/feishu/edict_bridge.py（EdictBridge 抛出，telegram 分支
    代码一直跨通道直接 import）；本批（B3-T1 批 C）上移 core，feishu 原位置
    留 re-export 保持向后兼容。
    """


__all__ = ["EdictBusyError"]
