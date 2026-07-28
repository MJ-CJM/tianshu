"""Versioned, immutable governance contracts shared by every executor."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type CapabilityId = Literal[
    "action_interception",
    "workspace_control",
    "network_control",
    "secret_control",
    "budget_enforcement",
    "decision_bridge",
    "pause",
    "durable_resume",
    "event_fidelity",
    "artifact_export",
    "side_effect_receipts",
    "pre_run_restore_point",
    "governed_apply_merge",
]

CAPABILITY_IDS: tuple[CapabilityId, ...] = (
    "action_interception",
    "workspace_control",
    "network_control",
    "secret_control",
    "budget_enforcement",
    "decision_bridge",
    "pause",
    "durable_resume",
    "event_fidelity",
    "artifact_export",
    "side_effect_receipts",
    "pre_run_restore_point",
    "governed_apply_merge",
)


def _sorted_unique(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


class CanonicalContractModel(BaseModel):
    """Strict immutable model with deterministic JSON and SHA-256."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ObjectiveV1(CanonicalContractModel):
    goal: str = Field(min_length=1)
    context: str | None = None
    output_format: str | None = None
    constraints: tuple[str, ...] = ()

    _normalize_constraints = field_validator("constraints", mode="before")(_sorted_unique)


class AcceptanceCheckV1(CanonicalContractModel):
    kind: Literal["bash", "lint", "rubric"] = "bash"
    name: str = Field(min_length=1)
    command: str | None = None
    rubric: str | None = None
    weight: float = Field(default=1.0, gt=0)
    pass_threshold: float = Field(default=0.8, ge=0, le=1)
    timeout_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.kind in {"bash", "lint"} and not self.command:
            raise ValueError(f"{self.kind} acceptance check requires command")
        if self.kind == "rubric" and not self.rubric:
            raise ValueError("rubric acceptance check requires rubric")
        return self


class AcceptancePolicyV1(CanonicalContractModel):
    checks: tuple[AcceptanceCheckV1, ...] = ()
    critic_persona_ids: tuple[str, ...] = ()
    critic_model: str | None = None
    critic_same_issue_threshold: int = Field(default=2, gt=0)
    critic_strictness: Literal["lenient", "balanced", "strict"] = "lenient"
    escalation_levels: tuple[Literal["L1", "L2", "L3"], ...] = ("L1", "L2", "L3")
    l1_max_rounds: int = Field(default=2, ge=0)
    l2_max_rounds: int = Field(default=1, ge=0)
    l1_thinking_budget: int = Field(default=8000, gt=0)
    l1_model_upgrade: str | None = None
    l2_consultation_personas: tuple[str, ...] = ()
    min_outer_iterations: int = Field(default=1, gt=0)
    max_outer_iterations: int = Field(default=5, gt=0)
    deadline_seconds: int | None = Field(default=None, gt=0)
    on_exhaustion: Literal["escalate", "best_effort", "fail"] = "escalate"
    on_critic_unavailable: Literal["escalate", "skip"] = "skip"
    on_approval_timeout: Literal["fail", "best_effort"] = "best_effort"

    _normalize_critics = field_validator("critic_persona_ids", mode="before")(_sorted_unique)
    _normalize_consultants = field_validator("l2_consultation_personas", mode="before")(
        _sorted_unique
    )

    @field_validator("escalation_levels", mode="before")
    @classmethod
    def normalize_levels(cls, values: Any) -> tuple[str, ...]:
        order = {"L1": 1, "L2": 2, "L3": 3}
        return tuple(sorted(set(values or ()), key=order.__getitem__))

    @model_validator(mode="after")
    def validate_iteration_bounds(self) -> Self:
        if self.min_outer_iterations > self.max_outer_iterations:
            raise ValueError("min_outer_iterations cannot exceed max_outer_iterations")
        return self


class ExecutorOptionV1(CanonicalContractModel):
    name: str = Field(min_length=1)
    value: str


