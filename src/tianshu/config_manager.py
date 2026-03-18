"""Runtime LLM configuration manager - thread-safe, immutable state."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfigState:
    name: str
    model: str
    api_key: str
    api_base: str = ""
    max_retries: int = 3
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    enabled: bool = True


@dataclass(frozen=True)
class AgentConfigState:
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    skills_char_budget: int = 30000


class ConfigManager:
    def __init__(
        self,
        initial: LLMConfigState,
        agent_config: AgentConfigState | None = None,
    ) -> None:
        self._configs: dict[str, LLMConfigState] = {initial.name: initial}
        self._active_name: str = initial.name
        self._agent_config: AgentConfigState = agent_config or AgentConfigState()
        self._lock = threading.Lock()

    @property
    def state(self) -> LLMConfigState:
        with self._lock:
            return self._configs[self._active_name]

    def update(self, **kwargs: object) -> LLMConfigState:
        with self._lock:
            return self._update_locked(self._active_name, **kwargs)

    def list_configs(self) -> tuple[list[LLMConfigState], str]:
        with self._lock:
            return list(self._configs.values()), self._active_name

    def get_config(self, name: str) -> LLMConfigState | None:
        with self._lock:
            return self._configs.get(name)

    def add_config(self, state: LLMConfigState) -> None:
        with self._lock:
            if state.name in self._configs:
                raise ValueError(f"Config '{state.name}' already exists")
            self._configs[state.name] = state

    def update_config(self, name: str, **kwargs: object) -> LLMConfigState:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            return self._update_locked(name, **kwargs)

    def delete_config(self, name: str) -> None:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            if name == self._active_name:
                raise ValueError("Cannot delete the active config")
            if len(self._configs) <= 1:
                raise ValueError("Cannot delete the last config")
            del self._configs[name]

    def set_active(self, name: str) -> None:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            self._active_name = name

    def _update_locked(self, name: str, **kwargs: object) -> LLMConfigState:
        current = self._configs[name]
        new_fields = {
            "name": current.name,
            "model": kwargs.get("model", current.model),
            "api_key": kwargs.get("api_key", current.api_key),
            "api_base": kwargs.get("api_base", current.api_base),
            "max_retries": kwargs.get("max_retries", current.max_retries),
            "temperature": kwargs.get("temperature", current.temperature),
            "top_p": kwargs.get("top_p", current.top_p),
            "max_tokens": kwargs.get("max_tokens", current.max_tokens),
            "enabled": kwargs.get("enabled", current.enabled),
        }
        self._configs[name] = LLMConfigState(**new_fields)
        return self._configs[name]

    @property
    def agent_config(self) -> AgentConfigState:
        with self._lock:
            return self._agent_config

    def update_agent_config(self, **kwargs: object) -> AgentConfigState:
        with self._lock:
            current = self._agent_config
            self._agent_config = AgentConfigState(
                agent_max_iterations=kwargs.get(
                    "agent_max_iterations", current.agent_max_iterations
                ),
                agent_timeout_seconds=kwargs.get(
                    "agent_timeout_seconds", current.agent_timeout_seconds
                ),
                skills_char_budget=kwargs.get(
                    "skills_char_budget", current.skills_char_budget
                ),
            )
            return self._agent_config

    @staticmethod
    def mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]
