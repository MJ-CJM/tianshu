"""Fault matrix for the explicitly managed side-effect execution boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.application.continuation_recovery import ContinuationRecoveryService
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.executor.side_effects import (
    ManagedSideEffectService,
    ProviderEffectReceipt,
)
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.decision import DecisionStatus, ResolveDecisionCommand
from tianshu.models.events import EventEnvelope
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
from tianshu.storage.side_effect_journal import SideEffectConflict

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


def _seed_other_root(storage: Storage) -> AttemptAuthority:
    storage.save_edict(Edict(id="edict-2", goal="attack"))
    storage.save_memorial(Memorial(id="memorial-2", edict_id="edict-2", status=TaskStatus.RUNNING))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-2",
            available_at=_NOW,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-2",
        owner_id="worker-2",
        now=_NOW + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert claimed is not None and claimed.owner_id is not None
    return AttemptAuthority(
        attempt_id=claimed.attempt_id,
        memorial_id=claimed.memorial_id,
        owner_id=claimed.owner_id,
        fencing_token=claimed.fencing_token,
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
async def test_existing_intent_rejects_cross_root_authority_before_provider_call(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "cross-root.db")
    original_authority, _ = _seed(storage)
    attacking_authority = _seed_other_root(storage)
    provider = _Provider(SideEffectSemantics.PROVIDER_IDEMPOTENT, lookup_enabled=False)
    intent = _intent(original_authority, provider.semantics)

    def stop_after_intent(boundary: str) -> None:
        if boundary == "after_intent":
            raise RuntimeError("intent persisted")

    try:
        with pytest.raises(RuntimeError, match="intent persisted"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=stop_after_intent,
            ).execute(original_authority, intent, provider)
        before = storage.side_effect_journal.load_intent(intent.intent_id)

        with pytest.raises(RuntimeError, match="authority conflict"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
            ).execute(attacking_authority, intent, provider)

        assert provider.invocation_keys == []
        assert storage.side_effect_journal.load_intent(intent.intent_id) == before
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
async def test_receipt_lookup_reconciles_after_reopen_with_descendant_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lookup-descendant.db"
    storage = _open(path)
    original_authority, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    original_intent = _intent(original_authority, provider.semantics)

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise RuntimeError("injected after provider")

    with pytest.raises(RuntimeError, match="after provider"):
        await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock()),
            clock=_Clock(),
            boundary_hook=crash,
        ).execute(original_authority, original_intent, provider)
    assert provider.effective_count == 1
    storage.close()

    reopened = _open(path)
    try:
        claimed = reopened.attempt_repo.claim(
            memorial_id=original_authority.memorial_id,
            owner_id="worker-recovery",
            now=_NOW + timedelta(seconds=62),
            lease_seconds=60,
        )
        assert claimed is not None and claimed.owner_id is not None
        recovered_authority = AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
        )
        assert recovered_authority.attempt_id != original_authority.attempt_id
        assert recovered_authority.fencing_token > original_authority.fencing_token

        recovered = await ManagedSideEffectService(
            reopened,
            DecisionService(reopened, clock=_Clock(_NOW + timedelta(seconds=63))),
            clock=_Clock(_NOW + timedelta(seconds=63)),
        ).execute(
            recovered_authority,
            _intent(recovered_authority, provider.semantics),
            provider,
        )

        assert recovered.receipt is not None
        assert recovered.reconciled
        assert provider.effective_count == 1
        assert provider.invocation_keys == [original_intent.intent_id]
        assert provider.lookup_keys == [original_intent.intent_id, original_intent.intent_id]
        durable_intent = reopened.side_effect_journal.load_intent(original_intent.intent_id)
        assert durable_intent is not None
        assert durable_intent.attempt_id == original_authority.attempt_id
        assert durable_intent.owner_id == original_authority.owner_id
        assert durable_intent.fencing_token == original_authority.fencing_token
        assert recovered.receipt.attempt_id == recovered_authority.attempt_id
        assert recovered.receipt.owner_id == recovered_authority.owner_id
        assert recovered.receipt.fencing_token == recovered_authority.fencing_token
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_receipt_reconciliation_allows_reclaimed_origin_with_higher_fence(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "lookup-reclaimed-origin.db")
    original, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    intent = _intent(original, provider.semantics)

    def stop_after_intent(boundary: str) -> None:
        if boundary == "after_intent":
            raise RuntimeError("intent persisted")

    try:
        with pytest.raises(RuntimeError, match="intent persisted"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=stop_after_intent,
            ).execute(original, intent, provider)
        assert storage.attempt_repo.complete(
            attempt_id=original.attempt_id,
            owner_id=original.owner_id,
            fencing_token=original.fencing_token,
            outcome=AttemptOutcomeV1(
                disposition=AttemptDisposition.SUSPENDED,
                completed_at=_NOW + timedelta(seconds=11),
            ),
        )
        with storage.unit_of_work() as unit_of_work:
            row = unit_of_work.connection.execute(
                "SELECT version FROM execution_attempts WHERE attempt_id=?",
                (original.attempt_id,),
            ).fetchone()
            assert row is not None
            assert storage.attempt_repo.resume_suspended_current(
                unit_of_work.connection,
                attempt_id=original.attempt_id,
                memorial_id=original.memorial_id,
                expected_version=int(row[0]),
                available_at=_NOW + timedelta(seconds=12),
            )
            unit_of_work.commit()
        claimed = storage.attempt_repo.claim(
            memorial_id=original.memorial_id,
            owner_id="worker-reclaimed",
            now=_NOW + timedelta(seconds=12),
            lease_seconds=60,
        )
        assert claimed is not None and claimed.owner_id is not None
        reclaimed = AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
        )

        result = await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock(_NOW + timedelta(seconds=13))),
            clock=_Clock(_NOW + timedelta(seconds=13)),
        ).execute(reclaimed, _intent(reclaimed, provider.semantics), provider)

        assert result.receipt is not None
        assert reclaimed.attempt_id == original.attempt_id
        assert reclaimed.fencing_token > original.fencing_token
        durable = storage.side_effect_journal.load_intent(intent.intent_id)
        assert durable is not None
        assert durable.attempt_id == intent.attempt_id
        assert durable.owner_id == intent.owner_id
        assert durable.fencing_token == intent.fencing_token
        assert result.receipt.owner_id == reclaimed.owner_id
        assert result.receipt.fencing_token == reclaimed.fencing_token
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_receipt_reconciliation_composes_reclaim_then_retry_descendant(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "lookup-reclaim-then-retry.db")
    original, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    intent = _intent(original, provider.semantics)

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise RuntimeError("provider returned before receipt")

    try:
        with pytest.raises(RuntimeError, match="before receipt"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=crash,
            ).execute(original, intent, provider)
        assert provider.effective_count == 1
        assert storage.attempt_repo.complete(
            attempt_id=original.attempt_id,
            owner_id=original.owner_id,
            fencing_token=original.fencing_token,
            outcome=AttemptOutcomeV1(
                disposition=AttemptDisposition.SUSPENDED,
                completed_at=_NOW + timedelta(seconds=11),
            ),
        )
        with storage.unit_of_work() as unit_of_work:
            row = unit_of_work.connection.execute(
                "SELECT version FROM execution_attempts WHERE attempt_id=?",
                (original.attempt_id,),
            ).fetchone()
            assert row is not None
            assert storage.attempt_repo.resume_suspended_current(
                unit_of_work.connection,
                attempt_id=original.attempt_id,
                memorial_id=original.memorial_id,
                expected_version=int(row[0]),
                available_at=_NOW + timedelta(seconds=12),
            )
            unit_of_work.commit()
        reclaimed = storage.attempt_repo.claim(
            memorial_id=original.memorial_id,
            owner_id="worker-reclaimed",
            now=_NOW + timedelta(seconds=12),
            lease_seconds=60,
        )
        assert reclaimed is not None
        assert reclaimed.attempt_id == original.attempt_id
        assert reclaimed.fencing_token == original.fencing_token + 1

        retried = storage.attempt_repo.claim(
            memorial_id=original.memorial_id,
            owner_id="worker-retry",
            now=_NOW + timedelta(seconds=73),
            lease_seconds=60,
        )
        assert retried is not None and retried.owner_id is not None
        authority = AttemptAuthority(
            attempt_id=retried.attempt_id,
            memorial_id=retried.memorial_id,
            owner_id=retried.owner_id,
            fencing_token=retried.fencing_token,
        )
        assert authority.attempt_id != original.attempt_id
        assert retried.attempt_no == reclaimed.attempt_no + 1
        assert authority.fencing_token == original.fencing_token + 2

        result = await ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock(_NOW + timedelta(seconds=74))),
            clock=_Clock(_NOW + timedelta(seconds=74)),
        ).execute(authority, _intent(authority, provider.semantics), provider)

        assert result.receipt is not None and result.reconciled
        assert provider.effective_count == 1
        assert provider.invocation_keys == [intent.intent_id]
        assert provider.lookup_keys == [intent.intent_id, intent.intent_id]
        durable = storage.side_effect_journal.load_intent(intent.intent_id)
        assert durable is not None
        assert durable.attempt_id == original.attempt_id
        assert durable.owner_id == original.owner_id
        assert durable.fencing_token == original.fencing_token
        assert result.receipt.attempt_id == authority.attempt_id
        assert result.receipt.owner_id == authority.owner_id
        assert result.receipt.fencing_token == authority.fencing_token
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_reconciliation_rejects_stale_and_mismatched_callers_before_provider(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "lookup-reject-stale-mismatch.db")
    original, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    original_intent = _intent(original, provider.semantics)

    def stop_after_intent(boundary: str) -> None:
        if boundary == "after_intent":
            raise RuntimeError("intent persisted")

    try:
        with pytest.raises(RuntimeError, match="intent persisted"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=stop_after_intent,
            ).execute(original, original_intent, provider)
        claimed = storage.attempt_repo.claim(
            memorial_id=original.memorial_id,
            owner_id="worker-recovery",
            now=_NOW + timedelta(seconds=62),
            lease_seconds=60,
        )
        assert claimed is not None and claimed.owner_id is not None
        recovered = AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
        )
        before = storage.side_effect_journal.load_intent(original_intent.intent_id)
        service = ManagedSideEffectService(
            storage,
            DecisionService(storage, clock=_Clock(_NOW + timedelta(seconds=63))),
            clock=_Clock(_NOW + timedelta(seconds=63)),
        )

        with pytest.raises(SideEffectConflict, match="execution fence"):
            await service.execute(original, original_intent, provider)

        mismatched = build_side_effect_intent(
            effect_id=original_intent.effect_id,
            edict_id=original_intent.edict_id,
            memorial_id=recovered.memorial_id,
            attempt_id=recovered.attempt_id,
            owner_id=recovered.owner_id,
            fencing_token=recovered.fencing_token,
            sequence_no=original_intent.sequence_no,
            boundary=original_intent.boundary,
            operation=original_intent.operation,
            semantics=original_intent.semantics,
            request_metadata={"subject": "different", "body_hash": "b" * 64},
            created_at=_NOW + timedelta(seconds=63),
        )
        with pytest.raises(SideEffectConflict, match="replay identity"):
            await service.execute(recovered, mismatched, provider)

        assert provider.lookup_keys == []
        assert provider.invocation_keys == []
        assert storage.side_effect_journal.load_intent(original_intent.intent_id) == before
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_reconciliation_rejects_same_root_without_repository_lineage(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "lookup-reject-unrelated.db")
    original, _ = _seed(storage)
    provider = _Provider(SideEffectSemantics.RECEIPT_LOOKUP, lookup_enabled=True)
    original_intent = _intent(original, provider.semantics)

    def stop_after_intent(boundary: str) -> None:
        if boundary == "after_intent":
            raise RuntimeError("intent persisted")

    try:
        with pytest.raises(RuntimeError, match="intent persisted"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock()),
                clock=_Clock(),
                boundary_hook=stop_after_intent,
            ).execute(original, original_intent, provider)
        unrelated = AttemptAuthority(
            attempt_id="attempt-unrelated-gap",
            memorial_id=original.memorial_id,
            owner_id="worker-unrelated",
            fencing_token=original.fencing_token + 1,
        )
        now = _NOW + timedelta(seconds=62)
        with storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            connection.execute(
                """
                UPDATE execution_attempts
                SET status='failed', owner_id=NULL, heartbeat_at=NULL,
                    lease_expires_at=NULL, failure_json=?,
                    version=version + 1, updated_at=?
                WHERE attempt_id=?
                """,
                (
                    '{"code":"test_failure","message":"test",'
                    '"retryable":false,"details_hash":null}',
                    now.isoformat(),
                    original.attempt_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO execution_attempts (
                    attempt_id, schema_version, memorial_id, attempt_no, status,
                    owner_id, fencing_token, lease_expires_at, heartbeat_at,
                    available_at, max_attempts, failure_json, version, created_at, updated_at
                ) VALUES (?, 1, ?, 3, 'claimed', ?, ?, ?, ?, ?, 3, NULL, 1, ?, ?)
                """,
                (
                    unrelated.attempt_id,
                    unrelated.memorial_id,
                    unrelated.owner_id,
                    unrelated.fencing_token,
                    (now + timedelta(seconds=60)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            unit_of_work.commit()
        before = storage.side_effect_journal.load_intent(original_intent.intent_id)

        with pytest.raises(SideEffectConflict, match="unrelated"):
            await ManagedSideEffectService(
                storage,
                DecisionService(storage, clock=_Clock(now)),
                clock=_Clock(now),
            ).execute(unrelated, _intent(unrelated, provider.semantics), provider)

        assert provider.lookup_keys == []
        assert provider.invocation_keys == []
        assert storage.side_effect_journal.load_intent(original_intent.intent_id) == before
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_attempt", "expected_phase", "expected_cursor"),
    (
        ("approve", "claimable", RunPhase.EXECUTING, 1),
        ("reject", "failed", RunPhase.FAILED, 0),
    ),
)
async def test_uncertain_effect_resolution_resumes_or_cancels_without_invocation(
    tmp_path: Path,
    action: str,
    expected_attempt: str,
    expected_phase: RunPhase,
    expected_cursor: int,
) -> None:
    storage = _open(tmp_path / f"opaque-{action}.db")
    authority, run_state = _seed(storage)
    provider = _OpaqueProvider()
    intent = _intent(authority, provider.semantics)
    clock = _Clock()
    decisions = DecisionService(storage, clock=clock)
    try:
        result = await ManagedSideEffectService(
            storage,
            decisions,
            clock=clock,
        ).execute(
            authority,
            intent,
            provider,
            uncertainty_run_state=run_state,
            decision_auth=_auth(),
            decision_expires_at=_NOW + timedelta(hours=1),
        )
        assert result.decision_request_id is not None
        decisions.resolve(
            result.decision_request_id,
            ResolveDecisionCommand(
                action=action,
                reason="reviewed",
                payload={"schema_version": 1},
                expected_version=1,
            ),
            auth=_auth(),
        )
        event = EventEnvelope(
            event_id=f"{result.decision_request_id}:resolved:test",
            event_type="decision.resolved",
            edict_id="edict-1",
            memorial_id="memorial-1",
            producer="test",
            timestamp=clock.now,
            payload={
                "schema_version": 1,
                "decision_request_id": result.decision_request_id,
                "kind": "tool",
                "action": action,
                "request_version": 2,
                "correlation_id": "effect:test",
            },
        )
        recovery = ContinuationRecoveryService(storage, clock=clock)
        assert await recovery.handle_decision_resolved(event) is True
        assert await recovery.handle_decision_resolved(event) is False

        durable = storage.run_state_repo.load(storage._conn, "memorial-1")  # noqa: SLF001
        assert durable is not None and durable.phase is expected_phase
        assert durable.side_effect_cursor == expected_cursor
        assert durable.continuation.pending_tool is None
        assert provider.invocations == 0
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE attempt_id=?",
                (authority.attempt_id,),
            ).fetchone()[0]
            == expected_attempt
        )
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
