"""Edict model — the imperial decree that initiates work."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, model_validator
from ulid import ULID

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import EdictStatus
from tianshu.models.governance_contract import AcceptancePolicyV1, RequestedGovernanceContractV1


class LongRunningScheduleError(ValueError):
    """A schedule cannot safely represent the requested long-running work."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EdictSchedule(BaseModel):
    type: Literal["immediate", "once", "cron", "interval"] = "immediate"
    at: datetime | None = None
    cron: str | None = None
    interval_seconds: int | None = None  # type=="interval": 周期间隔秒数
    timezone: str = "UTC"
    # 周期任务并发去重（Multica 借鉴 #2-A）：skip=上次未结束则跳过本次；allow=放行并发。
    # queue/replace 语义需任务队列基础设施，收敛到 #3 Worker 队列。
    concurrency_policy: Literal["skip", "allow"] = "skip"
    # 进程停机期间错过多个周期时，只补执行最近一次，避免恢复后集中触发。
    misfire_policy: Literal["coalesce"] = "coalesce"

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        timezone: tzinfo = UTC
        if self.timezone.upper() != "UTC":
            try:
                timezone = ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        if self.at is not None and self.at.tzinfo is None:
            self.at = self.at.replace(tzinfo=timezone)
        if self.cron is not None and not croniter.is_valid(self.cron):
            raise ValueError("invalid cron expression")
        if self.interval_seconds is not None and self.interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")
        return self


def validate_long_running_schedule(
    schedule: EdictSchedule,
    *,
    outer_loop: bool,
    execution_profile: str,
) -> None:
    """Fail closed for long-running schedules unsupported by the single-node model."""
    long_running = outer_loop or execution_profile in {"checkpointed", "background"}
    if not long_running:
        return
    if schedule.type in {"cron", "interval"}:
        raise LongRunningScheduleError(
            "recurring_long_running_unsupported",
            "recurring schedules do not support long-running tasks; "
            "use an immediate or once schedule",
        )
    if schedule.concurrency_policy != "skip":
        raise LongRunningScheduleError(
            "long_running_concurrency_must_skip",
            "long-running tasks require concurrency_policy='skip'",
        )


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
    executor_model: str | None = None
    # 对话模式：成功过审后不自动结案，敕令保持未结案、「继续批示」持续可用，
    # 由人工「结案」终止（follow-up 回放历史奏折的多轮上下文，等同与百官连续对话）。
    # 默认开启（2026-07-29 拍板）：人下的敕令天然是多轮的，结案权在人；
    # 机器自动化入口（agent 的 submit_edict 工具等）显式传 False 保持一次性闭环。
    conversation: bool = True


class Edict(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    title: str = ""
    goal: str
    context: str | None = None
    status: EdictStatus = EdictStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Phase 1 fields
    idempotency_key: str | None = None
    # consultation：廷议为了给官员的工具调用一个策略与审计锚点而合成的「议事敕令」
    # （issue #59）。它真实入库以保全证据链，但默认不出现在御书房列表里。
    source: Literal["cli", "api", "channel", "scheduler", "consultation"] = "api"
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
        if (
            self.governance_contract is not None
            and self.runtime.executor_model != self.governance_contract.executor.model
        ):
            raise ValueError(
                "runtime.executor_model conflicts with frozen governance_contract.executor.model"
            )
        return self


def edict_uses_outer_loop(edict: Edict) -> bool:
    contract = edict.governance_contract
    return edict.acceptance is not None or (
        contract is not None and contract.acceptance != AcceptancePolicyV1()
    )


def validate_edict_long_running_schedule(edict: Edict) -> None:
    validate_long_running_schedule(
        edict.schedule,
        outer_loop=edict_uses_outer_loop(edict),
        execution_profile=edict.execution_profile,
    )


def title_from_goal(goal: str, title: str | None = None) -> str:
    """缺省标题 = goal 前 20 字符（超长加省略号）。"""
    if title:
        return title
    return goal[:20] + "…" if len(goal) > 20 else goal
