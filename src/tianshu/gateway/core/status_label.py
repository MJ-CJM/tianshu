"""format_status_label：EdictStatus → 中文标签，feishu/telegram 共享。

原定义于 gateway/feishu/card_builder.py（telegram 一直跨通道直接 import 该
函数）；本批（B3-T1 批 C）上移 core，feishu 原位置留 re-export 保持向后兼容。
"""

from __future__ import annotations

from tianshu.models.common import EDICT_STATUS_LABELS


def format_status_label(status) -> str:
    """统一把 EdictStatus enum / str 渲染成中文友好标签。"""
    value = status.value if hasattr(status, "value") else str(status)
    return EDICT_STATUS_LABELS.get(value, value)


__all__ = ["format_status_label"]
