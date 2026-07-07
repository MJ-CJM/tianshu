"""鸿胪寺工具注册入口。Spec Section 5-6。

Handler call-time 从 engine_registry 取最新 engine 实例，以便热更。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from urllib.parse import urlparse

from tianshu.tools.hongluisi.engine_registry import (
    get_api_engine,
    get_extract_engine,
    get_fetch_engines_map,
    get_search_providers_map,
)
from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.hongluisi.rate_limiter import get_rate_limiter
from tianshu.tools.hongluisi.router import FetchRouter
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

logger = logging.getLogger(__name__)


def _host_of(url: str) -> str:
    """Extract hostname from URL for audit metadata."""
    return urlparse(url).hostname or ""


def _resolve_edict_context(
    edict_getter: Callable,
) -> tuple[str, NetworkPolicy, str | None, str | None]:
    """从 ambient ContextVar 拿当前 Edict，解析 NetworkPolicy + override。"""
    edict = edict_getter()
    if edict is None:
        raise RuntimeError("no ambient edict; tool called outside executor")
    # profile 解析走共享 helper（policy_profile.py），保证和 NetworkSafetyRule 同一套 fallback 语义
    from tianshu.tools.policy_profile import resolve_network_for_edict

    net = resolve_network_for_edict(edict)
    fe_ov = getattr(edict.runtime, "fetch_engine_override", None)
    sp_ov = getattr(edict.runtime, "search_provider_override", None)
    return edict.id, net, fe_ov, sp_ov


def _register_web_fetch(registry, edict_getter):
    async def web_fetch(url: str) -> ToolResult:
        edict_id, net, fe_ov, _ = _resolve_edict_context(edict_getter)
        if not net.fetch_engines and fe_ov is None:
            return error_result("fetch_not_allowed_in_profile")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_fetch", net.web_fetch_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        fetch_engines = get_fetch_engines_map()
        router = FetchRouter(fetch_engines, net, override=fe_ov)
        outcome, attempts = await router.dispatch(url)
        network_detail = {
            "tool": "web_fetch",
            "host": _host_of(url),
            "http_status": outcome.http_status,
            "bytes_out": outcome.bytes_fetched,
            "cached": outcome.cached,
            "final_url": outcome.final_url,
            "attempts": [
                {"engine": a.engine, "status": a.status, "reason": a.reason} for a in attempts
            ],
        }
        if outcome.status == "ok":
            return ok_result(outcome.content, details={"network": network_detail})
        return ToolResult(
            content=outcome.reason or "fetch_failed",
            details={"network": network_detail},
            is_error=True,
        )

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


def _register_web_search(registry, edict_getter):
    async def web_search(query: str, max_results: int = 5) -> ToolResult:
        edict_id, net, _, sp_ov = _resolve_edict_context(edict_getter)
        provider_name = sp_ov or net.search_provider
        if provider_name is None:
            return error_result("search_not_allowed_in_profile")
        search_providers = get_search_providers_map()
        if not search_providers:
            return error_result("search_no_providers_available")
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
            return ToolResult(
                content=f"provider_error:{type(e).__name__}",
                details={"network": {"tool": "web_search", "provider": provider_name}},
                is_error=True,
            )

        if not outcome.results:
            return ToolResult(
                content="search_empty",
                details={
                    "network": {"tool": "web_search", "provider": provider_name, "result_count": 0}
                },
                is_error=True,
            )

        lines = []
        for i, r in enumerate(outcome.results, 1):
            lines.append(f"### {i}. [{r.title}]({r.url})")
            if r.snippet:
                lines.append(r.snippet)
            lines.append("")
        network_detail = {
            "tool": "web_search",
            "provider": provider_name,
            "result_count": len(outcome.results),
        }
        return ok_result("\n".join(lines), details={"network": network_detail})

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
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=8000,
        ),
    )


def _register_api_request(registry, edict_getter):
    WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

    async def api_request(
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        query: dict | None = None,
        json_body: object | None = None,
    ) -> ToolResult:
        edict = edict_getter()
        if edict is None:
            return error_result("no_ambient_edict")
        edict_id, net, _, _ = _resolve_edict_context(edict_getter)

        if not net.allow_api_request:
            return error_result("api_request_not_allowed_in_profile")

        api_engine = get_api_engine()
        if api_engine is None:
            return error_result("api_engine_unavailable")

        host = urlparse(url).hostname or ""
        allow_hosts = tuple(getattr(edict.runtime, "api_request_hosts", ()) or ())
        if host not in allow_hosts:
            return error_result("host_not_whitelisted")

        if method.upper() in WRITE_METHODS:
            write_hosts = tuple(getattr(edict.runtime, "api_request_write_hosts", ()) or ())
            if host not in write_hosts:
                return error_result("write_method_host_not_whitelisted")
            # 写方法审批路径由 NetworkSafetyRule 拦截 (Task 13-14)，到达此处即已批

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "api_request", net.api_request_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        resp = await api_engine.request(
            url=url,
            method=method,
            headers=headers or {},
            query=query or {},
            json_body=json_body,
        )
        if resp.status != "ok":
            return ToolResult(
                content=resp.reason or "api_request_failed",
                details={
                    "network": {
                        "tool": "api_request",
                        "host": host,
                        "method": method.upper(),
                        "credential_name": resp.credential_name,
                        "http_status": resp.http_status,
                    }
                },
                is_error=True,
            )

        network_detail = {
            "tool": "api_request",
            "host": host,
            "method": method.upper(),
            "http_status": resp.http_status,
            "bytes_out": resp.bytes_read,
            "credential_name": resp.credential_name,
            "truncated": resp.truncated,
        }
        return ok_result(
            json.dumps(
                {
                    "status": resp.http_status,
                    "headers": resp.headers,
                    "body": resp.body,
                    "truncated": resp.truncated,
                },
                ensure_ascii=False,
            ),
            details={"network": network_detail},
        )

    registry.register(
        "api_request",
        api_request,
        ToolDefinition(
            name="api_request",
            description=(
                "Make an HTTP request to a whitelisted external API. "
                "Credentials are managed by the system — do not pass Authorization/Cookie/X-Api-Key "
                "headers. Methods GET/HEAD are read-only; POST/PUT/DELETE/PATCH require approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "query": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "json_body": {"type": ["object", "array", "null"]},
                },
                "required": ["url"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=16000,
            side_effect=True,
        ),
    )


def _register_web_extract(registry, edict_getter):
    async def web_extract(url: str, schema: dict, prompt: str | None = None) -> ToolResult:
        edict_id, net, _, _ = _resolve_edict_context(edict_getter)
        if "firecrawl" not in net.fetch_engines:
            return error_result("web_extract_not_allowed_in_profile")

        extract_engine = get_extract_engine()
        if extract_engine is None:
            return error_result("extract_engine_unavailable")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_extract", net.web_extract_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        outcome = await extract_engine.extract(url, schema, prompt=prompt)
        if outcome.status != "ok":
            return error_result(outcome.reason or "extract_failed")

        network_detail = {
            "tool": "web_extract",
            "host": _host_of(url),
            "http_status": outcome.http_status,
        }
        return ok_result(
            json.dumps(outcome.data, ensure_ascii=False),
            details={"network": network_detail},
        )

    registry.register(
        "web_extract",
        web_extract,
        ToolDefinition(
            name="web_extract",
            description=(
                "Extract structured data from a public web page using an AI extractor. "
                "Provide a JSON schema describing fields to extract."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "schema": {"type": "object"},
                    "prompt": {"type": "string"},
                },
                "required": ["url", "schema"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=8000,
        ),
    )


def register_hongluisi(registry: ToolRegistry, edict_getter: Callable) -> None:
    """启动期调用一次。build_engines 必须已被调过。

    所有 4 个 handler 都始终注册——engine 可用性在 handler 内 call-time check，
    以支持运行时 rebuild_engines() 热更凭证。
    """
    _register_web_fetch(registry, edict_getter)
    _register_web_search(registry, edict_getter)
    _register_api_request(registry, edict_getter)
    _register_web_extract(registry, edict_getter)

    logger.info(
        "[hongluisi] tools registered: web_fetch, web_search, api_request, web_extract "
        "(engines resolved at call time)"
    )