class ExecutorSelectionV1(CanonicalContractModel):
    adapter_id: str = Field(min_length=1)
    model: str | None = None
    config: tuple[ExecutorOptionV1, ...] = ()

    @field_validator("config", mode="after")
    @classmethod
    def validate_unique_config(
        cls, values: tuple[ExecutorOptionV1, ...]
    ) -> tuple[ExecutorOptionV1, ...]:
        if len({item.name for item in values}) != len(values):
            raise ValueError("executor config names must be unique")
        return tuple(sorted(values, key=lambda item: item.name))


class CapabilityRequirementsV1(CanonicalContractModel):
    mandatory: tuple[CapabilityId, ...] = ()
    advisory: tuple[CapabilityId, ...] = ()

    @field_validator("mandatory", "advisory", mode="before")
    @classmethod
    def normalize_capabilities(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted(set(values or ())))

    @model_validator(mode="after")
    def validate_disjoint(self) -> Self:
        overlap = set(self.mandatory) & set(self.advisory)
        if overlap:
            raise ValueError(f"mandatory/advisory capability overlap: {sorted(overlap)}")
        return self


class CapabilityResolutionV1(CanonicalContractModel):
    capability: CapabilityId
    requested_mode: Literal["mandatory", "advisory", "unrequested"]
    state: Literal["enforced", "best_effort", "observed", "unsupported"]
    evidence: tuple[str, ...] = ()

    _normalize_evidence = field_validator("evidence", mode="before")(_sorted_unique)


class CapabilityDegradationV1(CanonicalContractModel):
    capability: CapabilityId
    manifest_state: Literal["enforced", "best_effort", "observed", "unsupported"]
    effective_state: Literal["enforced", "best_effort", "observed", "unsupported"]
    reason: str = Field(min_length=1)


class PermissionPolicyV1(CanonicalContractModel):
    approval_required_tools: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_bash_prefixes: tuple[str, ...] = ()
    tier_overrides: tuple[tuple[str, int], ...] = ()
    auto_approve_max_tier: int = Field(default=1, ge=0, le=3)
    expires_after_seconds: int | None = Field(default=None, gt=0)
    policy_profile_name: str | None = None
    secret_refs: tuple[str, ...] = ()
    review_policy: Literal["never", "on_failure", "on_flag", "always"] = "never"

    _normalize_tools = field_validator("approval_required_tools", mode="before")(_sorted_unique)
    _normalize_paths = field_validator("allowed_paths", mode="before")(_sorted_unique)
    _normalize_prefixes = field_validator("allowed_bash_prefixes", mode="before")(_sorted_unique)
    _normalize_secret_refs = field_validator("secret_refs", mode="before")(_sorted_unique)

    @field_validator("tier_overrides", mode="before")
    @classmethod
    def normalize_tiers(cls, value: Any) -> tuple[tuple[str, int], ...]:
        items = value.items() if isinstance(value, dict) else value or ()
        normalized = tuple(sorted((str(name), int(tier)) for name, tier in items))
        if any(not 0 <= tier <= 3 for _, tier in normalized):
            raise ValueError("tier overrides must be between 0 and 3")
        if len({name for name, _ in normalized}) != len(normalized):
            raise ValueError("tier override names must be unique")
        return normalized


class NetworkPolicyV1(CanonicalContractModel):
    mode: Literal["deny", "allowlist", "unrestricted_requested"] = "deny"
    allowed_hosts: tuple[str, ...] = ()
    write_hosts: tuple[str, ...] = ()
    methods: tuple[str, ...] = ("GET", "HEAD")

    @field_validator("allowed_hosts", "write_hosts", mode="before")
    @classmethod
    def normalize_hosts(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted({str(value).strip().lower() for value in values or () if value}))

    @field_validator("methods", mode="before")
    @classmethod
    def normalize_methods(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted({str(value).strip().upper() for value in values or () if value}))

    @model_validator(mode="after")
    def validate_allowlist(self) -> Self:
        extra = set(self.write_hosts) - set(self.allowed_hosts)
        if extra:
            raise ValueError(f"write_hosts must be a subset of allowed_hosts: {sorted(extra)}")
        if self.mode == "allowlist" and not self.allowed_hosts:
            raise ValueError("allowlist network mode requires allowed_hosts")
        if self.mode == "deny" and (self.allowed_hosts or self.write_hosts):
            raise ValueError("deny network mode cannot include hosts")
        return self

    def downgraded_for_unenforceable(self) -> NetworkPolicyV1:
        """backend 无法强制网络时的等价降级:非 unrestricted 一律降为 unrestricted_requested。

        确定性治理变换(非篡改)——resolve(build effective)与运行时防篡改一致性校验
        (adapters/protocol)须用同一个变换,否则 effective 降级后会与 mapped 契约不匹配。
        """
        if self.mode == "unrestricted_requested":
            return self
        return self.model_copy(
            update={"mode": "unrestricted_requested", "allowed_hosts": (), "write_hosts": ()}
        )


