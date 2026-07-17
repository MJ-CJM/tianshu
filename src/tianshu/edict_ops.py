"""Deprecated compatibility entry point for Edict submission."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING

from tianshu.application.edicts import (
    EdictApplicationService,
    SubmitEdictCommand,
)
from tianshu.application.ingress import (
    make_ingress_auth_context,
    requested_contract_for_edict,
)
from tianshu.models import Edict, Memorial
from tianshu.models.canonical import JsonValue
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    PrincipalKind,
)
from tianshu.storage import Storage

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus


def submit_new_edict(
    storage: Storage,
    event_bus: EventBus,
    edict: Edict,
    *,
    producer: str,
    extra_payload: Mapping[str, JsonValue] | None = None,
    idempotency_key: str | None = None,
    auth: AuthContext | None = None,
    correlation_id: str | None = None,
    edict_application_service: EdictApplicationService,
) -> Memorial:
    """Delegate to the durable application service without owning persistence."""
    del event_bus
    warnings.warn(
        "submit_new_edict() is deprecated; call EdictApplicationService.submit()",
        DeprecationWarning,
        stacklevel=2,
    )
    stable_key = idempotency_key or edict.idempotency_key or f"legacy:{edict.id}"
    stable_correlation = correlation_id or f"legacy:{edict.id}"
    ingress_auth = auth or make_ingress_auth_context(
        principal_id=f"legacy:{edict.submitter or producer}",
        principal_kind=PrincipalKind.SERVICE,
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.SYSTEM,
        correlation_id=stable_correlation,
    )
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key=stable_key,
        requested_contract=requested_contract_for_edict(
            edict,
            default_workspace_id=str(edict.metadata.get("workspace_id") or "workspace-main"),
        ),
        extra_payload=extra_payload or {},
    )
    return edict_application_service.submit(
        command,
        auth=ingress_auth,
        producer=producer,
        correlation_id=stable_correlation,
    ).memorial
