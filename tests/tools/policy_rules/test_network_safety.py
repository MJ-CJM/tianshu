"""NetworkSafetyRule — profile gating for 4 network tools. Spec §7.4."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tianshu.tools.policy_rules.network_safety import NetworkSafetyRule
from tianshu.tools.types import ToolTier


def _ctx(
    tool_name: str,
    args: dict,
    template_name: str | None,
    hosts: tuple[str, ...] = (),
    write_hosts: tuple[str, ...] = (),
) -> SimpleNamespace:
    """Minimal PolicyContext-shaped mock.

    NetworkSafetyRule only reads: tool_name, args, edict.runtime.policy_profile.template_name,
    edict.runtime.api_request_hosts, edict.runtime.api_request_write_hosts.
    """
    return SimpleNamespace(
        tool_name=tool_name,
        args=args,
        tool_tier=ToolTier.T1_WORKSPACE,
        edict=SimpleNamespace(
            runtime=SimpleNamespace(
                policy_profile=SimpleNamespace(template_name=template_name),
                api_request_hosts=hosts,
                api_request_write_hosts=write_hosts,
            ),
        ),
        memorial=None,
        workspace_root=None,
        iteration=0,
        recent_calls=(),
    )


# -- non-network tool -> abstain ----------------------------------------------

async def test_non_network_tool_abstain() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx("shell_exec", {}, "trusted-automation")
    assert await rule.evaluate(ctx) is None


# -- web_fetch ----------------------------------------------------------------

async def test_offline_web_fetch_deny() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx("web_fetch", {"url": "https://example.com"}, "safe-explore")
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert "fetch_not_allowed" in d.reason


async def test_default_web_fetch_pass() -> None:
    """refactor-in-place (NETWORK_DEFAULT) allows web_fetch via local+jina."""
    rule = NetworkSafetyRule()
    ctx = _ctx("web_fetch", {"url": "https://example.com"}, "refactor-in-place")
    assert await rule.evaluate(ctx) is None


# -- web_search ---------------------------------------------------------------

async def test_offline_web_search_deny() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx("web_search", {"query": "x"}, "safe-explore")
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert "search_not_allowed" in d.reason


# -- web_extract (firecrawl only in RESEARCH) ---------------------------------

async def test_default_web_extract_deny() -> None:
    """NETWORK_DEFAULT has no firecrawl → web_extract denied."""
    rule = NetworkSafetyRule()
    ctx = _ctx("web_extract", {"url": "https://example.com"}, "refactor-in-place")
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert "web_extract_not_allowed" in d.reason


async def test_research_web_extract_pass() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx("web_extract", {"url": "https://example.com"}, "trusted-automation")
    assert await rule.evaluate(ctx) is None


# -- api_request --------------------------------------------------------------

async def test_default_api_request_deny() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx(
        "api_request",
        {"url": "https://api.github.com/x"},
        "refactor-in-place",
        hosts=("api.github.com",),
    )
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert "api_request_not_allowed_in_profile" in d.reason


async def test_research_api_request_get_pass() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx(
        "api_request",
        {"url": "https://api.github.com/x", "method": "GET"},
        "trusted-automation",
        hosts=("api.github.com",),
    )
    assert await rule.evaluate(ctx) is None


async def test_research_api_request_host_not_whitelisted() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx(
        "api_request",
        {"url": "https://evil.com/x", "method": "GET"},
        "trusted-automation",
        hosts=("api.github.com",),
    )
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert d.reason == "host_not_whitelisted"
    assert d.metadata["host"] == "evil.com"


async def test_research_api_request_post_approval() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx(
        "api_request",
        {"url": "https://api.github.com/x", "method": "POST"},
        "trusted-automation",
        hosts=("api.github.com",),
        write_hosts=("api.github.com",),
    )
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "require_approval"
    assert d.metadata["host"] == "api.github.com"
    assert d.metadata["method"] == "POST"


async def test_research_api_request_post_write_host_missing() -> None:
    rule = NetworkSafetyRule()
    ctx = _ctx(
        "api_request",
        {"url": "https://api.github.com/x", "method": "POST"},
        "trusted-automation",
        hosts=("api.github.com",),
        write_hosts=(),
    )
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
    assert "write_method_host_not_whitelisted" in d.reason


async def test_api_request_missing_runtime_defaults_safe() -> None:
    """No runtime / no profile → default NetworkPolicy → allow_api_request=False → deny."""
    rule = NetworkSafetyRule()
    ctx = SimpleNamespace(
        tool_name="api_request",
        args={"url": "https://api.github.com/x", "method": "GET"},
        tool_tier=ToolTier.T1_WORKSPACE,
        edict=SimpleNamespace(runtime=None),
        memorial=None,
        workspace_root=None,
        iteration=0,
        recent_calls=(),
    )
    d = await rule.evaluate(ctx)
    assert d is not None
    assert d.verdict == "deny"
