"""Built-in policy rules. Spec Section 3."""

from tianshu.models.canonical import canonical_sha256
from tianshu.tools.policy_rules.approval_required_list import ApprovalRequiredListRule
from tianshu.tools.policy_rules.bash_safety import BashSafetyRule
from tianshu.tools.policy_rules.default_tier import DefaultTierRule
from tianshu.tools.policy_rules.lark_cli_safety import LarkCliSafetyRule
from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule
from tianshu.tools.policy_rules.persona_tool import PersonaTierRule, PersonaToolAclRule
from tianshu.tools.policy_rules.tier_escalation import TierEscalationRule
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule

__all__ = [
    "PersonaToolAclRule",
    "PersonaTierRule",
    "TierEscalationRule",
    "WorkspaceBoundaryRule",
    "BashSafetyRule",
    "LarkCliSafetyRule",
    "NetworkSafetyRule",
    "ApprovalRequiredListRule",
    "DefaultTierRule",
    "ruleset_digest",
]


def build_default_rules() -> list:
    """返回内建规则的默认实例列表（按优先级顺序）。"""
    return [
        PersonaToolAclRule(),  # 110（官员职权名单契约先于一切审批通道，#40）
        TierEscalationRule(),  # 100
        WorkspaceBoundaryRule(),  # 90
        BashSafetyRule(),  # 80
        LarkCliSafetyRule(),  # 80（不同工具名，不与 bash 冲突）
        NetworkSafetyRule(),  # 75
        ApprovalRequiredListRule(),  # 70
        PersonaTierRule(),  # 15（官员越级奏请，须排在安全 deny 规则之后，#40）
        DefaultTierRule(),  # 10
    ]


def ruleset_digest() -> str:
    """Return the canonical identity of the built-in policy rule ordering."""

    return canonical_sha256({rule.rule_id: rule.priority for rule in build_default_rules()})
