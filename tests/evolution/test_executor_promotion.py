"""Exact executor canary preparation, activation, and rollback semantics."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import AdapterError
from tianshu.evolution.adapters.executor_promotion import ExecutorPromotionAdapter
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.capabilities import ExecutorCapabilityManifestV1, pi_manifest
from tianshu.executor.generation_controller import GenerationController
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
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityStatus,
)
from tianshu.models.runtime_generation import RuntimeGenerationState, RuntimeReleaseV1
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_SCOPE = "executor:keqing:pi"
_BASE_GENERATION_ID = "rg-" + "a" * 32
_TARGET_GENERATION_ID = "rg-" + "b" * 32
_RETRY_GENERATION_ID = "rg-" + "c" * 32
_MEDIA_TYPE = "application/vnd.tianshu.evolution.executor+json"


def _promotion_journal_id(command_key: str) -> str:
    return hashlib.sha256(f"{command_key}\0intended".encode()).hexdigest()


def _promotion_intent_entry(
    candidate: EvolutionCandidateV1,
    command_key: str,
) -> dict[str, object]:
    return {
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
        "gate_snapshot_version": candidate.gate_snapshot_version,
        "gate_report_hash": canonical_sha256({"gate": command_key}),
        "routing_version": 1,
        "allocation_basis_points": 500,
        "receipt": None,
    }


@dataclass(frozen=True, slots=True)
class _Executor:
    manifest: ExecutorCapabilityManifestV1

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def supported_execution_modes(self) -> tuple[str, ...]:
        return self.manifest.execution_modes


@dataclass(frozen=True, slots=True)
class _Bundle:
    release: RuntimeReleaseV1
    executor_adapter: _Executor

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
    def __init__(self, storage) -> None:
        self._storage = storage

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        assert not self._storage._conn.in_transaction  # noqa: SLF001
        manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        return _Bundle(release=release, executor_adapter=_Executor(manifest))


async def _successful_probe() -> tuple[bool, str | None]:
    return True, None


class _FaultController:
    """Inject fail-after-effect crashes without changing exact controller semantics."""

    def __init__(self, delegate: GenerationController) -> None:
        self._delegate = delegate
        self.fail_after_stage = False
        self.fail_after_warm = False
        self.fail_after_rollback = False

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def stage_exact(self, release, *, generation_id: str, stage_commit_hook=None):
        result = self._delegate.stage_exact(
            release,
            generation_id=generation_id,
            stage_commit_hook=stage_commit_hook,
        )
        if self.fail_after_stage:
            self.fail_after_stage = False
            raise RuntimeError("injected crash after stage")
        return result

    async def warm_or_resume(self, generation_id: str):
        result = await self._delegate.warm_or_resume(generation_id)
        if self.fail_after_warm:
            self.fail_after_warm = False
            raise RuntimeError("injected crash after warm")
        return result

    def rollback_exact(
        self,
        scope: str,
        *,
        expected_active_generation_id: str,
        expected_last_good_generation_id: str,
    ):
        result = self._delegate.rollback_exact(
            scope,
            expected_active_generation_id=expected_active_generation_id,
            expected_last_good_generation_id=expected_last_good_generation_id,
        )
        if self.fail_after_rollback:
            self.fail_after_rollback = False
            raise RuntimeError("injected crash after rollback")
        return result


@dataclass(frozen=True, slots=True)
class _Context:
    artifacts: ArtifactStore
    adapter: ExecutorPromotionAdapter
    controller: GenerationController
    candidate: EvolutionCandidateV1
    command_key: str
    promotion_journal_id: str
    unit_of_work_factory: Callable[[], SqliteUnitOfWork]

    def unit_of_work(self) -> SqliteUnitOfWork:
        return self.unit_of_work_factory()


def _release(marker: str) -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": _SCOPE,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
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
        "materializer_id": "test-pi-release",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def _candidate(
    *,
    base: CandidateVersionRefV1,
    challenger: CandidateVersionRefV1,
) -> EvolutionCandidateV1:
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
        candidate_id="candidate-executor-promotion",
        kind=CandidateKind.EXECUTOR,
        subject_key=_SCOPE,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.SYSTEM,
            source_uri_redacted=None,
            source_digest=canonical_sha256({"source": "pi-drift"}),
            actor_principal_id="system:pi-drift-scanner",
            actor_display_name="Pi drift scanner",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="pi-drift-scanner",
            producer_version="1",
            received_at=_NOW,
        ),
        base=base,
        candidate=challenger,
        diff_artifact_digest=canonical_sha256({"diff": "pi-release"}),
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=1,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="executor-base-generation",
            adapter_name="executor",
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _copy_candidate(
    candidate: EvolutionCandidateV1,
    *,
    lifecycle: CandidateLifecycle | None = None,
    version: int | None = None,
    base: CandidateVersionRefV1 | None = None,
    challenger: CandidateVersionRefV1 | None = None,
) -> EvolutionCandidateV1:
    payload = candidate.model_dump(mode="python")
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle
    if version is not None:
        payload["version"] = version
    if base is not None:
        payload["base"] = base
        payload["rollback"] = candidate.rollback.model_copy(update={"champion_ref": base})
    if challenger is not None:
        payload["candidate"] = challenger
    payload["updated_at"] = candidate.updated_at + timedelta(seconds=1)
    return EvolutionCandidateV1.model_validate(payload)


def _persist_lifecycle(
    context: _Context,
    lifecycle: CandidateLifecycle,
) -> EvolutionCandidateV1:
    with context.unit_of_work() as unit_of_work:
        repository = EvolutionRepository()
        current = repository.get_candidate(
            unit_of_work.connection,
            context.candidate.candidate_id,
        )
        assert current is not None
        durable = repository.save_candidate(
            unit_of_work.connection,
            current.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "updated_at": current.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=current.version,
        )
        unit_of_work.commit()
    return durable


async def _build_context(
    storage,
    tmp_path: Path,
    *,
    target_warm_ok: bool = True,
    target_cancel_once: bool = False,
) -> _Context:
    artifacts = ArtifactStore(
        tmp_path / "executor-promotion-artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: _NOW,
    )
    base_release = _release("base")
    candidate_release = _release("candidate")
    base_artifact = artifacts.put_bytes(
        canonical_json_bytes(base_release),
        media_type=_MEDIA_TYPE,
        redaction="governed_candidate",
    )
    candidate_artifact = artifacts.put_bytes(
        canonical_json_bytes(candidate_release),
        media_type=_MEDIA_TYPE,
        redaction="governed_candidate",
    )
    candidate = _candidate(
        base=CandidateVersionRefV1(
            version="pi-base",
            artifact_digest=base_artifact.digest,
            canonical_digest=canonical_sha256(base_release),
        ),
        challenger=CandidateVersionRefV1(
            version="pi-candidate",
            artifact_digest=candidate_artifact.digest,
            canonical_digest=canonical_sha256(candidate_release),
        ),
    )

    executor = _Executor(pi_manifest())
    registry = ExecutorAdapterRegistry((executor,))

    cancel_remaining = target_cancel_once

    async def warm_probe(bundle: _Bundle) -> tuple[bool, str | None]:
        nonlocal cancel_remaining
        if bundle.release.release_digest == candidate_release.release_digest:
            if cancel_remaining:
                cancel_remaining = False
                raise asyncio.CancelledError
            return target_warm_ok, None if target_warm_ok else "target_probe_failed"
        return True, None

    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        registry,
        warm_probe=warm_probe,
        clock=lambda: _NOW,
    )
    controller.stage_exact(base_release, generation_id=_BASE_GENERATION_ID)
    await controller.warm_or_resume(_BASE_GENERATION_ID)
    controller.activate(_BASE_GENERATION_ID)

    command_key = "start-canary:executor-promotion"
    promotion_journal_id = _promotion_journal_id(command_key)
    with storage.unit_of_work() as unit_of_work:
        EvolutionPolicyRepository().upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=candidate.subject_key,
                kind=CandidateKind.EXECUTOR,
                mode="canary",
                max_canary_basis_points=500,
                version=1,
                updated_at=_NOW,
            ),
            expected_version=None,
        )
        repository = EvolutionRepository()
        candidate = repository.insert_candidate(unit_of_work.connection, candidate)
        for offset, lifecycle in enumerate(
            (
                CandidateLifecycle.STAGED,
                CandidateLifecycle.EVALUATING,
                CandidateLifecycle.READY,
            ),
            start=1,
        ):
            candidate = repository.save_candidate(
                unit_of_work.connection,
                candidate.model_copy(
                    update={
                        "lifecycle": lifecycle,
                        "updated_at": _NOW + timedelta(milliseconds=offset),
                    }
                ),
                expected_version=candidate.version,
            )
        unit_of_work.connection.execute(
            """INSERT INTO evolution_promotion_journal (
                   promotion_journal_id, command_key, candidate_id, candidate_version,
                   gate_snapshot_version, action, status, decision_request_id,
                   entry_json, entry_hash, created_at
               ) VALUES (?, ?, ?, ?, ?, 'start_canary', 'intended', NULL, ?, ?, ?)""",
            (
                promotion_journal_id,
                command_key,
                candidate.candidate_id,
                candidate.version,
                candidate.gate_snapshot_version,
                (
                    entry_json := canonical_json_bytes(
                        _promotion_intent_entry(candidate, command_key)
                    ).decode("utf-8")
                ),
                hashlib.sha256(entry_json.encode()).hexdigest(),
                _NOW.isoformat(),
            ),
        )
        unit_of_work.commit()

    return _Context(
        artifacts=artifacts,
        adapter=ExecutorPromotionAdapter(
            artifacts,
            controller,
            storage.unit_of_work,
            clock=lambda: _NOW,
        ),
        controller=controller,
        candidate=candidate,
        command_key=command_key,
        promotion_journal_id=promotion_journal_id,
        unit_of_work_factory=storage.unit_of_work,
    )


async def _prepare(context: _Context, adapter: ExecutorPromotionAdapter | None = None):
    return await (adapter or context.adapter).prepare_canary(
        context.candidate,
        command_key=context.command_key,
        generation_id=_TARGET_GENERATION_ID,
        promotion_journal_id=context.promotion_journal_id,
    )


async def test_prepare_canary_recovers_after_warm_and_replays_exactly(storage, tmp_path) -> None:
    context = await _build_context(storage, tmp_path)
    fault = _FaultController(context.controller)
    fault.fail_after_warm = True
    adapter = ExecutorPromotionAdapter(
        context.artifacts,
        cast(GenerationController, fault),
        storage.unit_of_work,
        clock=lambda: _NOW,
    )

    with pytest.raises(AdapterError, match="executor canary preparation failed"):
        await _prepare(context, adapter)

    with context.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.PENDING
    assert target is not None and target.state is RuntimeGenerationState.READY

    receipt = await _prepare(context, adapter)
    assert await _prepare(context, adapter) == receipt
    with context.unit_of_work() as unit_of_work:
        current = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        assert current is not None
        assert current.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
        assert (
            len(
                ExecutorGenerationAuthorityRepository().list_journal(
                    unit_of_work.connection,
                    candidate_id=context.candidate.candidate_id,
                )
            )
            == 2
        )
        adapter.validate_canary_preparation_current(
            unit_of_work.connection,
            context.candidate,
            receipt,
        )
        unit_of_work.commit()


async def test_prepare_canary_recovers_atomic_stage_and_pending_authority_after_restart(
    storage,
    tmp_path,
) -> None:
    context = await _build_context(storage, tmp_path)
    fault = _FaultController(context.controller)
    fault.fail_after_stage = True
    crashing_adapter = ExecutorPromotionAdapter(
        context.artifacts,
        cast(GenerationController, fault),
        storage.unit_of_work,
        clock=lambda: _NOW,
    )

    with pytest.raises(AdapterError, match="executor canary preparation failed"):
        await _prepare(context, crashing_adapter)

    authority_repository = ExecutorGenerationAuthorityRepository()
    with context.unit_of_work() as unit_of_work:
        authority = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.PENDING
    assert target is not None and target.state is RuntimeGenerationState.STAGED

    restarted_registry = ExecutorAdapterRegistry((_Executor(pi_manifest()),))
    restarted = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        restarted_registry,
        warm_probe=lambda _bundle: _successful_probe(),
        recovery_root_provider=lambda connection: frozenset(
            item.generation_id for item in authority_repository.list_recovery_roots(connection)
        ),
        clock=lambda: _NOW,
    )
    recovery = restarted.recover()
    assert recovery.failed_generation_ids == ()
    assert recovery.materialized_generation_ids == (
        _BASE_GENERATION_ID,
        _TARGET_GENERATION_ID,
    )

    replay_adapter = ExecutorPromotionAdapter(
        context.artifacts,
        restarted,
        storage.unit_of_work,
        clock=lambda: _NOW,
    )
    receipt = await _prepare(context, replay_adapter)
    assert receipt.generation_id == _TARGET_GENERATION_ID
    with context.unit_of_work() as unit_of_work:
        current = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        generations = tuple(
            item
            for item in GenerationRepository().list_recovery_candidates(
                unit_of_work.connection,
            )
            if item.scope == _SCOPE
        )
        unit_of_work.commit()
    assert current is not None
    assert current.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
    assert tuple(item.generation_id for item in generations) == (
        _BASE_GENERATION_ID,
        _TARGET_GENERATION_ID,
    )


async def test_warm_failure_revokes_pending_authority_without_moving_pointer(
    storage,
    tmp_path,
) -> None:
    context = await _build_context(storage, tmp_path, target_warm_ok=False)

    with pytest.raises(AdapterError, match="executor canary preparation failed"):
        await _prepare(context)

    with context.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=_SCOPE,
        )
        candidate = EvolutionRepository().get_candidate(
            unit_of_work.connection,
            context.candidate.candidate_id,
        )
        unit_of_work.commit()

    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert target is not None and target.state is RuntimeGenerationState.FAILED
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert candidate is not None and candidate.lifecycle is CandidateLifecycle.READY


async def test_cancelled_warm_revokes_authority_and_allows_new_command_retry(
    storage,
    tmp_path,
) -> None:
    context = await _build_context(storage, tmp_path, target_cancel_once=True)

    with pytest.raises(asyncio.CancelledError):
        await _prepare(context)

    authority_repository = ExecutorGenerationAuthorityRepository()
    generation_repository = GenerationRepository()
    with context.unit_of_work() as unit_of_work:
        cancelled = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        failed = generation_repository.get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        pointer = generation_repository.get_pointer(unit_of_work.connection, scope=_SCOPE)
        unit_of_work.commit()
    assert cancelled is not None
    assert cancelled.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert failed is not None and failed.state is RuntimeGenerationState.FAILED
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID

    retry_command_key = "start-canary:executor-promotion:retry"
    retry_journal_id = _promotion_journal_id(retry_command_key)
    retry_entry = _promotion_intent_entry(context.candidate, retry_command_key)
    with context.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """INSERT INTO evolution_promotion_journal (
                   promotion_journal_id, command_key, candidate_id, candidate_version,
                   gate_snapshot_version, action, status, decision_request_id,
                   entry_json, entry_hash, created_at
               ) VALUES (?, ?, ?, ?, ?, 'start_canary', 'intended', NULL, ?, ?, ?)""",
            (
                retry_journal_id,
                retry_command_key,
                context.candidate.candidate_id,
                context.candidate.version,
                context.candidate.gate_snapshot_version,
                (retry_json := canonical_json_bytes(retry_entry).decode("utf-8")),
                hashlib.sha256(retry_json.encode()).hexdigest(),
                _NOW.isoformat(),
            ),
        )
        unit_of_work.commit()

    receipt = await context.adapter.prepare_canary(
        context.candidate,
        command_key=retry_command_key,
        generation_id=_RETRY_GENERATION_ID,
        promotion_journal_id=retry_journal_id,
    )
    with context.unit_of_work() as unit_of_work:
        retried = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        ready = generation_repository.get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_RETRY_GENERATION_ID,
        )
        unit_of_work.commit()
    assert receipt.generation_id == _RETRY_GENERATION_ID
    assert retried is not None
    assert retried.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
    assert retried.generation_id == _RETRY_GENERATION_ID
    assert ready is not None and ready.state is RuntimeGenerationState.READY


async def test_prepare_and_validation_fail_closed_on_tampered_bindings(storage, tmp_path) -> None:
    context = await _build_context(storage, tmp_path)
    wrong_ref = CandidateVersionRefV1(
        version=context.candidate.candidate.version,
        artifact_digest="f" * 64,
        canonical_digest="e" * 64,
    )
    tampered_candidate = _copy_candidate(context.candidate, challenger=wrong_ref)

    with pytest.raises(AdapterError, match="durable binding mismatch"):
        await context.adapter.prepare_canary(
            tampered_candidate,
            command_key=context.command_key,
            generation_id=_TARGET_GENERATION_ID,
            promotion_journal_id=context.promotion_journal_id,
        )
    with context.unit_of_work() as unit_of_work:
        assert (
            GenerationRepository().get_generation(
                unit_of_work.connection,
                scope=_SCOPE,
                generation_id=_TARGET_GENERATION_ID,
            )
            is None
        )
        assert (
            ExecutorGenerationAuthorityRepository().get_current(
                unit_of_work.connection,
                candidate_id=context.candidate.candidate_id,
            )
            is None
        )
        unit_of_work.commit()

    receipt = await _prepare(context)
    wrong_receipt = receipt.model_copy(update={"authority_version": receipt.authority_version + 1})
    with context.unit_of_work() as unit_of_work:
        with pytest.raises(AdapterError, match="not current"):
            context.adapter.validate_canary_preparation_current(
                unit_of_work.connection,
                context.candidate,
                wrong_receipt,
            )
        unit_of_work.rollback()
    context.controller.activate_exact(
        _TARGET_GENERATION_ID,
        expected_active_generation_id=_BASE_GENERATION_ID,
        expected_active_release_digest=receipt.base_release_digest,
    )
    with context.unit_of_work() as unit_of_work:
        with pytest.raises(AdapterError, match="no longer current"):
            context.adapter.validate_canary_preparation_current(
                unit_of_work.connection,
                context.candidate,
                receipt,
            )
        unit_of_work.commit()


async def test_activation_is_exact_replay_and_rejects_candidate_rebinding(
    storage, tmp_path
) -> None:
    context = await _build_context(storage, tmp_path)
    await _prepare(context)
    canary = _persist_lifecycle(context, CandidateLifecycle.CANARY)

    receipt = context.adapter.activate(canary)
    assert context.adapter.activate(canary) == receipt
    assert receipt.artifact_digest == context.candidate.candidate.artifact_digest
    assert receipt.generation_id == _TARGET_GENERATION_ID
    assert receipt.release_digest == _release("candidate").release_digest
    with context.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(unit_of_work.connection, scope=_SCOPE)
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        base = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_BASE_GENERATION_ID,
        )
        unit_of_work.commit()
    assert pointer is not None
    assert pointer.active_generation_id == _TARGET_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.ACTIVE
    assert base is not None and base.state is RuntimeGenerationState.DRAINING

    rebound = _copy_candidate(
        canary,
        challenger=CandidateVersionRefV1(
            version=canary.candidate.version,
            artifact_digest="f" * 64,
            canonical_digest="e" * 64,
        ),
    )
    with pytest.raises(AdapterError, match="durable binding mismatch"):
        context.adapter.activate(rebound)


async def test_canary_rollback_only_withdraws_authority_and_is_replay_safe(
    storage, tmp_path
) -> None:
    context = await _build_context(storage, tmp_path)
    await _prepare(context)
    pending = _copy_candidate(
        context.candidate,
        lifecycle=CandidateLifecycle.ROLLBACK_PENDING,
        version=context.candidate.version + 2,
    )
    rebound_base = CandidateVersionRefV1(
        version="unbound-base-version",
        artifact_digest=context.candidate.base.artifact_digest,
        canonical_digest=context.candidate.base.canonical_digest,
    )
    with pytest.raises(AdapterError, match="durable binding mismatch"):
        context.adapter.rollback(
            _copy_candidate(
                pending,
                base=rebound_base,
            )
        )

    assert context.adapter.verify_rollback(pending) is None
    receipt = context.adapter.rollback(pending)
    assert context.adapter.rollback(pending) == receipt
    assert context.adapter.verify_rollback(pending) == receipt
    assert receipt.artifact_digest == context.candidate.base.artifact_digest

    with context.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        pointer = GenerationRepository().get_pointer(unit_of_work.connection, scope=_SCOPE)
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKING
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.READY


async def test_promoted_rollback_recovers_after_pointer_switch_and_replays(
    storage, tmp_path
) -> None:
    context = await _build_context(storage, tmp_path)
    await _prepare(context)
    canary = _persist_lifecycle(context, CandidateLifecycle.CANARY)
    context.adapter.activate(canary)
    pending = _copy_candidate(
        context.candidate,
        lifecycle=CandidateLifecycle.ROLLBACK_PENDING,
        version=context.candidate.version + 3,
    )

    fault = _FaultController(context.controller)
    fault.fail_after_rollback = True
    adapter = ExecutorPromotionAdapter(
        context.artifacts,
        cast(GenerationController, fault),
        storage.unit_of_work,
        clock=lambda: _NOW,
    )
    with pytest.raises(AdapterError, match="executor rollback failed"):
        adapter.rollback(pending)

    with context.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        pointer = GenerationRepository().get_pointer(unit_of_work.connection, scope=_SCOPE)
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
    assert pointer is not None and pointer.active_generation_id == _BASE_GENERATION_ID

    receipt = adapter.rollback(pending)
    assert adapter.rollback(pending) == receipt
    assert adapter.verify_rollback(pending) == receipt
    with context.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        pointer = GenerationRepository().get_pointer(unit_of_work.connection, scope=_SCOPE)
        target = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_TARGET_GENERATION_ID,
        )
        base = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=_BASE_GENERATION_ID,
        )
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.DRAINING
    assert base is not None and base.state is RuntimeGenerationState.ACTIVE
