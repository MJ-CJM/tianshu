"""鸿胪寺工具注册入口。Spec Section 5-6。"""

from __future__ import annotations

import json
import logging
from typing import Callable
from urllib.parse import urlparse

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


def _register_api_request(registry, api_engine, edict_getter):
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

        host = urlparse(url).hostname or ""
        allow_hosts = tuple(getattr(edict.runtime, "api_request_hosts", ()) or ())
        if host not in allow_hosts:
            return error_result("host_not_whitelisted")

        if method.upper() in WRITE_METHODS:
            write_hosts = tuple(
                getattr(edict.runtime, "api_request_write_hosts", ()) or ()
            )
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
            return error_result(resp.reason or "api_request_failed")

        return ok_result(
            json.dumps(
                {
                    "status": resp.http_status,
                    "headers": resp.headers,
                    "body": resp.body,
                    "truncated": resp.truncated,
                },
                ensure_ascii=False,
            )
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
        ),
    )


def _register_web_extract(registry, extract_engine, edict_getter):
    async def web_extract(
        url: str, schema: dict, prompt: str | None = None
    ) -> ToolResult:
        edict_id, net, _, _ = _resolve_edict_context(edict_getter)
        if "firecrawl" not in net.fetch_engines:
            return error_result("web_extract_not_allowed_in_profile")

        rl = get_rate_limiter()
        rc = await rl.check(edict_id, "web_extract", net.web_extract_rate_per_min)
        if not rc.allowed:
            return error_result(f"rate_limited:retry_after_{rc.retry_after_sec:.1f}s")

        outcome = await extract_engine.extract(url, schema, prompt=prompt)
        if outcome.status != "ok":
            return error_result(outcome.reason or "extract_failed")

        return ok_result(json.dumps(outcome.data, ensure_ascii=False))

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


def register_hongluisi(
    registry: ToolRegistry,
    edict_getter: Callable,
    *,
    api_engine=None,
    extract_engine=None,
) -> None:
    """启动期调用一次。edict_getter 从 ambient.py 注入。"""
    fetch_engines, search_providers = build_engines()

    _register_web_fetch(registry, fetch_engines, edict_getter)
    if search_providers:
        _register_web_search(registry, search_providers, edict_getter)
    if api_engine is not None:
        _register_api_request(registry, api_engine, edict_getter)
    if extract_engine is not None:
        _register_web_extract(registry, extract_engine, edict_getter)

    logger.info(
        "[hongluisi] registered: web_fetch, web_search (%s), api_request=%s, web_extract=%s",
        list(search_providers),
        api_engine is not None,
        extract_engine is not None,
    )
