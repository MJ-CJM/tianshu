"""Fail closed when an application route lacks an explicit auth-scope policy."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.convertors import (
    FloatConvertor,
    IntegerConvertor,
    PathConvertor,
    StringConvertor,
    UUIDConvertor,
)
from starlette.routing import Mount, compile_path

import tianshu.gateway.route_policy as route_policy_module
from tianshu.app import create_app
from tianshu.config import TianshuSettings
from tianshu.gateway.route_policy import (
    PUBLIC_ROUTE_RULES,
    ROUTE_SCOPE_RULES,
    RouteScopeRule,
    match_public_route,
    match_route_scope,
)


@dataclass(frozen=True, order=True)
class _RouteRef:
    method: str
    path_template: str
    transport: str = "http"


_MOUNT_ROUTE_REFS = {
    "/mcp": frozenset(
        _RouteRef(method, path_template)
        for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
        for path_template in ("/mcp", "/mcp/{rest:path}")
    ),
    "/assets": frozenset(
        _RouteRef(method, path_template)
        for method in ("GET", "HEAD")
        for path_template in ("/assets", "/assets/{path:path}")
    ),
}

_HEALTH_PUBLIC_ROUTES = frozenset(
    _RouteRef(method, path)
    for method in ("GET", "HEAD")
    for path in ("/health", "/health/live", "/health/ready")
)
_AUTH_PUBLIC_ROUTES = frozenset(
    {
        _RouteRef("GET", "/api/auth/mode"),
        _RouteRef("POST", "/api/auth/session"),
        _RouteRef("POST", "/api/auth/refresh"),
    }
)
_STATIC_FALLBACK_PUBLIC_ROUTES = frozenset(
    {
        _RouteRef("GET", "/{path:path}"),
        # The security boundary historically admitted safe HEAD requests to
        # SPA paths even though FastAPI only reports GET on the catch-all.
        _RouteRef("HEAD", "/{path:path}"),
    }
)
_ASSET_PUBLIC_ROUTES = _MOUNT_ROUTE_REFS["/assets"]
_ALLOWED_PUBLIC_ROUTES = (
    _HEALTH_PUBLIC_ROUTES
    | _AUTH_PUBLIC_ROUTES
    | _STATIC_FALLBACK_PUBLIC_ROUTES
    | _ASSET_PUBLIC_ROUTES
)
_POLICY_ONLY_COMPATIBILITY_TARGETS = frozenset({_RouteRef("HEAD", "/{path:path}")})


class _DeterministicMcpServer:
    def streamable_http_app(self) -> Starlette:
        return Starlette()


def _build_deterministic_mcp_server(_app: FastAPI) -> _DeterministicMcpServer:
    return _DeterministicMcpServer()


@pytest.fixture
def route_inventory_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build one deterministic composition root independent of optional extras."""

    static_dir = tmp_path / "static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>route-policy</html>", encoding="utf-8")

    fake_mcp_module = types.ModuleType("tianshu.gateway.mcp_server")
    fake_mcp_module.build_mcp_server = _build_deterministic_mcp_server  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tianshu.gateway.mcp_server", fake_mcp_module)

    workspace = tmp_path / "workspace"
    return create_app(
        TianshuSettings(
            _env_file=None,
            db_path=str(tmp_path / "tianshu.db"),
            artifact_dir=str(tmp_path / "artifacts"),
            workspace_dir=str(workspace),
            workspace_staging_root=str(tmp_path / "workspace-staging"),
            static_dir=str(static_dir),
            plugins_dir=str(tmp_path / "plugins"),
            universe_repo_root=str(workspace),
            universe_root=str(tmp_path / "universes"),
            eval_repo_root=str(workspace),
            memory_dir=str(tmp_path / "memory"),
            runtime_personas_dir=str(tmp_path / "personas"),
            log_dir=str(tmp_path / "logs"),
            security_mode="trusted-local",
            allowed_hosts="test,testserver",
        )
    )


