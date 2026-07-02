"""跨渠道的 bot 实例描述（多实例支持）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelInstance:
    """一个 bot 实例：身份 + 渠道 + 启停 + 已构造的 settings。"""

    instance_id: str  # 如 "feishu-default" / "telegram-<ulid>"
    channel_type: str  # "feishu" | "telegram"
    label: str
    enabled: bool
    settings: object  # FeishuSettings | TelegramSettings


def default_instance_id(channel_type: str) -> str:
    """该渠道的默认实例 id（承接历史单 bot 配置与无标记旧敕令）。"""
    return f"{channel_type}-default"
