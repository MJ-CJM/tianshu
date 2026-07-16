"""Connection-level side-effect intent/receipt journal behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from tianshu.models import Edict, Memorial
from tianshu.models.canonical import canonical_sha256
from tianshu.models.side_effect import (
    SideEffectIntentV1,
    SideEffectReceiptV1,
    SideEffectSemantics,
    SideEffectStatus,
    build_side_effect_intent,
)
from tianshu.storage import Storage
from tianshu.storage.migrations import MIGRATIONS
from tianshu.storage.side_effect_journal import (
    SideEffectConflict,
    SideEffectDecodeError,
)

_NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _seed_claimed(storage: Storage, *, owner: str = "worker-1") -> object:
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id="memorial-1", edict_id="edict-1"))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-1",
        owner_id=owner,
        now=_NOW,
        lease_seconds=60,
    )
    assert claimed is not None
    return claimed


def _intent(claimed: object, **updates: object) -> SideEffectIntentV1:
    values: dict[str, object] = {
        "effect_id": "effect:1",
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "attempt_id": claimed.attempt_id,  # type: ignore[attr-defined]
        "owner_id": claimed.owner_id,  # type: ignore[attr-defined]
        "fencing_token": claimed.fencing_token,  # type: ignore[attr-defined]
        "sequence_no": 0,
        "boundary": "fake-provider",
        "operation": "send",
        "semantics": SideEffectSemantics.PROVIDER_IDEMPOTENT,
        "request_metadata": {"subject": "stable", "body_hash": "a" * 64},
        "created_at": _NOW + timedelta(seconds=1),
    }
    values.update(updates)
    return build_side_effect_intent(**values)  # type: ignore[arg-type]


def _receipt(
    intent: SideEffectIntentV1,
    claimed: object,
    *,
    version: int,
) -> SideEffectReceiptV1:
    result = {"accepted": True, "provider_sequence": 1}
    return SideEffectReceiptV1(
        intent_id=intent.intent_id,
        effect_id=intent.effect_id,
        edict_id=intent.edict_id,
        memorial_id=intent.memorial_id,
        attempt_id=claimed.attempt_id,  # type: ignore[attr-defined]
        owner_id=claimed.owner_id,  # type: ignore[attr-defined]
        fencing_token=claimed.fencing_token,  # type: ignore[attr-defined]
        provider_receipt_id="provider-receipt-1",
        result_metadata=result,
        result_hash=canonical_sha256(result),
        status=SideEffectStatus.RECEIPTED,
        reason_code=None,
        version=version,
        effective_at=_NOW + timedelta(seconds=2),
        recorded_at=_NOW + timedelta(seconds=3),
    )


def test_v15_is_append_only_live_tail_with_reviewable_journal_objects() -> None:
    assert MIGRATIONS[-2].version == 14
    assert MIGRATIONS[-2].name == "0014_execution_attempt_ledger"
    assert MIGRATIONS[-1].version == 15
    assert MIGRATIONS[-1].name == "0015_side_effect_journal"

    storage = Storage(":memory:")
    storage.init_db()
    try:
        columns = {
            row[1]
            for row in storage._conn.execute("PRAGMA table_info(side_effect_journal)")  # noqa: SLF001
        }
        assert {
            "intent_id",
            "effect_id",
            "edict_id",
            "memorial_id",
            "attempt_id",
            "fencing_token",
            "provider_idempotency_key",
            "request_hash",
            "intent_hash",
            "receipt_attempt_id",
            "receipt_fencing_token",
            "result_hash",
            "uncertainty_decision_id",
            "version",
        } <= columns
        indexes = {
            row[1]
            for row in storage._conn.execute(  # noqa: SLF001
                "PRAGMA index_list(side_effect_journal)"
            )
        }
        assert {
            "idx_side_effect_journal_attempt",
            "idx_side_effect_journal_uncertain",
            "uq_side_effect_journal_provider_key",
        } <= indexes
    finally:
        storage.close()


def test_begin_is_idempotent_and_mismatched_identity_conflicts(tmp_path: Path) -> None:
    storage = _open(tmp_path / "begin.db")
    claimed = _seed_claimed(storage)
    intent = _intent(claimed)
    try:
        first = storage.side_effect_journal.begin_intent(intent)
        replay = storage.side_effect_journal.begin_intent(intent)

        assert replay == first == intent
        assert storage.side_effect_journal.load_intent(intent.intent_id) == intent
        with pytest.raises(SideEffectConflict, match="identity"):
            storage.side_effect_journal.begin_intent(
                _intent(
                    claimed,
                    effect_id="effect:mismatch",
                    request_metadata={"subject": "changed"},
                )
            )
        assert storage.side_effect_journal.load_intent(intent.intent_id) == intent
    finally:
        storage.close()


def test_concurrent_duplicate_begin_has_one_durable_row(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.db"
    first = _open(path)
    claimed = _seed_claimed(first)
    second = _open(path)
    intent = _intent(claimed)
    barrier = Barrier(2)

    def begin(storage: Storage) -> SideEffectIntentV1:
        barrier.wait(timeout=2)
        return storage.side_effect_journal.begin_intent(intent)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(begin, (first, second)))
        assert results == [intent, intent]
        assert (
            first._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM side_effect_journal WHERE memorial_id='memorial-1'"
            ).fetchone()[0]
            == 1
        )
    finally:
        second.close()
        first.close()


def test_receipt_recording_is_versioned_fenced_and_idempotent(tmp_path: Path) -> None:
    storage = _open(tmp_path / "receipt.db")
    claimed = _seed_claimed(storage)
    intent = storage.side_effect_journal.begin_intent(_intent(claimed))
    receipt = _receipt(intent, claimed, version=2)
    try:
        saved = storage.side_effect_journal.record_receipt(
            receipt,
            expected_version=intent.version,
        )
        replay = storage.side_effect_journal.record_receipt(
            receipt,
            expected_version=intent.version,
        )

        assert saved == replay == receipt
        assert storage.side_effect_journal.load_receipt(intent.intent_id) == receipt
        with pytest.raises(SideEffectConflict, match="receipt"):
            storage.side_effect_journal.record_receipt(
                receipt.model_copy(
                    update={
                        "provider_receipt_id": "different",
                        "result_metadata": {"accepted": False},
                        "result_hash": canonical_sha256({"accepted": False}),
                    }
                ),
                expected_version=intent.version,
            )
    finally:
        storage.close()


def test_stale_fence_cannot_record_receipt_or_uncertainty(tmp_path: Path) -> None:
    storage = _open(tmp_path / "stale.db")
    claimed = _seed_claimed(storage)
    intent = storage.side_effect_journal.begin_intent(_intent(claimed))
    try:
        assert storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
            now=_NOW + timedelta(seconds=5),
        )
        with pytest.raises(SideEffectConflict, match="fence"):
            storage.side_effect_journal.record_receipt(
                _receipt(intent, claimed, version=2),
                expected_version=1,
            )
        with pytest.raises(SideEffectConflict, match="fence"):
            storage.side_effect_journal.mark_uncertain(
                intent.intent_id,
                attempt_id=claimed.attempt_id,
                owner_id=claimed.owner_id,
                fencing_token=claimed.fencing_token,
                expected_version=1,
                reason_code="unsupported_opaque_effect",
                decision_request_id=None,
                updated_at=_NOW + timedelta(seconds=3),
            )
        assert storage.side_effect_journal.load_intent(intent.intent_id) == intent
    finally:
        storage.close()


def test_corrupt_persisted_json_and_version_fail_closed(tmp_path: Path) -> None:
    storage = _open(tmp_path / "decode.db")
    claimed = _seed_claimed(storage)
    intent = storage.side_effect_journal.begin_intent(_intent(claimed))
    try:
        storage._conn.execute(  # noqa: SLF001
            "DROP TRIGGER side_effect_journal_identity_immutable"
        )
        storage._conn.execute(  # noqa: SLF001
            "UPDATE side_effect_journal SET request_metadata_json='{}' WHERE intent_id=?",
            (intent.intent_id,),
        )
        storage._conn.commit()  # noqa: SLF001
        with pytest.raises(SideEffectDecodeError):
            storage.side_effect_journal.load_intent(intent.intent_id)
    finally:
        storage.close()
