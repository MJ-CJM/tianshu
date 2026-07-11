"""Edict model — the imperial decree that initiates work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator
from ulid import ULID

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import EdictStatus
from tianshu.models.governance_contract import RequestedGovernanceContractV1


class EdictSchedule(BaseModel):
    type: Literal["immediate", "once", "cron", "interval"] = "immediate"
    at: datetime | None = None
    cron: str | None = None
    interval_seconds: int | None = None  # type=="interval": 周期间隔秒数
    timezone: str = "UTC"
    # 周期任务并发去重（Multica 借鉴 #2-A）：skip=上次未结束则跳过本次；allow=放行并发。
    # queue/replace 语义需任务队列基础设施，收敛到 #3 Worker 队列。
    concurrency_policy: Literal["skip", "allow"] = "skip"


class EdictDispatch(BaseModel):
    channels: list[str] = Field(default_factory=list)
    mode: Literal["broadcast", "first"] = "broadcast"
    notify_on_failure: bool = True
    target: str | None = None


class PolicyProfilePayload(BaseModel):
    """Pydantic 版 PolicyProfile — 用于 JSON 序列化。

    运行时 Executor 会把它转成 tools.policy_profile.PolicyProfile（frozen dataclass）
    再调用 expand_profile_to_rules。
    """

    allowed_paths: list[str] = Field(default_factory=list)
    allowed_bash_prefixes: list[str] = Field(default_factory=list)
    tier_overrides: dict[str, int] = Field(default_factory=dict)
    auto_approve_max_tier: int = 1  # T1_WORKSPACE
    expires_after_seconds: int | None = None
    template_name: str | None = None


class EdictRuntime(BaseModel):
    timeout_seconds: int = 300
    max_iterations: int = 20
    max_concurrency: int = 1
    retry_limit: int = 0
    token_budget: int | None = None
    cost_budget_cny: float | None = None
    approval_required_tools: list[str] = Field(default_factory=list)
    # Spec Section 5: Policy Profile 预配权限
    policy_profile: PolicyProfilePayload | None = None
    tier_overrides: dict[str, int] = Field(default_factory=dict)
    # 2026-04-21 web access: 钉死 engine / provider，存在则强制关闭 fallback
    fetch_engine_override: str | None = None
    search_provider_override: str | None = None
    api_request_hosts: tuple[str, ...] = Field(
        default_factory=tuple,
        description="允许 api_request 调用的 host 列表（读方法）",
    )
    api_request_write_hosts: tuple[str, ...] = Field(
        default_factory=tuple,
        description="允许 api_request 写方法 (POST/PUT/DELETE/PATCH) 的 host；必须 ⊆ api_request_hosts",
    )
    # 新增：纯运行时 lifecycle 状态（独立于 EdictStatus）
    lifecycle_phase: Literal["active", "paused", "winding_down", "complete"] = "active"
    # 迭代 3.5「客卿」：执行 backend 选择。"native"=自研引擎(默认);
    # "keqing:<agent>"=派外部 CLI 客卿出工(如 keqing:claude-code / keqing:codex)。
    executor: str = "native"


class Edict(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    title: str = ""
    goal: str
    context: str | None = None
    status: EdictStatus = EdictStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Phase 1 fields
    idempotency_key: str | None = None
    source: Literal["cli", "api", "channel", "scheduler"] = "api"
    submitter: str | None = None
    constraints: list[str] = Field(default_factory=list)
    output_format: str | None = None
    priority: Literal["urgent", "normal", "low"] = "normal"
    review_policy: Literal["never", "on_failure", "on_flag", "always"] = "never"
    schedule: EdictSchedule = Field(default_factory=EdictSchedule)
    dispatch: EdictDispatch | None = None
    runtime: EdictRuntime = Field(default_factory=EdictRuntime)
    assigned_persona_id: str | None = None  # None = 内阁决策; 具体 ID = 直接指派
    planner_persona_id: str | None = (
        None  # None = 全局配置规划; 具体 ID = 指定内阁 persona 的 LLM 配置
    )
    plan_review: bool = False  # True = 规划需人工审批后再执行
    acceptance: AcceptanceCriteria | None = None
    execution_profile: Literal["foreground", "checkpointed", "background"] = "foreground"
    metadata: dict[str, Any] = Field(default_factory=dict)
    governance_contract: RequestedGovernanceContractV1 | None = None

    @model_validator(mode="after")
    def validate_executor_contract_consistency(self) -> Self:
        if (
            self.governance_contract is not None
            and self.runtime.executor != self.governance_contract.executor.adapter_id
        ):
            raise ValueError("runtime.executor conflicts with frozen governance_contract.executor")
        return self


def title_from_goal(goal: str, title: str | None = None) -> str:
    """缺省标题 = goal 前 20 字符（超长加省略号）。"""
    if title:
        return title
    return goal[:20] + "…" if len(goal) > 20 else goal
