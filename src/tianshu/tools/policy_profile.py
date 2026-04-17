"""PolicyProfile — 任务级权限预配（proactive）。

Spec Section 5。在 edict 启动时展开为 edict-scope session rules，
解决长任务被频繁审批打断的问题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from tianshu.tools.policy_store import (
    assert_can_grant,
    make_session_rule,
)
from tianshu.tools.types import ToolTier

if TYPE_CHECKING:
    from tianshu.models.edict import Edict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyProfile:
    allowed_paths: tuple[str, ...] = ()
    allowed_bash_prefixes: tuple[str, ...] = ()
    tier_overrides: dict[str, int] = field(default_factory=dict)
    auto_approve_max_tier: int = ToolTier.T1_WORKSPACE.value
    expires_after_seconds: int | None = None
    template_name: str | None = None


# 3 个硬编码模板（Spec Section 5）
BUILTIN_TEMPLATES: dict[str, PolicyProfile] = {
    "safe-explore": PolicyProfile(
        allowed_paths=(),
        allowed_bash_prefixes=(),
        auto_approve_max_tier=ToolTier.T0_READONLY.value,
        template_name="safe-explore",
    ),
    "refactor-in-place": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git status", "git diff"),
        auto_approve_max_tier=ToolTier.T1_WORKSPACE.value,
        template_name="refactor-in-place",
    ),
    "trusted-automation": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git ", "pytest", "ruff", "black", "mypy"),
        auto_approve_max_tier=ToolTier.T2_WRITE.value,
        template_name="trusted-automation",
    ),
}


async def expand_profile_to_rules(
    profile: PolicyProfile,
    edict: "Edict",
    store: object,
) -> int:
    """把 profile 展开为一批 edict-scope session rules，返回创建数量。

    硬约束：只创建 edict scope，不能 always；bash + always 组合拒绝；
    每条 rule source='profile'。
    """
    if profile is None:
        return 0

    created = 0
    expires_after = (
        timedelta(seconds=profile.expires_after_seconds)
        if profile.expires_after_seconds
        else None
    )

    # 1. allowed_paths → edit_file / write_file rules
    for path_glob in profile.allowed_paths:
        for tool in ("edit_file", "write_file"):
            try:
                assert_can_grant(tool, "edict")
            except ValueError:
                continue
            rule = make_session_rule(
                tool_name=tool,
                arg_fingerprint=f"glob:{path_glob}",
                scope="edict",
                source="profile",
                reason=f"preconfigured by policy_profile (template={profile.template_name})",
                edict_id=edict.id,
                expires_after=expires_after,
            )
            await store.create(rule)
            created += 1

    # 2. allowed_bash_prefixes → shell_exec rules（bash + edict 是允许的，但不是 always）
    for prefix in profile.allowed_bash_prefixes:
        rule = make_session_rule(
            tool_name="shell_exec",
            arg_fingerprint=_prefix_to_fingerprint(prefix),
            scope="edict",
            source="profile",
            reason=f"preconfigured bash prefix {prefix!r} (template={profile.template_name})",
            edict_id=edict.id,
            expires_after=expires_after,
        )
        await store.create(rule)
        created += 1

    return created


def _prefix_to_fingerprint(prefix: str) -> str:
    """匹配 tools.policy_store.fingerprint_bash 的格式。"""
    tokens = prefix.strip().split()[:2]
    return "bash:" + " ".join(tokens)
