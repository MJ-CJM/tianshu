"""PolicyProfile — 任务级权限预配（proactive）。

Spec Section 5。在 edict 启动时展开为 edict-scope session rules，
解决长任务被频繁审批打断的问题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from tianshu.tools.hongluisi.policy import (
    NETWORK_DEFAULT,
    NETWORK_OFFLINE,
    NETWORK_RESEARCH,
    NetworkPolicy,
)
from tianshu.tools.policy_store import assert_can_grant, make_session_rule
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
    network: NetworkPolicy = field(default_factory=NetworkPolicy)


# 3 个硬编码模板（Spec Section 5）
BUILTIN_TEMPLATES: dict[str, PolicyProfile] = {
    "safe-explore": PolicyProfile(
        allowed_paths=(),
        allowed_bash_prefixes=(),
        auto_approve_max_tier=ToolTier.T0_READONLY.value,
        template_name="safe-explore",
        network=NETWORK_OFFLINE,
    ),
    "refactor-in-place": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git status", "git diff"),
        auto_approve_max_tier=ToolTier.T1_WORKSPACE.value,
        template_name="refactor-in-place",
        network=NETWORK_DEFAULT,
    ),
    "trusted-automation": PolicyProfile(
        allowed_paths=("**/*",),
        allowed_bash_prefixes=("git ", "pytest", "ruff", "black", "mypy"),
        auto_approve_max_tier=ToolTier.T3_WRITE.value,
        template_name="trusted-automation",
        network=NETWORK_RESEARCH,
    ),
}


# 模块级缓存 —— 运行时由 app.state / API PATCH 更新；resolve_network_for_edict 读
# 启动时 app.py 从 DB 加载一次；PATCH 时更新。
_system_engine_overrides: dict = {
    "fetch_chain": [],       # list[str]; 空数组 = 不覆盖
    "search_provider": None,  # str|None; None = 不覆盖
    "fallback_mode": None,   # str|None; None = 不覆盖
}


def set_system_engine_overrides(
    *,
    fetch_chain: list[str] | None = None,
    search_provider: str | None = None,
    fallback_mode: str | None = None,
) -> None:
    """PATCH 端点和启动加载调用。"""
    if fetch_chain is not None:
        _system_engine_overrides["fetch_chain"] = list(fetch_chain)
    if search_provider is not None or search_provider == "":
        _system_engine_overrides["search_provider"] = search_provider or None
    if fallback_mode is not None or fallback_mode == "":
        _system_engine_overrides["fallback_mode"] = fallback_mode or None


def get_system_engine_overrides() -> dict:
    return {
        "fetch_chain": list(_system_engine_overrides["fetch_chain"]),
        "search_provider": _system_engine_overrides["search_provider"],
        "fallback_mode": _system_engine_overrides["fallback_mode"],
    }


def resolve_network_for_edict(edict: object) -> NetworkPolicy:
    """根据 edict.runtime.policy_profile.template_name 找对应的 NetworkPolicy。

    三种情况：
    1. template_name 命中 BUILTIN_TEMPLATES → 用对应 NetworkPolicy
    2. template_name 为 None / 缺失 / 无效 → fallback 到 refactor-in-place (DEFAULT)
       这是合理的可用默认；想完全离线请显式选 safe-explore
    3. BUILTIN_TEMPLATES 本身都缺（不该发生）→ 退回裸 NetworkPolicy()

    融合系统级 override（优先级：Edict override > 系统默认 > profile 预设）。
    给 NetworkSafetyRule 和 hongluisi/tools.py 共用，防止两处 fallback 不一致。
    """
    runtime = getattr(edict, "runtime", None)
    profile = getattr(runtime, "policy_profile", None) if runtime else None
    tmpl_name = getattr(profile, "template_name", None) if profile else None
    if tmpl_name and tmpl_name in BUILTIN_TEMPLATES:
        base = BUILTIN_TEMPLATES[tmpl_name].network
    else:
        default = BUILTIN_TEMPLATES.get("refactor-in-place")
        base = default.network if default else NetworkPolicy()

    # 合并系统级 override（介于 profile 和 Edict override 之间的优先级）
    overrides = _system_engine_overrides
    sys_chain = overrides.get("fetch_chain") or []
    sys_search = overrides.get("search_provider")
    sys_fallback = overrides.get("fallback_mode")

    # 只覆盖非空字段；保持 dataclass frozen 用 replace
    from dataclasses import replace
    patch: dict = {}
    if sys_chain:
        patch["fetch_engines"] = tuple(sys_chain)
    if sys_search is not None:
        patch["search_provider"] = sys_search
    if sys_fallback is not None:
        patch["fallback_mode"] = sys_fallback
    return replace(base, **patch) if patch else base


async def expand_profile_to_rules(
    profile: PolicyProfile,
    edict: Edict,
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
