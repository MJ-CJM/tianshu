"""Feishu (Lark) Bot webhook channel.

DEPRECATED (2026-04-28): 新部署应配置 TIANSHU_FEISHU_APP_ID/SECRET 启用 app bot 模式
(gateway.feishu 子包)，它通过 lark-oapi 直接发消息，不再需要 incoming webhook URL。
本文件保留以兼容旧部署。当 app_id 未配时仍然生效。
"""

from __future__ import annotations

import logging

import httpx

from tianshu.notifier.channels.base import NotificationChannel

logger = logging.getLogger(__name__)


class FeishuChannel(NotificationChannel):
    """Send notifications via Feishu Bot webhook."""

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "feishu"

    async def send(self, message: dict, rendered: str) -> bool:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": message.get("title", "Tianshu Notification"),
                    },
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": rendered,
                    }
                ],
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._webhook_url, json=payload)
                if resp.status_code >= 400:
                    logger.warning("Feishu webhook returned %s", resp.status_code)
                    return False
            return True
        except Exception:
            logger.exception("Feishu notification failed")
            return False
