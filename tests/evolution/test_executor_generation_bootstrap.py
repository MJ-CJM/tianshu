"""First executor generation bootstrap and crash-resume invariants."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.evolution.executor_generation_bootstrap import ExecutorGenerationBootstrap
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.adapters.protocol import PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.executor.generation_controller import (
    GenerationController,
    GenerationRecoveryReport,
)
from tianshu.executor.keqing.generation import PI_GENERATION_SCOPE
from tianshu.models.canonical import canonical_sha256
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage import Storage
from tianshu.storage.generation_repo import (
    GenerationActivationResult,
    GenerationRepository,
)

_NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)
_UNAUTHORIZED_READY_ID = "rg-" + "f" * 32


@dataclass
class _Adapter:
    manifest: ExecutorCapabilityManifestV1

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def supported_execution_modes(self) -> tuple[str, ...]:
        return self.manifest.execution_modes

    def probe(self) -> HostCapabilityProbeV1:
        return HostCapabilityProbeV1(
            probe_id="executor-bootstrap-test",
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
        execution_mode: str,
    ) -> PreparedExecution:
        return PreparedExecution(
            run_id=run_id,
            effective=effective,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    async def execute(self, prepared: PreparedExecution, *_args, **_kwargs) -> str:
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
    def __init__(
        self,
        storage: Storage,
        release: RuntimeReleaseV1,
        *,
        fail_create: bool = False,
    ) -> None:
        self._storage = storage
        self.release = release
        self.fail_create = fail_create
        self.create_calls = 0
        self.materialize_calls: list[str] = []

    def create_release(self) -> RuntimeReleaseV1:
        assert not self._storage._conn.in_transaction  # noqa: SLF001
        self.create_calls += 1
        if self.fail_create:
            raise AssertionError("bootstrap unexpectedly scanned the managed Pi installation")
        return self.release

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        assert not self._storage._conn.in_transaction  # noqa: SLF001
        self.materialize_calls.append(release.release_digest)
        manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        return _Bundle(release=release, executor_adapter=_Adapter(manifest))


class _TrackingController(GenerationController):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.events: list[tuple[str, object]] = []

    def recover(
        self,
        *,
        pre_active_root_ids: frozenset[str] = frozenset(),
    ) -> GenerationRecoveryReport:
        self.events.append(("recover", pre_active_root_ids))
        return super().recover(pre_active_root_ids=pre_active_root_ids)

    def stage_exact(
        self,
        release: RuntimeReleaseV1,
        *,
        generation_id: str,
    ) -> RuntimeGenerationV1:
        self.events.append(("stage_exact", generation_id))
        return super().stage_exact(release, generation_id=generation_id)

    async def warm_or_resume(self, generation_id: str) -> RuntimeGenerationV1:
        self.events.append(("warm_or_resume", generation_id))
        return await super().warm_or_resume(generation_id)

    def activate(
        self,
        generation_id: str,
        *,
        expected_active_generation_id: str | None = None,
        expected_active_release_digest: str | None = None,
    ) -> GenerationActivationResult:
        self.events.append(("activate", generation_id))
        return super().activate(
            generation_id,
            expected_active_generation_id=expected_active_generation_id,
            expected_active_release_digest=expected_active_release_digest,
        )


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    active = Storage(str(tmp_path / "executor-bootstrap.db"))
    active.init_db()
    yield active
    active.close()


def _release() -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": PI_GENERATION_SCOPE,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": "/opt/tianshu/bin/pi",
        "binary_digest": canonical_sha256({"binary": "bootstrap"}),
        "package_name": "@mariozechner/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": canonical_sha256({"package": "bootstrap"}),
        "single_argv_shape": "pi-single-v1",
        "session_argv_shape": "pi-session-v1",
        "pi_wire_version": 3,
        "materializer_id": "test-pi-release",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def _generation_id(release: RuntimeReleaseV1, *, attempt: int = 1) -> str:
    identity = canonical_sha256(
        {
            "attempt": attempt,
            "purpose": "executor-baseline",
            "release_digest": release.release_digest,
            "schema_version": 1,
        }
    )
    return f"rg-{identity[:32]}"


def _controller(storage: Storage, materializer: _Materializer) -> _TrackingController:
    async def warm_probe(_bundle: _Bundle) -> tuple[bool, str | None]:
        assert not storage._conn.in_transaction  # noqa: SLF001
        return True, None

    return _TrackingController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        ExecutorAdapterRegistry((_Adapter(pi_manifest()),)),
        warm_probe=warm_probe,
        generation_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("bootstrap must provide a deterministic generation id")
        ),
        # Simulate a restarted process after every durable crash fixture timestamp.
        clock=lambda: _NOW + timedelta(seconds=10),
    )


def _bootstrap(
    storage: Storage,
    controller: _TrackingController,
    materializer: _Materializer,
    *,
    enabled: bool,
) -> ExecutorGenerationBootstrap:
    return ExecutorGenerationBootstrap(
        unit_of_work_factory=storage.unit_of_work,
        controller=controller,
        materializer=materializer,  # type: ignore[arg-type]
        enabled=enabled,
    )


def _seed_generation(
    storage: Storage,
    release: RuntimeReleaseV1,
    *,
    generation_id: str,
    state: RuntimeGenerationState,
) -> RuntimeGenerationV1:
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_release(unit_of_work.connection, release, first_seen_at=_NOW)
        generation = repository.insert_staged(
            unit_of_work.connection,
            RuntimeGenerationV1(
                generation_id=generation_id,
                scope=release.scope,
                release_digest=release.release_digest,
                state=RuntimeGenerationState.STAGED,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        )
        if state in {RuntimeGenerationState.WARMING, RuntimeGenerationState.READY}:
            generation = repository.transition_pre_activation(
                unit_of_work.connection,
                scope=release.scope,
                generation_id=generation_id,
                target_state=RuntimeGenerationState.WARMING,
                expected_version=generation.version,
                updated_at=_NOW + timedelta(seconds=1),
            )
        if state is RuntimeGenerationState.READY:
            generation = repository.transition_pre_activation(
                unit_of_work.connection,
                scope=release.scope,
                generation_id=generation_id,
                target_state=RuntimeGenerationState.READY,
                expected_version=generation.version,
                updated_at=_NOW + timedelta(seconds=2),
            )
        if state is RuntimeGenerationState.FAILED:
            generation = repository.transition_pre_activation(
                unit_of_work.connection,
                scope=release.scope,
                generation_id=generation_id,
                target_state=RuntimeGenerationState.FAILED,
                expected_version=generation.version,
                updated_at=_NOW + timedelta(seconds=1),
            )
        unit_of_work.commit()
    return generation


def _seed_active_pointer(
    storage: Storage,
    release: RuntimeReleaseV1,
    *,
    generation_id: str,
) -> RuntimeGenerationV1:
    ready = _seed_generation(
        storage,
        release,
        generation_id=generation_id,
        state=RuntimeGenerationState.READY,
    )
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        activated = repository.activate(
            unit_of_work.connection,
            scope=release.scope,
            target_generation_id=generation_id,
            expected_generation_version=ready.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        ).activated
        unit_of_work.commit()
    return activated


def _generations(storage: Storage) -> tuple[RuntimeGenerationV1, ...]:
    with storage.unit_of_work() as unit_of_work:
        generations = GenerationRepository().list_by_scope(
            unit_of_work.connection,
            scope=PI_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    return generations


def _pointer(storage: Storage):
    with storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=PI_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    return pointer


def _journal_states(storage: Storage, generation_id: str) -> tuple[RuntimeGenerationState, ...]:
    with storage.unit_of_work() as unit_of_work:
        entries = GenerationRepository().list_journal(
            unit_of_work.connection,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    return tuple(entry.to_state for entry in entries)


def _candidate_count(storage: Storage) -> int:
    return storage._conn.execute(  # noqa: SLF001 - durable test observation
        "SELECT COUNT(*) FROM evolution_candidates"
    ).fetchone()[0]


async def test_disabled_only_recovers_without_establishing_a_baseline(storage: Storage) -> None:
    release = _release()
    materializer = _Materializer(storage, release, fail_create=True)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=False,
    ).initialize()

    assert report.enabled is False
    assert report.bootstrapped is False
    assert report.active_generation_id is None
    assert report.release_digest is None
    assert report.recovery == GenerationRecoveryReport((), ())
    assert controller.events == [("recover", frozenset())]
    assert materializer.create_calls == 0
    assert materializer.materialize_calls == []
    assert _generations(storage) == ()
    assert _pointer(storage) is None
    assert _candidate_count(storage) == 0


async def test_existing_pointer_only_recovers_without_scanning_or_creating(
    storage: Storage,
) -> None:
    release = _release()
    generation_id = _generation_id(release)
    _seed_active_pointer(storage, release, generation_id=generation_id)
    materializer = _Materializer(storage, release, fail_create=True)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=True,
    ).initialize()

    assert report.enabled is True
    assert report.bootstrapped is False
    assert report.active_generation_id == generation_id
    assert report.release_digest == release.release_digest
    assert report.recovery.materialized_generation_ids == (generation_id,)
    assert report.recovery.failed_generation_ids == ()
    assert controller.events == [("recover", frozenset())]
    assert materializer.create_calls == 0
    assert materializer.materialize_calls == [release.release_digest]
    assert len(_generations(storage)) == 1
    assert _candidate_count(storage) == 0


async def test_first_start_stages_warms_and_activates_one_last_good_baseline(
    storage: Storage,
) -> None:
    release = _release()
    generation_id = _generation_id(release)
    materializer = _Materializer(storage, release)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=True,
    ).initialize()

    pointer = _pointer(storage)
    assert report.enabled is True
    assert report.bootstrapped is True
    assert report.active_generation_id == generation_id
    assert report.release_digest == release.release_digest
    assert report.recovery == GenerationRecoveryReport((), ())
    assert controller.events == [
        ("recover", frozenset({generation_id})),
        ("stage_exact", generation_id),
        ("warm_or_resume", generation_id),
        ("activate", generation_id),
    ]
    assert materializer.create_calls == 1
    assert pointer is not None
    assert pointer.active_generation_id == generation_id
    assert pointer.last_good_generation_id == generation_id
    assert _journal_states(storage, generation_id) == (
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
        RuntimeGenerationState.ACTIVE,
    )
    assert len(_generations(storage)) == 1
    assert _candidate_count(storage) == 0


@pytest.mark.parametrize(
    "crash_state",
    [
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
    ],
)
async def test_pre_active_crash_resumes_the_same_deterministic_generation(
    storage: Storage,
    crash_state: RuntimeGenerationState,
) -> None:
    release = _release()
    generation_id = _generation_id(release)
    _seed_generation(
        storage,
        release,
        generation_id=generation_id,
        state=crash_state,
    )
    materializer = _Materializer(storage, release)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=True,
    ).initialize()

    generations = _generations(storage)
    assert report.bootstrapped is True
    assert report.active_generation_id == generation_id
    assert report.recovery.materialized_generation_ids == (generation_id,)
    assert report.recovery.failed_generation_ids == ()
    assert controller.events == [
        ("recover", frozenset({generation_id})),
        ("stage_exact", generation_id),
        ("warm_or_resume", generation_id),
        ("activate", generation_id),
    ]
    assert materializer.create_calls == 1
    assert len(generations) == 1
    assert generations[0].generation_id == generation_id
    assert generations[0].state is RuntimeGenerationState.ACTIVE
    assert _journal_states(storage, generation_id) == (
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
        RuntimeGenerationState.ACTIVE,
    )
    assert _candidate_count(storage) == 0


async def test_failed_history_advances_to_the_next_deterministic_attempt(
    storage: Storage,
) -> None:
    release = _release()
    failed_id = _generation_id(release, attempt=1)
    next_id = _generation_id(release, attempt=2)
    _seed_generation(
        storage,
        release,
        generation_id=failed_id,
        state=RuntimeGenerationState.FAILED,
    )
    materializer = _Materializer(storage, release)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=True,
    ).initialize()

    by_id = {generation.generation_id: generation for generation in _generations(storage)}
    assert report.active_generation_id == next_id
    assert set(by_id) == {failed_id, next_id}
    assert by_id[failed_id].state is RuntimeGenerationState.FAILED
    assert by_id[next_id].state is RuntimeGenerationState.ACTIVE
    assert controller.events[0] == ("recover", frozenset({next_id}))
    assert _pointer(storage).active_generation_id == next_id
    assert _pointer(storage).last_good_generation_id == next_id
    assert _candidate_count(storage) == 0


async def test_unauthorized_ready_generation_is_failed_closed_not_activated(
    storage: Storage,
) -> None:
    release = _release()
    trusted_id = _generation_id(release)
    assert trusted_id != _UNAUTHORIZED_READY_ID
    _seed_generation(
        storage,
        release,
        generation_id=_UNAUTHORIZED_READY_ID,
        state=RuntimeGenerationState.READY,
    )
    materializer = _Materializer(storage, release)
    controller = _controller(storage, materializer)

    report = await _bootstrap(
        storage,
        controller,
        materializer,
        enabled=True,
    ).initialize()

    by_id = {generation.generation_id: generation for generation in _generations(storage)}
    pointer = _pointer(storage)
    assert report.recovery.materialized_generation_ids == ()
    assert report.recovery.failed_generation_ids == (_UNAUTHORIZED_READY_ID,)
    assert by_id[_UNAUTHORIZED_READY_ID].state is RuntimeGenerationState.FAILED
    assert by_id[trusted_id].state is RuntimeGenerationState.ACTIVE
    assert pointer is not None
    assert pointer.active_generation_id == trusted_id
    assert pointer.last_good_generation_id == trusted_id
    assert _candidate_count(storage) == 0
