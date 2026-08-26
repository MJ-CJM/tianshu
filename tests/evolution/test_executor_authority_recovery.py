"""Executor authority startup recovery and revocation retention contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tianshu.evolution.reconciler import GenerationReconciler
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.adapters.protocol import ExecutionMode, PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.executor.generation_controller import (
    GenerationController,
    GenerationRecoveryError,
)
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
    new_pending_executor_generation_authority,
    transition_executor_generation_authority,
)
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage import Storage
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityDecodeError,
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository

_NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
_SCOPE = "executor:keqing:pi"
_BASE_ID = "rg-" + "a" * 32
_AUTHORIZED_ID = "rg-" + "b" * 32
_UNAUTHORIZED_ID = "rg-" + "c" * 32
_SNAPSHOT_DIGEST = "d" * 64


@dataclass
class _Adapter:
    manifest: ExecutorCapabilityManifestV1

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def supported_execution_modes(self) -> tuple[ExecutionMode, ...]:
        return self.manifest.execution_modes

    def probe(self) -> HostCapabilityProbeV1:
        return HostCapabilityProbeV1(
            probe_id="executor-authority-recovery-test",
            os_name="test",
            architecture="test",
            git_available=True,
            process_groups_available=True,
            sandbox_backend=None,
        )

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecution:
        return PreparedExecution(
            run_id=run_id,
            effective=effective,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    async def execute(self, prepared: PreparedExecution, *_args: Any, **_kwargs: Any) -> str:
        return prepared.run_id

    async def cancel(self, _run_id: str) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class _Bundle:
    release: RuntimeReleaseV1
    executor_adapter: _Adapter

    @property
    def scope(self) -> str:
        return self.release.scope

    @property
    def adapter_id(self) -> str:
        return self.executor_adapter.adapter_id

    @property
    def release_digest(self) -> str:
        return self.release.release_digest

    @property
    def manifest_content_hash(self) -> str:
        return self.release.manifest_hash


class _Materializer:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        assert not self._storage._conn.in_transaction  # noqa: SLF001
        manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        return _Bundle(release=release, executor_adapter=_Adapter(manifest))


@dataclass(frozen=True, slots=True)
class _AuthoritySeed:
    base: RuntimeGenerationV1
    target: RuntimeGenerationV1
    authority: ExecutorGenerationAuthorityV1


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    active = Storage(str(tmp_path / "executor-authority-recovery.db"))
    active.init_db()
    yield active
    active.close()


def _release(marker: str) -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": _SCOPE,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": f"/opt/tianshu/bin/pi-{marker}",
        "binary_digest": canonical_sha256({"binary": marker}),
        "package_name": "@mariozechner/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": canonical_sha256({"package": marker}),
        "single_argv_shape": "pi-single-v1",
        "session_argv_shape": "pi-session-v1",
        "pi_wire_version": 3,
        "materializer_id": "authority-recovery-test",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def _insert_ready(
    connection: sqlite3.Connection,
    repository: GenerationRepository,
    *,
    release: RuntimeReleaseV1,
    generation_id: str,
    seconds: int,
) -> RuntimeGenerationV1:
    repository.insert_release(connection, release, first_seen_at=_NOW + timedelta(seconds=seconds))
    generation = repository.insert_staged(
        connection,
        RuntimeGenerationV1(
            generation_id=generation_id,
            scope=_SCOPE,
            release_digest=release.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=_NOW + timedelta(seconds=seconds),
            updated_at=_NOW + timedelta(seconds=seconds),
        ),
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 1),
    )
    return repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 2),
    )


def _insert_active_base(
    connection: sqlite3.Connection,
    repository: GenerationRepository,
) -> tuple[RuntimeReleaseV1, RuntimeGenerationV1]:
    release = _release("base")
    ready = _insert_ready(
        connection,
        repository,
        release=release,
        generation_id=_BASE_ID,
        seconds=0,
    )
    activated = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=ready.generation_id,
        expected_generation_version=ready.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    ).activated
    return release, activated


def _candidate(marker: str) -> EvolutionCandidateV1:
    base = CandidateVersionRefV1(
        version="pi-base",
        artifact_digest=canonical_sha256({"base-artifact": marker}),
        canonical_digest=canonical_sha256({"base-canonical": marker}),
    )
    candidate_ref = CandidateVersionRefV1(
        version=f"pi-candidate-{marker}",
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


def _insert_ready_candidate(
    connection: sqlite3.Connection,
    *,
    marker: str,
) -> EvolutionCandidateV1:
    repository = EvolutionRepository()
    candidate = repository.insert_candidate(connection, _candidate(marker))
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
    return candidate


def _insert_promotion_intent(
    connection: sqlite3.Connection,
    *,
    candidate: EvolutionCandidateV1,
    command_key: str,
) -> str:
    journal_id = hashlib.sha256(f"{command_key}\0intended".encode()).hexdigest()
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
            journal_id,
            command_key,
            candidate.candidate_id,
            candidate.version,
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
            _NOW.isoformat(),
        ),
    )
    return journal_id


def _seed_authority(
    connection: sqlite3.Connection,
    *,
    status: ExecutorGenerationAuthorityStatus,
) -> _AuthoritySeed:
    generation_repository = GenerationRepository()
    authority_repository = ExecutorGenerationAuthorityRepository()
    base_release, base = _insert_active_base(connection, generation_repository)
    candidate = _insert_ready_candidate(connection, marker="authority")
    target_release = _release("authority")
    generation_repository.insert_release(
        connection,
        target_release,
        first_seen_at=_NOW + timedelta(seconds=4),
    )
    target = generation_repository.insert_staged(
        connection,
        RuntimeGenerationV1(
            generation_id=_AUTHORIZED_ID,
            scope=_SCOPE,
            release_digest=target_release.release_digest,
            state=RuntimeGenerationState.STAGED,
            version=1,
            created_at=_NOW + timedelta(seconds=4),
            updated_at=_NOW + timedelta(seconds=4),
        ),
    )
    command_key = "start-canary-authority"
    promotion_journal_id = _insert_promotion_intent(
        connection,
        candidate=candidate,
        command_key=command_key,
    )
    authority = new_pending_executor_generation_authority(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        candidate_artifact_digest=candidate.candidate.artifact_digest,
        candidate_canonical_digest=candidate.candidate.canonical_digest,
        release_digest=target_release.release_digest,
        scope=_SCOPE,
        generation_id=target.generation_id,
        base_generation_id=base.generation_id,
        base_release_digest=base_release.release_digest,
        promotion_journal_id=promotion_journal_id,
        start_command_key=command_key,
        now=_NOW + timedelta(seconds=5),
    )
    authority_repository.save(
        connection,
        authority,
        expected_version=0,
        reason_code="start_canary_intended",
    )
    target = generation_repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=target.generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=target.version,
        updated_at=_NOW + timedelta(seconds=6),
    )
    target = generation_repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=target.generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=target.version,
        updated_at=_NOW + timedelta(seconds=7),
    )
    authority = transition_executor_generation_authority(
        authority,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=_NOW + timedelta(seconds=8),
    )
    authority_repository.save(
        connection,
        authority,
        expected_version=1,
        reason_code="warm_ready",
    )
    if status is ExecutorGenerationAuthorityStatus.REVOKING:
        authority = transition_executor_generation_authority(
            authority,
            ExecutorGenerationAuthorityStatus.REVOKING,
            now=_NOW + timedelta(seconds=9),
            revocation_reason="canary_rollback",
        )
        authority_repository.save(
            connection,
            authority,
            expected_version=2,
            reason_code="rollback_started",
        )
    elif status is not ExecutorGenerationAuthorityStatus.AUTHORIZED:
        raise ValueError("test seed supports only authorized or revoking authority")
    return _AuthoritySeed(base=base, target=target, authority=authority)


def _controller(
    storage: Storage,
    registry: ExecutorAdapterRegistry,
    authority_repository: ExecutorGenerationAuthorityRepository,
) -> GenerationController:
    async def warm_probe(_bundle: _Bundle) -> tuple[bool, str | None]:
        return True, None

    def recovery_roots(connection: sqlite3.Connection) -> frozenset[str]:
        return frozenset(
            authority.generation_id
            for authority in authority_repository.list_recovery_roots(connection)
        )

    return GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        registry,
        warm_probe=warm_probe,
        recovery_root_provider=recovery_roots,
        clock=lambda: _NOW + timedelta(seconds=20),
    )


def _generation(storage: Storage, generation_id: str) -> RuntimeGenerationV1:
    with storage.unit_of_work() as unit_of_work:
        generation = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    assert generation is not None
    return generation


def _insert_durable_reference(storage: Storage, generation_id: str) -> None:
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            """INSERT INTO edicts (id, goal, status, created_at, schedule_json)
               VALUES ('edict-authority', 'authority retention', 'open', ?, ?)""",
            (_NOW.isoformat(), json.dumps({"type": "immediate"}, separators=(",", ":"))),
        )
        connection.execute(
            """INSERT INTO memorials (id, edict_id, status, created_at, dag_node_id)
               VALUES ('memorial-authority', 'edict-authority', 'pending', ?, NULL)""",
            (_NOW.isoformat(),),
        )
        connection.execute(
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            (_SNAPSHOT_DIGEST, _NOW.isoformat()),
        )
        connection.execute(
            """INSERT INTO run_system_bindings (
                   memorial_id, attempt_id, snapshot_digest, generation_ids_json, created_at
               ) VALUES ('memorial-authority', 'attempt-authority', ?, ?, ?)""",
            (
                _SNAPSHOT_DIGEST,
                json.dumps([generation_id], separators=(",", ":")),
                _NOW.isoformat(),
            ),
        )
        unit_of_work.commit()


def test_recovery_retains_authorized_ready_and_fails_unauthorized_ready(
    storage: Storage,
) -> None:
    authority_repository = ExecutorGenerationAuthorityRepository()
    with storage.unit_of_work() as unit_of_work:
        seed = _seed_authority(
            unit_of_work.connection,
            status=ExecutorGenerationAuthorityStatus.AUTHORIZED,
        )
        _insert_ready(
            unit_of_work.connection,
            GenerationRepository(),
            release=_release("unauthorized"),
            generation_id=_UNAUTHORIZED_ID,
            seconds=10,
        )
        unit_of_work.commit()

    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    report = _controller(storage, registry, authority_repository).recover()

    assert set(report.materialized_generation_ids) == {
        seed.base.generation_id,
        seed.target.generation_id,
    }
    assert report.failed_generation_ids == (_UNAUTHORIZED_ID,)
    assert _generation(storage, seed.target.generation_id).state is RuntimeGenerationState.READY
    assert _generation(storage, _UNAUTHORIZED_ID).state is RuntimeGenerationState.FAILED
    assert registry.generation_record(seed.target.generation_id) is not None
    assert registry.generation_record(_UNAUTHORIZED_ID) is None
    with storage.unit_of_work() as unit_of_work:
        durable_authority = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=seed.authority.candidate_id,
        )
        unit_of_work.commit()
    assert durable_authority is not None
    assert durable_authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED

    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        registry,
        authority_repository=authority_repository,
    )
    assert reconciler.reconcile_once() == 0
    assert reconciler.readiness_snapshot() == (True, ())
    assert _generation(storage, seed.target.generation_id).state is RuntimeGenerationState.READY


@pytest.mark.parametrize("reference_kind", ("durable", "process"))
def test_revoking_ready_waits_for_references_then_revokes(
    storage: Storage,
    reference_kind: str,
) -> None:
    authority_repository = ExecutorGenerationAuthorityRepository()
    with storage.unit_of_work() as unit_of_work:
        seed = _seed_authority(
            unit_of_work.connection,
            status=ExecutorGenerationAuthorityStatus.REVOKING,
        )
        unit_of_work.commit()

    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    _controller(storage, registry, authority_repository).recover()
    if reference_kind == "durable":
        _insert_durable_reference(storage, seed.target.generation_id)
    else:
        registry.reserve_binding(
            "attempt-live",
            pinned_ids=(seed.target.generation_id,),
            required_scopes=(_SCOPE,),
            allow_ready=True,
        )
    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW + timedelta(seconds=30),
        authority_repository=authority_repository,
    )

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, seed.target.generation_id).state is RuntimeGenerationState.READY
    with storage.unit_of_work() as unit_of_work:
        retained = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=seed.authority.candidate_id,
        )
        unit_of_work.commit()
    assert retained is not None
    assert retained.status is ExecutorGenerationAuthorityStatus.REVOKING

    if reference_kind == "durable":
        with storage.unit_of_work() as unit_of_work:
            unit_of_work.connection.execute(
                "UPDATE edicts SET status='archived' WHERE id='edict-authority'"
            )
            unit_of_work.commit()
    else:
        assert registry.release("attempt-live") is True

    assert reconciler.reconcile_once() == 1
    assert _generation(storage, seed.target.generation_id).state is RuntimeGenerationState.FAILED
    assert registry.generation_record(seed.target.generation_id) is None
    with storage.unit_of_work() as unit_of_work:
        revoked = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=seed.authority.candidate_id,
        )
        roots = authority_repository.list_retention_roots(unit_of_work.connection)
        unit_of_work.commit()
    assert revoked is not None
    assert revoked.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert roots == ()
    assert reconciler.reconcile_once() == 0


@pytest.mark.parametrize(
    ("corruption", "expected_error", "message"),
    (
        ("authority_hash", ExecutorGenerationAuthorityDecodeError, "authority_hash"),
        ("missing_journal", ExecutorGenerationAuthorityDecodeError, "journal is missing"),
        (
            "promotion_hash",
            ExecutorGenerationAuthorityDecodeError,
            "start-canary intent entry_hash",
        ),
        ("unavailable_root", GenerationRecoveryError, "unavailable generation"),
    ),
)
def test_startup_fails_closed_on_corrupt_or_inconsistent_authority_root(
    storage: Storage,
    corruption: str,
    expected_error: type[Exception],
    message: str,
) -> None:
    authority_repository = ExecutorGenerationAuthorityRepository()
    with storage.unit_of_work() as unit_of_work:
        seed = _seed_authority(
            unit_of_work.connection,
            status=ExecutorGenerationAuthorityStatus.AUTHORIZED,
        )
        unit_of_work.commit()

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        if corruption == "authority_hash":
            connection.execute("DROP TRIGGER executor_generation_authorities_transition_guard")
            connection.execute(
                """UPDATE executor_generation_authorities
                   SET authority_hash=? WHERE candidate_id=?""",
                ("0" * 64, seed.authority.candidate_id),
            )
        elif corruption == "missing_journal":
            connection.execute("DROP TRIGGER executor_generation_authority_journal_no_delete")
            connection.execute(
                "DELETE FROM executor_generation_authority_journal WHERE candidate_id=?",
                (seed.authority.candidate_id,),
            )
        elif corruption == "promotion_hash":
            connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
            connection.execute(
                """UPDATE evolution_promotion_journal
                   SET entry_hash=? WHERE promotion_journal_id=?""",
                ("0" * 64, seed.authority.promotion_journal_id),
            )
        else:
            GenerationRepository().transition_pre_activation(
                connection,
                scope=_SCOPE,
                generation_id=seed.target.generation_id,
                target_state=RuntimeGenerationState.FAILED,
                expected_version=seed.target.version,
                updated_at=_NOW + timedelta(seconds=10),
            )
        unit_of_work.commit()

    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    with pytest.raises(expected_error, match=message):
        _controller(storage, registry, authority_repository).recover()

    assert registry.generation_records() == ()
    expected_state = (
        RuntimeGenerationState.FAILED
        if corruption == "unavailable_root"
        else RuntimeGenerationState.READY
    )
    assert _generation(storage, seed.target.generation_id).state is expected_state
