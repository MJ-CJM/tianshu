"""FeishuSettings：从全局 TianshuSettings 抽取飞书相关字段，附启动校验。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    domain: str
    connection_mode: str
    allowed_users: tuple[str, ...]
    home_channel: str
    encrypt_key: str
    verification_token: str
    bot_open_id: str
    bot_name: str
    webhook_path: str
    ws_reconnect_interval: int
    text_batch_delay: float
    dedup_cache_size: int

    @property
    def enabled(self) -> bool:
        """app_id 为空 → 整个机器人不启用，保持向后兼容。"""
        return bool(self.app_id)

    def validate_or_raise(self) -> None:
        """启动检查：v1 单人模式必须配 allowlist 避免误开放。"""
        if not self.enabled:
            return
        if not self.app_secret:
            raise RuntimeError("TIANSHU_FEISHU_APP_SECRET is required when app_id is set")
        # allowlist 空 = 放行任意人（与 hermes 行为一致）；启动时由 FeishuBot 打 warning 提示
        if self.connection_mode not in ("websocket", "webhook"):
            raise RuntimeError(f"invalid connection_mode: {self.connection_mode}")
        if self.domain not in ("feishu", "lark"):
            raise RuntimeError(f"invalid domain: {self.domain}")


def from_global_settings(s) -> FeishuSettings:
    """从 TianshuSettings 构造 FeishuSettings。"""
    allowed = tuple(u.strip() for u in (s.feishu_allowed_users or "").split(",") if u.strip())
    return FeishuSettings(
        app_id=s.feishu_app_id,
        app_secret=s.feishu_app_secret,
        domain=s.feishu_domain,
        connection_mode=s.feishu_connection_mode,
        allowed_users=allowed,
        home_channel=s.feishu_home_channel,
        encrypt_key=s.feishu_encrypt_key,
        verification_token=s.feishu_verification_token,
        bot_open_id=s.feishu_bot_open_id,
        bot_name=s.feishu_bot_name,
        webhook_path=s.feishu_webhook_path,
        ws_reconnect_interval=s.feishu_ws_reconnect_interval,
        text_batch_delay=s.feishu_text_batch_delay,
        dedup_cache_size=s.feishu_dedup_cache_size,
    )
