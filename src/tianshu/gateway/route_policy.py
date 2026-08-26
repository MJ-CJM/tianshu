"""Explicit route authorization policy for the ASGI security boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from starlette.routing import compile_path

Transport = Literal["http", "websocket"]


@dataclass(frozen=True, slots=True)
class RouteScopeRule:
    """Authorization requirements for one method and route template."""

    method: str
    path_template: str
    any_scopes: frozenset[str] = frozenset()
    all_scopes: frozenset[str] = frozenset()
    transport: Transport = "http"
    excluded_prefixes: tuple[str, ...] = ()
    _path_regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        method = self.method.upper()
        if self.transport == "websocket":
            method = "WEBSOCKET"
        object.__setattr__(self, "method", method)
        path_regex, _path_format, _convertors = compile_path(self.path_template)
        object.__setattr__(self, "_path_regex", path_regex)

    def matches_template(
        self,
        method: str,
        path_template: str,
        transport: Transport = "http",
    ) -> bool:
        """Return whether this rule owns an application route declaration."""

        normalized_method = "WEBSOCKET" if transport == "websocket" else method.upper()
        return (
            self.transport == transport
            and self.method == normalized_method
            and self.path_template == path_template
        )

    def _matches(self, method: str, path: str, transport: Transport) -> bool:
        normalized_method = "WEBSOCKET" if transport == "websocket" else method.upper()
        return (
            self.transport == transport
            and self.method == normalized_method
            and not path.startswith(self.excluded_prefixes)
            and self._path_regex.fullmatch(path) is not None
        )


_API_ROUTES = (
    ("GET", "/openapi.json"),
    ("HEAD", "/openapi.json"),
    ("GET", "/docs"),
    ("HEAD", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("HEAD", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    ("HEAD", "/redoc"),
    ("POST", "/api/consultations"),
    ("GET", "/api/consultations"),
    ("GET", "/api/consultations/{consultation_id}"),
    ("POST", "/api/consultations/{consultation_id}/rounds"),
    ("POST", "/api/consultations/{consultation_id}/rounds/{round_id}/synthesis"),
    ("PUT", "/api/consultations/{consultation_id}/verdict"),
    ("DELETE", "/api/auth/session"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/audit/rules"),
    ("GET", "/api/policy/stats"),
    ("GET", "/api/policy/templates"),
    ("GET", "/api/workspace"),
    ("PUT", "/api/workspace"),
    ("GET", "/api/control"),
    ("GET", "/api/decisions"),
    ("GET", "/api/decisions/{decision_request_id}"),
    ("POST", "/api/decisions/{decision_request_id}/resolve"),
    ("POST", "/api/edicts"),
    ("POST", "/api/edicts/parse"),
    ("POST", "/api/edicts/governance/preview"),
    ("GET", "/api/edicts"),
    ("GET", "/api/edicts/{edict_id}"),
    ("GET", "/api/edicts/{edict_id}/detail"),
    ("PATCH", "/api/edicts/{edict_id}"),
    ("DELETE", "/api/edicts/{edict_id}"),
    ("POST", "/api/edicts/{edict_id}/pause"),
    ("POST", "/api/edicts/{edict_id}/steer"),
    ("POST", "/api/edicts/{edict_id}/resume"),
    ("GET", "/api/edicts/{edict_id}/memorial"),
    ("GET", "/api/edicts/{edict_id}/memorials"),
    ("POST", "/api/edicts/latest-memorials"),
    ("POST", "/api/edicts/{edict_id}/plan/approve"),
    ("POST", "/api/edicts/{edict_id}/plan/reject"),
    ("POST", "/api/edicts/{edict_id}/follow-up"),
    ("PATCH", "/api/edicts/{edict_id}/status"),
    ("GET", "/api/edicts/{edict_id}/events"),
    ("GET", "/api/edicts/{edict_id}/iterations"),
    ("GET", "/api/edicts/outer-loop/pending"),
    ("POST", "/api/edicts/{edict_id}/outer-loop/decide"),
    ("GET", "/api/edicts/{edict_id}/supervision-reports"),
    ("GET", "/api/edicts/{edict_id}/supervision-report"),
    ("GET", "/api/edicts/{edict_id}/policy_events"),
    ("GET", "/api/edicts/{edict_id}/evidence"),
    ("GET", "/api/evidence/{bundle_id}/download"),
    ("GET", "/api/evolution"),
    ("GET", "/api/evolution/candidates/{candidate_id}"),
    ("GET", "/api/evolution/candidates/{candidate_id}/gate"),
    ("GET", "/api/estop"),
    ("GET", "/api/evals/runs"),
    ("GET", "/api/evals/runs/{run_id}"),
    ("GET", "/api/evals/sets"),
    ("GET", "/api/evals/failure-distribution"),
    ("GET", "/api/memorials"),
    ("GET", "/api/memorials/{memorial_id}"),
    ("GET", "/api/scheduler/jobs"),
    ("DELETE", "/api/scheduler/jobs/{job_id}"),
    ("POST", "/api/scheduler/jobs/{job_id}/pause"),
    ("POST", "/api/scheduler/jobs/{job_id}/resume"),
    ("POST", "/api/scheduler/jobs/{job_id}/run-now"),
    ("PATCH", "/api/scheduler/jobs/{job_id}"),
    ("GET", "/api/scheduler/jobs/{job_id}/runs"),
    ("POST", "/api/decrees"),
    ("GET", "/api/approvals/pending_tool_calls"),
    ("POST", "/api/approvals/tool_decision"),
    ("GET", "/api/dag/by-edict/{edict_id}"),
    ("GET", "/api/dag/{dag_id}"),
    ("POST", "/api/dag/{dag_id}/cancel"),
    ("POST", "/api/dag/{dag_id}/retry"),
    ("GET", "/api/memorials/by-persona/{persona_id}"),
    ("GET", "/api/planner/stats"),
    ("GET", "/api/hongluisi/engine-status"),
    ("GET", "/api/hongluisi/engine-preferences"),
    ("GET", "/api/keqing/agents"),
    ("GET", "/api/keqing/status"),
    ("GET", "/api/edicts/{edict_id}/snapshots"),
    ("POST", "/api/edicts/{edict_id}/snapshots/revert"),
    ("GET", "/api/model-catalog/status"),
    ("GET", "/api/departments"),
    ("GET", "/api/personas/jingcha"),
    ("GET", "/api/personas"),
    ("GET", "/api/persona-templates"),
    ("GET", "/api/persona-templates/{template_id}"),
    ("GET", "/api/personas/{persona_id}/metrics"),
    ("GET", "/api/personas/{persona_id}/profile"),
    ("GET", "/api/personas/{persona_id}/profile/history/{version}"),
    ("GET", "/api/routing/rules"),
    ("GET", "/api/providers"),
    ("GET", "/api/providers/{name}/status"),
    ("GET", "/api/providers/pricing/defaults"),
    ("GET", "/api/providers/{name}/pricing/effective"),
    ("GET", "/api/plugins"),
    ("GET", "/api/plugins/{name}"),
    ("GET", "/api/skills"),
    ("GET", "/api/skills/{name}"),
    ("GET", "/api/tools"),
    ("GET", "/api/event-bus/handlers"),
    ("GET", "/api/event-bus/stats"),
    ("GET", "/api/event-bus/recent"),
    ("GET", "/api/hooks/registry"),
    ("GET", "/api/notifications/channels"),
    ("GET", "/api/universes"),
    ("GET", "/api/universes/_diff"),
    ("GET", "/api/universes/_status"),
    ("GET", "/api/universes/petitions"),
    ("GET", "/api/universes/taiyi/report"),
    ("GET", "/api/universes/flags"),
    ("GET", "/api/universes/{universe_id}"),
    ("GET", "/api/universes/{universe_id}/code-diff"),
    ("GET", "/api/universes/{universe_id}/eval-runs"),
    ("GET", "/api/workspace-runs/{run_id}/status"),
    ("GET", "/api/workspace-runs/{run_id}/changes"),
    ("GET", "/api/tongzheng/channels/feishu"),
    ("GET", "/api/tongzheng/personas"),
    ("GET", "/api/tongzheng/channels/feishu/status"),
    ("GET", "/api/tongzheng/channels/telegram"),
    ("GET", "/api/tongzheng/channels/telegram/status"),
    ("GET", "/api/tongzheng/instances"),
    ("GET", "/api/tongzheng/instances/{instance_id}"),
    ("GET", "/api/tongzheng/instances/{instance_id}/status"),
)

_ADMIN_ROUTES = (
    ("GET", "/api/auth/tokens"),
    ("POST", "/api/auth/tokens"),
    ("POST", "/api/auth/tokens/{token_id}/rotate"),
    ("DELETE", "/api/auth/tokens/{token_id}"),
    ("GET", "/api/audit/stats"),
    ("GET", "/api/audit/network-events"),
    ("GET", "/api/policy/session_rules"),
    ("POST", "/api/policy/session_rules"),
    ("DELETE", "/api/policy/session_rules/{rule_id}"),
    ("GET", "/api/agent-config"),
    ("PUT", "/api/agent-config"),
    ("GET", "/api/config"),
    ("PUT", "/api/config"),
    ("GET", "/api/configs"),
    ("POST", "/api/configs"),
    ("PUT", "/api/configs/{name}"),
    ("DELETE", "/api/configs/{name}"),
    ("PUT", "/api/configs/{name}/activate"),
    ("GET", "/api/cost/summary"),
    ("GET", "/api/cost/records"),
    ("GET", "/api/cost/budget"),
    ("PUT", "/api/cost/budget"),
    ("GET", "/api/cost/export"),
    ("GET", "/api/credentials"),
    ("POST", "/api/credentials"),
    ("PATCH", "/api/credentials/{cred_id}"),
    ("DELETE", "/api/credentials/{cred_id}"),
    ("POST", "/api/evolution/candidates/{candidate_id}/gate/evaluate"),
    ("POST", "/api/evolution/candidates/{candidate_id}/canary"),
    ("POST", "/api/evolution/candidates/{candidate_id}/promote"),
    ("POST", "/api/evolution/candidates/{candidate_id}/rollback"),
    ("GET", "/api/evolution/policies/{subject_key}"),
    ("PUT", "/api/evolution/policies/{subject_key}"),
    ("POST", "/api/estop/engage"),
    ("POST", "/api/estop/resume"),
    ("GET", "/api/workers"),
    ("GET", "/api/workers/status"),
    ("PATCH", "/api/hongluisi/engine-preferences"),
    ("PATCH", "/api/mcp/servers/{name}"),
    ("POST", "/api/mcp/servers"),
    ("DELETE", "/api/mcp/servers/{name}/override"),
    ("POST", "/api/mcp/reload"),
    ("POST", "/api/memory/recall"),
    ("POST", "/api/memory/sync"),
    ("DELETE", "/api/memory/{entry_id}"),
    ("POST", "/api/memory/batch-delete"),
    ("GET", "/api/memory/policies"),
    ("PUT", "/api/memory/policies/{persona_id}"),
    ("POST", "/api/memory/compact"),
    ("POST", "/api/memory/reflect"),
    ("GET", "/api/memory/stats"),
    ("GET", "/api/memory/{persona_id}"),
    ("GET", "/api/memory-palace/search"),
    ("GET", "/api/memory-palace/l1"),
    ("GET", "/api/model-providers/profiles"),
    ("GET", "/api/model-providers"),
    ("POST", "/api/model-providers"),
    ("PUT", "/api/model-providers/{provider_id}"),
    ("PUT", "/api/model-providers/{provider_id}/key"),
    ("DELETE", "/api/model-providers/{provider_id}"),
    ("GET", "/api/model-providers/{provider_id}/models"),
    ("POST", "/api/model-providers/{provider_id}/test"),
    ("POST", "/api/model-catalog/refresh"),
    ("POST", "/api/departments"),
    ("PUT", "/api/departments/{dept_id}"),
    ("DELETE", "/api/departments/{dept_id}"),
    ("POST", "/api/personas/import/preview"),
    ("POST", "/api/personas"),
    ("PUT", "/api/personas/{persona_id}"),
    ("POST", "/api/personas/{persona_id}/regenerate-identity"),
    ("DELETE", "/api/personas/{persona_id}"),
    ("POST", "/api/personas/{persona_id}/synthesize"),
    ("PUT", "/api/personas/{persona_id}/profile/manual"),
    ("POST", "/api/providers"),
    ("PUT", "/api/providers/{name}"),
    ("DELETE", "/api/providers/{name}"),
    ("PUT", "/api/providers/{name}/pricing"),
    ("DELETE", "/api/providers/{name}/pricing"),
    ("POST", "/api/plugins/install"),
    ("PUT", "/api/plugins/{name}/status"),
    ("POST", "/api/skills/curate"),
    ("PUT", "/api/skills/{name}"),
    ("POST", "/api/skills/{name}/archive"),
    ("POST", "/api/skills/{name}/pin"),
    ("POST", "/api/skills"),
    ("POST", "/api/skills/candidates/{candidate_id}/gate/evaluate"),
    ("POST", "/api/skills/candidates/{candidate_id}/stage"),
    ("DELETE", "/api/skills/{name}"),
    ("PATCH", "/api/tools/{tool_name}"),
    ("GET", "/api/audit/system"),
    ("GET", "/api/audit/system/export"),
    ("GET", "/api/system-prompt/files"),
    ("GET", "/api/system-prompt/files/{persona_id}/{filename}"),
    ("PUT", "/api/system-prompt/files/{persona_id}/{filename}"),
    ("POST", "/api/system-prompt/files/{persona_id}/{filename}/reset"),
    ("GET", "/api/system-prompt/preview/{persona_id}"),
    ("GET", "/api/system-prompt/layers/{persona_id}"),
    ("POST", "/api/universes/enable"),
    ("POST", "/api/universes/petitions/{petition_id}/grant"),
    ("POST", "/api/universes/petitions/{petition_id}/dismiss"),
    ("POST", "/api/universes/taiyi/report"),
    ("PUT", "/api/universes/flags/{key}"),
    ("DELETE", "/api/universes/flags/{key}"),
    ("POST", "/api/universes/feedback"),
    ("POST", "/api/universes/evolve"),
    ("POST", "/api/universes/propose-code"),
    ("POST", "/api/universes/propose-auto"),
    ("DELETE", "/api/universes/{universe_id}"),
    ("POST", "/api/universes/{universe_id}/branch"),
    ("POST", "/api/universes/{universe_id}/switch"),
    ("POST", "/api/universes/{universe_id}/archive"),
    ("POST", "/api/universes/{universe_id}/restore"),
    ("POST", "/api/universes/{universe_id}/promote-code"),
    ("PUT", "/api/tongzheng/channels/feishu"),
    ("PUT", "/api/tongzheng/channels/telegram"),
    ("POST", "/api/tongzheng/instances"),
    ("PUT", "/api/tongzheng/instances/{instance_id}"),
    ("PATCH", "/api/tongzheng/instances/{instance_id}/enabled"),
    ("DELETE", "/api/tongzheng/instances/{instance_id}"),
)

_API_OR_ADMIN_ROUTES = (
    ("GET", "/api/evolution/runs/{memorial_id}/assignment"),
    ("GET", "/api/mcp/servers"),
    ("GET", "/api/mcp/servers/{name}"),
    ("GET", "/api/mcp/servers/{name}/tools"),
)

_API_AND_WORKSPACE_APPLY_ROUTES = (
    ("POST", "/api/workspace-runs/{run_id}/apply-decisions"),
    ("POST", "/api/workspace-runs/{run_id}/apply"),
)

_MCP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")


def _scope_rules(
    routes: tuple[tuple[str, str], ...],
    *,
    any_scopes: frozenset[str],
    all_scopes: frozenset[str] = frozenset(),
) -> tuple[RouteScopeRule, ...]:
    return tuple(
        RouteScopeRule(
            method,
            path_template,
            any_scopes=any_scopes,
            all_scopes=all_scopes,
        )
        for method, path_template in routes
    )


ROUTE_SCOPE_RULES: tuple[RouteScopeRule, ...] = (
    *_scope_rules(_API_ROUTES, any_scopes=frozenset({"api"})),
    *_scope_rules(_ADMIN_ROUTES, any_scopes=frozenset({"admin"})),
    *_scope_rules(
        _API_OR_ADMIN_ROUTES,
        any_scopes=frozenset({"api", "admin"}),
    ),
    *_scope_rules(
        _API_AND_WORKSPACE_APPLY_ROUTES,
        any_scopes=frozenset({"api"}),
        all_scopes=frozenset({"workspace:apply"}),
    ),
    *(
        RouteScopeRule(
            method,
            path_template,
            any_scopes=frozenset({"mcp:read", "mcp:submit"}),
        )
        for path_template in ("/mcp", "/mcp/{rest:path}")
        for method in _MCP_METHODS
    ),
    RouteScopeRule(
        "WEBSOCKET",
        "/api/ws",
        any_scopes=frozenset({"api"}),
        transport="websocket",
    ),
)

_STATIC_EXCLUDED_PREFIXES = ("/api", "/mcp", "/docs", "/redoc", "/openapi.json")
PUBLIC_ROUTE_RULES: tuple[RouteScopeRule, ...] = tuple(
    RouteScopeRule(method, path_template)
    for method, path_template in (
        ("GET", "/health"),
        ("HEAD", "/health"),
        ("GET", "/health/live"),
        ("HEAD", "/health/live"),
        ("GET", "/health/ready"),
        ("HEAD", "/health/ready"),
        ("GET", "/api/auth/mode"),
        ("POST", "/api/auth/session"),
        ("POST", "/api/auth/refresh"),
        ("GET", "/assets"),
        ("HEAD", "/assets"),
        ("GET", "/assets/{path:path}"),
        ("HEAD", "/assets/{path:path}"),
    )
) + (
    RouteScopeRule("GET", "/{path:path}", excluded_prefixes=_STATIC_EXCLUDED_PREFIXES),
    RouteScopeRule("HEAD", "/{path:path}", excluded_prefixes=_STATIC_EXCLUDED_PREFIXES),
)

AUTH_AWARE_PUBLIC_SCOPES: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType(
    {
        ("GET", "/health/ready"): frozenset({"api"}),
        ("HEAD", "/health/ready"): frozenset({"api"}),
    }
)


def match_route_scope(
    method: str,
    path: str,
    transport: Transport = "http",
) -> RouteScopeRule | None:
    """Return the explicit protected-route policy, or fail closed with ``None``."""

    return next(
        (rule for rule in ROUTE_SCOPE_RULES if rule._matches(method, path, transport)),
        None,
    )


def match_public_route(method: str, path: str, transport: Transport = "http") -> bool:
    """Return whether an explicit public route rule matches the request."""

    return any(rule._matches(method, path, transport) for rule in PUBLIC_ROUTE_RULES)


__all__ = [
    "AUTH_AWARE_PUBLIC_SCOPES",
    "PUBLIC_ROUTE_RULES",
    "ROUTE_SCOPE_RULES",
    "RouteScopeRule",
    "match_public_route",
    "match_route_scope",
]
