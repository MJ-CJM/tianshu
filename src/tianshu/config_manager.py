"""Runtime LLM configuration manager - thread-safe, immutable state."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tianshu.config import TianshuSettings

if TYPE_CHECKING:
    from tianshu.storage import Storage

_SETTINGS_DEFAULTS = TianshuSettings.model_fields


@dataclass(frozen=True)
class LLMConfigState:
    name: str
    model: str
    api_key: str
    api_base: str = ""
    max_retries: int = _SETTINGS_DEFAULTS["llm_max_retries"].default
    temperature: float = _SETTINGS_DEFAULTS["llm_temperature"].default
    top_p: float = _SETTINGS_DEFAULTS["llm_top_p"].default
    max_tokens: int = _SETTINGS_DEFAULTS["llm_max_tokens"].default
    enabled: bool = True


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


class ConfigManager:
    def __init__(
        self,
        initial: LLMConfigState,
        agent_config: AgentConfigState | None = None,
        storage: Storage | None = None,
    ) -> None:
        self._storage = storage
        self._agent_config: AgentConfigState = agent_config or AgentConfigState()
        self._lock = threading.Lock()

        # Load from DB first; seed with initial if DB is empty
        self._configs: dict[str, LLMConfigState] = {}
        self._active_name: str = initial.name

        if storage:
            self._load_from_db()

        if not self._configs:
            # No persisted configs — use initial as seed
            self._configs[initial.name] = initial
            self._active_name = initial.name
            self._persist(initial, is_active=True)

    def _load_from_db(self) -> None:
        """Load all configs from SQLite into memory."""
        if not self._storage:
            return
        rows = self._storage.list_llm_configs()
        for row in rows:
            state = LLMConfigState(
                name=row["name"],
                model=row["model"],
                api_key=row["api_key"],
                api_base=row.get("api_base", ""),
                max_retries=row.get("max_retries", 3),
                temperature=row.get("temperature", 0.7),
                top_p=row.get("top_p", 1.0),
                max_tokens=row.get("max_tokens", 4096),
                enabled=bool(row.get("enabled", 1)),
            )
            self._configs[state.name] = state
            if row.get("is_active"):
                self._active_name = state.name

    def _persist(self, state: LLMConfigState, *, is_active: bool | None = None) -> None:
        """Write a single config to DB."""
        if not self._storage:
            return
        if is_active is None:
            is_active = state.name == self._active_name
        self._storage.save_llm_config(
            {
                "name": state.name,
                "model": state.model,
                "api_key": state.api_key,
                "api_base": state.api_base,
                "max_retries": state.max_retries,
                "temperature": state.temperature,
                "top_p": state.top_p,
                "max_tokens": state.max_tokens,
                "enabled": state.enabled,
                "is_active": is_active,
            }
        )

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
        from dataclasses import fields, replace

        with self._lock:
            valid = {f.name for f in fields(AgentConfigState)}
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            self._agent_config = replace(self._agent_config, **filtered)
            return self._agent_config

    @staticmethod
    def mask_api_key(key: str) -> str:
        if not key or len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]
