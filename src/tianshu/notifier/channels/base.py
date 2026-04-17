"""Base notification channel interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    """Abstract base for notification channels."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier."""
        ...

    @abstractmethod
    async def send(self, message: dict, rendered: str) -> bool:
        """Send a notification. Returns True on success."""
        ...

    async def close(self) -> None:
        """Cleanup resources."""
        pass
