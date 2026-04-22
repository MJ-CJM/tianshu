"""Built-in policy rules. Spec Section 3."""

from tianshu.tools.policy_rules.approval_required_list import ApprovalRequiredListRule
from tianshu.tools.policy_rules.bash_safety import BashSafetyRule
from tianshu.tools.policy_rules.default_tier import DefaultTierRule
from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule
from tianshu.tools.policy_rules.tier_escalation import TierEscalationRule
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule

__all__ = [
    "TierEscalationRule",
    "WorkspaceBoundaryRule",
    "BashSafetyRule",
    "NetworkSafetyRule",
    "ApprovalRequiredListRule",
    "DefaultTierRule",
]


def build_default_rules() -> list:
    """返回 6 条内建规则的默认实例列表（按优先级顺序）。"""
    return [
        TierEscalationRule(),        # 100
        WorkspaceBoundaryRule(),     # 90
        BashSafetyRule(),            # 80
        NetworkSafetyRule(),         # 75
        ApprovalRequiredListRule(),  # 70
        DefaultTierRule(),           # 10
    ]