def _route_inventory(app: FastAPI) -> frozenset[_RouteRef]:
    refs: set[_RouteRef] = set()
    mounts: set[str] = set()

    for route in app.routes:
        path_template = getattr(route, "path", None)
        if not isinstance(path_template, str):
            continue
        if isinstance(route, Mount):
            mounts.add(path_template)
            continue

        methods = getattr(route, "methods", None)
        if methods:
            refs.update(_RouteRef(str(method).upper(), path_template) for method in methods)
            continue

        if route.__class__.__name__.endswith("WebSocketRoute"):
            refs.add(_RouteRef("WEBSOCKET", path_template, "websocket"))

    assert mounts == set(_MOUNT_ROUTE_REFS), (
        "A new ASGI Mount needs an explicit supported-method expansion in this coverage gate"
    )
    for mount_refs in _MOUNT_ROUTE_REFS.values():
        refs.update(mount_refs)

    assert _RouteRef("WEBSOCKET", "/api/ws", "websocket") in refs
    return frozenset(refs)


def _matching_rules(ref: _RouteRef) -> tuple[tuple[object, ...], tuple[object, ...]]:
    protected = tuple(
        rule
        for rule in ROUTE_SCOPE_RULES
        if rule.matches_template(ref.method, ref.path_template, transport=ref.transport)
    )
    public = tuple(
        rule
        for rule in PUBLIC_ROUTE_RULES
        if rule.matches_template(ref.method, ref.path_template, transport=ref.transport)
    )
    return protected, public


def _concrete_path(path_template: str) -> str:
    """Materialize every Starlette template, including nested ``:path`` values."""

    _path_regex, path_format, convertors = compile_path(path_template)
    values: dict[str, str] = {}
    for name, convertor in convertors.items():
        if isinstance(convertor, PathConvertor):
            values[name] = "route-policy/nested"
        elif isinstance(convertor, StringConvertor):
            values[name] = "route-policy-sample"
        elif isinstance(convertor, IntegerConvertor):
            values[name] = "4242"
        elif isinstance(convertor, FloatConvertor):
            values[name] = "42.5"
        elif isinstance(convertor, UUIDConvertor):
            values[name] = "123e4567-e89b-12d3-a456-426614174000"
        else:  # pragma: no cover - a new Starlette convertor must be deliberately sampled
            raise AssertionError(f"Unsupported route convertor: {convertor!r}")
    return path_format.format(**values)


def _runtime_policy_violation(
    ref: _RouteRef,
    *,
    declared_protected: RouteScopeRule | None,
    declared_public: bool,
) -> str | None:
    concrete_path = _concrete_path(ref.path_template)
    runtime_protected = match_route_scope(ref.method, concrete_path, transport=ref.transport)
    runtime_public = match_public_route(ref.method, concrete_path, transport=ref.transport)

    if (runtime_protected is not None) == runtime_public:
        return (
            f"{ref.transport} {ref.method} {ref.path_template} -> {concrete_path}: "
            f"runtime_protected={runtime_protected!r}, runtime_public={runtime_public}"
        )
    if declared_public:
        if runtime_protected is not None or not runtime_public:
            return (
                f"{ref.transport} {ref.method} {ref.path_template} -> {concrete_path}: "
                "declared public but runtime did not preserve that declaration"
            )
        return None
    if runtime_public or runtime_protected != declared_protected:
        return (
            f"{ref.transport} {ref.method} {ref.path_template} -> {concrete_path}: "
            f"declared={declared_protected!r}, runtime winner={runtime_protected!r}, "
            f"runtime_public={runtime_public}"
        )
    return None


def test_every_application_route_has_exactly_one_scope_declaration(
    route_inventory_app: FastAPI,
) -> None:
    inventory = _route_inventory(route_inventory_app)

    violations: list[str] = []
    for ref in sorted(inventory):
        protected, public = _matching_rules(ref)
        if len(protected) + len(public) != 1:
            violations.append(
                f"{ref.transport} {ref.method} {ref.path_template}: "
                f"protected={protected!r}, public={public!r}"
            )

    assert violations == [], "Every route must be exactly public XOR protected:\n" + "\n".join(
        violations
    )


