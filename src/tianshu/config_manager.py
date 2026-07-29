"""Runtime LLM configuration manager - thread-safe, immutable state."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from tianshu.config import TianshuSettings

if TYPE_CHECKING:
    from tianshu.providers.registry import ModelProviderRegistry
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_SETTINGS_DEFAULTS = TianshuSettings.model_fields

_DEMO_MODEL_SENTINEL = "mock/tianshu-demo"

# 内部任务槽位：按任务类型指派"配置名"（如廷议用便宜模型）。
# 未配置的槽位一律落全局 active 配置。
TASK_SLOTS = ("court", "memory", "synthesis", "edict_parse")

_AGENT_CONFIG_SETTING_KEY = "agent_config"
_AGENT_CONFIG_TUPLE_FIELDS = {"universe_fitness_weights", "code_variant_evolvable_paths"}


@dataclass(frozen=True)
class LLMConfigState:
    """命名模型指派：name → provider + model + 采样参数。

    api_key 是**运行时解析结果**（从 provider 注册表的加密凭证 / env 引用
    解出），不再持久化到 llm_configs——明文列已由迁移 0020 物理删除。
    """

    name: str
    model: str
    api_key: str
    api_base: str = ""
    max_retries: int = _SETTINGS_DEFAULTS["llm_max_retries"].default
    temperature: float = _SETTINGS_DEFAULTS["llm_temperature"].default
    top_p: float = _SETTINGS_DEFAULTS["llm_top_p"].default
    max_tokens: int = _SETTINGS_DEFAULTS["llm_max_tokens"].default
    enabled: bool = True
    provider_id: str = ""  # model_providers.id；"" = 未关联（demo/legacy）


@dataclass(frozen=True)
class AgentConfigState:
    agent_max_iterations: int = _SETTINGS_DEFAULTS["agent_max_iterations"].default
    agent_timeout_seconds: int = _SETTINGS_DEFAULTS["agent_timeout_seconds"].default
    # 平台级 edict 运行时默认(Q7):创建 edict 未显式指定时用这些打底,表单只覆盖差异。
    # 与 agent_timeout/iterations 一致——内存态,env 设持久值。
    agent_max_concurrency: int = 1
    agent_retry_limit: int = 0
    agent_token_budget: int | None = None
    agent_cost_budget_cny: float | None = None
    skills_char_budget: int = _SETTINGS_DEFAULTS["skills_char_budget"].default
    skill_review_enabled: bool = True
    skill_review_interval: int = 5
    fallback_llm_config_name: str | None = None
    # Skill curator (修撰) — periodic self-optimization of the agent skill library
    skill_curator_enabled: bool = True
    skill_curator_idle_hours: int = 2
    skill_stale_after_days: int = 30
    skill_archive_after_days: int = 90
    skill_curator_prune_builtins: bool = False
    # 前景主导技能学习
    skill_guard_agent_created: bool = True
    skill_iterate_min_success_rate: float = 0.5
    skill_iterate_min_usage: int = 3
    # 修撰效果门（迭代 6,ADR-0007）：修撰后须过配对评估提升才生效。默认关(保守 +
    # 依赖 eval 装配 + 子进程 skills 重定向路径需实机验证);开启后未过门的修撰不激活(fail-safe)。
    skill_effect_gate_enabled: bool = False
    skill_effect_gate_margin: float = 0.05
    skill_effect_gate_eval_set_size: int = 10
    # 司礼监·代批（迭代 7,制度补全）：学习用户批红习惯,对低风险 decree 自动代批,解审批疲劳。
    # 默认关(保守 + 防"宦官专权");开启后仍受四道闸约束(低风险类/留痕可撤/急停即停/进京察)。
    silijian_enabled: bool = False
    silijian_max_tier: int = 1  # 仅代批 ≤ T1_WORKSPACE 的低风险工具
    silijian_min_approval_rate: float = 0.9  # 近期人工批红通过率阈值
    silijian_min_samples: int = 10  # 达此人工样本量才敢代批(冷启动不代批)
    # 六科给事中·封驳（迭代 7）：诏令提交时预检——超长封还(入站防护)、成本预估超阈自动置
    # plan_review 联动票拟(D9,非硬拒,升级为人工规划审批)。默认开(只升级不删事,非破坏)。
    liuke_enabled: bool = True
    liuke_max_goal_chars: int = 8000  # 目标超此长度直接封还(zeroclaw body 上限类比)
    liuke_cost_threshold_cny: float = 5.0  # 预估/声明成本超此则升 plan_review
    # 平行位面（parallel universe）
    parallel_universe_enabled: bool = False
    universe_min_samples: int = 20
    universe_promote_margin: float = 0.05
    universe_auto_promote: bool = False
    universe_evolver_idle_hours: int = 2
    # fitness 权重（success/cost/audit/retry/feedback）
    universe_fitness_weights: tuple[float, ...] = (0.4, 0.15, 0.2, 0.1, 0.15)
    # Phase 2 / 2b — 代码变体
    code_variant_enabled: bool = False
    code_variant_evolvable_paths: tuple[str, ...] = (
        "src/tianshu/persona/selector.py",
        "src/tianshu/planner/",
        "src/tianshu/tools/",
    )
    code_variant_auto_promote: bool = False
    code_variant_sandbox_timeout_s: int = 900
    code_variant_eval_set_size: int = 20
    code_variant_eval_budget_cny: float = 20.0
    code_variant_auto_propose: bool = False
    code_variant_daily_propose_quota: int = 2
    # 客卿（外聘 coding agent，外臣——见 domain-model「执行主体本体论」）治理默认。
    # 敕令未指定时打底;网关/预算/白名单是天枢对下游客卿的治理策略。内存态,env 设持久值。
    # per-客卿默认模型 {backend: "provider/id:thinking"};每个客卿 provider 各异
    # (pi=GLM/claude-code=anthropic/codex=openai…),故按客卿分设。空=交该客卿自身登录默认。
    keqing_default_models: dict[str, str] = field(default_factory=dict)
    keqing_gateway_enabled: bool = False  # 走凭证网关(OpenShell:raw key 不进客卿进程)
    keqing_per_run_budget_cny: float = 0.0  # per-run 预算,0=沿用全局护栏
    keqing_model_allowlist: str = ""  # 逗号分隔;网关模式下约束可用模型
    # 内部任务槽位 → 配置名（TASK_SLOTS）；经 app_settings 持久化。
    task_slots: dict[str, str] = field(default_factory=dict)


class ConfigManager:
    def __init__(
        self,
        initial: LLMConfigState,
        agent_config: AgentConfigState | None = None,
        storage: Storage | None = None,
        runtime_override: LLMConfigState | None = None,
        model_registry: ModelProviderRegistry | None = None,
    ) -> None:
        self._storage = storage
        self._agent_config: AgentConfigState = agent_config or AgentConfigState()
        self._lock = threading.Lock()
        # provider 注册表：key 解析（加密凭证/$ENV/profile env）与自动供给。
        # None 时退化为纯内存语义（单测/脚本），api_key 按字面值使用。
        self._registry = model_registry
        # demo 等 runtime-only 档位：掩蔽全部解析路径（state/get_config），
        # 但绝不写入 llm_configs，也不影响 list_configs 的持久化视图——
        # 退出该档位后 live 行为原样恢复。
        self._runtime_override = runtime_override

        # Load from DB first; seed with initial if DB is empty
        self._configs: dict[str, LLMConfigState] = {}
        self._active_name: str = initial.name

        if storage:
            self._load_agent_config()
            self._load_from_db()

        if not self._configs:
            # No persisted configs — use initial as seed（env 种子经自动供给转译）
            state = self._provisioned_state(initial)
            self._configs[state.name] = state
            self._active_name = state.name
            self._persist(state, is_active=True)

    def _load_from_db(self) -> None:
        """Load all configs from SQLite into memory；api_key 运行时解析填充。"""
        if not self._storage:
            return
        rows = self._storage.list_llm_configs()
        for row in rows:
            state = LLMConfigState(
                name=row["name"],
                model=row["model"],
                api_key=row.get("api_key", "") or "",
                api_base=row.get("api_base", ""),
                max_retries=row.get("max_retries", 3),
                temperature=row.get("temperature", 0.7),
                top_p=row.get("top_p", 1.0),
                max_tokens=row.get("max_tokens", 4096),
                enabled=bool(row.get("enabled", 1)),
                provider_id=row.get("provider_id", "") or "",
            )
            if state.provider_id and self._registry is not None:
                key, _source = self._registry.resolve_key(state.provider_id)
                if key:
                    state = replace(state, api_key=key)
            self._configs[state.name] = state
            if row.get("is_active"):
                self._active_name = state.name

    def _persist(self, state: LLMConfigState, *, is_active: bool | None = None) -> None:
        """Write a single config to DB（api_key 永不落盘）。"""
        if not self._storage:
            return
        if is_active is None:
            is_active = state.name == self._active_name
        self._storage.save_llm_config(
            {
                "name": state.name,
                "model": state.model,
                "provider_id": state.provider_id,
                "api_base": state.api_base,
                "max_retries": state.max_retries,
                "temperature": state.temperature,
                "top_p": state.top_p,
                "max_tokens": state.max_tokens,
                "enabled": state.enabled,
                "is_active": is_active,
            }
        )

    # --- provider 自动供给（key 写穿注册表）---

    def _provisioned_state(self, state: LLMConfigState) -> LLMConfigState:
        """确保配置挂到 provider 实例上；api_key 写穿注册表并替换为解析结果。"""
        if self._registry is None or state.model == _DEMO_MODEL_SENTINEL:
            return state
        provider_id, resolved = self._provision_key(
            model=state.model,
            api_base=state.api_base,
            api_key=state.api_key,
            provider_id=state.provider_id,
        )
        return replace(state, provider_id=provider_id, api_key=resolved)

    def _provision_key(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        provider_id: str,
        clear_if_empty: bool = False,
    ) -> tuple[str, str]:
        """返回 (provider_id, 解析后的 api_key)。

        - 已关联 provider：key 写到该 provider（'$ENV:NAME' 引用原样支持）。
        - 未关联：按 (model, api_base) 匹配 profile，找同 profile+端点的现有
          provider 或新建一个，再落 key。
        - clear_if_empty：显式传空 key（更新写面的清除意图）时清掉 provider
          的 key 引用；创建/种子路径的空 key 只表示"复用 provider 现有 key"。
        - vault 缺失且给了明文 key：不阻断——key 只保留在内存（本次进程可用），
          告警提示补设 TIANSHU_SECRET_MASTER_KEY。
        """
        assert self._registry is not None
        registry = self._registry
        if not provider_id or registry.get_provider_row(provider_id) is None:
            provider_id = self._find_or_create_provider(model, api_base)
        if not provider_id:
            return "", api_key
        if api_key or clear_if_empty:
            try:
                registry.set_key(provider_id, api_key)
            except ValueError as exc:
                logger.warning(
                    "[llm-config] key 写入注册表失败（%s）；本次进程内仍可用，但重启后需要重新录入",
                    exc,
                )
                return provider_id, api_key
        resolved, _source = registry.resolve_key(provider_id)
        return provider_id, resolved or api_key

    def _find_or_create_provider(self, model: str, api_base: str) -> str:
        from tianshu.providers.profiles import (
            CUSTOM_PROFILE_ID,
            custom_profile,
            match_profile_for_config,
        )

        assert self._registry is not None
        registry = self._registry
        profile = match_profile_for_config(model, api_base) or custom_profile()
        target_base = (api_base or "").strip() or profile.default_base_url
        for row in self._storage.list_model_providers() if self._storage else []:
            if row["profile_id"] != profile.id:
                continue
            effective = row.get("base_url") or profile.default_base_url
            if effective == target_base:
                return str(row["id"])
        stored_base = "" if target_base == profile.default_base_url else target_base
        base_id = profile.id
        if profile.id == CUSTOM_PROFILE_ID:
            import hashlib

            base_id = f"custom-{hashlib.sha256(target_base.encode()).hexdigest()[:8]}"
        provider_id = base_id
        suffix = 2
        while self._storage is not None and self._storage.get_model_provider(provider_id):
            provider_id = f"{base_id}-{suffix}"
            suffix += 1
        try:
            registry.create_provider(
                profile_id=profile.id, provider_id=provider_id, base_url=stored_base
            )
        except ValueError as exc:
            logger.warning("[llm-config] provider 自动供给失败：%s", exc)
            return ""
        return provider_id

    def refresh_resolved_keys(self) -> None:
        """provider key 变更后重解析内存态（API 层在 key 写面调用）。"""
        if self._registry is None:
            return
        with self._lock:
            for name, cfg in list(self._configs.items()):
                if not cfg.provider_id:
                    continue
                key, _source = self._registry.resolve_key(cfg.provider_id)
                if key and key != cfg.api_key:
                    self._configs[name] = replace(cfg, api_key=key)

    @property
    def state(self) -> LLMConfigState:
        if self._runtime_override is not None:
            return self._runtime_override
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
            state = self._configs.get(name)
        if state is None:
            # 掩蔽只替换内容、不伪造存在性：不存在的名字仍返回 None，
            # 否则 persona llm_config_name 存在性校验会被静默禁用。
            return None
        if self._runtime_override is not None:
            # persona/named 覆盖不得从 runtime 档位逃逸回 live 网络
            return self._runtime_override
        return state

    @property
    def runtime_locked(self) -> bool:
        """runtime 档位（demo）掩蔽生效时，provider 配置写面必须只读。"""
        return self._runtime_override is not None

    def add_config(self, state: LLMConfigState) -> None:
        with self._lock:
            if state.name in self._configs:
                raise ValueError(f"Config '{state.name}' already exists")
            state = self._provisioned_state(state)
            self._configs[state.name] = state
            self._persist(state)

    def update_config(self, name: str, **kwargs: object) -> LLMConfigState:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            new_state = self._update_locked(name, **kwargs)
            self._persist(new_state)
            return new_state

    def delete_config(self, name: str) -> None:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            if name == self._active_name:
                raise ValueError("Cannot delete the active config")
            if len(self._configs) <= 1:
                raise ValueError("Cannot delete the last config")
            del self._configs[name]
        if self._storage:
            self._storage.delete_llm_config(name)

    def set_active(self, name: str) -> None:
        with self._lock:
            if name not in self._configs:
                raise KeyError(f"Config '{name}' not found")
            self._active_name = name
        if self._storage:
            self._storage.set_active_llm_config(name)

    def _update_locked(self, name: str, **kwargs: object) -> LLMConfigState:
        current = self._configs[name]
        model = str(kwargs.get("model", current.model))
        api_base = str(kwargs.get("api_base", current.api_base))
        provider_id = str(kwargs.get("provider_id", current.provider_id) or "")
        api_key_update = kwargs.get("api_key")
        resolved_key = current.api_key
        if api_key_update is not None:
            api_key_update = str(api_key_update)
            if self._registry is not None and model != _DEMO_MODEL_SENTINEL:
                provider_id, resolved_key = self._provision_key(
                    model=model,
                    api_base=api_base,
                    api_key=api_key_update,
                    provider_id=provider_id,
                    clear_if_empty=True,
                )
            else:
                resolved_key = api_key_update
        new_fields = {
            "name": current.name,
            "model": model,
            "api_key": resolved_key,
            "api_base": api_base,
            "max_retries": kwargs.get("max_retries", current.max_retries),
            "temperature": kwargs.get("temperature", current.temperature),
            "top_p": kwargs.get("top_p", current.top_p),
            "max_tokens": kwargs.get("max_tokens", current.max_tokens),
            "enabled": kwargs.get("enabled", current.enabled),
            "provider_id": provider_id,
        }
        self._configs[name] = LLMConfigState(**new_fields)
        return self._configs[name]

    @property
    def agent_config(self) -> AgentConfigState:
        with self._lock:
            return self._agent_config

    def update_agent_config(self, **kwargs: object) -> AgentConfigState:
        from dataclasses import asdict, fields

        with self._lock:
            valid = {f.name for f in fields(AgentConfigState)}
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            self._agent_config = replace(self._agent_config, **filtered)
            # 全量持久化到 app_settings：用户改过的运行参数重启不丢。
            if self._storage is not None:
                try:
                    self._storage.set_app_setting(
                        _AGENT_CONFIG_SETTING_KEY, asdict(self._agent_config)
                    )
                except Exception:  # noqa: BLE001 - 持久化失败不阻断运行态更新
                    logger.exception("[agent-config] persist to app_settings failed")
            return self._agent_config

    def _load_agent_config(self) -> None:
        """启动时用 app_settings 里的用户态覆盖 env 默认（持久优先）。"""
        from dataclasses import fields

        if self._storage is None:
            return
        try:
            data = self._storage.get_app_setting(_AGENT_CONFIG_SETTING_KEY)
        except Exception:  # noqa: BLE001 - 表缺失/损坏时保持 env 默认
            logger.exception("[agent-config] load from app_settings failed")
            return
        if not isinstance(data, dict):
            return
        valid = {f.name for f in fields(AgentConfigState)}
        filtered: dict[str, object] = {}
        for key, value in data.items():
            if key not in valid:
                continue
            if key in _AGENT_CONFIG_TUPLE_FIELDS and isinstance(value, list):
                value = tuple(value)
            filtered[key] = value
        if filtered:
            self._agent_config = replace(self._agent_config, **filtered)

    @staticmethod
    def mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]
