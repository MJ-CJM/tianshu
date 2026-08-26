"""PromotionService integration coverage for the executor generation saga."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from tests.evolution.test_executor_promotion import (
    _BASE_GENERATION_ID,
    _MEDIA_TYPE,
    _NOW,
    _candidate,
    _Executor,
    _Materializer,
    _release,
)

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.executor_promotion import ExecutorPromotionAdapter
from tianshu.evolution.gates import EvolutionGateReportV1, EvolutionGateResultV1, GateStatus
from tianshu.evolution.promotion import (
    PromoteCommand,
    PromotionConflict,
    PromotionService,
    RollbackCommand,
    StartCanaryCommand,
)
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.capabilities import pi_manifest
from tianshu.executor.generation_controller import GenerationController
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
)
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
    GateName,
)
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.executor_generation_authority import ExecutorGenerationAuthorityStatus
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.runtime_generation import RuntimeGenerationState, RuntimeReleaseV1
from tianshu.storage import Storage
from tianshu.storage.decision_repo import DecisionRepository
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository

_SERVICE_NOW = _NOW + timedelta(hours=1)


class _GateAuthority:
    def __init__(self, report: EvolutionGateReportV1) -> None:
        self.reports = {report.candidate_id: report}

    def add(self, report: EvolutionGateReportV1) -> None:
        self.reports[report.candidate_id] = report

    def get_current_report_current(
        self,
        connection: object,
        candidate_id: str,
    ) -> EvolutionGateReportV1:
        del connection
        return self.reports[candidate_id]

    def validate_bound_green_report_current(
        self,
        connection: object,
        candidate_id: str,
        *,
        candidate_version: int,
        gate_snapshot_version: int,
        candidate_digest: str,
        report_hash: str,
    ) -> EvolutionGateReportV1:
        del connection
        report = self.reports[candidate_id]
        assert (
            candidate_id,
            candidate_version,
            gate_snapshot_version,
            candidate_digest,
            report_hash,
        ) == (
            report.candidate_id,
            report.candidate_version,
            report.gate_snapshot_version,
            report.candidate_digest,
            report.report_hash,
        )
        return report


@dataclass(frozen=True, slots=True)
class _Context:
    storage: Storage
    artifacts: ArtifactStore
    controller: GenerationController
    adapter: ExecutorPromotionAdapter
    gates: _GateAuthority
    service: PromotionService
    auth: AuthContext
    candidate: EvolutionCandidateV1
    base_release: RuntimeReleaseV1
    challenger_release: RuntimeReleaseV1


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    active = Storage(str(tmp_path / "executor-promotion-service.db"))
    active.init_db()
    yield active
    active.close()


def _green(candidate: EvolutionCandidateV1) -> EvolutionGateReportV1:
    return EvolutionGateReportV1.from_results(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        candidate_digest=candidate.candidate.artifact_digest,
        gate_snapshot_version=candidate.gate_snapshot_version,
        results=tuple(
            EvolutionGateResultV1(
                gate=gate,
                status=GateStatus.PASSED,
                reason_code="gate_check_passed",
            )
            for gate in GateName
        ),
        evidence_bundle_ids=candidate.evidence_bundle_ids,
        evaluated_at=_NOW + timedelta(minutes=1),
    )


async def _build_context(storage: Storage, tmp_path: Path) -> _Context:
    artifacts = ArtifactStore(
        tmp_path / "executor-promotion-service-artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: _NOW,
    )
    base_release = _release("service-base")
    challenger_release = _release("service-challenger")
    base_artifact = artifacts.put_bytes(
        canonical_json_bytes(base_release),
        media_type=_MEDIA_TYPE,
        redaction="governed_candidate",
    )
    challenger_artifact = artifacts.put_bytes(
        canonical_json_bytes(challenger_release),
        media_type=_MEDIA_TYPE,
        redaction="governed_candidate",
    )
    candidate = _candidate(
        base=CandidateVersionRefV1(
            version="pi-service-base",
            artifact_digest=base_artifact.digest,
            canonical_digest=canonical_sha256(base_release),
        ),
        challenger=CandidateVersionRefV1(
            version="pi-service-challenger",
            artifact_digest=challenger_artifact.digest,
            canonical_digest=canonical_sha256(challenger_release),
        ),
    )

    async def warm_probe(_bundle: object) -> tuple[bool, str | None]:
        return True, None

    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        ExecutorAdapterRegistry((_Executor(pi_manifest()),)),
        warm_probe=warm_probe,
        clock=lambda: _NOW,
    )
    controller.stage_exact(base_release, generation_id=_BASE_GENERATION_ID)
    await controller.warm_or_resume(_BASE_GENERATION_ID)
    controller.activate(_BASE_GENERATION_ID)

    with storage.unit_of_work() as unit_of_work:
        policy_repository = EvolutionPolicyRepository()
        policy_repository.upsert_policy(
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
        unit_of_work.commit()

    report = _green(candidate)
    snapshot_json = canonical_json_bytes(report).decode("utf-8")
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """INSERT INTO evolution_gate_snapshots (
                   gate_snapshot_id, candidate_id, candidate_version,
                   gate_snapshot_version, snapshot_json, snapshot_hash,
                   evidence_bundle_ids_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)""",
            (
                f"gate:{candidate.candidate_id}:{candidate.gate_snapshot_version}",
                candidate.candidate_id,
                candidate.version,
                candidate.gate_snapshot_version,
                snapshot_json,
                hashlib.sha256(snapshot_json.encode()).hexdigest(),
                report.evaluated_at.isoformat(),
            ),
        )
        unit_of_work.commit()

    auth = AuthContext(
        principal=Principal(
            id=candidate.provenance.actor_principal_id,
            kind=PrincipalKind.HUMAN,
            display_name="Executor operator",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="executor-promotion-service-test",
    )
    gates = _GateAuthority(report)
    adapter = ExecutorPromotionAdapter(
        artifacts,
        controller,
        storage.unit_of_work,
        clock=lambda: _SERVICE_NOW,
    )
    service = _service(storage, gates, adapter)
    return _Context(
        storage=storage,
        artifacts=artifacts,
        controller=controller,
        adapter=adapter,
        gates=gates,
        service=service,
        auth=auth,
        candidate=candidate,
        base_release=base_release,
        challenger_release=challenger_release,
    )


def _add_ready_candidate(
    context: _Context,
    *,
    candidate_id: str,
    base: CandidateVersionRefV1,
    marker: str,
) -> tuple[EvolutionCandidateV1, RuntimeReleaseV1]:
    release = _release(marker)
    artifact = context.artifacts.put_bytes(
        canonical_json_bytes(release),
        media_type=_MEDIA_TYPE,
        redaction="governed_candidate",
    )
    candidate = _candidate(
        base=base,
        challenger=CandidateVersionRefV1(
            version=f"pi-{marker}",
            artifact_digest=artifact.digest,
            canonical_digest=canonical_sha256(release),
        ),
    ).model_copy(
        update={
            "candidate_id": candidate_id,
            "diff_artifact_digest": canonical_sha256({"diff": marker}),
        }
    )
    with context.storage.unit_of_work() as unit_of_work:
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
                        "updated_at": _NOW + timedelta(seconds=offset),
                    }
                ),
                expected_version=candidate.version,
            )
        report = _green(candidate)
        snapshot_json = canonical_json_bytes(report).decode("utf-8")
        unit_of_work.connection.execute(
            """INSERT INTO evolution_gate_snapshots (
                   gate_snapshot_id, candidate_id, candidate_version,
                   gate_snapshot_version, snapshot_json, snapshot_hash,
                   evidence_bundle_ids_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)""",
            (
                f"gate:{candidate.candidate_id}:{candidate.gate_snapshot_version}",
                candidate.candidate_id,
                candidate.version,
                candidate.gate_snapshot_version,
                snapshot_json,
                hashlib.sha256(snapshot_json.encode()).hexdigest(),
                report.evaluated_at.isoformat(),
            ),
        )
        unit_of_work.commit()
    context.gates.add(report)
    return candidate, release


def _candidate_by_id(context: _Context, candidate_id: str) -> EvolutionCandidateV1:
    with context.storage.unit_of_work() as unit_of_work:
        candidate = EvolutionRepository().get_candidate(unit_of_work.connection, candidate_id)
        unit_of_work.commit()
    assert candidate is not None
    return candidate


def _service(
    storage: Storage,
    gates: _GateAuthority,
    adapter: ExecutorPromotionAdapter,
) -> PromotionService:
    return PromotionService(
        storage,
        gates,
        adapter_resolver=lambda kind: (
            adapter
            if kind is CandidateKind.EXECUTOR
            else pytest.fail("unexpected promotion adapter kind")
        ),
        clock=lambda: _SERVICE_NOW,
    )


def _disabled_service(context: _Context) -> tuple[ExecutorPromotionAdapter, PromotionService]:
    adapter = ExecutorPromotionAdapter(
        context.artifacts,
        context.controller,
        context.storage.unit_of_work,
        evolution_enabled=False,
        clock=lambda: _SERVICE_NOW,
    )
    return adapter, _service(context.storage, context.gates, adapter)


def _start_command(candidate: EvolutionCandidateV1) -> StartCanaryCommand:
    return StartCanaryCommand(
        expected_version=candidate.version,
        idempotency_key="executor-start-canary",
        reason="begin reviewed executor canary",
        allocation_basis_points=250,
        allocation_seed_id="executor-seed",
    )


def _command_key(auth: AuthContext, idempotency_key: str) -> str:
    return f"promotion:{canonical_sha256({'principal_id': auth.principal.id, 'idempotency_key': idempotency_key})}"


def _journal_id(command_key: str, status: str) -> str:
    return hashlib.sha256(f"{command_key}\0{status}".encode()).hexdigest()


def _target_generation_id(
    auth: AuthContext,
    candidate: EvolutionCandidateV1,
    command: StartCanaryCommand,
) -> str:
    command_key = _command_key(auth, command.idempotency_key)
    identity = canonical_sha256(
        {
            "candidate_artifact_digest": candidate.candidate.artifact_digest,
            "candidate_id": candidate.candidate_id,
            "candidate_version": candidate.version,
            "command_key": command_key,
            "schema_version": 1,
        }
    )
    return f"rg-{identity[:32]}"


def _candidate_current(context: _Context) -> EvolutionCandidateV1:
    with context.storage.unit_of_work() as unit_of_work:
        candidate = EvolutionRepository().get_candidate(
            unit_of_work.connection,
            context.candidate.candidate_id,
        )
        unit_of_work.commit()
    assert candidate is not None
    return candidate


def _promotion_rows(context: _Context) -> tuple[object, ...]:
    return tuple(
        context.storage._conn.execute(  # noqa: SLF001 - immutable journal observation
            """SELECT rowid, promotion_journal_id, command_key, action, status,
                      decision_request_id, entry_json, entry_hash
               FROM evolution_promotion_journal
               WHERE candidate_id=? ORDER BY rowid""",
            (context.candidate.candidate_id,),
        ).fetchall()
    )


def _generation_state(context: _Context, generation_id: str):
    with context.storage.unit_of_work() as unit_of_work:
        generation = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=context.candidate.subject_key,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    return generation


def _pointer(context: _Context):
    with context.storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=context.candidate.subject_key,
        )
        unit_of_work.commit()
    return pointer


def _add_promotion_decision(
    context: _Context,
    candidate: EvolutionCandidateV1,
    *,
    decision_id: str,
) -> None:
    with context.storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        edict_id = f"edict-{decision_id}"
        memorial_id = f"memorial-{decision_id}"
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            (edict_id, "review executor promotion", _NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
            (memorial_id, edict_id, "submitted", _NOW.isoformat()),
        )
        payload = {
            "schema_version": 1,
            "candidate_id": candidate.candidate_id,
            "candidate_version": candidate.version,
            "candidate_artifact_digest": candidate.candidate.artifact_digest,
            "gate_snapshot_version": candidate.gate_snapshot_version,
            "action": "promote",
            "risk_tier": "high",
        }
        request = DecisionRequestV1(
            decision_request_id=decision_id,
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id=edict_id,
            memorial_id=memorial_id,
            request_key=f"request-{decision_id}",
            payload=payload,
            payload_hash=canonical_sha256(payload),
            requested_by=context.auth.principal.id,
            expires_at=_SERVICE_NOW + timedelta(hours=1),
            status=DecisionStatus.PENDING,
            version=1,
            created_at=_NOW,
            updated_at=_NOW,
        )
        decisions = DecisionRepository()
        decisions.add_or_get(connection, request)
        decisions.resolve(
            connection,
            DecisionResolutionV1(
                decision_request_id=decision_id,
                action="approve",
                reason="reviewed high-risk executor",
                payload={"schema_version": 1},
                actor_principal_id="reviewer-1",
                actor_display_name="Reviewer",
                resolved_at=_NOW + timedelta(minutes=30),
            ),
            expected_version=1,
            now=_NOW + timedelta(minutes=30),
        )
        unit_of_work.commit()


async def _start_canary(context: _Context):
    command = _start_command(context.candidate)
    receipt = await context.service.start_canary_async(
        context.candidate.candidate_id,
        command,
        auth=context.auth,
    )
    return command, receipt


async def _promote(context: _Context):
    start_command, start_receipt = await _start_canary(context)
    canary = _candidate_current(context)
    decision_id = "decision-executor-promote"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    receipt = context.service.promote(
        context.candidate.candidate_id,
        PromoteCommand(
            expected_version=start_receipt.candidate_version,
            idempotency_key="executor-promote",
            reason="promote reviewed executor generation",
            decision_request_id=decision_id,
        ),
        auth=context.auth,
    )
    return start_command, receipt, decision_id


async def test_sync_start_canary_for_executor_fails_closed(
    tmp_path: Path, storage: Storage
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)
    before = (
        len(_promotion_rows(context)),
        len(context.storage._conn.execute("SELECT 1 FROM runtime_generations").fetchall()),  # noqa: SLF001
    )

    with pytest.raises(PromotionConflict, match="^executor_canary_requires_async_path$"):
        context.service.start_canary(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    assert _candidate_current(context).lifecycle is CandidateLifecycle.READY
    assert (
        (
            len(_promotion_rows(context)),
            len(context.storage._conn.execute("SELECT 1 FROM runtime_generations").fetchall()),  # noqa: SLF001
        )
        == before
        == (0, 1)
    )
    assert (
        context.storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        == 0
    )
    assert (
        context.storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM executor_generation_authorities"
        ).fetchone()[0]
        == 0
    )


async def test_async_start_canary_persists_exact_generation_authority_and_routing(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    command, receipt = await _start_canary(context)
    command_key = _command_key(context.auth, command.idempotency_key)
    target_id = _target_generation_id(context.auth, context.candidate, command)
    current = _candidate_current(context)
    pointer = _pointer(context)
    target = _generation_state(context, target_id)
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        authority_journal = ExecutorGenerationAuthorityRepository().list_journal(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        unit_of_work.commit()

    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "completed"),
    ]
    assert receipt.lifecycle is CandidateLifecycle.CANARY
    assert receipt.generation_id == target_id
    assert receipt.release_digest == context.challenger_release.release_digest
    assert current.lifecycle is CandidateLifecycle.CANARY
    assert current.routing is not None
    assert current.routing.allocation_basis_points == 250
    assert current.routing.routing_version == 1
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.READY
    assert target.release_digest == context.challenger_release.release_digest
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
    assert authority.candidate_id == context.candidate.candidate_id
    assert authority.candidate_version == context.candidate.version
    assert authority.candidate_artifact_digest == context.candidate.candidate.artifact_digest
    assert authority.candidate_canonical_digest == context.candidate.candidate.canonical_digest
    assert authority.release_digest == context.challenger_release.release_digest
    assert authority.generation_id == target_id
    assert authority.base_generation_id == _BASE_GENERATION_ID
    assert authority.base_release_digest == context.base_release.release_digest
    assert authority.start_command_key == command_key
    assert authority.promotion_journal_id == _journal_id(command_key, "intended")
    assert tuple(record.entry.transition.value for record in authority_journal) == (
        "pending",
        "authorized",
    )


async def test_finalization_failure_compensates_exact_executor_preparation(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)
    target_id = _target_generation_id(context.auth, context.candidate, command)

    def reject_after_applied(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected deterministic finalization failure")

    monkeypatch.setattr(
        context.adapter, "validate_canary_preparation_current", reject_after_applied
    )
    with pytest.raises(PromotionConflict, match="^executor_generation_authority_invalid$"):
        await context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "failed"),
    ]
    assert _candidate_current(context).lifecycle is CandidateLifecycle.READY
    target = _generation_state(context, target_id)
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        unit_of_work.commit()
    assert target is not None and target.state is RuntimeGenerationState.FAILED
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID

    with pytest.raises(PromotionConflict, match="^executor_canary_preparation_failed$"):
        await context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )


async def test_applied_process_crash_replays_without_a_second_generation(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)
    target_id = _target_generation_id(context.auth, context.candidate, command)
    complete = context.service._complete_executor_canary  # noqa: SLF001

    class _InjectedProcessExit(BaseException):
        pass

    def crash_after_applied(**_kwargs: object) -> None:
        raise _InjectedProcessExit

    monkeypatch.setattr(context.service, "_complete_executor_canary", crash_after_applied)
    with pytest.raises(_InjectedProcessExit):
        await context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
    ]
    assert _candidate_current(context).lifecycle is CandidateLifecycle.READY
    assert _generation_state(context, target_id).state is RuntimeGenerationState.READY

    monkeypatch.setattr(context.service, "_complete_executor_canary", complete)
    restarted = _service(storage, context.gates, context.adapter)
    receipt = await restarted.start_canary_async(
        context.candidate.candidate_id,
        command,
        auth=context.auth,
    )
    assert (
        await restarted.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
        == receipt
    )

    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "completed"),
    ]
    assert set(
        row["generation_id"]
        for row in storage._conn.execute(  # noqa: SLF001
            "SELECT generation_id FROM runtime_generations"
        )
    ) == {_BASE_GENERATION_ID, target_id}


async def test_disabled_executor_terminalizes_an_applied_start_after_restart(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)

    class _InjectedProcessExit(BaseException):
        pass

    def crash_after_applied(**_kwargs: object) -> None:
        raise _InjectedProcessExit

    monkeypatch.setattr(context.service, "_complete_executor_canary", crash_after_applied)
    with pytest.raises(_InjectedProcessExit):
        await context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    disabled_adapter, disabled = _disabled_service(context)
    assert disabled_adapter.new_evolution_enabled is False
    with pytest.raises(PromotionConflict, match="^executor_generation_authority_invalid$"):
        await disabled.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    assert _candidate_current(context).lifecycle is CandidateLifecycle.READY
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "failed"),
    ]


async def test_disabled_executor_rejects_a_fresh_start_before_journaling(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _disabled_adapter, disabled = _disabled_service(context)

    with pytest.raises(PromotionConflict, match="^executor_generation_unavailable$"):
        await disabled.start_canary_async(
            context.candidate.candidate_id,
            _start_command(context.candidate),
            auth=context.auth,
        )

    assert _promotion_rows(context) == ()
    assert _candidate_current(context).lifecycle is CandidateLifecycle.READY


async def test_disabled_executor_can_rollback_a_live_canary(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start, canary = await _start_canary(context)
    _disabled_adapter, disabled = _disabled_service(context)

    receipt = disabled.rollback(
        context.candidate.candidate_id,
        RollbackCommand(
            expected_version=canary.candidate_version,
            idempotency_key="executor-disabled-canary-rollback",
            reason="rollback remains available while evolution is disabled",
        ),
        auth=context.auth,
    )

    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID


async def test_disabled_executor_can_rollback_a_promoted_generation(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start, promoted, _decision = await _promote(context)
    _disabled_adapter, disabled = _disabled_service(context)

    receipt = disabled.rollback(
        context.candidate.candidate_id,
        RollbackCommand(
            expected_version=promoted.candidate_version,
            idempotency_key="executor-disabled-promoted-rollback",
            reason="restore last-good while forward evolution is disabled",
        ),
        auth=context.auth,
    )

    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID


async def test_disabled_executor_reconciles_a_pending_rollback(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start, promoted, _decision = await _promote(context)
    command = RollbackCommand(
        expected_version=promoted.candidate_version,
        idempotency_key="executor-disabled-pending-rollback",
        reason="resume rollback after disabling forward evolution",
    )

    def interrupt(_candidate: EvolutionCandidateV1):
        raise RuntimeError("injected rollback interruption")

    monkeypatch.setattr(context.adapter, "rollback", interrupt)
    with pytest.raises(PromotionConflict, match="^rollback_restore_failed$"):
        context.service.rollback(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
    assert _candidate_current(context).lifecycle is CandidateLifecycle.ROLLBACK_PENDING

    _disabled_adapter, disabled = _disabled_service(context)
    assert disabled.reconcile_pending_rollbacks() == 1
    assert _candidate_current(context).lifecycle is CandidateLifecycle.ROLLED_BACK
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID


async def test_executor_promote_requires_decision_and_activates_the_mapped_generation(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    start_command, start_receipt = await _start_canary(context)
    target_id = _target_generation_id(context.auth, context.candidate, start_command)

    with pytest.raises(PromotionConflict, match="^promotion_decision_required$"):
        context.service.promote(
            context.candidate.candidate_id,
            PromoteCommand(
                expected_version=start_receipt.candidate_version,
                idempotency_key="executor-promote-without-decision",
                reason="must not promote without a Decision",
            ),
            auth=context.auth,
        )
    assert not any(row["action"] == "promote" for row in _promotion_rows(context))
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID

    canary = _candidate_current(context)
    decision_id = "decision-executor-promote-required"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    receipt = context.service.promote(
        context.candidate.candidate_id,
        PromoteCommand(
            expected_version=start_receipt.candidate_version,
            idempotency_key="executor-promote-approved",
            reason="apply approved executor promotion",
            decision_request_id=decision_id,
        ),
        auth=context.auth,
    )

    pointer = _pointer(context)
    current = _candidate_current(context)
    target = _generation_state(context, target_id)
    base = _generation_state(context, _BASE_GENERATION_ID)
    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert receipt.generation_id == target_id
    assert receipt.release_digest == context.challenger_release.release_digest
    assert current.lifecycle is CandidateLifecycle.PROMOTED
    assert current.routing is not None and current.routing.allocation_basis_points == 0
    assert pointer is not None
    assert pointer.active_generation_id == target_id
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.ACTIVE
    assert base is not None and base.state is RuntimeGenerationState.DRAINING
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)[3:]] == [
        ("promote", "intended"),
        ("promote", "applied"),
        ("promote", "completed"),
    ]
    promote_rows = _promotion_rows(context)[3:]
    for row in promote_rows[1:]:
        journal_receipt = json.loads(row["entry_json"])["receipt"]
        assert journal_receipt["generation_id"] == target_id
        assert journal_receipt["release_digest"] == context.challenger_release.release_digest
    assert all(row["decision_request_id"] == decision_id for row in _promotion_rows(context)[3:])


async def test_promote_replays_after_pointer_switch_before_applied_journal(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    start_command, start_receipt = await _start_canary(context)
    target_id = _target_generation_id(context.auth, context.candidate, start_command)
    canary = _candidate_current(context)
    decision_id = "decision-executor-promote-pointer-crash-replay"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    command = PromoteCommand(
        expected_version=start_receipt.candidate_version,
        idempotency_key="executor-promote-pointer-crash-replay",
        reason="replay an approved executor promotion",
        decision_request_id=decision_id,
    )
    activate = context.adapter.activate

    def crash_after_pointer_switch(candidate: EvolutionCandidateV1):
        activate(candidate)
        raise RuntimeError("injected crash after executor pointer switch")

    monkeypatch.setattr(context.adapter, "activate", crash_after_pointer_switch)
    with pytest.raises(PromotionConflict, match="^promotion_activation_failed$"):
        context.service.promote(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )

    assert _candidate_current(context).lifecycle is CandidateLifecycle.CANARY
    assert _pointer(context).active_generation_id == target_id
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)[3:]] == [
        ("promote", "intended")
    ]

    monkeypatch.setattr(context.adapter, "activate", activate)
    receipt = context.service.promote(
        context.candidate.candidate_id,
        command,
        auth=context.auth,
    )
    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert _candidate_current(context).lifecycle is CandidateLifecycle.PROMOTED
    assert _pointer(context).active_generation_id == target_id
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)[3:]] == [
        ("promote", "intended"),
        ("promote", "applied"),
        ("promote", "completed"),
    ]


async def test_rollback_recovers_pointer_switch_before_promote_applied_journal(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    start_command, start_receipt = await _start_canary(context)
    target_id = _target_generation_id(context.auth, context.candidate, start_command)
    canary = _candidate_current(context)
    decision_id = "decision-executor-promote-pointer-crash-rollback"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    activate = context.adapter.activate

    def crash_after_pointer_switch(candidate: EvolutionCandidateV1):
        activate(candidate)
        raise RuntimeError("injected crash after executor pointer switch")

    monkeypatch.setattr(context.adapter, "activate", crash_after_pointer_switch)
    with pytest.raises(PromotionConflict, match="^promotion_activation_failed$"):
        context.service.promote(
            context.candidate.candidate_id,
            PromoteCommand(
                expected_version=start_receipt.candidate_version,
                idempotency_key="executor-promote-pointer-crash-rollback",
                reason="exercise rollback after an interrupted promotion",
                decision_request_id=decision_id,
            ),
            auth=context.auth,
        )

    receipt = context.service.rollback(
        context.candidate.candidate_id,
        RollbackCommand(
            expected_version=start_receipt.candidate_version,
            idempotency_key="executor-pointer-crash-direct-rollback",
            reason="restore last-good after interrupted promotion",
        ),
        auth=context.auth,
    )
    pointer = _pointer(context)
    target = _generation_state(context, target_id)
    base = _generation_state(context, _BASE_GENERATION_ID)
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        unit_of_work.commit()

    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert _candidate_current(context).lifecycle is CandidateLifecycle.ROLLED_BACK
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert base is not None and base.state is RuntimeGenerationState.ACTIVE
    assert target is not None and target.state is RuntimeGenerationState.DRAINING
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKED


async def test_rollback_wins_between_activation_check_and_pointer_cas(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    start_command, start_receipt = await _start_canary(context)
    target_id = _target_generation_id(context.auth, context.candidate, start_command)
    canary = _candidate_current(context)
    decision_id = "decision-executor-promote-rollback-cas-race"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    original_activate_exact = context.controller.activate_exact
    rollback_lifecycles: list[CandidateLifecycle] = []

    def rollback_before_pointer_cas(
        generation_id: str,
        *,
        expected_active_generation_id: str,
        expected_active_release_digest: str,
        activation_commit_hook=None,
    ):
        rollback = context.service.rollback(
            context.candidate.candidate_id,
            RollbackCommand(
                expected_version=start_receipt.candidate_version,
                idempotency_key="executor-activation-cas-race-rollback",
                reason="rollback must win before the pointer CAS",
            ),
            auth=context.auth,
        )
        rollback_lifecycles.append(rollback.lifecycle)
        return original_activate_exact(
            generation_id,
            expected_active_generation_id=expected_active_generation_id,
            expected_active_release_digest=expected_active_release_digest,
            activation_commit_hook=activation_commit_hook,
        )

    monkeypatch.setattr(context.controller, "activate_exact", rollback_before_pointer_cas)
    with pytest.raises(PromotionConflict, match="^promotion_activation_failed$"):
        context.service.promote(
            context.candidate.candidate_id,
            PromoteCommand(
                expected_version=start_receipt.candidate_version,
                idempotency_key="executor-activation-cas-race-promote",
                reason="exercise rollback versus activation CAS",
                decision_request_id=decision_id,
            ),
            auth=context.auth,
        )

    pointer = _pointer(context)
    target = _generation_state(context, target_id)
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        unit_of_work.commit()
    assert rollback_lifecycles == [CandidateLifecycle.ROLLED_BACK]
    assert _candidate_current(context).lifecycle is CandidateLifecycle.ROLLED_BACK
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert target is not None and target.state is RuntimeGenerationState.READY
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKING


async def test_promoted_rollback_restores_last_good_and_keeps_auditable_logs(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    start_command, promoted, decision_id = await _promote(context)
    target_id = _target_generation_id(context.auth, context.candidate, start_command)

    receipt = context.service.rollback(
        context.candidate.candidate_id,
        RollbackCommand(
            expected_version=promoted.candidate_version,
            idempotency_key="executor-promoted-rollback",
            reason="restore the exact last-good executor generation",
        ),
        auth=context.auth,
    )
    replay = context.service.rollback(
        context.candidate.candidate_id,
        RollbackCommand(
            expected_version=promoted.candidate_version,
            idempotency_key="executor-promoted-rollback",
            reason="restore the exact last-good executor generation",
        ),
        auth=context.auth,
    )

    pointer = _pointer(context)
    current = _candidate_current(context)
    target = _generation_state(context, target_id)
    base = _generation_state(context, _BASE_GENERATION_ID)
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        authority_journal = ExecutorGenerationAuthorityRepository().list_journal(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        lifecycle_states = tuple(
            row["to_lifecycle"]
            for row in unit_of_work.connection.execute(
                """SELECT to_lifecycle FROM evolution_lifecycle_journal
                   WHERE candidate_id=? ORDER BY candidate_version""",
                (context.candidate.candidate_id,),
            )
        )
        unit_of_work.commit()

    assert replay == receipt
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert pointer is not None
    assert pointer.active_generation_id == _BASE_GENERATION_ID
    assert pointer.last_good_generation_id == _BASE_GENERATION_ID
    assert base is not None and base.state is RuntimeGenerationState.ACTIVE
    assert target is not None and target.state is RuntimeGenerationState.DRAINING
    assert current.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert current.routing is not None
    assert current.routing.allocation_basis_points == 0
    assert current.routing.routing_version == 3
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert tuple(record.entry.transition.value for record in authority_journal) == (
        "pending",
        "authorized",
        "revoked",
    )
    assert lifecycle_states[-5:] == (
        "ready",
        "canary",
        "promoted",
        "rollback_pending",
        "rolled_back",
    )

    rows = _promotion_rows(context)
    assert [(row["action"], row["status"]) for row in rows] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "completed"),
        ("promote", "intended"),
        ("promote", "applied"),
        ("promote", "completed"),
        ("rollback", "rollback_pending"),
        ("rollback", "applied"),
        ("rollback", "completed"),
    ]
    assert all(
        hashlib.sha256(row["entry_json"].encode()).hexdigest() == row["entry_hash"]
        and canonical_json_bytes(json.loads(row["entry_json"])).decode() == row["entry_json"]
        for row in rows
    )
    assert all(
        row["decision_request_id"] == decision_id for row in rows if row["action"] == "promote"
    )


async def test_second_start_command_is_rejected_before_preparation(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    outer = _start_command(context.candidate)
    competing = outer.model_copy(update={"idempotency_key": "executor-start-canary-competing"})
    prepare = context.adapter.prepare_canary
    competing_errors: list[str] = []

    async def prepare_with_competing_command(*args: object, **kwargs: object):
        with pytest.raises(PromotionConflict) as raised:
            await context.service.start_canary_async(
                context.candidate.candidate_id,
                competing,
                auth=context.auth,
            )
        competing_errors.append(str(raised.value))
        return await prepare(*args, **kwargs)

    monkeypatch.setattr(context.adapter, "prepare_canary", prepare_with_competing_command)
    receipt = await context.service.start_canary_async(
        context.candidate.candidate_id,
        outer,
        auth=context.auth,
    )

    assert receipt.lifecycle is CandidateLifecycle.CANARY
    assert competing_errors == ["subject_transition_in_progress"]
    competing_key = _command_key(context.auth, competing.idempotency_key)
    assert not any(row["command_key"] == competing_key for row in _promotion_rows(context))


async def test_same_start_command_is_single_flight_and_replays_one_receipt(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)
    prepare = context.adapter.prepare_canary
    entered = asyncio.Event()
    release = asyncio.Event()
    prepare_calls = 0

    async def paused_prepare(*args: object, **kwargs: object):
        nonlocal prepare_calls
        prepare_calls += 1
        entered.set()
        await release.wait()
        return await prepare(*args, **kwargs)

    monkeypatch.setattr(context.adapter, "prepare_canary", paused_prepare)
    first = asyncio.create_task(
        context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
    )
    await asyncio.sleep(0)
    assert prepare_calls == 1
    release.set()
    first_receipt, second_receipt = await asyncio.gather(first, second)

    assert first_receipt == second_receipt
    assert prepare_calls == 1
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "completed"),
    ]
    with storage.unit_of_work() as unit_of_work:
        authority = ExecutorGenerationAuthorityRepository().get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        unit_of_work.commit()
    assert authority is not None
    assert authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED


async def test_cancelled_waiter_does_not_compensate_the_start_command_owner(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    command = _start_command(context.candidate)
    prepare = context.adapter.prepare_canary
    entered = asyncio.Event()
    release = asyncio.Event()

    async def paused_prepare(*args: object, **kwargs: object):
        entered.set()
        await release.wait()
        return await prepare(*args, **kwargs)

    monkeypatch.setattr(context.adapter, "prepare_canary", paused_prepare)
    owner = asyncio.create_task(
        context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
    )
    await entered.wait()
    waiter = asyncio.create_task(
        context.service.start_canary_async(
            context.candidate.candidate_id,
            command,
            auth=context.auth,
        )
    )
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    receipt = await owner

    assert receipt.lifecycle is CandidateLifecycle.CANARY
    assert [(row["action"], row["status"]) for row in _promotion_rows(context)] == [
        ("start_canary", "intended"),
        ("start_canary", "applied"),
        ("start_canary", "completed"),
    ]


async def test_second_promote_command_cannot_orphan_an_applied_journal(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start, start_receipt = await _start_canary(context)
    canary = _candidate_current(context)
    decision_id = "decision-executor-promote-exclusive"
    _add_promotion_decision(context, canary, decision_id=decision_id)
    outer = PromoteCommand(
        expected_version=start_receipt.candidate_version,
        idempotency_key="executor-promote-exclusive-outer",
        reason="serialize exact executor activation",
        decision_request_id=decision_id,
    )
    competing = outer.model_copy(update={"idempotency_key": "executor-promote-exclusive-competing"})
    activate = context.adapter.activate
    competing_errors: list[str] = []

    def activate_with_competing_command(candidate: EvolutionCandidateV1):
        with pytest.raises(PromotionConflict) as raised:
            context.service.promote(
                candidate.candidate_id,
                competing,
                auth=context.auth,
            )
        competing_errors.append(str(raised.value))
        return activate(candidate)

    monkeypatch.setattr(context.adapter, "activate", activate_with_competing_command)
    receipt = context.service.promote(
        context.candidate.candidate_id,
        outer,
        auth=context.auth,
    )

    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert competing_errors == ["subject_transition_in_progress"]
    competing_key = _command_key(context.auth, competing.idempotency_key)
    assert not any(row["command_key"] == competing_key for row in _promotion_rows(context))


async def test_next_generation_canary_rollback_preserves_earlier_last_good(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start_b, promoted_b, _decision_b = await _promote(context)
    generation_b = promoted_b.generation_id
    assert generation_b is not None
    candidate_c, _release_c = _add_ready_candidate(
        context,
        candidate_id="candidate-executor-promotion-c-canary",
        base=context.candidate.candidate,
        marker="service-c-canary",
    )
    command_c = StartCanaryCommand(
        expected_version=candidate_c.version,
        idempotency_key="executor-c-canary-start",
        reason="exercise next-generation canary rollback",
        allocation_basis_points=250,
        allocation_seed_id="executor-c-canary-seed",
    )
    canary_c = await context.service.start_canary_async(
        candidate_c.candidate_id,
        command_c,
        auth=context.auth,
    )
    before = _pointer(context)
    assert before.active_generation_id == generation_b
    assert before.last_good_generation_id == _BASE_GENERATION_ID

    rolled_back = context.service.rollback(
        candidate_c.candidate_id,
        RollbackCommand(
            expected_version=canary_c.candidate_version,
            idempotency_key="executor-c-canary-rollback",
            reason="withdraw only the unpromoted challenger",
        ),
        auth=context.auth,
    )

    after = _pointer(context)
    assert rolled_back.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert after.active_generation_id == generation_b
    assert after.last_good_generation_id == _BASE_GENERATION_ID


async def test_base_rollback_is_rejected_while_a_newer_canary_is_live(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start_b, promoted_b, _decision_b = await _promote(context)
    generation_b = promoted_b.generation_id
    assert generation_b is not None
    candidate_c, _release_c = _add_ready_candidate(
        context,
        candidate_id="candidate-executor-promotion-c-block-base-rollback",
        base=context.candidate.candidate,
        marker="service-c-block-base-rollback",
    )
    start_c = StartCanaryCommand(
        expected_version=candidate_c.version,
        idempotency_key="executor-c-block-base-rollback-start",
        reason="keep C canary live while B remains champion",
        allocation_basis_points=250,
        allocation_seed_id="executor-c-block-base-rollback-seed",
    )
    canary_c = await context.service.start_canary_async(
        candidate_c.candidate_id,
        start_c,
        auth=context.auth,
    )
    b_before = _candidate_current(context)
    b_rows_before = len(_promotion_rows(context))

    with pytest.raises(PromotionConflict, match="^subject_canary_exists$"):
        context.service.rollback(
            context.candidate.candidate_id,
            RollbackCommand(
                expected_version=b_before.version,
                idempotency_key="executor-b-blocked-by-c-canary",
                reason="must withdraw C before rolling B back",
            ),
            auth=context.auth,
        )

    assert _candidate_current(context) == b_before
    assert len(_promotion_rows(context)) == b_rows_before
    assert _pointer(context).active_generation_id == generation_b
    current_c = _candidate_by_id(context, candidate_c.candidate_id)
    decision_c = "decision-executor-promote-c-after-blocked-b-rollback"
    _add_promotion_decision(context, current_c, decision_id=decision_c)
    promoted_c = context.service.promote(
        candidate_c.candidate_id,
        PromoteCommand(
            expected_version=canary_c.candidate_version,
            idempotency_key="executor-c-promote-after-blocked-b-rollback",
            reason="promote C after the unsafe B rollback was rejected",
            decision_request_id=decision_c,
        ),
        auth=context.auth,
    )
    assert _pointer(context).active_generation_id == promoted_c.generation_id
    assert _pointer(context).last_good_generation_id == generation_b


async def test_pending_rollback_blocks_a_new_start_and_can_replay(
    tmp_path: Path,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start_b, promoted_b, _decision_b = await _promote(context)
    candidate_c, _release_c = _add_ready_candidate(
        context,
        candidate_id="candidate-executor-promotion-c-blocked-by-rollback",
        base=context.candidate.candidate,
        marker="service-c-blocked-by-rollback",
    )
    rollback_command = RollbackCommand(
        expected_version=promoted_b.candidate_version,
        idempotency_key="executor-b-interrupted-rollback",
        reason="exercise durable rollback subject fence",
    )
    rollback = context.adapter.rollback

    def crash_before_rollback_effect(_candidate: EvolutionCandidateV1):
        raise RuntimeError("injected rollback effect interruption")

    monkeypatch.setattr(context.adapter, "rollback", crash_before_rollback_effect)
    with pytest.raises(PromotionConflict, match="^rollback_restore_failed$"):
        context.service.rollback(
            context.candidate.candidate_id,
            rollback_command,
            auth=context.auth,
        )
    assert _candidate_current(context).lifecycle is CandidateLifecycle.ROLLBACK_PENDING

    with pytest.raises(PromotionConflict, match="^subject_transition_in_progress$"):
        await context.service.start_canary_async(
            candidate_c.candidate_id,
            StartCanaryCommand(
                expected_version=candidate_c.version,
                idempotency_key="executor-c-blocked-start",
                reason="must not prepare C during B rollback",
                allocation_basis_points=250,
                allocation_seed_id="executor-c-blocked-seed",
            ),
            auth=context.auth,
        )
    assert not context.storage._conn.execute(  # noqa: SLF001
        "SELECT 1 FROM evolution_promotion_journal WHERE candidate_id=?",
        (candidate_c.candidate_id,),
    ).fetchall()

    monkeypatch.setattr(context.adapter, "rollback", rollback)
    completed = context.service.rollback(
        context.candidate.candidate_id,
        rollback_command,
        auth=context.auth,
    )
    assert completed.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert _pointer(context).active_generation_id == _BASE_GENERATION_ID


async def test_executor_generations_evolve_a_to_b_to_c_and_roll_back_to_b(
    tmp_path: Path,
    storage: Storage,
) -> None:
    context = await _build_context(storage, tmp_path)
    _start_b, promoted_b, _decision_b = await _promote(context)
    generation_b = promoted_b.generation_id
    assert generation_b is not None
    candidate_c, release_c = _add_ready_candidate(
        context,
        candidate_id="candidate-executor-promotion-c",
        base=context.candidate.candidate,
        marker="service-c",
    )
    start_c = StartCanaryCommand(
        expected_version=candidate_c.version,
        idempotency_key="executor-c-start",
        reason="begin C canary from active B",
        allocation_basis_points=250,
        allocation_seed_id="executor-c-seed",
    )
    canary_c = await context.service.start_canary_async(
        candidate_c.candidate_id,
        start_c,
        auth=context.auth,
    )
    generation_c = canary_c.generation_id
    assert generation_c is not None
    pointer_before_c = _pointer(context)
    assert pointer_before_c.active_generation_id == generation_b
    assert pointer_before_c.last_good_generation_id == _BASE_GENERATION_ID

    current_c = _candidate_by_id(context, candidate_c.candidate_id)
    decision_c = "decision-executor-promote-c"
    _add_promotion_decision(context, current_c, decision_id=decision_c)
    promoted_c = context.service.promote(
        candidate_c.candidate_id,
        PromoteCommand(
            expected_version=canary_c.candidate_version,
            idempotency_key="executor-c-promote",
            reason="promote C while retaining B as last-good",
            decision_request_id=decision_c,
        ),
        auth=context.auth,
    )
    pointer_c = _pointer(context)
    assert promoted_c.release_digest == release_c.release_digest
    assert pointer_c.active_generation_id == generation_c
    assert pointer_c.last_good_generation_id == generation_b

    with storage.unit_of_work() as unit_of_work:
        authorities = ExecutorGenerationAuthorityRepository()
        authority_b = authorities.get_current(
            unit_of_work.connection,
            candidate_id=context.candidate.candidate_id,
        )
        authority_c = authorities.get_current(
            unit_of_work.connection,
            candidate_id=candidate_c.candidate_id,
        )
        recovery_roots = authorities.list_recovery_roots(unit_of_work.connection)
        unit_of_work.commit()
    assert authority_b is not None
    assert authority_b.status is ExecutorGenerationAuthorityStatus.REVOKED
    assert authority_c is not None
    assert authority_c.status is ExecutorGenerationAuthorityStatus.AUTHORIZED
    assert {item.generation_id for item in recovery_roots} == {generation_c}

    b_before = _candidate_current(context)
    b_rows_before = len(_promotion_rows(context))
    with pytest.raises(PromotionConflict, match="^rollback_preconditions_not_met$"):
        context.service.rollback(
            context.candidate.candidate_id,
            RollbackCommand(
                expected_version=b_before.version,
                idempotency_key="executor-b-stale-rollback",
                reason="must not roll back a superseded generation",
            ),
            auth=context.auth,
        )
    assert _candidate_current(context) == b_before
    assert len(_promotion_rows(context)) == b_rows_before
    assert _pointer(context) == pointer_c

    rolled_back_c = context.service.rollback(
        candidate_c.candidate_id,
        RollbackCommand(
            expected_version=promoted_c.candidate_version,
            idempotency_key="executor-c-promoted-rollback",
            reason="restore B from C",
        ),
        auth=context.auth,
    )
    pointer_b = _pointer(context)
    assert rolled_back_c.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert pointer_b.active_generation_id == generation_b
    assert pointer_b.last_good_generation_id == generation_b
