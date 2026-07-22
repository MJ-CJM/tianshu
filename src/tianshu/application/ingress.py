"""Small shared mappings for authenticated Edict ingress adapters."""

from __future__ import annotations

from tianshu.models import Edict
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)


def requested_contract_for_edict(
    edict: Edict,
    *,
    default_workspace_id: str = "workspace-main",
) -> RequestedGovernanceContractV1:
    return edict.governance_contract or LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id=default_workspace_id,
    )


def make_ingress_auth_context(
    *,
    principal_id: str,
    principal_kind: PrincipalKind,
    source: AuthenticationSource,
    client_kind: ClientKind,
    correlation_id: str,
) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=principal_kind,
            display_name=principal_id,
        ),
        source=source,
        client_kind=client_kind,
        correlation_id=correlation_id,
    )


__all__ = ["make_ingress_auth_context", "requested_contract_for_edict"]
