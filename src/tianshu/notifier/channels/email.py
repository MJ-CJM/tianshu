"""Email (SMTP) notification channel."""

from __future__ import annotations

import logging

from tianshu.notifier.channels.base import NotificationChannel

logger = logging.getLogger(__name__)


class EmailChannel(NotificationChannel):
    """Send notifications via SMTP email."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        username: str = "",
        password: str = "",
        from_addr: str = "",
        to_addrs: list[str] | None = None,
        use_tls: bool = True,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._password = password
        self._from_addr = from_addr
        self._to_addrs = to_addrs or []
        self._use_tls = use_tls

    @property
    def name(self) -> str:
        return "email"

    async def send(self, message: dict, rendered: str) -> bool:
        if not self._to_addrs:
            logger.warning("Email channel: no recipients configured")
            return False

        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            import aiosmtplib

            msg = MIMEMultipart("alternative")
            msg["Subject"] = message.get("title", "Tianshu Notification")
            msg["From"] = self._from_addr
            msg["To"] = ", ".join(self._to_addrs)
            msg.attach(MIMEText(rendered, "plain"))

            await aiosmtplib.send(
                msg,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._username or None,
                password=self._password or None,
                use_tls=self._use_tls,
            )
            return True
        except ImportError:
            logger.warning("aiosmtplib not installed, email notifications disabled")
            return False
        except Exception:
            logger.exception("Email notification failed")
            return False
