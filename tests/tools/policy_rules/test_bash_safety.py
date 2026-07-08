"""BashSafetyRule 分段风险分级(迭代 3「深防御」)——绕过洞红队用例。

核心锚点:白名单前缀 + 分号藏危险命令(`git log; rm -rf /`)必须 deny,
旧的 startswith 逻辑会误放行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tianshu.tools.policy_rules.bash_safety import BashSafetyRule
from tianshu.tools.types import ToolTier


def _ctx(command: str, prefixes: tuple[str, ...] = ()) -> SimpleNamespace:
    profile = SimpleNamespace(allowed_bash_prefixes=list(prefixes)) if prefixes else None
    return SimpleNamespace(
        tool_name="shell_exec",
        args={"command": command},
        tool_tier=ToolTier.T4_DANGEROUS,
        edict=SimpleNamespace(runtime=SimpleNamespace(policy_profile=profile)),
        memorial=None,
        workspace_root=None,
        iteration=0,
        recent_calls=(),
    )


async def _verdict(command: str, prefixes: tuple[str, ...] = ()) -> str:
    decision = await BashSafetyRule().evaluate(_ctx(command, prefixes))
    return decision.verdict


class TestDenylist:
    async def test_denylist_direct(self):
        assert await _verdict("rm -rf /") == "deny"
        assert await _verdict("sudo reboot") == "deny"

    async def test_denylist_hidden_in_later_segment(self):
        # 危险命令藏后段:朴素整串子串匹配可能漏,逐段查堵死
        assert await _verdict("echo ok && sudo rm x") == "deny"


class TestWhitelistBypass:
    async def test_prefix_plus_semicolon_bypass_denied(self):
        # ⭐ 核心洞:以白名单前缀开头 + 分号藏危险命令 → 必须 deny(不是 allow)
        assert await _verdict("git log; rm -rf /", ("git ",)) == "deny"

    async def test_all_segments_whitelisted_allowed(self):
        assert await _verdict("git log", ("git ",)) == "allow"
        assert await _verdict("git log && git status", ("git ",)) == "allow"

    async def test_partial_whitelist_requires_approval(self):
        # 一段白名单一段非白名单 → 不全放行 → 审批
        assert await _verdict("git log; ls -la", ("git ",)) == "require_approval"


class TestStructuralRisk:
    async def test_command_substitution_not_allowed(self):
        # 良性替换也不放行(隐藏子命令是白名单看不见的);危险替换会被黑名单更早 deny
        assert await _verdict("git log $(whoami)", ("git ",)) == "require_approval"
        assert await _verdict("git log $(rm -rf /)", ("git ",)) == "deny"

    async def test_redirection_not_allowed(self):
        assert await _verdict("git log > /etc/passwd", ("git ",)) == "require_approval"

    async def test_background_not_allowed(self):
        assert await _verdict("git log &", ("git ",)) == "require_approval"


class TestQuoting:
    async def test_quoted_separator_not_split(self):
        # 引号内分号不切段,整体作为一段匹配白名单
        assert await _verdict("echo 'a; b'", ("echo ",)) == "allow"


class TestBasics:
    async def test_non_bash_tool_abstains(self):
        rule = BashSafetyRule()
        ctx = _ctx("x")
        ctx.tool_name = "read_file"
        assert await rule.evaluate(ctx) is None

    async def test_empty_command_abstains(self):
        rule = BashSafetyRule()
        assert await rule.evaluate(_ctx("   ")) is None

    async def test_no_whitelist_requires_approval(self):
        assert await _verdict("ls -la") == "require_approval"


@pytest.mark.parametrize(
    "cmd",
    ["rm -rf /", "git log; rm -rf /", "echo a; sudo x"],
)
async def test_dangerous_never_allowed(cmd):
    assert await _verdict(cmd, ("git ", "echo ")) in ("deny", "require_approval")
