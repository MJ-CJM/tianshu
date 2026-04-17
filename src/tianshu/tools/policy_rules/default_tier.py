"""DefaultTierRule — 兜底按 tier 裁决。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision
from tianshu.tools.types import ToolTier


@dataclass
class DefaultTierRule:
    rule_id: str = "default_tier"
    priority: int = 10

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        # profile 的 auto_approve_max_tier 允许放行低 tier
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        max_auto = None
        if profile is not None:
            raw_max = getattr(profile, "auto_approve_max_tier", None)
            if raw_max is not None:
                try:
                    max_auto = ToolTier(int(raw_max))
                except (TypeError, ValueError):
                    max_auto = None

        if max_auto is not None and ctx.tool_tier <= max_auto:
            return PolicyDecision(
                verdict="allow",
                rule_id=self.rule_id,
                reason=f"tier {ctx.tool_tier.name} <= profile.auto_approve_max_tier {max_auto.name}",
            )

        if ctx.tool_tier == ToolTier.T3_DANGEROUS:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason="T3_DANGEROUS tool requires approval by default",
            )
        if ctx.tool_tier == ToolTier.T2_WRITE:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason="T2_WRITE tool requires approval by default",
            )

        return PolicyDecision(
            verdict="allow",
            rule_id=self.rule_id,
            reason=f"tier {ctx.tool_tier.name} is safe by default",
        )