class WorkspacePolicyV1(CanonicalContractModel):
    source_id: str | None = None
    base_revision: str | None = None
    staging_mode: Literal["legacy_shared", "isolated", "ephemeral"] = "legacy_shared"
    apply_mode: Literal["none", "governed"] = "none"
    require_clean_source: bool = False


class BudgetPolicyV1(CanonicalContractModel):
    token_limit: int | None = Field(default=None, gt=0)
    cost_limit_cny: Decimal | None = Field(default=None, gt=0)
    wall_clock_seconds: int = Field(default=300, gt=0)
    max_iterations: int = Field(default=20, gt=0)
    max_concurrency: int = Field(default=1, gt=0)
    retry_limit: int = Field(default=0, ge=0)


class RecoveryPolicyV1(CanonicalContractModel):
    require_restore_point: bool = False
    failure_cleanup: Literal["required", "best_effort", "none"] = "best_effort"
    rollback_on_apply_failure: bool = True


class RequestedGovernanceContractV1(CanonicalContractModel):
    objective: ObjectiveV1
    acceptance: AcceptancePolicyV1 = Field(default_factory=AcceptancePolicyV1)
    executor: ExecutorSelectionV1 = Field(
        default_factory=lambda: ExecutorSelectionV1(adapter_id="native")
    )
    capabilities: CapabilityRequirementsV1 = Field(default_factory=CapabilityRequirementsV1)
    permissions: PermissionPolicyV1 = Field(default_factory=PermissionPolicyV1)
    network: NetworkPolicyV1 = Field(default_factory=NetworkPolicyV1)
    workspace: WorkspacePolicyV1 = Field(default_factory=WorkspacePolicyV1)
    budget: BudgetPolicyV1 = Field(default_factory=BudgetPolicyV1)
    recovery: RecoveryPolicyV1 = Field(default_factory=RecoveryPolicyV1)


class EffectiveGovernanceContractV1(CanonicalContractModel):
    requested_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: ObjectiveV1
    acceptance: AcceptancePolicyV1
    executor: ExecutorSelectionV1
    permissions: PermissionPolicyV1
    network: NetworkPolicyV1
    workspace: WorkspacePolicyV1
    budget: BudgetPolicyV1
    recovery: RecoveryPolicyV1
    executor_manifest_id: str = Field(min_length=1)
    executor_manifest_version: str = Field(min_length=1)
    executor_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_probe_id: str = Field(min_length=1)
    effective_controls: tuple[CapabilityResolutionV1, ...]
    unsupported_advisory: tuple[CapabilityId, ...] = ()
    degradations: tuple[CapabilityDegradationV1, ...] = ()
    resolved_source_id: str | None = None
    resolved_base_revision: str | None = None

    @field_validator("unsupported_advisory", mode="before")
    @classmethod
    def normalize_advisory(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted(set(values or ())))

    @field_validator("effective_controls", mode="after")
    @classmethod
    def validate_controls(
        cls, values: tuple[CapabilityResolutionV1, ...]
    ) -> tuple[CapabilityResolutionV1, ...]:
        capabilities = [value.capability for value in values]
        if set(capabilities) != set(CAPABILITY_IDS) or len(capabilities) != len(CAPABILITY_IDS):
            raise ValueError("effective controls must declare every capability exactly once")
        return tuple(sorted(values, key=lambda value: value.capability))

    def state(
        self, capability: CapabilityId
    ) -> Literal["enforced", "best_effort", "observed", "unsupported"]:
        return next(
            control.state for control in self.effective_controls if control.capability == capability
        )


