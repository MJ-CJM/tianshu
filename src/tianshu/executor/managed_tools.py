"""Production adapter from ToolRegistry calls to the durable effect journal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.executor.side_effects import (
    ManagedSideEffectService,
    ProviderEffectReceipt,
)
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.ambient import bind_tool_invocation_id
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.models.side_effect import (
    SideEffectIntentV1,
    SideEffectSemantics,
    build_side_effect_intent,
)
from tianshu.security.redact import redact_text
from tianshu.tools.types import ToolResult

_current_authority: ContextVar[AttemptAuthority | None] = ContextVar(
    "managed_attempt_authority",
    default=None,
)


class ManagedRunSuspended(RuntimeError):
    """Structured unwind after durable suspension; not an execution failure."""


@contextmanager
def bind_managed_attempt_authority(authority: AttemptAuthority) -> Iterator[None]:
    token: Token[AttemptAuthority | None] = _current_authority.set(authority)
    try:
        yield
    finally:
        _current_authority.reset(token)


def get_managed_attempt_authority() -> AttemptAuthority | None:
    return _current_authority.get()


class _RegistryBoundary:
    name = "tool-registry"

    def __init__(
        self,
        semantics: SideEffectSemantics,
        invoke: Callable[[str], Awaitable[ToolResult]],
        clock: Callable[[], datetime],
    ) -> None:
        self.semantics = semantics
        self._invoke = invoke
        self._clock = clock

    async def invoke(self, intent: SideEffectIntentV1) -> ProviderEffectReceipt:
        key = intent.provider_idempotency_key
        if key is None:  # pragma: no cover - model enforces this for supported semantics
            raise ValueError("managed provider invocation requires an idempotency key")
        with bind_tool_invocation_id(key):
            result = await self._invoke(key)
        content = redact_text(result.content or "")[:4096]
        return ProviderEffectReceipt(
            provider_receipt_id=key,
            result_metadata={"content": content, "is_error": result.is_error},
            effective_at=self._clock(),
        )

    async def lookup_receipt(self, idempotency_key: str) -> ProviderEffectReceipt | None:
        del idempotency_key
        return None


class ManagedToolEffectExecutor:
    """Translate one real tool invocation into durable intent/receipt evidence."""

    def __init__(
        self,
        storage: object,
        decision_service: DecisionService,
        *,
        clock: Callable[[], datetime] | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))
        self._service = ManagedSideEffectService(
            storage,  # type: ignore[arg-type]
            decision_service,
            clock=self._clock,
            boundary_hook=boundary_hook,
        )

    async def execute(
        self,
        *,
        authority: AttemptAuthority,
        tool_name: str,
        arguments: dict[str, object],
        invocation_id: str | None,
        semantics: SideEffectSemantics | None,
        invoke: Callable[[str], Awaitable[ToolResult]],
    ) -> ToolResult:
        if not invocation_id:
            raise ValueError("managed side effect requires a durable invocation id")
        now = self._now()
        effect_id = "tool:" + canonical_sha256(
            {"memorial_id": authority.memorial_id, "invocation_id": invocation_id}
        )
        sequence_no, edict_id = self._identity(authority, effect_id)
        effective_semantics = semantics or SideEffectSemantics.OPAQUE_CLI
        request_metadata: dict[str, JsonValue] = {
            "arguments_hash": canonical_sha256(arguments),
            "tool_name": tool_name,
        }
        intent = build_side_effect_intent(
            effect_id=effect_id,
            edict_id=edict_id,
            memorial_id=authority.memorial_id,
            attempt_id=authority.attempt_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token,
            sequence_no=sequence_no,
            boundary="tool-registry",
            operation=tool_name,
            semantics=effective_semantics,
            request_metadata=request_metadata,
            created_at=now,
        )
        run_state = None
        auth = None
        expires_at = None
        if effective_semantics not in {
            SideEffectSemantics.PROVIDER_IDEMPOTENT,
            SideEffectSemantics.RECEIPT_LOOKUP,
        }:
            run_state = self._ensure_agent_run_state(authority, edict_id, now)
            auth = _decision_auth(intent.intent_id)
            expires_at = now + timedelta(days=1)
        result = await self._service.execute(
            authority,
            intent,
            _RegistryBoundary(effective_semantics, invoke, self._clock),
            uncertainty_run_state=run_state,
            decision_auth=auth,
            decision_expires_at=expires_at,
        )
        if result.uncertain:
            raise ManagedRunSuspended(result.decision_request_id or "managed effect suspended")
        if result.receipt is None:  # pragma: no cover - service result contract
            raise RuntimeError("managed effect completed without a receipt")
        metadata = result.receipt.result_metadata
        return ToolResult(
            content=str(metadata.get("content", "")),
            is_error=bool(metadata.get("is_error", False)),
        )

    def _identity(self, authority: AttemptAuthority, effect_id: str) -> tuple[int, str]:
        storage = self._storage
        with storage.unit_of_work() as unit_of_work:  # type: ignore[attr-defined]
            connection = unit_of_work.connection
            row = connection.execute(
                "SELECT edict_id FROM memorials WHERE id=?",
                (authority.memorial_id,),
            ).fetchone()
            if row is None:
                raise ValueError("managed attempt memorial does not exist")
            existing = storage.side_effect_journal.load_by_effect_current(  # type: ignore[attr-defined]
                connection,
                effect_id,
            )
            if existing is None:
                sequence_no = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence_no), -1) + 1 "
                        "FROM side_effect_journal WHERE memorial_id=?",
                        (authority.memorial_id,),
                    ).fetchone()[0]
                )
            else:
                sequence_no = existing.sequence_no
            unit_of_work.commit()
        return sequence_no, str(row[0])

    def _ensure_agent_run_state(
        self,
        authority: AttemptAuthority,
        edict_id: str,
        now: datetime,
    ) -> RunStateV1:
        storage = self._storage
        with storage.unit_of_work() as unit_of_work:  # type: ignore[attr-defined]
            current = storage.run_state_repo.load(  # type: ignore[attr-defined]
                unit_of_work.connection,
                authority.memorial_id,
            )
            if current is None:
                current = RunStateV1(
                    memorial_id=authority.memorial_id,
                    edict_id=edict_id,
                    phase=RunPhase.EXECUTING,
                    continuation=AgentContinuationV1(
                        messages=(),
                        pending_tool=None,
                        iteration=0,
                        usage=PersistedUsageSummaryV1(
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            cache_read_tokens=0,
                            cost_cny=0.0,
                            actual_model=None,
                            upstream_provider=None,
                        ),
                        checkpoint_ref=None,
                        resolved_decision_id=None,
                        side_effect_cursor=0,
                    ),
                    checkpoint_ref=None,
                    side_effect_cursor=0,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                storage.run_state_repo.create(unit_of_work.connection, current)  # type: ignore[attr-defined]
            unit_of_work.commit()
        return current

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("managed tool clock must be timezone-aware")
        return now.astimezone(UTC)


def _decision_auth(intent_id: str) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="system:managed-tool-effect",
            kind=PrincipalKind.SERVICE,
            display_name="Managed tool effect",
            scopes=frozenset({"decision:request"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.SYSTEM,
        correlation_id=f"effect:{intent_id}",
    )


__all__ = [
    "ManagedRunSuspended",
    "ManagedToolEffectExecutor",
    "bind_managed_attempt_authority",
    "get_managed_attempt_authority",
]
