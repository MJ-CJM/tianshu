"""Fault matrix for the explicitly managed side-effect execution boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.executor.side_effects import (
    ManagedSideEffectService,
    ProviderEffectReceipt,
)
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.decision import DecisionStatus
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
    SideEffectStatus,
    build_side_effect_intent,
)
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime = _NOW + timedelta(seconds=10)) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Provider:
    def __init__(
        self,
        semantics: SideEffectSemantics,
        *,
        lookup_enabled: bool,
    ) -> None:
        self.name = "fake-provider"
        self.semantics = semantics
        self.lookup_enabled = lookup_enabled
        self.invocation_keys: list[str] = []
        self.lookup_keys: list[str] = []
        self.effective_count = 0
        self._receipts: dict[str, ProviderEffectReceipt] = {}

    async def invoke(self, intent: SideEffectIntentV1) -> ProviderEffectReceipt:
        assert intent.provider_idempotency_key is not None
        key = intent.provider_idempotency_key
        self.invocation_keys.append(key)
        existing = self._receipts.get(key)
        if existing is not None:
            return existing
        self.effective_count += 1
        receipt = ProviderEffectReceipt(
            provider_receipt_id=f"provider-{self.effective_count}",
            result_metadata={"accepted": True, "effective_count": self.effective_count},
            effective_at=_NOW + timedelta(seconds=6),
        )
        self._receipts[key] = receipt
        return receipt

    async def lookup_receipt(self, idempotency_key: str) -> ProviderEffectReceipt | None:
        self.lookup_keys.append(idempotency_key)
        if not self.lookup_enabled:
            return None
        return self._receipts.get(idempotency_key)


class _OpaqueProvider:
    name = "fake-provider"
    semantics = SideEffectSemantics.OPAQUE_CLI

    def __init__(self) -> None:
        self.invocations = 0

    async def invoke(self, intent: SideEffectIntentV1) -> ProviderEffectReceipt:
        del intent
        self.invocations += 1
        raise AssertionError("opaque provider must not be invoked")

    async def lookup_receipt(self, idempotency_key: str) -> ProviderEffectReceipt | None:
        del idempotency_key
        raise AssertionError("opaque provider must not be queried")


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _seed(storage: Storage) -> tuple[AttemptAuthority, RunStateV1]:
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id="memorial-1", edict_id="edict-1", status=TaskStatus.RUNNING))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
        )
        state = RunStateV1(
            memorial_id="memorial-1",
            edict_id="edict-1",
            phase=RunPhase.EXECUTING,
            continuation=AgentContinuationV1(
                messages=(),
                pending_tool=None,
                iteration=2,
                usage=PersistedUsageSummaryV1(
                    prompt_tokens=1,
                    completion_tokens=2,
                    total_tokens=3,
                    cache_read_tokens=0,
                    cost_cny=0.1,
                    actual_model="test",
                    upstream_provider="fake",
                ),
                checkpoint_ref=None,
                resolved_decision_id=None,
                side_effect_cursor=0,
            ),
            checkpoint_ref=None,
            side_effect_cursor=0,
            version=1,
            created_at=_NOW,
            updated_at=_NOW,
        )
        storage.run_state_repo.create(unit_of_work.connection, state)
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-1",
        owner_id="worker-1",
        now=_NOW + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert claimed is not None and claimed.owner_id is not None
    return (
        AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
        ),
        state,
    )


def _intent(
    authority: AttemptAuthority,
    semantics: SideEffectSemantics,
) -> SideEffectIntentV1:
    return build_side_effect_intent(
        effect_id="effect:stable:1",
        edict_id="edict-1",
        memorial_id=authority.memorial_id,
        attempt_id=authority.attempt_id,
        owner_id=authority.owner_id,
        fencing_token=authority.fencing_token,
        sequence_no=0,
        boundary="fake-provider",
        operation="send",
        semantics=semantics,
        request_metadata={"subject": "redacted", "body_hash": "a" * 64},
        created_at=_NOW + timedelta(seconds=2),
    )


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="system:managed-effect",
            kind=PrincipalKind.SERVICE,
            display_name="Managed effect boundary",
            scopes=frozenset({"decision:request"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.SYSTEM,
        correlation_id="effect:test",
    )


@pytest.mark.asyncio
async def test_crash_after_intent_before_provider_leaves_replayable_intent(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "before-provider.db")
    authority, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.PROVIDER_IDEMPOTENT, lookup_enabled=False)
    intent = _intent(authority, provider.semantics)

    def crash(boundary: str) -> None:
        if boundary == "after_intent":
            raise RuntimeError("injected after intent")

    try:
        with pytest.raises(RuntimeError, match="after intent"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=crash,
            ).execute(authority, intent, provider)
        assert provider.invocation_keys == []
        assert storage.side_effect_journal.load_intent(intent.intent_id) == intent

        result = await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock()),
            clock=_Clock(),
        ).execute(authority, intent, provider)
        assert result.receipt is not None
        assert provider.effective_count == 1
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_crash_after_effect_before_receipt_recovers_by_lookup(tmp_path: Path) -> None:
    storage = _open(tmp_path / "lookup.db")
    authority, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    intent = _intent(authority, provider.semantics)

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise RuntimeError("injected after provider")

    try:
        with pytest.raises(RuntimeError, match="after provider"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=crash,
            ).execute(authority, intent, provider)
        assert provider.effective_count == 1
        assert storage.side_effect_journal.load_receipt(intent.intent_id) is None

        recovered = await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock()),
            clock=_Clock(),
        ).execute(authority, intent, provider)
        assert recovered.receipt is not None
        assert recovered.reconciled
        assert provider.effective_count == 1
        assert provider.lookup_keys == [intent.intent_id, intent.intent_id]
        assert provider.invocation_keys == [intent.intent_id]
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_same_idempotency_key_replay_has_one_effect_and_receipt_precedes_ack(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "idempotent.db")
    authority, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.PROVIDER_IDEMPOTENT, lookup_enabled=False)
    intent = _intent(authority, provider.semantics)

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise RuntimeError("injected after provider")

    try:
        with pytest.raises(RuntimeError):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=crash,
            ).execute(authority, intent, provider)
        result = await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock()),
            clock=_Clock(),
        ).execute(authority, intent, provider)

        assert result.receipt is not None
        assert provider.invocation_keys == [intent.intent_id, intent.intent_id]
        assert provider.effective_count == 1
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()
        assert row[0] == "claimed"
        assert storage.side_effect_journal.load_receipt(intent.intent_id) == result.receipt

        assert storage.attempt_repo.complete(
            attempt_id=authority.attempt_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token,
            outcome=AttemptOutcomeV1(
                disposition=AttemptDisposition.SUCCEEDED,
                completed_at=_NOW + timedelta(seconds=7),
            ),
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE attempt_id=?",
                (authority.attempt_id,),
            ).fetchone()[0]
            == "succeeded"
        )
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_opaque_effect_suspends_once_with_atomic_decision_and_no_invocation(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "opaque.db")
    authority, run_state = _seed(storage)
    provider = _OpaqueProvider()
    intent = _intent(authority, provider.semantics)
    service = ManagedSideEffectService(
        storage,
        DecisionService(storage, clock=_Clock()),
        clock=_Clock(),
    )
    try:
        first = await service.execute(
            authority,
            intent,
            provider,
            uncertainty_run_state=run_state,
            decision_auth=_auth(),
            decision_expires_at=_NOW + timedelta(hours=1),
        )
        replay = await service.execute(
            authority,
            intent,
            provider,
            uncertainty_run_state=run_state,
            decision_auth=_auth(),
            decision_expires_at=_NOW + timedelta(hours=1),
        )

        assert first == replay
        assert first.uncertain
        assert first.decision_request_id is not None
        assert provider.invocations == 0
        record = DecisionService(storage, clock=_Clock()).get(first.decision_request_id)
        assert record is not None
        assert record.request.status is DecisionStatus.PENDING
        assert record.request.request_key == f"side-effect:{intent.intent_id}"
        durable_state = storage.run_state_repo.load(storage._conn, "memorial-1")  # noqa: SLF001
        assert durable_state is not None
        assert durable_state.phase is RunPhase.WAITING_DECISION
        assert durable_state.continuation.pending_decision_id == first.decision_request_id
        journal = storage.side_effect_journal.load_intent(intent.intent_id)
        assert journal is not None and journal.status is SideEffectStatus.UNCERTAIN
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE attempt_id=?",
                (authority.attempt_id,),
            ).fetchone()[0]
            == "suspended"
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_requests WHERE memorial_id='memorial-1'"
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='decision.requested'"
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_decision_outbox_failure_rolls_back_uncertainty_suspension(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "rollback.db")
    authority, run_state = _seed(storage)
    provider = _OpaqueProvider()
    intent = _intent(authority, provider.semantics)

    def fail(boundary: str) -> None:
        if boundary == "before_decision_outbox":
            raise RuntimeError("injected decision outbox failure")

    try:
        with pytest.raises(RuntimeError, match="outbox failure"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=fail,
            ).execute(
                authority,
                intent,
                provider,
                uncertainty_run_state=run_state,
                decision_auth=_auth(),
                decision_expires_at=_NOW + timedelta(hours=1),
            )
        journal = storage.side_effect_journal.load_intent(intent.intent_id)
        assert journal is not None and journal.status is SideEffectStatus.INTENDED
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_requests"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='decision.requested'"
            ).fetchone()[0]
            == 0
        )
        durable_state = storage.run_state_repo.load(storage._conn, "memorial-1")  # noqa: SLF001
        assert durable_state == run_state
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE attempt_id=?",
                (authority.attempt_id,),
            ).fetchone()[0]
            == "claimed"
        )
        assert provider.invocations == 0
    finally:
        storage.close()


def test_provider_exception_is_redacted_and_never_stored_as_raw_payload() -> None:
    error = RedactedError(
        code="managed_effect_failed",
        message="Managed side effect failed",
        retryable=True,
        details_hash="a" * 64,
    )
    assert "secret" not in error.model_dump_json()
