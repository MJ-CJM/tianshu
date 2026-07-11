"""Universe operation contexts preserve identity, correlation, and contract scope."""

from __future__ import annotations

from tianshu.gateway.auth import bind_auth_context
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.universe.execution import UniverseExecutionContextFactory


def test_system_contexts_are_unique_and_bind_secret_refs() -> None:
    factory = UniverseExecutionContextFactory(security_mode="trusted-local")

    first = factory.create(
        operation="gate",
        timeout_seconds=30,
        secret_refs=("settings:eval_llm_api_key",),
    )
    second = factory.create(operation="gate", timeout_seconds=30)

    assert first.actor.id == "system:universe"
    assert first.correlation_id != second.correlation_id
    assert first.effective_contract.permissions.secret_refs == ("settings:eval_llm_api_key",)
    assert first.effective_contract.network.mode == "unrestricted_requested"


def test_manual_context_uses_authenticated_principal() -> None:
    principal = Principal(
        id="human:operator",
        kind=PrincipalKind.HUMAN,
        display_name="Operator",
        scopes=frozenset({"api"}),
    )
    auth = AuthContext(
        principal=principal,
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id="request-correlation",
    )

    with bind_auth_context(auth):
        context = UniverseExecutionContextFactory(security_mode="trusted-local").create(
            operation="sandbox", timeout_seconds=30
        )

    assert context.actor is principal
    assert context.correlation_id.startswith("universe:sandbox:")


def test_secure_remote_context_freezes_denied_network() -> None:
    context = UniverseExecutionContextFactory(security_mode="secure-remote").create(
        operation="sandbox",
        timeout_seconds=30,
    )

    assert context.effective_contract.network.mode == "deny"
