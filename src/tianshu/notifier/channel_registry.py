"""Channel registry — manage and dispatch to notification channels."""

from __future__ import annotations

import logging
import time

from tianshu.notifier.channels.base import NotificationChannel

logger = logging.getLogger(__name__)

# Default: max 10 messages per minute per channel
DEFAULT_RPM = 10
DEFAULT_WINDOW_SECONDS = 60.0


class ChannelRegistry:
    """Registry for notification channels with per-channel rate limiting."""

    def __init__(self) -> None:
        self._channels: dict[str, NotificationChannel] = {}
        self._rate_limits: dict[str, int] = {}  # channel_name -> max per window
        self._send_log: dict[str, list[float]] = {}  # channel_name -> timestamps

    def register(
        self,
        channel: NotificationChannel,
        rpm: int = DEFAULT_RPM,
    ) -> None:
        self._channels[channel.name] = channel
        self._rate_limits[channel.name] = rpm
        self._send_log[channel.name] = []
        logger.info("Notification channel registered: %s (rpm=%d)", channel.name, rpm)

    def get(self, name: str) -> NotificationChannel | None:
        return self._channels.get(name)

    def list_channels(self) -> list[str]:
        return list(self._channels.keys())

    def _is_rate_limited(self, name: str) -> bool:
        """Check if channel has exceeded its per-minute rate limit."""
        limit = self._rate_limits.get(name, DEFAULT_RPM)
        log = self._send_log.get(name, [])
        now = time.monotonic()
        # Prune entries older than window
        cutoff = now - DEFAULT_WINDOW_SECONDS
        log = [t for t in log if t > cutoff]
        self._send_log[name] = log
        return len(log) >= limit

    def _record_send(self, name: str) -> None:
        if name not in self._send_log:
            self._send_log[name] = []
        self._send_log[name].append(time.monotonic())

    async def send_all(self, message: dict, rendered: str) -> dict[str, bool]:
        """Send to all registered channels. Returns {channel_name: success}."""
        results: dict[str, bool] = {}
        for name, channel in self._channels.items():
            if self._is_rate_limited(name):
                logger.warning("Channel %s rate limited, skipping", name)
                results[name] = False
                continue
            try:
                results[name] = await channel.send(message, rendered)
                self._record_send(name)
            except Exception:
                logger.exception("Channel %s failed", name)
                results[name] = False
        return results

    async def send_to(
        self,
        channel_names: list[str],
        message: dict,
        rendered: str,
    ) -> dict[str, bool]:
        """Send to specific channels."""
        results: dict[str, bool] = {}
        for name in channel_names:
            channel = self._channels.get(name)
            if not channel:
                results[name] = False
                continue
            if self._is_rate_limited(name):
                logger.warning("Channel %s rate limited, skipping", name)
                results[name] = False
                continue
            try:
                results[name] = await channel.send(message, rendered)
                self._record_send(name)
            except Exception:
                logger.exception("Channel %s failed", name)
                results[name] = False
        return results

    async def close_all(self) -> None:
        for channel in self._channels.values():
            await channel.close()
