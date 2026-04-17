"""DingTalk Bot webhook channel."""

from __future__ import annotations

import logging

import httpx

from tianshu.notifier.channels.base import NotificationChannel

logger = logging.getLogger(__name__)


class DingTalkChannel(NotificationChannel):
    """Send notifications via DingTalk Bot webhook."""

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self._webhook_url = webhook_url
        self._secret = secret

    @property
    def name(self) -> str:
        return "dingtalk"

    async def send(self, message: dict, rendered: str) -> bool:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": message.get("title", "Tianshu Notification"),
                "text": rendered,
            },
        }
        try:
            url = self._webhook_url
            if self._secret:
                url = self._sign_url(url, self._secret)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning("DingTalk webhook returned %s", resp.status_code)
                    return False
            return True
        except Exception:
            logger.exception("DingTalk notification failed")
            return False

    @staticmethod
    def _sign_url(url: str, secret: str) -> str:
        import hashlib
        import hmac
        import time
        import urllib.parse
        from base64 import b64encode

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(b64encode(hmac_code).decode("utf-8"))
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}timestamp={timestamp}&sign={sign}"
