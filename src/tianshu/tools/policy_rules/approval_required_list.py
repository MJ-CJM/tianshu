"""ApprovalRequiredListRule — 兼容已有 edict.runtime.approval_required_tools。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision


@dataclass
class ApprovalRequiredListRule:
    rule_id: str = "approval_required_list"
    priority: int = 70

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        runtime = getattr(ctx.edict, "runtime", None)
        required = getattr(runtime, "approval_required_tools", None) or []
        if ctx.tool_name not in required:
            return None
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason="tool listed in edict.runtime.approval_required_tools",
        )
