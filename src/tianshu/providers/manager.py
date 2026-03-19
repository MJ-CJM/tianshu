"""ProviderManager — multi-provider routing and selection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.llm import LLMClient
from tianshu.providers.litellm_provider import create_llm_client
from tianshu.providers.protocol import ProviderCapability, ProviderInfo, TaskRequirements

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages multiple LLM providers and routes requests to the best one.

    Wraps ConfigManager — does not replace it.
    Falls back to ConfigManager's active config when no providers are registered.
    """

    def __init__(
        self,
        storage: Storage,
        config_manager: ConfigManager,
    ) -> None:
        self._storage = storage
        self._config_manager = config_manager

    def register(self, info: ProviderInfo) -> None:
        """Register or update a provider."""
        self._storage.save_provider({
            "name": info.name,
            "model": info.model,
            "api_base": info.api_base,
            "capabilities": [c.value for c in info.capabilities],
            "rpm_limit": info.rpm_limit,
            "tpm_limit": info.tpm_limit,
            "status": info.status,
            "priority": info.priority,
            "cost_per_1k_prompt": info.cost_per_1k_prompt,
            "cost_per_1k_completion": info.cost_per_1k_completion,
        })

    def unregister(self, name: str) -> bool:
        return self._storage.delete_provider(name)

    def list_providers(self) -> list[ProviderInfo]:
        rows = self._storage.list_providers()
        return [self._row_to_info(r) for r in rows]

    def get_provider(self, name: str) -> ProviderInfo | None:
        row = self._storage.get_provider(name)
        return self._row_to_info(row) if row else None

    def get_client(
        self,
        requirements: TaskRequirements | None = None,
    ) -> LLMClient:
        """Select the best provider and return an LLMClient.

        Falls back to ConfigManager's active config if no suitable provider found.
        """
        providers = self.list_providers()
        active_providers = [p for p in providers if p.status == "active"]

        if not active_providers or requirements is None:
            return self._fallback_client()

        # Filter by capabilities
        if requirements.capabilities:
            required = set(requirements.capabilities)
            active_providers = [
                p for p in active_providers
                if required.issubset(set(p.capabilities))
            ]

        # Filter out providers that have exceeded RPM/TPM quota
        active_providers = [
            p for p in active_providers
            if self._within_quota(p)
        ]

        if not active_providers:
            return self._fallback_client()

        # Apply routing strategy
        selected = self._select(active_providers, requirements.strategy)
        if not selected:
            return self._fallback_client()

        # Use ConfigManager to get API key for this provider
        state = self._config_manager.state
        return create_llm_client(
            model=selected.model,
            api_key=state.api_key,
            api_base=selected.api_base or state.api_base,
            max_retries=state.max_retries,
            temperature=state.temperature,
            top_p=state.top_p,
            max_tokens=state.max_tokens,
        )

    def _fallback_client(self) -> LLMClient:
        """Create LLMClient from ConfigManager's active config."""
        state = self._config_manager.state
        return LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            max_retries=state.max_retries,
            temperature=state.temperature,
            top_p=state.top_p,
            max_tokens=state.max_tokens,
        )

    def record_usage(self, name: str, tokens: int = 0) -> None:
        """Record a request/token usage against a provider's quota."""
        from datetime import UTC, datetime
        row = self._storage.get_provider(name)
        if not row:
            return
        now = datetime.now(UTC)
        window_start = row.get("rpm_window_start")
        # Reset counters if window expired (1 minute)
        if window_start:
            try:
                ws = datetime.fromisoformat(window_start)
                if (now - ws).total_seconds() >= 60:
                    self._storage.update_provider(name, {
                        "rpm_current": 1,
                        "tpm_current": tokens,
                        "rpm_window_start": now.isoformat(),
                    })
                    return
            except (ValueError, TypeError):
                pass
        else:
            self._storage.update_provider(name, {
                "rpm_current": 1,
                "tpm_current": tokens,
                "rpm_window_start": now.isoformat(),
            })
            return
        # Increment counters within window
        self._storage.update_provider(name, {
            "rpm_current": (row.get("rpm_current") or 0) + 1,
            "tpm_current": (row.get("tpm_current") or 0) + tokens,
        })

    def _within_quota(self, provider: ProviderInfo) -> bool:
        """Check if a provider is within its RPM/TPM quota."""
        if not provider.rpm_limit and not provider.tpm_limit:
            return True
        row = self._storage.get_provider(provider.name)
        if not row:
            return True
        rpm_current = row.get("rpm_current") or 0
        tpm_current = row.get("tpm_current") or 0
        if provider.rpm_limit and rpm_current >= provider.rpm_limit:
            logger.warning("Provider %s RPM quota exceeded: %d/%d", provider.name, rpm_current, provider.rpm_limit)
            return False
        if provider.tpm_limit and tpm_current >= provider.tpm_limit:
            logger.warning("Provider %s TPM quota exceeded: %d/%d", provider.name, tpm_current, provider.tpm_limit)
            return False
        return True

    @staticmethod
    def _select(
        providers: list[ProviderInfo],
        strategy: str,
    ) -> ProviderInfo | None:
        if not providers:
            return None

        if strategy == "cheapest":
            # Sort by prompt cost, lowest first
            return min(
                providers,
                key=lambda p: p.cost_per_1k_prompt or float("inf"),
            )
        elif strategy == "priority":
            # Sort by priority number, lowest first
            return min(providers, key=lambda p: p.priority)
        else:
            # Default: priority
            return min(providers, key=lambda p: p.priority)

    @staticmethod
    def _row_to_info(row: dict) -> ProviderInfo:
        caps = []
        for c in row.get("capabilities", []):
            try:
                caps.append(ProviderCapability(c))
            except ValueError:
                pass
        return ProviderInfo(
            name=row["name"],
            model=row["model"],
            api_base=row.get("api_base"),
            capabilities=caps,
            status=row.get("status", "active"),
            priority=row.get("priority", 100),
            rpm_limit=row.get("rpm_limit"),
            tpm_limit=row.get("tpm_limit"),
            cost_per_1k_prompt=row.get("cost_per_1k_prompt"),
            cost_per_1k_completion=row.get("cost_per_1k_completion"),
        )
