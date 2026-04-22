"""鸿胪寺工具注册入口。Spec Section 5-6。"""

from __future__ import annotations

import json
import logging
from typing import Callable

from tianshu.tools.hongluisi.engine_registry import build_engines
from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.rate_limiter import get_rate_limiter
from tianshu.tools.hongluisi.router import FetchRouter
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

logger = logging.getLogger(__name__)


def _resolve_edict_context(
    edict_getter: Callable,
) -> tuple[str, NetworkPolicy, str | None, str | None]:
    """从 ambient ContextVar 拿当前 Edict，解析 NetworkPolicy + override。"""
    edict = edict_getter()
    if edict is None:
        raise RuntimeError("no ambient edict; tool called outside executor")
    # 解析 NetworkPolicy：优先 edict.runtime.policy_profile.template_name
    from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

    net = NetworkPolicy()
    tmpl_name = getattr(edict.runtime.policy_profile, "template_name", None)
    if tmpl_name and tmpl_name in BUILTIN_TEMPLATES:
        net = BUILTIN_TEMPLATES[tmpl_name].network
    fe_ov = getattr(edict.runtime, "fetch_engine_override", None)
    sp_ov = getattr(edict.runtime, "search_provider_override", None)
    return edict.id, net, fe_ov, sp_ov


def _register_web_fetch(registry, fetch_engines, edict_getter):
    async def web_fetch(url: str) -> ToolResult:
        edict_id, net, fe_ov, _ = _resolve_edict_context(edict_getter)
        if not net.fetch_engines and fe_ov is None:
            return error_result("fetch_not_allowed_in_profile")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_fetch", net.web_fetch_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        router = FetchRouter(fetch_engines, net, override=fe_ov)
        outcome, _attempts = await router.dispatch(url)
        if outcome.status == "ok":
            return ok_result(outcome.content)
        return error_result(outcome.reason or "fetch_failed")

    registry.register(
        "web_fetch",
        web_fetch,
        ToolDefinition(
            name="web_fetch",
            description=(
                "Fetch a public web page and return its readable text as Markdown. "
                "Only public URLs are allowed; internal/private IPs are rejected."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=16000,
        ),
    )


def _register_web_search(registry, search_providers, edict_getter):
    async def web_search(query: str, max_results: int = 5) -> ToolResult:
        edict_id, net, _, sp_ov = _resolve_edict_context(edict_getter)
        provider_name = sp_ov or net.search_provider
        if provider_name is None:
            return error_result("search_not_allowed_in_profile")
        provider = search_providers.get(provider_name)
        if provider is None:
            return error_result(f"provider_not_registered:{provider_name}")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_search", net.web_search_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        try:
            outcome = await provider.search(query, max_results=max_results)
        except Exception as e:
            return error_result(f"provider_error:{type(e).__name__}")

        if not outcome.results:
            return error_result("search_empty")

        lines = []
        for i, r in enumerate(outcome.results, 1):
            lines.append(f"### {i}. [{r.title}]({r.url})")
            if r.snippet:
                lines.append(r.snippet)
            lines.append("")
        return ok_result("\n".join(lines))

    registry.register(
        "web_search",
        web_search,
        ToolDefinition(
            name="web_search",
            description=(
                "Search the public web and return ranked result summaries. "
                "Use for discovery; follow up with web_fetch for full content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {
                        "type": "integer", "minimum": 1, "maximum": 10, "default": 5
                    },
                },
                "required": ["query"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=8000,
        ),
    )


def register_hongluisi(registry: ToolRegistry, edict_getter: Callable) -> None:
    """启动期调用一次。edict_getter 从 ambient.py 注入。"""
    fetch_engines, search_providers = build_engines()

    _register_web_fetch(registry, fetch_engines, edict_getter)
    if search_providers:
        _register_web_search(registry, search_providers, edict_getter)

    logger.info(
        "[hongluisi] registered: web_fetch, web_search (providers: %s)",
        list(search_providers),
    )
