"""ProviderManager — multi-provider routing and selection."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from tianshu.cost.tracker import lookup_pricing
from tianshu.llm import LLMClient
from tianshu.providers.capabilities import ProviderCapability, ProviderInfo, TaskRequirements
from tianshu.providers.litellm_provider import create_llm_client

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager, LLMConfigState
    from tianshu.providers.registry import ModelProviderRegistry
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
        demo_mode: bool = False,
        model_registry: ModelProviderRegistry | None = None,
    ) -> None:
        self._storage = storage
        self._config_manager = config_manager
        # provider 注册表：profile 驱动的 litellm 前缀/usage 方言/prompt caching
        # 与目录定价；None 时保持旧启发式路径（单测/脚本）。
        self._registry = model_registry
        # demo 档位：get_client 全路径只解析 runtime demo 状态（不选 live provider、
        # 不建 Router），sync_all 成为 no-op（providers 表零改动）。
        self._demo_mode = demo_mode
        # 共享 litellm.Router(spec P1-A):configs 指纹变更时懒重建
        self._router: object | None = None
        self._router_fp: tuple | None = None
        self._router_names: set[str] = set()

    def _profile_kwargs(self, cfg: LLMConfigState | None) -> dict:
        """按配置关联的 ProviderProfile 构造 LLMClient 的声明式参数。"""
        if self._registry is None or cfg is None or not cfg.provider_id:
            return {}
        row = self._registry.get_provider_row(cfg.provider_id)
        if row is None:
            return {}
        profile = self._registry.profile_for(row)
        kwargs: dict = {
            "litellm_prefix": profile.litellm_prefix,
            "usage_dialect": profile.cache_usage_field,
            "prompt_caching": profile.supports_prompt_caching,
        }
        # 目录里的真实上下文窗口（agent 压缩阈值用）；带前缀的模型串剥前缀再查
        catalog = self._registry.catalog()
        for candidate in (cfg.model, cfg.model.split("/", 1)[-1]):
            model_info = catalog.get_model(profile, candidate)
            if model_info is not None and model_info.context_window:
                kwargs["context_window"] = model_info.context_window
                break
        return kwargs

    def _effective_config(self, cfg: LLMConfigState | None) -> LLMConfigState | None:
        """把 provider 的端点与 litellm 前缀合成进配置视图（仅消费侧，不落盘）。

        配置关联 provider 且 api_base 留空时，生效端点来自 provider（实例覆盖
        或 profile 默认）——否则裸模型串会被 litellm 按默认路由打到官方端点
        （如 GLM key 发往 api.openai.com 的 401）。Router deployment 与直连
        客户端共用此视图。
        """
        if cfg is None or self._registry is None or not cfg.provider_id:
            return cfg
        row = self._registry.get_provider_row(cfg.provider_id)
        if row is None:
            return cfg
        from dataclasses import replace

        profile = self._registry.profile_for(row)
        updates: dict = {}
        if not cfg.api_base:
            base = row.get("base_url") or profile.default_base_url
            if base:
                updates["api_base"] = base
        if profile.litellm_prefix and "/" not in cfg.model:
            updates["model"] = f"{profile.litellm_prefix}/{cfg.model}"
        return replace(cfg, **updates) if updates else cfg

    def _catalog_default_pricing(self, name: str, model: str) -> tuple[float, float, float]:
        """默认价：配置关联 provider 的目录价优先，未命中落全局目录查询。"""
        if self._registry is not None:
            cfg = self._config_manager.get_config(name)
            if cfg is not None and cfg.provider_id:
                pricing = self._registry.pricing_cny(cfg.provider_id, cfg.model)
                if pricing is not None:
                    return pricing
        return lookup_pricing(model)

    # --- LLM Router(可靠性配置化,见 llm_router.py)---

    def _get_router(self) -> object | None:
        """构建/复用共享 Router;LLM 配置(增删改/启停)变更时自动重建。"""
        from tianshu.llm_router import build_router, configs_fingerprint

        raw_configs, active_name = self._config_manager.list_configs()
        # Router deployment 用生效视图（provider 端点 + litellm 前缀），
        # 指纹同源——provider 端点变更也会触发重建。
        configs = [c for c in (self._effective_config(c) for c in raw_configs) if c is not None]
        fp = configs_fingerprint(configs)
        if fp != self._router_fp:
            self._router = build_router(configs, active_name)
            self._router_fp = fp
            self._router_names = {c.name for c in configs if c.enabled and c.api_key}
            if self._router is not None:
                logger.info(
                    "[llm-router] built with %d deployment(s): %s",
                    len(self._router_names),
                    sorted(self._router_names),
                )
        return self._router

    def _router_kwargs(self, config_name: str) -> dict:
        """构造 LLMClient 的 Router 注入参数;配置名不在 deployments 中则回退直连。"""
        router = self._get_router()
        if router is not None and config_name in self._router_names:
            return {"router": router, "router_model_name": config_name}
        return {}

    # --- Config ↔ Provider sync ---

    def sync_from_config(self, config: LLMConfigState) -> None:
        """Create or update a provider entry from an LLMConfigState."""
        if self._demo_mode:
            # demo 状态 runtime-only：任何同步路径都不得写 providers 表
            logger.warning("demo profile: sync_from_config(%s) ignored", config.name)
            return
        configs, active_name = self._config_manager.list_configs()
        is_active = config.name == active_name
        self._storage.save_provider(
            {
                "name": config.name,
                "model": config.model,
                "api_base": config.api_base or None,
                "capabilities": ["chat", "streaming"],
                "status": "active" if config.enabled else "disabled",
                "priority": 0 if is_active else 100,
            }
        )

    def sync_all(self) -> None:
        """Sync all LLM configs to the providers table."""
        if self._demo_mode:
            # demo 状态是 runtime-only 的：不同步、不删除任何 providers 行
            return
        configs, active_name = self._config_manager.list_configs()
        synced_names: set[str] = set()
        for cfg in configs:
            self._storage.save_provider(
                {
                    "name": cfg.name,
                    "model": cfg.model,
                    "api_base": cfg.api_base or None,
                    "capabilities": ["chat", "streaming"],
                    "status": "active" if cfg.enabled else "disabled",
                    "priority": 0 if cfg.name == active_name else 100,
                }
            )
            synced_names.add(cfg.name)
        # Remove orphaned providers that no longer have a config
        for row in self._storage.list_providers():
            if row["name"] not in synced_names:
                self._storage.delete_provider(row["name"])

    def register(self, info: ProviderInfo) -> None:
        """Register or update a provider."""
        self._storage.save_provider(
            {
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
                "cost_per_1k_cache_read": info.cost_per_1k_cache_read,
            }
        )

    def get_effective_pricing(self, name: str) -> tuple[float, float, float]:
        """计算 provider 当前生效的 3 维价格 (input_miss, input_hit, output)。

        - 自定义字段非 NULL → 用自定义
        - 自定义字段 NULL → 落到 models.dev 目录默认价
        - 部分自定义：未填的字段单独走默认表（每维独立 fallback）
        - cost_per_1k_cache_read NULL 时特殊：默认表的 hit 价生效；
          若默认表也无（fallback hit=miss），则等同于 cost_per_1k_prompt
        """
        info = self.get_provider(name)
        if not info:
            return lookup_pricing("")  # 兜底
        default_miss, default_hit, default_out = self._catalog_default_pricing(name, info.model)
        miss = info.cost_per_1k_prompt if info.cost_per_1k_prompt is not None else default_miss
        out = (
            info.cost_per_1k_completion if info.cost_per_1k_completion is not None else default_out
        )
        if info.cost_per_1k_cache_read is not None:
            hit = info.cost_per_1k_cache_read
        elif info.cost_per_1k_prompt is not None:
            # 用户自定义了 miss 但没填 hit → 默认无折扣（hit = miss）
            hit = miss
        else:
            # 完全没自定义 → 用默认表的 hit
            hit = default_hit
        return (miss, hit, out)

    def get_pricing_with_source(self, name: str) -> dict:
        """带"来源"与"计费方式"标记的生效价（前端展示用）。

        source 取值：
        - "custom"：三维都自定义
        - "default"：三维都未自定义（落 models.dev 目录价）
        - "mixed"：部分自定义
        billing 取值：
        - "per_token"：按量计费（目录价/自定义价）
        - "subscription"：订阅制（coding plan，按 0 记账）
        """
        info = self.get_provider(name)
        if not info:
            return {
                "miss": None,
                "hit": None,
                "out": None,
                "source": "default",
                "billing": "per_token",
            }
        miss, hit, out = self.get_effective_pricing(name)
        custom_count = sum(
            1
            for v in (
                info.cost_per_1k_prompt,
                info.cost_per_1k_cache_read,
                info.cost_per_1k_completion,
            )
            if v is not None
        )
        if custom_count == 0:
            source = "default"
        elif custom_count == 3:
            source = "custom"
        else:
            source = "mixed"
        return {
            "miss": miss,
            "hit": hit,
            "out": out,
            "source": source,
            "billing": self._billing(name),
        }

    def _billing(self, name: str) -> str:
        """配置关联 provider 的计费方式（profile.billing）；未关联按按量。"""
        if self._registry is None:
            return "per_token"
        cfg = self._config_manager.get_config(name)
        if cfg is None or not cfg.provider_id:
            return "per_token"
        row = self._registry.get_provider_row(cfg.provider_id)
        if row is None:
            return "per_token"
        return self._registry.profile_for(row).billing

    def unregister(self, name: str) -> bool:
        if self._demo_mode:
            logger.warning("demo profile: unregister(%s) ignored", name)
            return False
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
        config_name_override: str | None = None,
        model_override: str | None = None,
    ) -> LLMClient:
        """Select the best provider and return an LLMClient.

        Falls back to ConfigManager's active config if no suitable provider found.
        If *config_name_override* is given (e.g. from a persona), use that config directly.
        """
        if self._demo_mode:
            # demo：任何 override/requirements 都解析到 runtime demo 状态，
            # 不触碰 live provider 选择与 Router。
            state = self._config_manager.state
            return create_llm_client(
                model=state.model,
                api_key=state.api_key,
                api_base=state.api_base,
                max_retries=state.max_retries,
                temperature=state.temperature,
                top_p=state.top_p,
                max_tokens=state.max_tokens,
                provider_name=state.name,
            )
        # Persona-level LLM config override
        if config_name_override:
            cfg = self._config_manager.get_config(config_name_override)
            if cfg and cfg.enabled:
                pricing = (
                    self.get_effective_pricing(cfg.name) if self.get_provider(cfg.name) else None
                )
                eff = self._effective_config(cfg) or cfg
                return create_llm_client(
                    model=model_override or eff.model,
                    api_key=eff.api_key,
                    api_base=eff.api_base,
                    max_retries=eff.max_retries,
                    temperature=eff.temperature,
                    top_p=eff.top_p,
                    max_tokens=eff.max_tokens,
                    provider_name=cfg.name,
                    pricing_override=pricing,
                    **self._profile_kwargs(cfg),
                    **({} if model_override else self._router_kwargs(cfg.name)),
                )
            logger.warning(
                "Persona LLM config '%s' not found or disabled, falling back",
                config_name_override,
            )

        providers = self.list_providers()
        active_providers = [p for p in providers if p.status == "active"]

        if not active_providers or requirements is None:
            return self._fallback_client(model_override=model_override)

        # Filter by capabilities
        if requirements.capabilities:
            required = set(requirements.capabilities)
            active_providers = [
                p for p in active_providers if required.issubset(set(p.capabilities))
            ]

        # Filter out providers that have exceeded RPM/TPM quota
        active_providers = [p for p in active_providers if self._within_quota(p)]

        if not active_providers:
            return self._fallback_client(model_override=model_override)

        # Apply routing strategy
        selected = self._select(active_providers, requirements.strategy)
        if not selected:
            return self._fallback_client(model_override=model_override)

        # Use the selected provider's own config for api_key if available
        cfg = self._config_manager.get_config(selected.name)
        fallback = self._config_manager.state
        state = cfg or fallback
        eff = self._effective_config(state) or state
        return create_llm_client(
            model=model_override or selected.model,
            api_key=eff.api_key,
            api_base=selected.api_base or eff.api_base,
            max_retries=eff.max_retries,
            temperature=eff.temperature,
            top_p=eff.top_p,
            max_tokens=eff.max_tokens,
            provider_name=selected.name,
            pricing_override=self.get_effective_pricing(selected.name),
            **self._profile_kwargs(state),
            **({} if model_override else self._router_kwargs(selected.name)),
        )

    def get_client_for_slot(
        self,
        slot: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMClient:
        """按内部任务槽位（config_manager.TASK_SLOTS）取客户端。

        槽位绑"配置名"（agent_config.task_slots）；未配置一律落全局 active。
        temperature/max_tokens 是任务级微调（不影响配置本身）。
        """
        slots = self._config_manager.agent_config.task_slots or {}
        override = slots.get(slot) or None
        client = self.get_client(config_name_override=override)
        if temperature is not None or max_tokens is not None:
            client = client.with_params(temperature=temperature, max_tokens=max_tokens)
        return client

    def _fallback_client(self, *, model_override: str | None = None) -> LLMClient:
        """Create LLMClient from ConfigManager's active config."""
        state = self._config_manager.state
        # active config 通常也对应一个同名 provider；若有则注入 effective pricing
        pricing = self.get_effective_pricing(state.name) if self.get_provider(state.name) else None
        eff = self._effective_config(state) or state
        return LLMClient(
            model=model_override or eff.model,
            api_key=eff.api_key,
            api_base=eff.api_base,
            max_retries=eff.max_retries,
            temperature=eff.temperature,
            top_p=eff.top_p,
            max_tokens=eff.max_tokens,
            provider_name=state.name,
            pricing_override=pricing,
            **self._profile_kwargs(state),
            **({} if model_override else self._router_kwargs(state.name)),
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
                    self._storage.update_provider(
                        name,
                        {
                            "rpm_current": 1,
                            "tpm_current": tokens,
                            "rpm_window_start": now.isoformat(),
                        },
                    )
                    return
            except (ValueError, TypeError):
                pass
        else:
            self._storage.update_provider(
                name,
                {
                    "rpm_current": 1,
                    "tpm_current": tokens,
                    "rpm_window_start": now.isoformat(),
                },
            )
            return
        # Increment counters within window
        self._storage.update_provider(
            name,
            {
                "rpm_current": (row.get("rpm_current") or 0) + 1,
                "tpm_current": (row.get("tpm_current") or 0) + tokens,
            },
        )

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
            logger.warning(
                "Provider %s RPM quota exceeded: %d/%d",
                provider.name,
                rpm_current,
                provider.rpm_limit,
            )
            return False
        if provider.tpm_limit and tpm_current >= provider.tpm_limit:
            logger.warning(
                "Provider %s TPM quota exceeded: %d/%d",
                provider.name,
                tpm_current,
                provider.tpm_limit,
            )
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
            with contextlib.suppress(ValueError):
                caps.append(ProviderCapability(c))
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
            cost_per_1k_cache_read=row.get("cost_per_1k_cache_read"),
        )
