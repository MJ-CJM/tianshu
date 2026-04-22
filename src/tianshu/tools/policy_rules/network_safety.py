"""NetworkSafetyRule — 拦截 4 个网络工具，在 PolicyEngine 前置做白名单 + 审批判定。

Spec: docs/superpowers/specs/2026-04-22-external-network-capability-expansion-design.md §7.4
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from tianshu.tools.hongluisi.policy import NetworkPolicy
from tianshu.tools.policy import PolicyContext, PolicyDecision
from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

NETWORK_TOOLS = frozenset({"web_fetch", "web_search", "api_request", "web_extract"})
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


@dataclass
class NetworkSafetyRule:
    rule_id: str = "network_safety"
    priority: int = 75  # 在 DefaultTier (10) 前、ApprovalRequired (70) 后

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in NETWORK_TOOLS:
            return None  # 非网络工具 → 弃权

        net = self._resolve_network_policy(ctx)

        if ctx.tool_name == "web_fetch":
            if not net.fetch_engines:
                return self._deny("fetch_not_allowed_in_profile")
            return None

        if ctx.tool_name == "web_search":
            if net.search_provider is None:
                return self._deny("search_not_allowed_in_profile")
            return None

        if ctx.tool_name == "api_request":
            return self._evaluate_api_request(ctx, net)

        if ctx.tool_name == "web_extract":
            if "firecrawl" not in net.fetch_engines:
                return self._deny("web_extract_not_allowed_in_profile")
            return None

        return None

    def _resolve_network_policy(self, ctx: PolicyContext) -> NetworkPolicy:
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        tmpl_name = getattr(profile, "template_name", None) if profile else None
        if tmpl_name and tmpl_name in BUILTIN_TEMPLATES:
            return BUILTIN_TEMPLATES[tmpl_name].network
        return NetworkPolicy()

    def _evaluate_api_request(
        self, ctx: PolicyContext, net: NetworkPolicy
    ) -> PolicyDecision | None:
        if not net.allow_api_request:
            return self._deny("api_request_not_allowed_in_profile")

        url = ctx.args.get("url", "") or ""
        method = (ctx.args.get("method") or "GET").upper()
        host = urlparse(url).hostname or ""

        runtime = getattr(ctx.edict, "runtime", None)
        allow_hosts = set(getattr(runtime, "api_request_hosts", ()) or ())
        if host not in allow_hosts:
            return self._deny("host_not_whitelisted", metadata={"host": host})

        if method in WRITE_METHODS:
            write_hosts = set(getattr(runtime, "api_request_write_hosts", ()) or ())
            if host not in write_hosts:
                return self._deny(
                    "write_method_host_not_whitelisted",
                    metadata={"host": host, "method": method},
                )
            # 写方法 → 触发审批
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason=f"api_request {method} {host} requires approval",
                metadata={"host": host, "method": method},
            )

        if method not in net.api_request_methods:
            return self._deny(
                f"method_not_allowed:{method}",
                metadata={"method": method},
            )

        return None

    def _deny(self, reason: str, metadata: dict | None = None) -> PolicyDecision:
        return PolicyDecision(
            verdict="deny",
            rule_id=self.rule_id,
            reason=reason,
            metadata=metadata or {},
        )
