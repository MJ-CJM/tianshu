"""Explicit managed boundary for durable side-effect intent and receipt evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.governance.decision_service import DecisionService
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext
from tianshu.models.run_state import (
    AgentContinuationV1,
    RunPhase,
    RunStateV1,
    ToolProposalV1,
)
from tianshu.models.side_effect import (
    SideEffectIntentV1,
    SideEffectReceiptV1,
    SideEffectSemantics,
    SideEffectStatus,
)
from tianshu.storage.attempt_ledger import AttemptFenceLost
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.side_effect_journal import SideEffectConflict


@dataclass(frozen=True, slots=True)
class ProviderEffectReceipt:
    """Allowlisted provider result returned by one explicitly managed adapter."""

    provider_receipt_id: str
    result_metadata: dict[str, JsonValue]
    effective_at: datetime


class ManagedSideEffectBoundary(Protocol):
    name: str
    semantics: SideEffectSemantics

    async def invoke(self, intent: SideEffectIntentV1) -> ProviderEffectReceipt: ...

    async def lookup_receipt(self, idempotency_key: str) -> ProviderEffectReceipt | None: ...


@dataclass(frozen=True, slots=True)
class ManagedSideEffectResult:
    receipt: SideEffectReceiptV1 | None
    decision_request_id: str | None
    uncertain: bool
    reconciled: bool


class _ManagedEffectStorage(Protocol):
    attempt_repo: object
    decision_repo: object
    run_state_repo: object
    side_effect_journal: object

    def unit_of_work(self) -> object: ...


class ManagedSideEffectService:
    """Persist intent, execute/reconcile supported effects, then persist receipt."""

    def __init__(
        self,
        storage: _ManagedEffectStorage,
        decision_service: DecisionService,
        *,
        clock: Callable[[], datetime] | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = storage
        self._decision_service = decision_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._boundary_hook = boundary_hook
        self._outbox = OutboxRepository()

    async def execute(
        self,
        authority: AttemptAuthority,
        intent: SideEffectIntentV1,
        boundary: ManagedSideEffectBoundary,
        *,
        uncertainty_run_state: RunStateV1 | None = None,
        decision_auth: AuthContext | None = None,
        decision_expires_at: datetime | None = None,
    ) -> ManagedSideEffectResult:
        self._validate_boundary(intent, boundary)
        unsupported = intent.semantics not in {
            SideEffectSemantics.PROVIDER_IDEMPOTENT,
            SideEffectSemantics.RECEIPT_LOOKUP,
        }
        if unsupported:
            self._validate_uncertainty_inputs(
                intent,
                uncertainty_run_state=uncertainty_run_state,
                decision_auth=decision_auth,
                decision_expires_at=decision_expires_at,
            )
        durable = self._begin_or_recover(authority, intent)
        if durable.status is SideEffectStatus.RECEIPTED:
            receipt = self._storage.side_effect_journal.load_receipt(durable.intent_id)
            if receipt is None:
                raise SideEffectConflict("receipted intent has no durable receipt")
            return ManagedSideEffectResult(receipt, None, False, True)
        if durable.status is SideEffectStatus.UNCERTAIN:
            decision_id = self._uncertainty_decision_id(durable.intent_id)
            if decision_id is None:
                raise SideEffectConflict("uncertain intent has no durable decision")
            return ManagedSideEffectResult(None, decision_id, True, False)
        self._observe("after_intent")
        if unsupported:
            assert uncertainty_run_state is not None
            assert decision_auth is not None
            assert decision_expires_at is not None
            return self._suspend_uncertain(
                authority,
                durable,
                run_state=uncertainty_run_state,
                auth=decision_auth,
                expires_at=decision_expires_at,
            )
        self._require_current(authority, now=self._now())
        provider_receipt: ProviderEffectReceipt | None = None
        reconciled = False
        if durable.semantics is SideEffectSemantics.RECEIPT_LOOKUP:
            assert durable.provider_idempotency_key is not None
            provider_receipt = await boundary.lookup_receipt(durable.provider_idempotency_key)
            reconciled = provider_receipt is not None
        if provider_receipt is None:
            provider_receipt = await boundary.invoke(durable)
            self._observe("after_provider")
        receipt = self._record_receipt(authority, durable, provider_receipt)
        self._observe("after_receipt")
        return ManagedSideEffectResult(receipt, None, False, reconciled)

    def _begin_or_recover(
        self,
        authority: AttemptAuthority,
        requested: SideEffectIntentV1,
    ) -> SideEffectIntentV1:
        self._require_authority_matches_requested(authority, requested)
        with self._storage.unit_of_work() as unit_of_work:
            existing = self._storage.side_effect_journal.load_by_position_current(
                unit_of_work.connection,
                memorial_id=requested.memorial_id,
                sequence_no=requested.sequence_no,
            )
            if existing is None:
                durable = self._storage.side_effect_journal.begin_intent_current(
                    unit_of_work.connection,
                    requested,
                )
            else:
                if not self._same_business_intent(existing, requested):
                    raise SideEffectConflict("side-effect replay identity conflict")
                durable = existing
            unit_of_work.commit()
            return durable

    def _record_receipt(
        self,
        authority: AttemptAuthority,
        intent: SideEffectIntentV1,
        provider: ProviderEffectReceipt,
    ) -> SideEffectReceiptV1:
        now = self._now()
        receipt = SideEffectReceiptV1(
            intent_id=intent.intent_id,
            effect_id=intent.effect_id,
            edict_id=intent.edict_id,
            memorial_id=intent.memorial_id,
            attempt_id=authority.attempt_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token,
            provider_receipt_id=provider.provider_receipt_id,
            result_metadata=provider.result_metadata,
            result_hash=canonical_sha256(provider.result_metadata),
            status=SideEffectStatus.RECEIPTED,
            reason_code=None,
            version=intent.version + 1,
            effective_at=provider.effective_at,
            recorded_at=now,
        )
        return self._storage.side_effect_journal.record_receipt(
            receipt,
            expected_version=intent.version,
        )

    def _suspend_uncertain(
        self,
        authority: AttemptAuthority,
        intent: SideEffectIntentV1,
        *,
        run_state: RunStateV1,
        auth: AuthContext,
        expires_at: datetime,
    ) -> ManagedSideEffectResult:
        now = self._now()
        arguments: dict[str, JsonValue] = {
            "intent_id": intent.intent_id,
            "effect_id": intent.effect_id,
            "request_hash": intent.request_hash,
            "semantics": intent.semantics.value,
        }
        command = RequestDecisionCommand(
            kind=DecisionKind.TOOL,
            edict_id=intent.edict_id,
            memorial_id=intent.memorial_id,
            request_key=f"side-effect:{intent.intent_id}",
            payload={
                "schema_version": 1,
                "tool_name": f"{intent.boundary}:{intent.operation}",
                "arguments": arguments,
                "tool_tier": "uncertain",
                "policy_rule_id": "managed_side_effect_unsupported",
            },
            expires_at=expires_at,
        )
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            self._storage.attempt_repo.require_current(
                connection,
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                now=now,
            )
            current = self._storage.run_state_repo.load(connection, intent.memorial_id)
            if current != run_state:
                raise SideEffectConflict("side-effect RunState changed before suspension")
            if (
                current.phase is not RunPhase.EXECUTING
                or not isinstance(current.continuation, AgentContinuationV1)
                or current.continuation.pending_decision_id is not None
                or current.continuation.pending_tool is not None
            ):
                raise SideEffectConflict("side-effect RunState cannot be suspended")
            request = self._decision_service.request_in_transaction(
                unit_of_work,
                command,
                auth=auth,
            )
            proposal = ToolProposalV1(
                tool_call_id=intent.effect_id,
                tool_name=f"{intent.boundary}:{intent.operation}",
                arguments=arguments,
                arguments_hash=canonical_sha256(arguments),
                tool_tier="uncertain",
                policy_rule_id="managed_side_effect_unsupported",
                proposed_at=request.created_at,
            )
            continuation = current.continuation.model_copy(
                update={
                    "pending_tool": proposal,
                    "pending_decision_id": request.decision_request_id,
                    "resolved_decision_id": None,
                }
            )
            waiting = current.model_copy(
                update={
                    "phase": RunPhase.WAITING_DECISION,
                    "continuation": continuation,
                    "updated_at": now,
                }
            )
            self._storage.run_state_repo.compare_and_swap(
                connection,
                waiting,
                expected_version=current.version,
            )
            self._storage.side_effect_journal.mark_uncertain_current(
                connection,
                intent.intent_id,
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                expected_version=intent.version,
                reason_code=f"unsupported_{intent.semantics.value}",
                decision_request_id=request.decision_request_id,
                updated_at=now,
            )
            self._observe("before_decision_outbox")
            self._outbox.add(
                connection,
                EventEnvelope(
                    event_id=f"{request.decision_request_id}:decision.requested",
                    event_type="decision.requested",
                    edict_id=intent.edict_id,
                    memorial_id=intent.memorial_id,
                    producer="managed-side-effect.v1",
                    timestamp=now,
                    payload={
                        "schema_version": 1,
                        "decision_request_id": request.decision_request_id,
                        "intent_id": intent.intent_id,
                        "kind": request.kind.value,
                        "correlation_id": auth.correlation_id,
                    },
                ),
            )
            self._observe("before_suspend_attempt")
            if not self._storage.attempt_repo.complete_current(
                connection,
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                outcome=AttemptOutcomeV1(
                    disposition=AttemptDisposition.SUSPENDED,
                    completed_at=now,
                ),
            ):
                raise AttemptFenceLost("attempt authority is no longer current")
            unit_of_work.commit()
        return ManagedSideEffectResult(
            receipt=None,
            decision_request_id=request.decision_request_id,
            uncertain=True,
            reconciled=False,
        )

    def _require_current(self, authority: AttemptAuthority, *, now: datetime) -> None:
        with self._storage.unit_of_work() as unit_of_work:
            self._storage.attempt_repo.require_current(
                unit_of_work.connection,
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                now=now,
            )
            unit_of_work.commit()

    def _uncertainty_decision_id(self, intent_id: str) -> str | None:
        with self._storage.unit_of_work() as unit_of_work:
            decision_id = self._storage.side_effect_journal.uncertainty_decision_id_current(
                unit_of_work.connection,
                intent_id,
            )
            unit_of_work.commit()
            return decision_id

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("managed side-effect clock must be timezone-aware")
        return now.astimezone(UTC)

    def _observe(self, boundary: str) -> None:
        if self._boundary_hook is not None:
            self._boundary_hook(boundary)

    @staticmethod
    def _validate_boundary(
        intent: SideEffectIntentV1,
        boundary: ManagedSideEffectBoundary,
    ) -> None:
        if boundary.name != intent.boundary or boundary.semantics is not intent.semantics:
            raise ValueError("managed side-effect boundary does not match intent")

    @staticmethod
    def _validate_uncertainty_inputs(
        intent: SideEffectIntentV1,
        *,
        uncertainty_run_state: RunStateV1 | None,
        decision_auth: AuthContext | None,
        decision_expires_at: datetime | None,
    ) -> None:
        if (
            uncertainty_run_state is None
            or decision_auth is None
            or decision_expires_at is None
            or uncertainty_run_state.memorial_id != intent.memorial_id
            or uncertainty_run_state.edict_id != intent.edict_id
        ):
            raise ValueError("unsupported effect requires durable Decision suspension inputs")

    @staticmethod
    def _require_authority_matches_requested(
        authority: AttemptAuthority,
        intent: SideEffectIntentV1,
    ) -> None:
        if (
            authority.attempt_id != intent.attempt_id
            or authority.memorial_id != intent.memorial_id
            or authority.owner_id != intent.owner_id
            or authority.fencing_token != intent.fencing_token
        ):
            raise SideEffectConflict("side-effect originating authority conflict")

    @staticmethod
    def _same_business_intent(
        existing: SideEffectIntentV1,
        requested: SideEffectIntentV1,
    ) -> bool:
        return (
            existing.intent_id == requested.intent_id
            and existing.effect_id == requested.effect_id
            and existing.edict_id == requested.edict_id
            and existing.memorial_id == requested.memorial_id
            and existing.attempt_id == requested.attempt_id
            and existing.owner_id == requested.owner_id
            and existing.fencing_token == requested.fencing_token
            and existing.sequence_no == requested.sequence_no
            and existing.boundary == requested.boundary
            and existing.operation == requested.operation
            and existing.semantics is requested.semantics
            and existing.request_hash == requested.request_hash
            and existing.intent_hash == requested.intent_hash
            and existing.provider_idempotency_key == requested.provider_idempotency_key
        )


__all__ = [
    "ManagedSideEffectBoundary",
    "ManagedSideEffectResult",
    "ManagedSideEffectService",
    "ProviderEffectReceipt",
]
