"""API request/response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import EdictStatus


class EdictScheduleRequest(BaseModel):
    type: str = "immediate"
    cron: str | None = None
    at: str | None = None


class EdictRuntimeRequest(BaseModel):
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    max_iterations: int | None = Field(default=None, ge=1, le=200)
    max_concurrency: int | None = Field(default=None, ge=1, le=8)
    retry_limit: int | None = Field(default=None, ge=0, le=10)
    token_budget: int | None = None
    cost_budget_cny: float | None = None
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
    schedule: EdictScheduleRequest | None = None
    priority: str | None = None
    review_policy: str | None = None
    constraints: list[str] | None = None
    output_format: str | None = None
    runtime: EdictRuntimeRequest | None = None
    assigned_persona_id: str | None = None
    planner_persona_id: str | None = None
    plan_review: bool = False
    # 长任务 outer loop（None = 走老路径单回合 agent）
    acceptance: AcceptanceCriteria | None = None
    execution_profile: Literal["foreground", "checkpointed", "background"] = "foreground"


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

    memorial_id: str = Field(min_length=1)
    action: Literal["approve", "reject"]
    comment: str | None = None
    actor: str = "human"
    grant_scope: Literal["once", "edict", "always"] | None = None
    grant_reason: str | None = None


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
    skills_char_budget: int
    skill_review_enabled: bool
    skill_review_interval: int
    fallback_llm_config_name: str | None


class AgentConfigUpdateRequest(BaseModel):
    agent_max_iterations: int | None = Field(default=None, ge=1, le=200)
    agent_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    skills_char_budget: int | None = Field(default=None, ge=1000, le=500000)
    skill_review_enabled: bool | None = None
    skill_review_interval: int | None = Field(default=None, ge=1, le=100)
    fallback_llm_config_name: str | None = None
