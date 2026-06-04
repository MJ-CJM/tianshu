"""TelegramSettings：从全局 TianshuSettings 抽取 Telegram 相关字段，附启动校验。

与 feishu/settings.py 并列。bot_token 为空 → 整个机器人不启用（向后兼容）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    connection_mode: str           # polling | webhook
    allowed_users: tuple[int, ...]  # 允许的 Telegram user_id
    home_channel: str              # cron / 无源审批兜底 chat_id（群为负数，以 str 存）
    webhook_path: str
    webhook_secret: str            # webhook 模式 X-Telegram-Bot-Api-Secret-Token
    poll_timeout: int
    text_batch_delay: float
    dedup_cache_size: int
    assistant_persona_id: str = "tongzheng"   # 默认通政司
    disable_assistant_mode: bool = False        # 紧急逃生开关
    enable_edict_submission: bool = False       # 助手是否允许在对话中颁敕

    @property
    def enabled(self) -> bool:
        """bot_token 为空 → 整个机器人不启用，保持向后兼容。"""
        return bool(self.bot_token)

    def validate_or_raise(self) -> None:
        """启动检查。"""
        if not self.enabled:
            return
        if self.connection_mode not in ("polling", "webhook"):
            raise RuntimeError(f"invalid connection_mode: {self.connection_mode}")
        if self.connection_mode == "webhook" and not self.webhook_secret:
            raise RuntimeError(
                "TIANSHU_TELEGRAM_WEBHOOK_SECRET is required when connection_mode='webhook'"
            )


def _parse_allowed_users(raw: str) -> tuple[int, ...]:
    """逗号分隔 user_id 字符串 → tuple[int]。非数字项忽略。"""
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            continue
    return tuple(out)


def from_global_settings(s) -> TelegramSettings:
    """从 TianshuSettings 构造 TelegramSettings。"""
    return TelegramSettings(
        bot_token=s.telegram_bot_token,
        connection_mode=s.telegram_connection_mode,
        allowed_users=_parse_allowed_users(s.telegram_allowed_users),
        home_channel=s.telegram_home_channel,
        webhook_path=s.telegram_webhook_path,
        webhook_secret=s.telegram_webhook_secret,
        poll_timeout=s.telegram_poll_timeout,
        text_batch_delay=s.telegram_text_batch_delay,
        dedup_cache_size=s.telegram_dedup_cache_size,
        assistant_persona_id=getattr(s, "telegram_assistant_persona_id", "tongzheng"),
        disable_assistant_mode=getattr(s, "telegram_disable_assistant_mode", False),
        enable_edict_submission=getattr(s, "telegram_enable_edict_submission", False),
    )
