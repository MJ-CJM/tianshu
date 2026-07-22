"""Fault injection for the Skill rollback authority marker."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.diagnostics.test_doctor_report import _inputs
from tests.evolution.test_rollback_fault_matrix import _auth, _real_skill_rollback_case

import tianshu.evolution.promotion as promotion_module
from tianshu.diagnostics import assess_readiness
from tianshu.evolution.adapters.base import AdapterError
from tianshu.evolution.promotion import PromotionConflict
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.evolution_candidate import CandidateLifecycle, EvolutionCandidateV1
from tianshu.storage.evolution_repo import EvolutionRepository


def _marker_payload(candidate: EvolutionCandidateV1) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "candidate_id": candidate.candidate_id,
            "subject_key": candidate.subject_key,
            "base_digest": candidate.base.artifact_digest,
        }
    )


def _future_candidate(candidate: EvolutionCandidateV1) -> EvolutionCandidateV1:
    return candidate.model_copy(update={"candidate_id": "candidate-future-marker"})


def _assert_failed_before_effect(storage, service, candidate_id: str) -> None:
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        durable = EvolutionRepository().get_candidate(connection, candidate_id)
        allocation = connection.execute(
            """SELECT allocation_basis_points FROM evolution_routing_allocations
               WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()[0]
        statuses = tuple(
            row[0]
            for row in connection.execute(
                """SELECT status FROM evolution_promotion_journal
                   WHERE candidate_id=? AND action='rollback'
                   ORDER BY status""",
                (candidate_id,),
            ).fetchall()
        )
        unit_of_work.commit()
    readiness = assess_readiness(
        _inputs(evolution_rollback_ready=lambda: not service.has_pending_rollbacks())
    )

    assert durable is not None
    assert durable.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert allocation == 0
    assert statuses == ("rollback_pending",)
    assert readiness.status == "degraded"


def test_marker_write_all_retries_short_writes_to_exact_payload(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        _adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_write = promotion_module.os.write
    calls = 0

    def half_write(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        return original_write(descriptor, data[: max(1, len(data) // 2)])

    monkeypatch.setattr(promotion_module.os, "write", half_write)
    receipt = service.rollback(current.candidate_id, command, auth=_auth())
    marker = next(live_root.glob(".rollback-authority-*.json"))

    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert calls > 1
    assert marker.read_bytes() == _marker_payload(canary_candidate)


def test_zero_byte_marker_write_fails_before_effect_and_does_not_fence_future_candidate(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_write = promotion_module.os.write
    calls = 0

    def partial_then_zero(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, data[: len(data) // 2])
        return 0

    monkeypatch.setattr(promotion_module.os, "write", partial_then_zero)
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())

    _assert_failed_before_effect(storage, service, current.candidate_id)
    assert not tuple(live_root.glob(".rollback-authority-*"))
    quarantine = tuple(live_root.glob(".rollback-quarantine-*"))
    assert len(quarantine) == 1
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())
    assert tuple(live_root.glob(".rollback-quarantine-*")) == quarantine
    adapter.activate(_future_candidate(canary_candidate))


def test_truncated_final_marker_is_retained_and_fails_closed(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_replace = promotion_module.os.replace

    def replace_then_truncate(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination.name.startswith(".rollback-authority-"):
            destination.write_bytes(destination.read_bytes()[:-1])

    monkeypatch.setattr(promotion_module.os, "replace", replace_then_truncate)
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())

    _assert_failed_before_effect(storage, service, current.candidate_id)
    marker = next(live_root.glob(".rollback-authority-*.json"))
    assert marker.read_bytes() == _marker_payload(canary_candidate)[:-1]
    with pytest.raises(AdapterError):
        adapter.activate(_future_candidate(canary_candidate))


def test_final_marker_identity_drift_fails_closed_without_deleting_external_replacement(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        _canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_verify = adapter._verify_marker_file  # noqa: SLF001
    external_payload = b"external replacement"

    def replace_before_final_verify(
        path: Path,
        payload: bytes,
        *,
        expected_identity: tuple[int, int],
        sync: bool,
    ) -> None:
        if sync and path.name.startswith(".rollback-authority-"):
            replacement = live_root / ".external-marker"
            replacement.write_bytes(external_payload)
            promotion_module.os.replace(replacement, path)
        original_verify(
            path,
            payload,
            expected_identity=expected_identity,
            sync=sync,
        )

    monkeypatch.setattr(adapter, "_verify_marker_file", replace_before_final_verify)
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())

    _assert_failed_before_effect(storage, service, current.candidate_id)
    marker = next(live_root.glob(".rollback-authority-*.json"))
    assert marker.read_bytes() == external_payload


def test_replace_return_identity_drift_preserves_external_marker_and_fails_closed(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_replace = promotion_module.os.replace
    external_payload = b"external replacement before replace returns"

    def replace_then_swap_before_return(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination.name.startswith(".rollback-authority-"):
            replacement = live_root / ".external-marker"
            replacement.write_bytes(external_payload)
            original_replace(replacement, destination)

    monkeypatch.setattr(promotion_module.os, "replace", replace_then_swap_before_return)
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())

    _assert_failed_before_effect(storage, service, current.candidate_id)
    marker = next(live_root.glob(".rollback-authority-*.json"))
    assert marker.read_bytes() == external_payload
    with pytest.raises(AdapterError):
        adapter.activate(_future_candidate(canary_candidate))


def test_cleanup_never_unlinks_canonical_marker_and_retry_self_heals(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_replace = promotion_module.os.replace
    original_unlink = Path.unlink
    external_source = live_root / ".external-cleanup-marker"
    external_payload = b"external replacement at unlink boundary"
    external_source.write_bytes(external_payload)
    canonical_unlink_calls = 0

    def replace_then_truncate(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination.name.startswith(".rollback-authority-"):
            destination.write_bytes(destination.read_bytes()[:-1])

    def swap_external_then_unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal canonical_unlink_calls
        if path.name.startswith(".rollback-authority-") and path.suffix == ".json":
            canonical_unlink_calls += 1
            original_replace(external_source, path)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(promotion_module.os, "replace", replace_then_truncate)
    monkeypatch.setattr(Path, "unlink", swap_external_then_unlink)
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())

    _assert_failed_before_effect(storage, service, current.candidate_id)
    assert canonical_unlink_calls == 0
    marker = next(live_root.glob(".rollback-authority-*.json"))
    assert marker.read_bytes() == _marker_payload(canary_candidate)[:-1]
    assert external_source.read_bytes() == external_payload

    monkeypatch.setattr(promotion_module.os, "replace", original_replace)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    receipt = service.rollback(current.candidate_id, command, auth=_auth())

    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert marker.read_bytes() == _marker_payload(canary_candidate)
    adapter.activate(_future_candidate(canary_candidate))


def test_complete_marker_rejects_old_candidate_but_allows_new_candidate(storage, tmp_path) -> None:
    (
        live_root,
        _live_skill,
        _base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    service.rollback(current.candidate_id, command, auth=_auth())
    marker = next(live_root.glob(".rollback-authority-*.json"))

    assert marker.read_bytes() == _marker_payload(canary_candidate)
    with pytest.raises(AdapterError):
        adapter.activate(canary_candidate)
    adapter.activate(_future_candidate(canary_candidate))
