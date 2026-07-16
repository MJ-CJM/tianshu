"""Strict contracts for managed side-effect intent and receipt evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256
from tianshu.models.side_effect import (
    SideEffectIntentV1,
    SideEffectReceiptV1,
    SideEffectSemantics,
    SideEffectStatus,
    build_side_effect_intent,
)

_NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def _intent(**updates: object) -> SideEffectIntentV1:
    values: dict[str, object] = {
        "effect_id": "effect:notify:42",
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "attempt_id": "attempt-1",
        "owner_id": "worker-1",
        "fencing_token": 7,
        "sequence_no": 2,
        "boundary": "fake-notifier",
        "operation": "send",
        "semantics": SideEffectSemantics.PROVIDER_IDEMPOTENT,
        "request_metadata": {"channel": "test", "body_hash": "a" * 64},
        "created_at": _NOW,
    }
    values.update(updates)
    return build_side_effect_intent(**values)  # type: ignore[arg-type]


def _receipt(intent: SideEffectIntentV1, **updates: object) -> SideEffectReceiptV1:
    result_metadata = {"delivery": "accepted", "provider_sequence": 9}
    values: dict[str, object] = {
        "intent_id": intent.intent_id,
        "effect_id": intent.effect_id,
        "edict_id": intent.edict_id,
        "memorial_id": intent.memorial_id,
        "attempt_id": "attempt-2",
        "owner_id": "worker-2",
        "fencing_token": 8,
        "provider_receipt_id": "receipt-9",
        "result_metadata": result_metadata,
        "result_hash": canonical_sha256(result_metadata),
        "status": SideEffectStatus.RECEIPTED,
        "reason_code": None,
        "version": 2,
        "effective_at": _NOW + timedelta(seconds=1),
        "recorded_at": _NOW + timedelta(seconds=2),
    }
    values.update(updates)
    return SideEffectReceiptV1.model_validate(values)


def test_intent_is_strict_frozen_versioned_and_canonically_stable() -> None:
    first = _intent()
    reordered = build_side_effect_intent(
        effect_id="effect:notify:42",
        edict_id="edict-1",
        memorial_id="memorial-1",
        attempt_id="attempt-1",
        owner_id="worker-1",
        fencing_token=7,
        sequence_no=2,
        boundary="fake-notifier",
        operation="send",
        semantics=SideEffectSemantics.PROVIDER_IDEMPOTENT,
        request_metadata={"body_hash": "a" * 64, "channel": "test"},
        created_at=_NOW,
    )

    assert reordered.intent_id == first.intent_id
    assert reordered.intent_hash == first.intent_hash
    assert reordered.request_hash == first.request_hash
    assert first.schema_version == 1
    assert first.status is SideEffectStatus.INTENDED
    assert first.provider_idempotency_key == first.intent_id
    with pytest.raises(ValidationError, match="frozen"):
        first.status = SideEffectStatus.RECEIPTED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SideEffectIntentV1.model_validate(first.model_dump() | {"schema_version": 2})
    with pytest.raises(ValidationError):
        SideEffectIntentV1.model_validate(first.model_dump() | {"raw_payload": {}})


@pytest.mark.parametrize(
    "metadata",
    [
        {"authorization": "Bearer live-secret"},
        {"nested": {"api_key": "sk-live-secret"}},
        {"environment": {"SERVICE_TOKEN": "live-secret"}},
    ],
)
def test_intent_rejects_raw_credentials(metadata: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="secret"):
        _intent(request_metadata=metadata)


def test_intent_rejects_hash_identity_and_capability_mismatch() -> None:
    intent = _intent()
    with pytest.raises(ValidationError, match="request_hash"):
        SideEffectIntentV1.model_validate(intent.model_dump() | {"request_hash": "b" * 64})
    with pytest.raises(ValidationError, match="intent_hash"):
        SideEffectIntentV1.model_validate(intent.model_dump() | {"intent_hash": "b" * 64})
    with pytest.raises(ValidationError, match="idempotency"):
        SideEffectIntentV1.model_validate(
            intent.model_dump() | {"provider_idempotency_key": "different"}
        )
    with pytest.raises(ValidationError, match="idempotency"):
        _intent(
            semantics=SideEffectSemantics.OPAQUE_CLI,
            provider_idempotency_key="must-not-be-present",
        )


def test_unsupported_intent_has_no_provider_key_and_keeps_truthful_semantics() -> None:
    opaque = _intent(semantics=SideEffectSemantics.OPAQUE_CLI)
    untracked = _intent(
        effect_id="effect:external:42",
        semantics=SideEffectSemantics.UNTRACKED_EXTERNAL,
    )

    assert opaque.provider_idempotency_key is None
    assert untracked.provider_idempotency_key is None
    assert opaque.status is SideEffectStatus.INTENDED


def test_receipt_is_strict_bound_hashed_redacted_and_utc() -> None:
    intent = _intent()
    receipt = _receipt(intent)

    assert receipt.schema_version == 1
    assert receipt.result_hash == canonical_sha256(receipt.result_metadata)
    assert receipt.attempt_id == "attempt-2"
    with pytest.raises(ValidationError, match="result_hash"):
        _receipt(intent, result_hash="b" * 64)
    with pytest.raises(ValidationError, match="secret"):
        _receipt(intent, result_metadata={"token": "raw-provider-token"})
    with pytest.raises(ValidationError, match="timezone-aware"):
        _receipt(intent, recorded_at=datetime(2026, 7, 16, 8))
    with pytest.raises(ValidationError, match="recorded_at"):
        _receipt(intent, recorded_at=_NOW)
    with pytest.raises(ValidationError):
        SideEffectReceiptV1.model_validate(receipt.model_dump() | {"provider_payload": {}})
