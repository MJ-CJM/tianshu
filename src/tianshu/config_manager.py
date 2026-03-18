"""Runtime LLM configuration manager - thread-safe, immutable state."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfigState:
    model: str
    api_key: str
    api_base: str = ""
    max_retries: int = 3
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    enabled: bool = True


class ConfigManager:
    def __init__(self, initial: LLMConfigState) -> None:
        self._state = initial
        self._lock = threading.Lock()

    @property
    def state(self) -> LLMConfigState:
        with self._lock:
            return self._state

    def update(self, **kwargs: object) -> LLMConfigState:
        with self._lock:
            current = self._state
            new_fields = {
                "model": kwargs.get("model", current.model),
                "api_key": kwargs.get("api_key", current.api_key),
                "api_base": kwargs.get("api_base", current.api_base),
                "max_retries": kwargs.get("max_retries", current.max_retries),
                "temperature": kwargs.get("temperature", current.temperature),
                "top_p": kwargs.get("top_p", current.top_p),
                "max_tokens": kwargs.get("max_tokens", current.max_tokens),
                "enabled": kwargs.get("enabled", current.enabled),
            }
            self._state = LLMConfigState(**new_fields)
            return self._state

    @staticmethod
    def mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]
