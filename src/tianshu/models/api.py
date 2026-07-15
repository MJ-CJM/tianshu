"""API request/response models."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import EdictStatus
from tianshu.models.edict import PolicyProfilePayload
from tianshu.models.governance_contract import (
    RequestedGovernanceContractV1,
    acceptance_policy_from_legacy,
)


class EdictRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_concurrency: int | None = Field(default=None, ge=1, le=8)
    retry_limit: int | None = Field(default=None, ge=0, le=10)
    token_budget: int | None = Field(default=None, gt=0)
    cost_budget_cny: float | None = Field(default=None, gt=0)
    approval_required_tools: list[str] = Field(default_factory=list)
    policy_profile: PolicyProfilePayload | None = None
    executor: str | None = Field(default=None, min_length=1)
    executor_model: str | None = Field(default=None, min_length=1)
    fetch_engine_override: str | None = Field(
        default=None,
        description="Pin web_fetch to specific engine: local | jina | firecrawl",
    )
    search_provider_override: str | None = Field(
        default=None,
        description="Pin web_search to specific provider: tavily | jina",
    )
    api_request_hosts: list[str] = Field(
        default_factory=list,
        description="允许 api_request 调用的 host 列表（读方法）",
    )
    api_request_write_hosts: list[str] = Field(
        default_factory=list,
        description="允许 api_request 写方法 (POST/PUT/DELETE/PATCH) 的 host；必须是 api_request_hosts 的子集",
    )


class EdictCreateRequest(BaseModel):
    goal: str = Field(min_length=1)
    title: str | None = None
    context: str | None = None
    idempotency_key: str | None = None
    submitter: str | None = None
    priority: Literal["urgent", "normal", "low"] | None = None
    review_policy: Literal["never", "on_failure", "on_flag", "always"] | None = None
    constraints: list[str] | None = None
    output_format: str | None = None
    runtime: EdictRuntimeRequest | None = None
    assigned_persona_id: str | None = None
    planner_persona_id: str | None = None
    plan_review: bool = False
    # 长任务 outer loop（None = 走老路径单回合 agent）
    acceptance: AcceptanceCriteria | None = None
    execution_profile: Literal["foreground", "checkpointed", "background"] = "foreground"
    governance_contract: RequestedGovernanceContractV1 | None = None

    @model_validator(mode="after")
    def reject_conflicting_legacy_contract_fields(self):
        contract = self.governance_contract
        if contract is None:
            return self

        conflicts: list[str] = []
        if self.goal != contract.objective.goal:
            conflicts.append("goal")
        if "context" in self.model_fields_set and self.context != contract.objective.context:
            conflicts.append("context")
        if (
            "output_format" in self.model_fields_set
            and self.output_format != contract.objective.output_format
        ):
            conflicts.append("output_format")
        if (
            "constraints" in self.model_fields_set
            and tuple(sorted(self.constraints or ())) != contract.objective.constraints
        ):
            conflicts.append("constraints")
        if (
            self.acceptance is not None
            and acceptance_policy_from_legacy(self.acceptance) != contract.acceptance
        ):
            conflicts.append("acceptance")
        if (
            self.review_policy is not None
            and self.review_policy != contract.permissions.review_policy
        ):
            conflicts.append("review_policy")

        runtime = self.runtime
        if runtime is not None:
            executor_options = {item.name: item.value for item in contract.executor.config}
            comparisons = {
                "executor": (runtime.executor, contract.executor.adapter_id),
                "executor_model": (runtime.executor_model, contract.executor.model),
                "timeout_seconds": (runtime.timeout_seconds, contract.budget.wall_clock_seconds),
                "max_iterations": (runtime.max_iterations, contract.budget.max_iterations),
                "max_concurrency": (runtime.max_concurrency, contract.budget.max_concurrency),
                "retry_limit": (runtime.retry_limit, contract.budget.retry_limit),
                "token_budget": (runtime.token_budget, contract.budget.token_limit),
                "approval_required_tools": (
                    tuple(sorted(runtime.approval_required_tools)),
                    contract.permissions.approval_required_tools,
                ),
                "api_request_hosts": (
                    tuple(sorted(runtime.api_request_hosts)),
                    contract.network.allowed_hosts,
                ),
                "api_request_write_hosts": (
                    tuple(sorted(runtime.api_request_write_hosts)),
                    contract.network.write_hosts,
                ),
                "fetch_engine_override": (
                    runtime.fetch_engine_override,
                    executor_options.get("fetch_engine_override"),
                ),
                "search_provider_override": (
                    runtime.search_provider_override,
                    executor_options.get("search_provider_override"),
                ),
            }
            for field, (legacy_value, contract_value) in comparisons.items():
                if field in runtime.model_fields_set and legacy_value != contract_value:
                    conflicts.append(f"runtime.{field}")
            if "cost_budget_cny" in runtime.model_fields_set:
                legacy_cost = (
                    Decimal(str(runtime.cost_budget_cny))
                    if runtime.cost_budget_cny is not None
                    else None
                )
                if legacy_cost != contract.budget.cost_limit_cny:
                    conflicts.append("runtime.cost_budget_cny")
            if "policy_profile" in runtime.model_fields_set:
                profile = runtime.policy_profile
                actual = (
                    profile.template_name if profile else None,
                    tuple(sorted(profile.allowed_paths)) if profile else (),
                    tuple(sorted(profile.allowed_bash_prefixes)) if profile else (),
                    tuple(sorted((profile.tier_overrides if profile else {}).items())),
                    profile.auto_approve_max_tier if profile else 1,
                    profile.expires_after_seconds if profile else None,
                )
                expected = (
                    contract.permissions.policy_profile_name,
                    contract.permissions.allowed_paths,
                    contract.permissions.allowed_bash_prefixes,
                    contract.permissions.tier_overrides,
                    contract.permissions.auto_approve_max_tier,
                    contract.permissions.expires_after_seconds,
                )
                if actual != expected:
                    conflicts.append("runtime.policy_profile")
        if conflicts:
            raise ValueError(
                "conflicting legacy and governance_contract fields: " + ", ".join(conflicts)
            )
        return self


class ParseEdictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FollowUpRequest(BaseModel):
    instruction: str = Field(min_length=1)
    context: str | None = None
    # 2026-04-28: 本次 follow-up 单独覆盖 edict.runtime / acceptance；
    # 留 None = 沿用 edict 原配置；填写即本次覆盖（不影响后续 follow-up）。
    runtime_override: EdictRuntimeRequest | None = None
    acceptance_override: AcceptanceCriteria | None = None


class EdictUpdateRequest(BaseModel):
    title: str | None = None
    goal: str | None = None
    context: str | None = None


class EdictStatusUpdateRequest(BaseModel):
    status: EdictStatus


class DecreeCreateRequest(BaseModel):
    memorial_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    comment: str | None = None
    amended_goal: str | None = None
    actor: str = "human"


class ToolDecisionRequest(BaseModel):
    """Mid-execution tool approval / rejection — targets PolicyHook waits.

    Unlike DecreeCreateRequest, this request does NOT mutate memorial status.
    """

    model_config = ConfigDict(extra="forbid")

    decision_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    memorial_id: str | None = Field(default=None, min_length=1, max_length=128)
    action: Literal["approve", "reject", "guide"]  # guide=驳回+指导(迭代 5)
    comment: str | None = Field(default=None, max_length=2000)
    grant_scope: Literal["once", "edict", "always"] | None = None
    grant_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_target(self):
        if (self.decision_request_id is None) == (self.memorial_id is None):
            raise ValueError("provide exactly one decision_request_id or memorial_id")
        if self.action == "guide" and not (self.comment and self.comment.strip()):
            raise ValueError("guide requires a non-blank comment")
        if self.action != "approve" and self.grant_scope is not None:
            raise ValueError("grant_scope is only valid for approval")
        return self


class LLMConfig(BaseModel):
    name: str
    model: str
    api_key_masked: str
    api_base: str
    max_retries: int
    temperature: float
    top_p: float
    max_tokens: int
    enabled: bool


class LLMConfigCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = ""
    api_base: str = ""
    max_retries: int = Field(default=3, ge=0, le=10)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, ge=1, le=128000)
    enabled: bool = True


class LLMConfigUpdateRequest(BaseModel):
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    max_retries: int | None = Field(default=None, ge=0, le=10)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    enabled: bool | None = None


class LLMConfigListResponse(BaseModel):
    configs: list[LLMConfig]
    active_name: str


class AgentConfig(BaseModel):
    agent_max_iterations: int
    agent_timeout_seconds: int
    agent_max_concurrency: int
    agent_retry_limit: int
    agent_token_budget: int | None
    agent_cost_budget_cny: float | None
    skills_char_budget: int
    skill_review_enabled: bool
    skill_review_interval: int
    fallback_llm_config_name: str | None


class AgentConfigUpdateRequest(BaseModel):
    agent_max_iterations: int | None = Field(default=None, ge=1, le=200)
    agent_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    agent_max_concurrency: int | None = Field(default=None, ge=1, le=8)
    agent_retry_limit: int | None = Field(default=None, ge=0, le=10)
    agent_token_budget: int | None = Field(default=None, ge=1)
    agent_cost_budget_cny: float | None = Field(default=None, ge=0)
    skills_char_budget: int | None = Field(default=None, ge=1000, le=500000)
    skill_review_enabled: bool | None = None
    skill_review_interval: int | None = Field(default=None, ge=1, le=100)
    fallback_llm_config_name: str | None = None