def test_every_static_scope_declaration_targets_a_real_application_route(
    route_inventory_app: FastAPI,
) -> None:
    targets = _route_inventory(route_inventory_app) | _POLICY_ONLY_COMPATIBILITY_TARGETS

    zombies = [
        rule
        for rule in (*ROUTE_SCOPE_RULES, *PUBLIC_ROUTE_RULES)
        if not any(
            rule.matches_template(ref.method, ref.path_template, transport=ref.transport)
            for ref in targets
        )
    ]

    assert zombies == [], f"Route-scope declarations without a registered route: {zombies!r}"


def test_runtime_first_match_preserves_each_static_scope_declaration(
    route_inventory_app: FastAPI,
) -> None:
    targets = _route_inventory(route_inventory_app) | _POLICY_ONLY_COMPATIBILITY_TARGETS
    violations: list[str] = []

    for ref in sorted(targets):
        protected, public = _matching_rules(ref)
        if len(protected) + len(public) != 1:
            violations.append(
                f"{ref.transport} {ref.method} {ref.path_template}: static declaration is not unique"
            )
            continue
        violation = _runtime_policy_violation(
            ref,
            declared_protected=protected[0] if protected else None,
            declared_public=bool(public),
        )
        if violation is not None:
            violations.append(violation)

    assert violations == [], (
        "Runtime first-match policy must equal the route's static declaration:\n"
        + "\n".join(violations)
    )


def test_public_declarations_are_limited_to_bootstrap_and_static_surface(
    route_inventory_app: FastAPI,
) -> None:
    inventory = _route_inventory(route_inventory_app) | _POLICY_ONLY_COMPATIBILITY_TARGETS
    declared_public = {
        ref
        for ref in inventory
        if any(
            rule.matches_template(ref.method, ref.path_template, transport=ref.transport)
            for rule in PUBLIC_ROUTE_RULES
        )
    }

    assert declared_public == _ALLOWED_PUBLIC_ROUTES


def test_runtime_guard_detects_parameterized_rule_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dynamic_api = RouteScopeRule(
        "GET",
        "/api/universes/{universe_id}",
        any_scopes=frozenset({"api"}),
    )
    exact_admin = RouteScopeRule(
        "GET",
        "/api/universes/secrets",
        any_scopes=frozenset({"admin"}),
    )
    monkeypatch.setattr(
        route_policy_module,
        "ROUTE_SCOPE_RULES",
        (dynamic_api, exact_admin),
    )
    monkeypatch.setattr(route_policy_module, "PUBLIC_ROUTE_RULES", ())

    violation = _runtime_policy_violation(
        _RouteRef("GET", "/api/universes/secrets"),
        declared_protected=exact_admin,
        declared_public=False,
    )

    assert violation is not None
    assert "runtime winner" in violation


def test_runtime_guard_detects_public_fallback_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_admin = RouteScopeRule(
        "GET",
        "/internal-status",
        any_scopes=frozenset({"admin"}),
    )
    public_fallback = RouteScopeRule("GET", "/{path:path}")
    monkeypatch.setattr(route_policy_module, "ROUTE_SCOPE_RULES", (exact_admin,))
    monkeypatch.setattr(route_policy_module, "PUBLIC_ROUTE_RULES", (public_fallback,))

    violation = _runtime_policy_violation(
        _RouteRef("GET", "/internal-status"),
        declared_protected=exact_admin,
        declared_public=False,
    )

    assert violation is not None
    assert "runtime_public=True" in violation


@pytest.mark.parametrize(
    ("method", "path", "transport"),
    [
        ("GET", "/api/__unregistered_route_policy_probe__", "http"),
        ("POST", "/api/__unregistered_route_policy_probe__", "http"),
        ("WEBSOCKET", "/api/__unregistered_route_policy_probe__", "websocket"),
        ("GET", "/mcp-unregistered", "http"),
    ],
)
def test_unknown_protected_namespace_routes_have_no_scope_match(
    method: str,
    path: str,
    transport: str,
) -> None:
    assert match_public_route(method, path, transport=transport) is False
    assert match_route_scope(method, path, transport=transport) is None