def acceptance_policy_from_legacy(acceptance: Any | None) -> AcceptancePolicyV1:
    if acceptance is None:
        return AcceptancePolicyV1()
    critic_ids = acceptance.critic.effective_persona_ids()
    return AcceptancePolicyV1(
        checks=tuple(
            AcceptanceCheckV1(
                kind=check.kind,
                name=check.name,
                command=check.command,
                rubric=check.rubric,
                weight=check.weight,
                pass_threshold=check.pass_threshold,
                timeout_seconds=check.timeout_seconds,
            )
            for check in acceptance.checks
        ),
        critic_persona_ids=tuple(critic_ids),
        critic_model=acceptance.critic.model,
        critic_same_issue_threshold=acceptance.critic.same_issue_threshold,
        critic_strictness=acceptance.critic.strictness,
        escalation_levels=tuple(acceptance.escalation.enabled_levels),
        l1_max_rounds=acceptance.escalation.l1_max_rounds,
        l2_max_rounds=acceptance.escalation.l2_max_rounds,
        l1_thinking_budget=acceptance.escalation.l1_thinking_budget,
        l1_model_upgrade=acceptance.escalation.l1_model_upgrade,
        l2_consultation_personas=tuple(acceptance.escalation.l2_consultation_personas),
        min_outer_iterations=acceptance.min_outer_iterations,
        max_outer_iterations=acceptance.max_outer_iterations,
        deadline_seconds=acceptance.deadline_seconds,
        on_exhaustion=acceptance.on_exhaustion,
        on_critic_unavailable=acceptance.on_critic_unavailable,
        on_approval_timeout=acceptance.on_approval_timeout,
    )


def acceptance_policy_to_legacy(policy: AcceptancePolicyV1):
    """Create the legacy execution model without making contracts depend on it."""
    from tianshu.models.acceptance import (
        AcceptanceCriteria,
        CheckSpec,
        CriticSpec,
        EscalationSpec,
    )

    return AcceptanceCriteria(
        checks=[
            CheckSpec(
                kind=check.kind,
                name=check.name,
                command=check.command,
                rubric=check.rubric,
                weight=check.weight,
                pass_threshold=check.pass_threshold,
                timeout_seconds=check.timeout_seconds,
            )
            for check in policy.checks
        ],
        critic=CriticSpec(
            persona_ids=list(policy.critic_persona_ids),
            model=policy.critic_model,
            same_issue_threshold=policy.critic_same_issue_threshold,
            strictness=policy.critic_strictness,
        ),
        escalation=EscalationSpec(
            enabled_levels=list(policy.escalation_levels),
            l1_max_rounds=policy.l1_max_rounds,
            l2_max_rounds=policy.l2_max_rounds,
            l1_thinking_budget=policy.l1_thinking_budget,
            l1_model_upgrade=policy.l1_model_upgrade,
            l2_consultation_personas=list(policy.l2_consultation_personas),
        ),
        min_outer_iterations=policy.min_outer_iterations,
        max_outer_iterations=policy.max_outer_iterations,
        deadline_seconds=policy.deadline_seconds,
        on_exhaustion=policy.on_exhaustion,
        on_critic_unavailable=policy.on_critic_unavailable,
        on_approval_timeout=policy.on_approval_timeout,
    )


