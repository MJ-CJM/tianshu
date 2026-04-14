"""TierEscalationRule — 任务级 tier 提升（只升不降）。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision
from tianshu.tools.types import ToolTier


@dataclass
class TierEscalationRule:
    rule_id: str = "tier_escalation"
    priority: int = 100

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        """读取 edict.runtime.tier_overrides，仅生效 tier > 原 tier 的提升。"""
        runtime = getattr(ctx.edict, "runtime", None)
        overrides = getattr(runtime, "tier_overrides", None) or {}
        if not overrides:
            return None

        override_val = overrides.get(ctx.tool_name)
        if override_val is None:
            return None

        try:
            override_tier = ToolTier(int(override_val))
        except (TypeError, ValueError):
            return None

        if override_tier <= ctx.tool_tier:
            return None  # 不生效（安全单向）

        # tier 提升后，直接要求审批（除非后续规则覆盖）
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"tier escalated from {ctx.tool_tier.name} to {override_tier.name} via edict.runtime.tier_overrides",
            metadata={"original_tier": ctx.tool_tier.name, "escalated_tier": override_tier.name},
        )
