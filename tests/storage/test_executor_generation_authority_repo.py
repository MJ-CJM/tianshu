"""Executor candidate-to-generation authority persistence and integrity matrix."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
    EvolutionContractV1,
    EvolutionProvenanceV1,
    GateName,
    RollbackSpecV1,
)
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityStatus,
    ExecutorGenerationAuthorityV1,
    executor_generation_authority_id,
    new_pending_executor_generation_authority,
    transition_executor_generation_authority,
    validate_executor_generation_authority_transition,
)
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityConflict,
    ExecutorGenerationAuthorityDecodeError,
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
_SCOPE = "executor:keqing:pi"


@pytest.fixture
def connection() -> sqlite3.Connection:
    value = sqlite3.connect(":memory:")
    value.row_factory = sqlite3.Row
    value.execute("PRAGMA foreign_keys=ON")
    apply_migrations(value, MIGRATIONS)
    value.commit()
    yield value
    value.close()


def _release(marker: str) -> RuntimeReleaseV1:
    manifest = {"adapter_id": "keqing:pi", "marker": marker, "schema_version": 1}
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": _SCOPE,
        "manifest": manifest,
        "manifest_hash": canonical_sha256(manifest),
        "cli_version": f"0.83.{marker}",
        "cli_version_source": "package_json",
        "binary_path": f"/opt/tianshu/bin/pi-{marker}",
        "binary_digest": canonical_sha256({"binary": marker}),
        "package_name": "@mariozechner/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": canonical_sha256({"package": marker}),
        "single_argv_shape": "pi-single-v1",
        "session_argv_shape": "pi-session-v1",
        "pi_wire_version": 3,
        "materializer_id": "pi-release",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def _staged(
    release: RuntimeReleaseV1,
    generation_marker: str,
    *,
    at: datetime,
) -> RuntimeGenerationV1:
    return RuntimeGenerationV1(
        generation_id="rg-" + generation_marker * 32,
        scope=release.scope,
        release_digest=release.release_digest,
        state=RuntimeGenerationState.STAGED,
        version=1,
        created_at=at,
        updated_at=at,
    )


def _insert_staged(
    connection: sqlite3.Connection,
    *,
    release_marker: str,
    generation_marker: str,
    at: datetime,
) -> tuple[RuntimeReleaseV1, RuntimeGenerationV1]:
    repository = GenerationRepository()
    release = _release(release_marker)
    repository.insert_release(connection, release, first_seen_at=at)
    generation = repository.insert_staged(
        connection,
        _staged(release, generation_marker, at=at),
    )
    return release, generation


def _insert_active_base(
    connection: sqlite3.Connection,
) -> tuple[RuntimeReleaseV1, RuntimeGenerationV1]:
    repository = GenerationRepository()
    release, generation = _insert_staged(
        connection,
        release_marker="base",
        generation_marker="a",
        at=_NOW,
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=1,
        updated_at=_NOW + timedelta(seconds=1),
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=2,
        updated_at=_NOW + timedelta(seconds=2),
    )
    return release, repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=generation.generation_id,
        expected_generation_version=3,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    ).activated


def _candidate(marker: str) -> EvolutionCandidateV1:
    base = CandidateVersionRefV1(
        version="pi-base",
        artifact_digest=canonical_sha256({"base-artifact": marker}),
        canonical_digest=canonical_sha256({"base-canonical": marker}),
    )
    candidate_ref = CandidateVersionRefV1(
        version=f"pi-drift-{marker}",
        artifact_digest=canonical_sha256({"candidate-artifact": marker}),
        canonical_digest=canonical_sha256({"candidate-canonical": marker}),
    )
    contract = EvolutionContractV1(
        kind=CandidateKind.EXECUTOR,
        subject_key=_SCOPE,
        governance_contract_hash="1" * 64,
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.ROLLBACK),
        regression_policy_artifact_digest="2" * 64,
        sample_policy_artifact_digest="3" * 64,
        budget_policy_artifact_digest="4" * 64,
        minimum_canary_samples=5,
        max_canary_allocation_basis_points=500,
        rollback_slo_seconds=30,
    )
    return EvolutionCandidateV1(
        candidate_id=f"candidate-{marker}",
        kind=CandidateKind.EXECUTOR,
        subject_key=_SCOPE,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.SYSTEM,
            source_uri_redacted=None,
            source_digest=canonical_sha256({"source": marker}),
            actor_principal_id="system:pi-drift-scanner",
            actor_display_name="Pi drift scanner",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="pi-drift-scanner",
            producer_version="1",
            received_at=_NOW,
        ),
        base=base,
        candidate=candidate_ref,
        diff_artifact_digest=canonical_sha256({"diff": marker}),
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=0,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref=f"executor-base-{marker}",
            adapter_name="executor",
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _insert_promotion_intent(
    connection: sqlite3.Connection,
    *,
    candidate: EvolutionCandidateV1,
    command_key: str,
) -> str:
    promotion_journal_id = hashlib.sha256(f"{command_key}\0intended".encode()).hexdigest()
    entry = {
        "schema_version": 1,
        "command_key": command_key,
        "idempotency_key": command_key,
        "candidate_id": candidate.candidate_id,
        "action": "start_canary",
        "status": "intended",
        "decision_request_id": None,
        "request_hash": canonical_sha256({"request": command_key}),
        "principal_id": "system:test",
        "reason": "test start canary",
        "pre_transition_candidate_version": candidate.version,
        "candidate_digest": candidate.candidate.artifact_digest,
        "base_digest": candidate.base.artifact_digest,
        "gate_snapshot_version": 1,
        "gate_report_hash": canonical_sha256({"gate": command_key}),
        "routing_version": 1,
        "allocation_basis_points": 500,
        "receipt": None,
    }
    raw = canonical_json_bytes(entry).decode("utf-8")
    connection.execute(
        """INSERT INTO evolution_promotion_journal (
               promotion_journal_id, command_key, candidate_id, candidate_version,
               gate_snapshot_version, action, status, decision_request_id,
               entry_json, entry_hash, created_at
           ) VALUES (?, ?, ?, ?, 1, 'start_canary', 'intended', NULL, ?, ?, ?)""",
        (
            promotion_journal_id,
            command_key,
            candidate.candidate_id,
            candidate.version,
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
            _NOW.isoformat(),
        ),
    )
    return promotion_journal_id


def _seed_candidate_target(
    connection: sqlite3.Connection,
    *,
    candidate_marker: str,
    release_marker: str,
    generation_marker: str,
    at: datetime,
) -> tuple[EvolutionCandidateV1, RuntimeReleaseV1, RuntimeGenerationV1, str, str]:
    repository = EvolutionRepository()
    candidate = repository.insert_candidate(connection, _candidate(candidate_marker))
    for offset, lifecycle in enumerate(
        (
            CandidateLifecycle.STAGED,
            CandidateLifecycle.EVALUATING,
            CandidateLifecycle.READY,
        ),
        start=1,
    ):
        candidate = repository.save_candidate(
            connection,
            candidate.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "updated_at": _NOW + timedelta(milliseconds=offset),
                }
            ),
            expected_version=candidate.version,
        )
    release, generation = _insert_staged(
        connection,
        release_marker=release_marker,
        generation_marker=generation_marker,
        at=at,
    )
    command_key = f"start-canary-{candidate_marker}"
    promotion_journal_id = _insert_promotion_intent(
        connection,
        candidate=candidate,
        command_key=command_key,
    )
    return candidate, release, generation, command_key, promotion_journal_id


def _pending(
    *,
    candidate: EvolutionCandidateV1,
    release: RuntimeReleaseV1,
    generation: RuntimeGenerationV1,
    base_release: RuntimeReleaseV1,
    base_generation: RuntimeGenerationV1,
    command_key: str,
    promotion_journal_id: str,
    at: datetime,
    previous: ExecutorGenerationAuthorityV1 | None = None,
) -> ExecutorGenerationAuthorityV1:
    return new_pending_executor_generation_authority(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        candidate_artifact_digest=candidate.candidate.artifact_digest,
        candidate_canonical_digest=candidate.candidate.canonical_digest,
        release_digest=release.release_digest,
        scope=_SCOPE,
        generation_id=generation.generation_id,
        base_generation_id=base_generation.generation_id,
        base_release_digest=base_release.release_digest,
        promotion_journal_id=promotion_journal_id,
        start_command_key=command_key,
        now=at,
        previous=previous,
    )


def _seed_pending_context(
    connection: sqlite3.Connection,
) -> tuple[
    ExecutorGenerationAuthorityRepository,
    ExecutorGenerationAuthorityV1,
    RuntimeReleaseV1,
    RuntimeGenerationV1,
]:
    base_release, base_generation = _insert_active_base(connection)
    candidate, release, generation, command_key, journal_id = _seed_candidate_target(
        connection,
        candidate_marker="1",
        release_marker="candidate-1",
        generation_marker="b",
        at=_NOW + timedelta(seconds=4),
    )
    pending = _pending(
        candidate=candidate,
        release=release,
        generation=generation,
        base_release=base_release,
        base_generation=base_generation,
        command_key=command_key,
        promotion_journal_id=journal_id,
        at=_NOW + timedelta(seconds=5),
    )
    return ExecutorGenerationAuthorityRepository(), pending, base_release, base_generation


def test_writes_require_caller_owned_transaction_and_exact_replay(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    connection.commit()

    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        repository.save(
            connection,
            pending,
            expected_version=0,
            reason_code="start_canary_intended",
        )

    connection.execute("BEGIN IMMEDIATE")
    assert (
        repository.save(
            connection,
            pending,
            expected_version=0,
            reason_code="start_canary_intended",
        )
        == pending
    )
    assert (
        repository.save(
            connection,
            pending,
            expected_version=0,
            reason_code="start_canary_intended",
        )
        == pending
    )
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="replay reason"):
        repository.save(
            connection,
            pending,
            expected_version=0,
            reason_code="different_command",
        )
    assert len(repository.list_journal(connection, candidate_id=pending.candidate_id)) == 1
    connection.rollback()


def test_cas_lifecycle_roots_and_same_candidate_next_epoch(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, base_release, base_generation = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    assert repository.get_current(connection, candidate_id=pending.candidate_id) == pending
    assert repository.get_by_generation(connection, generation_id=pending.generation_id) == pending
    assert repository.list_recovery_roots(connection) == (pending,)
    assert repository.list_retention_roots(connection) == (pending,)

    authorized = transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=_NOW + timedelta(seconds=6),
    )
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="compare-and-swap"):
        repository.save(
            connection,
            authorized,
            expected_version=0,
            reason_code="warm_ready",
        )
    repository.save(
        connection,
        authorized,
        expected_version=1,
        reason_code="warm_ready",
    )
    revoking = transition_executor_generation_authority(
        authorized,
        ExecutorGenerationAuthorityStatus.REVOKING,
        now=_NOW + timedelta(seconds=7),
        revocation_reason="canary_rollback",
    )
    repository.save(
        connection,
        revoking,
        expected_version=2,
        reason_code="rollback_started",
    )
    assert repository.list_retention_roots(connection) == (revoking,)
    revoked = transition_executor_generation_authority(
        revoking,
        ExecutorGenerationAuthorityStatus.REVOKED,
        now=_NOW + timedelta(seconds=8),
    )
    repository.save(
        connection,
        revoked,
        expected_version=3,
        reason_code="references_drained",
    )
    assert repository.list_recovery_roots(connection) == ()
    assert repository.list_retention_roots(connection) == ()

    candidate = EvolutionRepository().get_candidate(connection, pending.candidate_id)
    assert candidate is not None
    release, generation = _insert_staged(
        connection,
        release_marker="candidate-2",
        generation_marker="c",
        at=_NOW + timedelta(seconds=9),
    )
    command_key = "start-canary-1-epoch-2"
    promotion_journal_id = _insert_promotion_intent(
        connection,
        candidate=candidate,
        command_key=command_key,
    )
    next_pending = _pending(
        candidate=candidate,
        release=release,
        generation=generation,
        base_release=base_release,
        base_generation=base_generation,
        command_key=command_key,
        promotion_journal_id=promotion_journal_id,
        at=_NOW + timedelta(seconds=10),
        previous=revoked,
    )
    repository.save(
        connection,
        next_pending,
        expected_version=4,
        reason_code="retry_start_canary",
    )
    assert (next_pending.epoch, next_pending.version) == (2, 5)
    assert next_pending.authority_id != pending.authority_id
    assert repository.get_by_generation(connection, generation_id=pending.generation_id) is None
    assert [
        record.entry.authority_version
        for record in repository.list_journal(connection, candidate_id=pending.candidate_id)
    ] == [1, 2, 3, 4, 5]
    connection.rollback()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ExecutorGenerationAuthorityStatus.PENDING, ExecutorGenerationAuthorityStatus.REVOKING),
        (ExecutorGenerationAuthorityStatus.AUTHORIZED, ExecutorGenerationAuthorityStatus.PENDING),
        (
            ExecutorGenerationAuthorityStatus.REVOKING,
            ExecutorGenerationAuthorityStatus.AUTHORIZED,
        ),
        (ExecutorGenerationAuthorityStatus.REVOKED, ExecutorGenerationAuthorityStatus.AUTHORIZED),
    ],
)
def test_transition_helper_rejects_edges_outside_the_matrix(
    source: ExecutorGenerationAuthorityStatus,
    target: ExecutorGenerationAuthorityStatus,
) -> None:
    now = _NOW
    pending = new_pending_executor_generation_authority(
        candidate_id="candidate-1",
        candidate_version=1,
        candidate_artifact_digest="1" * 64,
        candidate_canonical_digest="2" * 64,
        release_digest="3" * 64,
        scope=_SCOPE,
        generation_id="rg-" + "1" * 32,
        base_generation_id="rg-" + "0" * 32,
        base_release_digest="4" * 64,
        promotion_journal_id="promotion-1",
        start_command_key="start-1",
        now=now,
    )
    authorized = transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=now + timedelta(seconds=1),
    )
    revoking = transition_executor_generation_authority(
        authorized,
        ExecutorGenerationAuthorityStatus.REVOKING,
        now=now + timedelta(seconds=2),
        revocation_reason="rollback",
    )
    revoked = transition_executor_generation_authority(
        revoking,
        ExecutorGenerationAuthorityStatus.REVOKED,
        now=now + timedelta(seconds=3),
    )
    authorities = {
        ExecutorGenerationAuthorityStatus.PENDING: pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED: authorized,
        ExecutorGenerationAuthorityStatus.REVOKING: revoking,
        ExecutorGenerationAuthorityStatus.REVOKED: revoked,
    }
    with pytest.raises(ValueError, match="illegal executor authority transition"):
        transition_executor_generation_authority(
            authorities[source],
            target,
            now=now + timedelta(seconds=4),
            revocation_reason=(
                "rollback" if target is ExecutorGenerationAuthorityStatus.REVOKING else None
            ),
        )


def test_transition_validator_rejects_version_gap_and_same_epoch_rebinding() -> None:
    pending = new_pending_executor_generation_authority(
        candidate_id="candidate-1",
        candidate_version=1,
        candidate_artifact_digest="1" * 64,
        candidate_canonical_digest="2" * 64,
        release_digest="3" * 64,
        scope=_SCOPE,
        generation_id="rg-" + "1" * 32,
        base_generation_id="rg-" + "0" * 32,
        base_release_digest="4" * 64,
        promotion_journal_id="promotion-1",
        start_command_key="start-1",
        now=_NOW,
    )
    authorized = transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=_NOW + timedelta(seconds=1),
    )
    version_gap = ExecutorGenerationAuthorityV1.model_validate(
        authorized.model_dump(mode="python") | {"version": 3}
    )
    with pytest.raises(ValueError, match="version must advance"):
        validate_executor_generation_authority_transition(pending, version_gap)

    payload = authorized.model_dump(mode="python")
    payload["generation_id"] = "rg-" + "2" * 32
    payload["authority_id"] = executor_generation_authority_id(
        candidate_id=authorized.candidate_id,
        epoch=authorized.epoch,
        candidate_version=authorized.candidate_version,
        candidate_artifact_digest=authorized.candidate_artifact_digest,
        candidate_canonical_digest=authorized.candidate_canonical_digest,
        release_digest=authorized.release_digest,
        scope=authorized.scope,
        generation_id=str(payload["generation_id"]),
        base_generation_id=authorized.base_generation_id,
        base_release_digest=authorized.base_release_digest,
        promotion_journal_id=authorized.promotion_journal_id,
        start_command_key=authorized.start_command_key,
    )
    rebound = ExecutorGenerationAuthorityV1.model_validate(payload)
    with pytest.raises(ValueError, match="same-epoch authority identity"):
        validate_executor_generation_authority_transition(pending, rebound)


@pytest.mark.parametrize("wrong_binding", ["base_generation", "base_release", "command_key"])
def test_pending_authority_fails_closed_on_wrong_base_or_command_binding(
    connection: sqlite3.Connection,
    wrong_binding: str,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    payload = pending.model_dump(mode="python")
    if wrong_binding == "base_generation":
        payload["base_generation_id"] = "rg-" + "f" * 32
    elif wrong_binding == "base_release":
        payload["base_release_digest"] = "f" * 64
    else:
        payload["start_command_key"] = "another-command"
    payload["authority_id"] = executor_generation_authority_id(
        candidate_id=pending.candidate_id,
        epoch=pending.epoch,
        candidate_version=pending.candidate_version,
        candidate_artifact_digest=pending.candidate_artifact_digest,
        candidate_canonical_digest=pending.candidate_canonical_digest,
        release_digest=pending.release_digest,
        scope=pending.scope,
        generation_id=pending.generation_id,
        base_generation_id=str(payload["base_generation_id"]),
        base_release_digest=str(payload["base_release_digest"]),
        promotion_journal_id=pending.promotion_journal_id,
        start_command_key=str(payload["start_command_key"]),
    )
    wrong = ExecutorGenerationAuthorityV1.model_validate(payload)
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="base|start-canary"):
        repository.save(
            connection,
            wrong,
            expected_version=0,
            reason_code="start_canary_intended",
        )
    assert repository.get_current(connection, candidate_id=pending.candidate_id) is None
    connection.rollback()


def test_generation_cannot_be_bound_to_two_candidates_even_after_rebinding(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, base_release, base_generation = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    candidate2 = EvolutionRepository().insert_candidate(connection, _candidate("2"))
    command2 = "start-canary-2"
    journal2 = _insert_promotion_intent(
        connection,
        candidate=candidate2,
        command_key=command2,
    )
    same_generation = _pending(
        candidate=candidate2,
        release=GenerationRepository().get_release(
            connection,
            scope=_SCOPE,
            release_digest=pending.release_digest,
        ),
        generation=RuntimeGenerationV1(
            generation_id=pending.generation_id,
            scope=pending.scope,
            release_digest=pending.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=_NOW + timedelta(seconds=4),
            updated_at=_NOW + timedelta(seconds=4),
        ),
        base_release=base_release,
        base_generation=base_generation,
        command_key=command2,
        promotion_journal_id=journal2,
        at=_NOW + timedelta(seconds=6),
    )
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="already bound"):
        repository.save(
            connection,
            same_generation,
            expected_version=0,
            reason_code="start_canary_intended",
        )

    revoked = transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.REVOKED,
        now=_NOW + timedelta(seconds=7),
        revocation_reason="aborted_before_warm",
    )
    repository.save(
        connection,
        revoked,
        expected_version=1,
        reason_code="start_canary_aborted",
    )
    candidate1 = EvolutionRepository().get_candidate(connection, pending.candidate_id)
    assert candidate1 is not None
    next_release, next_generation = _insert_staged(
        connection,
        release_marker="candidate-1-retry",
        generation_marker="c",
        at=_NOW + timedelta(seconds=8),
    )
    next_command = "start-canary-1-retry"
    next_journal = _insert_promotion_intent(
        connection,
        candidate=candidate1,
        command_key=next_command,
    )
    next_pending = _pending(
        candidate=candidate1,
        release=next_release,
        generation=next_generation,
        base_release=base_release,
        base_generation=base_generation,
        command_key=next_command,
        promotion_journal_id=next_journal,
        at=_NOW + timedelta(seconds=9),
        previous=revoked,
    )
    repository.save(
        connection,
        next_pending,
        expected_version=2,
        reason_code="retry_start_canary",
    )
    assert repository.get_by_generation(connection, generation_id=pending.generation_id) is None
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="already bound"):
        repository.save(
            connection,
            same_generation,
            expected_version=0,
            reason_code="start_canary_intended",
        )
    connection.rollback()


def test_authority_json_hash_and_base_binding_tampering_fail_closed(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.execute("DROP TRIGGER executor_generation_authorities_transition_guard")
    raw = connection.execute(
        "SELECT authority_json FROM executor_generation_authorities WHERE candidate_id=?",
        (pending.candidate_id,),
    ).fetchone()[0]
    payload = json.loads(raw)
    payload["base_release_digest"] = "f" * 64
    tampered = canonical_json_bytes(payload).decode("utf-8")
    connection.execute(
        """UPDATE executor_generation_authorities
           SET authority_json=?, authority_hash=? WHERE candidate_id=?""",
        (tampered, canonical_sha256(payload), pending.candidate_id),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="v1 contract"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    connection.rollback()


def test_wrong_authority_digest_and_missing_journal_tail_fail_closed(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.execute("DROP TRIGGER executor_generation_authorities_transition_guard")
    connection.execute(
        "UPDATE executor_generation_authorities SET authority_hash=? WHERE candidate_id=?",
        ("0" * 64, pending.candidate_id),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="authority_hash"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    connection.rollback()

    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.execute("DROP TRIGGER executor_generation_authority_journal_no_delete")
    connection.execute(
        "DELETE FROM executor_generation_authority_journal WHERE candidate_id=?",
        (pending.candidate_id,),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="journal is missing"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    connection.rollback()


def test_tampered_journal_digest_fails_closed(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.execute("DROP TRIGGER executor_generation_authority_journal_no_update")
    connection.execute(
        """UPDATE executor_generation_authority_journal
           SET entry_hash=? WHERE candidate_id=?""",
        ("0" * 64, pending.candidate_id),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="entry_hash"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    connection.rollback()


@pytest.mark.parametrize(
    "corruption",
    ["entry_hash", "noncanonical_json", "entry_binding", "row_columns"],
)
def test_promotion_intent_corruption_fails_closed_for_reads_and_recovery_roots(
    connection: sqlite3.Connection,
    corruption: str,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
    if corruption == "entry_hash":
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET entry_hash=? WHERE promotion_journal_id=?""",
            ("0" * 64, pending.promotion_journal_id),
        )
    elif corruption == "noncanonical_json":
        raw = connection.execute(
            """SELECT entry_json FROM evolution_promotion_journal
               WHERE promotion_journal_id=?""",
            (pending.promotion_journal_id,),
        ).fetchone()[0]
        noncanonical = json.dumps(json.loads(raw), indent=2)
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET entry_json=?, entry_hash=? WHERE promotion_journal_id=?""",
            (
                noncanonical,
                hashlib.sha256(noncanonical.encode()).hexdigest(),
                pending.promotion_journal_id,
            ),
        )
    elif corruption == "entry_binding":
        raw = connection.execute(
            """SELECT entry_json FROM evolution_promotion_journal
               WHERE promotion_journal_id=?""",
            (pending.promotion_journal_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["candidate_digest"] = "f" * 64
        tampered = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET entry_json=?, entry_hash=? WHERE promotion_journal_id=?""",
            (
                tampered,
                hashlib.sha256(tampered.encode()).hexdigest(),
                pending.promotion_journal_id,
            ),
        )
    else:
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET candidate_version=candidate_version + 1
               WHERE promotion_journal_id=?""",
            (pending.promotion_journal_id,),
        )

    for read in (
        lambda: repository.get_current(connection, candidate_id=pending.candidate_id),
        lambda: repository.get_by_generation(connection, generation_id=pending.generation_id),
        lambda: repository.list_recovery_roots(connection),
        lambda: repository.list_retention_roots(connection),
        lambda: repository.verify_journal_tail(connection, candidate_id=pending.candidate_id),
    ):
        with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="start-canary"):
            read()
    connection.rollback()


def test_missing_promotion_intent_fails_closed_for_current_and_recovery_reads(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    connection.commit()

    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("DROP TRIGGER evolution_promotion_journal_no_delete")
    connection.execute(
        "DELETE FROM evolution_promotion_journal WHERE promotion_journal_id=?",
        (pending.promotion_journal_id,),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="intent is missing"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="intent is missing"):
        repository.list_recovery_roots(connection)


def test_non_content_addressed_promotion_journal_id_cannot_grant_authority(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    wrong_journal_id = "f" * 64
    connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
    connection.execute(
        """UPDATE evolution_promotion_journal
           SET promotion_journal_id=? WHERE promotion_journal_id=?""",
        (wrong_journal_id, pending.promotion_journal_id),
    )
    payload = pending.model_dump(mode="python")
    payload["promotion_journal_id"] = wrong_journal_id
    payload["authority_id"] = executor_generation_authority_id(
        candidate_id=pending.candidate_id,
        epoch=pending.epoch,
        candidate_version=pending.candidate_version,
        candidate_artifact_digest=pending.candidate_artifact_digest,
        candidate_canonical_digest=pending.candidate_canonical_digest,
        release_digest=pending.release_digest,
        scope=pending.scope,
        generation_id=pending.generation_id,
        base_generation_id=pending.base_generation_id,
        base_release_digest=pending.base_release_digest,
        promotion_journal_id=wrong_journal_id,
        start_command_key=pending.start_command_key,
    )
    malformed = ExecutorGenerationAuthorityV1.model_validate(payload)
    with pytest.raises(ExecutorGenerationAuthorityConflict, match="start-canary journal"):
        repository.save(
            connection,
            malformed,
            expected_version=0,
            reason_code="start_canary_intended",
        )
    connection.rollback()


def test_self_consistent_current_row_tamper_is_rejected_by_journal_tail(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    repository, pending, _, _ = _seed_pending_context(connection)
    repository.save(
        connection,
        pending,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    authorized = transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=_NOW + timedelta(seconds=6),
    )
    repository.save(
        connection,
        authorized,
        expected_version=1,
        reason_code="warm_ready",
    )
    revoking = transition_executor_generation_authority(
        authorized,
        ExecutorGenerationAuthorityStatus.REVOKING,
        now=_NOW + timedelta(seconds=7),
        revocation_reason="canary_rollback",
    )
    repository.save(
        connection,
        revoking,
        expected_version=2,
        reason_code="rollback_started",
    )
    connection.execute("DROP TRIGGER executor_generation_authorities_transition_guard")
    payload = revoking.model_dump(mode="python")
    payload["revocation_reason"] = "rewritten_reason"
    tampered = ExecutorGenerationAuthorityV1.model_validate(payload)
    connection.execute(
        """UPDATE executor_generation_authorities
           SET authority_json=?, authority_hash=?, revocation_reason=?
           WHERE candidate_id=?""",
        (
            canonical_json_bytes(tampered).decode("utf-8"),
            canonical_sha256(tampered),
            tampered.revocation_reason,
            pending.candidate_id,
        ),
    )
    with pytest.raises(ExecutorGenerationAuthorityDecodeError, match="journal tail"):
        repository.get_current(connection, candidate_id=pending.candidate_id)
    connection.rollback()


def test_model_rejects_wrong_content_addressed_authority_id() -> None:
    with pytest.raises(ValidationError, match="authority_id does not match"):
        ExecutorGenerationAuthorityV1(
            authority_id="0" * 64,
            candidate_id="candidate-1",
            epoch=1,
            candidate_version=1,
            candidate_artifact_digest="1" * 64,
            candidate_canonical_digest="2" * 64,
            release_digest="3" * 64,
            scope=_SCOPE,
            generation_id="rg-" + "1" * 32,
            base_generation_id="rg-" + "0" * 32,
            base_release_digest="4" * 64,
            promotion_journal_id="promotion-1",
            start_command_key="start-1",
            status=ExecutorGenerationAuthorityStatus.PENDING,
            version=1,
            created_at=_NOW,
            updated_at=_NOW,
        )