class LegacyEdictGovernanceMapper:
    """Pure, deterministic conversion from legacy Edict fields."""

    LEGACY_ADVISORY_CAPABILITIES: ClassVar[tuple[CapabilityId, ...]] = CAPABILITY_IDS

    @classmethod
    def from_edict(
        cls,
        edict: Any,
        *,
        default_workspace_id: str | None = None,
    ) -> RequestedGovernanceContractV1:
        runtime = edict.runtime
        profile = runtime.policy_profile
        hosts = tuple(runtime.api_request_hosts)
        return RequestedGovernanceContractV1(
            objective=ObjectiveV1(
                goal=edict.goal,
                context=edict.context,
                output_format=edict.output_format,
                constraints=tuple(edict.constraints),
            ),
            acceptance=acceptance_policy_from_legacy(edict.acceptance),
            executor=ExecutorSelectionV1(
                adapter_id=runtime.executor,
                model=runtime.executor_model,
                config=tuple(
                    ExecutorOptionV1(name=name, value=value)
                    for name, value in (
                        ("fetch_engine_override", runtime.fetch_engine_override),
                        ("search_provider_override", runtime.search_provider_override),
                    )
                    if value is not None
                ),
            ),
            capabilities=CapabilityRequirementsV1(
                mandatory=(),
                advisory=cls.LEGACY_ADVISORY_CAPABILITIES,
            ),
            permissions=PermissionPolicyV1(
                approval_required_tools=tuple(runtime.approval_required_tools),
                allowed_paths=tuple(profile.allowed_paths) if profile else (),
                allowed_bash_prefixes=tuple(profile.allowed_bash_prefixes) if profile else (),
                tier_overrides=profile.tier_overrides if profile else runtime.tier_overrides,
                auto_approve_max_tier=profile.auto_approve_max_tier if profile else 1,
                expires_after_seconds=profile.expires_after_seconds if profile else None,
                policy_profile_name=profile.template_name if profile else None,
                review_policy=edict.review_policy,
            ),
            network=NetworkPolicyV1(
                mode="allowlist" if hosts else "deny",
                allowed_hosts=hosts,
                write_hosts=tuple(runtime.api_request_write_hosts),
                methods=("DELETE", "GET", "HEAD", "PATCH", "POST", "PUT")
                if runtime.api_request_write_hosts
                else ("GET", "HEAD"),
            ),
            workspace=WorkspacePolicyV1(
                source_id=default_workspace_id,
                staging_mode="legacy_shared",
                apply_mode="none",
            ),
            budget=BudgetPolicyV1(
                token_limit=runtime.token_budget,
                cost_limit_cny=(
                    Decimal(str(runtime.cost_budget_cny))
                    if runtime.cost_budget_cny is not None
                    else None
                ),
                wall_clock_seconds=runtime.timeout_seconds,
                max_iterations=runtime.max_iterations,
                max_concurrency=runtime.max_concurrency,
                retry_limit=runtime.retry_limit,
            ),
            recovery=RecoveryPolicyV1(
                require_restore_point=False,
                failure_cleanup="best_effort",
                rollback_on_apply_failure=True,
            ),
        )

    @classmethod
    def apply_run_overrides(
        cls,
        base: RequestedGovernanceContractV1,
        edict: Any,
        *,
        runtime_overridden: bool,
        acceptance_overridden: bool,
    ) -> RequestedGovernanceContractV1:
        """Map persisted Memorial overrides without mutating the Edict contract."""
        mapped = cls.from_edict(
            edict,
            default_workspace_id=base.workspace.source_id,
        )
        updates: dict[str, Any] = {}
        if runtime_overridden:
            updates.update(
                executor=mapped.executor,
                permissions=mapped.permissions,
                network=mapped.network,
                budget=mapped.budget,
            )
        if acceptance_overridden:
            updates["acceptance"] = mapped.acceptance
        if not updates:
            return base
        return RequestedGovernanceContractV1.model_validate(
            {**base.model_dump(mode="json"), **updates}
        )


__all__ = [
    "CAPABILITY_IDS",
    "AcceptanceCheckV1",
    "AcceptancePolicyV1",
    "BudgetPolicyV1",
    "CapabilityId",
    "CapabilityDegradationV1",
    "CapabilityResolutionV1",
    "CapabilityRequirementsV1",
    "CanonicalContractModel",
    "ExecutorOptionV1",
    "ExecutorSelectionV1",
    "EffectiveGovernanceContractV1",
    "LegacyEdictGovernanceMapper",
    "NetworkPolicyV1",
    "ObjectiveV1",
    "PermissionPolicyV1",
    "RecoveryPolicyV1",
    "RequestedGovernanceContractV1",
    "WorkspacePolicyV1",
    "acceptance_policy_from_legacy",
    "acceptance_policy_to_legacy",
]
