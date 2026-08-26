from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tianshu.gateway.route_policy import (
    AUTH_AWARE_PUBLIC_SCOPES,
    PUBLIC_ROUTE_RULES,
    ROUTE_SCOPE_RULES,
    match_public_route,
    match_route_scope,
)


def test_policy_collections_are_immutable() -> None:
    assert isinstance(ROUTE_SCOPE_RULES, tuple)
    assert isinstance(PUBLIC_ROUTE_RULES, tuple)

    rule = ROUTE_SCOPE_RULES[0]
    with pytest.raises(FrozenInstanceError):
        rule.method = "DELETE"  # type: ignore[misc]
    with pytest.raises(TypeError):
        AUTH_AWARE_PUBLIC_SCOPES[("GET", "/health/ready")] = frozenset(  # type: ignore[index]
            {"admin"}
        )


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/api/not-registered"),
        ("POST", "/api/not-registered"),
        ("HEAD", "/api/edicts"),
        ("POST", "/api/evolution/runs/run-1/assignment"),
        ("OPTIONS", "/mcp"),
    ),
)
def test_unknown_method_and_path_pairs_have_no_protected_fallback(
    method: str,
    path: str,
) -> None:
    assert match_route_scope(method, path) is None


def test_special_scope_combinations_are_explicit() -> None:
    assignment = match_route_scope(
        "GET",
        "/api/evolution/runs/memorial-1/assignment",
    )
    assert assignment is not None
    assert assignment.any_scopes == frozenset({"api", "admin"})
    assert assignment.all_scopes == frozenset()

    workspace_apply = match_route_scope(
        "POST",
        "/api/workspace-runs/run-1/apply",
    )
    assert workspace_apply is not None
    assert workspace_apply.any_scopes == frozenset({"api"})
    assert workspace_apply.all_scopes == frozenset({"workspace:apply"})

    mcp = match_route_scope("PATCH", "/mcp/sessions/session-1")
    assert mcp is not None
    assert mcp.any_scopes == frozenset({"mcp:read", "mcp:submit"})
    assert mcp.all_scopes == frozenset()

    websocket = match_route_scope("GET", "/api/ws", transport="websocket")
    assert websocket is not None
    assert websocket.any_scopes == frozenset({"api"})
    assert websocket.matches_template("WEBSOCKET", "/api/ws", "websocket")
    assert match_route_scope("GET", "/api/ws") is None


@pytest.mark.parametrize(
    "path",
    (
        "/api",
        "/apiary",
        "/mcp",
        "/mcp-tools",
        "/docs",
        "/docs-extra",
        "/redoc",
        "/redocument",
        "/openapi.json",
        "/openapi.json.backup",
    ),
)
def test_static_fallback_excludes_security_boundary_prefixes(path: str) -> None:
    assert match_public_route("GET", path) is False
    assert match_public_route("HEAD", path) is False


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/"),
        ("HEAD", "/settings/plugins"),
        ("GET", "/assets/app.js"),
        ("HEAD", "/assets/styles/app.css"),
        ("GET", "/api/auth/mode"),
        ("POST", "/api/auth/session"),
        ("POST", "/api/auth/refresh"),
    ),
)
def test_declared_public_routes_match(method: str, path: str) -> None:
    assert match_public_route(method, path) is True


def test_readiness_auth_aware_scopes_are_exact_and_read_only() -> None:
    assert {
        ("GET", "/health/ready"): frozenset({"api"}),
        ("HEAD", "/health/ready"): frozenset({"api"}),
    } == AUTH_AWARE_PUBLIC_SCOPES


@pytest.mark.parametrize("method", ("GET", "PUT"))
def test_evolution_policy_routes_are_explicitly_admin_only(method: str) -> None:
    rule = match_route_scope(method, "/api/evolution/policies/skill:foo")

    assert rule is not None
    assert rule.any_scopes == frozenset({"admin"})
    assert rule.all_scopes == frozenset()


def test_matches_template_compares_declarations_not_concrete_paths() -> None:
    rule = next(
        rule for rule in ROUTE_SCOPE_RULES if rule.matches_template("GET", "/api/edicts/{edict_id}")
    )

    assert rule.matches_template("GET", "/api/edicts/{edict_id}")
    assert not rule.matches_template("GET", "/api/edicts/edict-1")
    assert match_route_scope("GET", "/api/edicts/edict-1") is rule
