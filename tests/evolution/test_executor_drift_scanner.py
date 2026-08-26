"""Executor drift scanning is explicit, deterministic, and detached from reads."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.candidate_service import CandidateLiveAuthorities, CandidateService
from tianshu.evolution.executor_drift import ExecutorDriftScanner
from tianshu.executor.capabilities import pi_manifest
from tianshu.executor.keqing.generation import PI_GENERATION_SCOPE
from tianshu.gateway.keqing_api import keqing_router
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateLifecycle
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage import Storage
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.generation_repo import GenerationRepository

_NOW = datetime(2026, 8, 26, 9, tzinfo=UTC)


class _FakeMaterializer:
    def __init__(self, observed: RuntimeReleaseV1, *, fail_on_use: bool = False) -> None:
        self.observed = observed
        self.fail_on_use = fail_on_use
        self.verified: list[RuntimeReleaseV1] = []
        self.create_calls = 0

    def verify_release(self, release: RuntimeReleaseV1) -> None:
        if self.fail_on_use:
            raise AssertionError("disabled scanner used the materializer")
        self.verified.append(release)

    def create_release(self) -> RuntimeReleaseV1:
        if self.fail_on_use:
            raise AssertionError("disabled scanner used the materializer")
        self.create_calls += 1
        return self.observed


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    active = Storage(str(tmp_path / "executor-drift.db"))
    active.init_db()
    yield active
    active.close()


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def candidate_service(
    storage: Storage,
    artifact_root: Path,
    tmp_path: Path,
) -> CandidateService:
    roots = {
        name: tmp_path / "live" / name
        for name in ("memory", "skill", "policy", "persona", "code", "executor")
    }
    for root in roots.values():
        root.mkdir(parents=True)
    artifacts = ArtifactStore(
        artifact_root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: _NOW,
    )
    return CandidateService(
        storage,
        artifacts,
        live_authorities=CandidateLiveAuthorities(
            memory_root=roots["memory"],
            skill_target=roots["skill"],
            policy_root=roots["policy"],
            persona_root=roots["persona"],
            code_worktree=roots["code"],
            executor_root=roots["executor"],
        ),
        clock=lambda: _NOW,
    )


def _release(marker: str) -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": PI_GENERATION_SCOPE,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
        # Deliberately keep the CLI version fixed: drift must compare the full release.
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": "/opt/tianshu/bin/pi",
        "binary_digest": canonical_sha256({"binary": "stable"}),
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


def _install_active_baseline(storage: Storage, release: RuntimeReleaseV1) -> None:
    repository = GenerationRepository()
    generation_id = f"rg-{canonical_sha256({'release': release.release_digest})[:32]}"
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
        generation = repository.transition_pre_activation(
            unit_of_work.connection,
            scope=release.scope,
            generation_id=generation.generation_id,
            target_state=RuntimeGenerationState.WARMING,
            expected_version=generation.version,
            updated_at=_NOW + timedelta(seconds=1),
        )
        generation = repository.transition_pre_activation(
            unit_of_work.connection,
            scope=release.scope,
            generation_id=generation.generation_id,
            target_state=RuntimeGenerationState.READY,
            expected_version=generation.version,
            updated_at=_NOW + timedelta(seconds=2),
        )
        pointer = repository.get_pointer(unit_of_work.connection, scope=release.scope)
        repository.activate(
            unit_of_work.connection,
            scope=release.scope,
            target_generation_id=generation.generation_id,
            expected_generation_version=generation.version,
            expected_pointer_version=pointer.version if pointer is not None else None,
            updated_at=_NOW + timedelta(seconds=3),
        )
        unit_of_work.commit()


def _scanner(
    storage: Storage,
    candidate_service: CandidateService,
    materializer: _FakeMaterializer,
    *,
    enabled: bool = True,
    monotonic=lambda: 0.0,
) -> ExecutorDriftScanner:
    return ExecutorDriftScanner(
        unit_of_work_factory=storage.unit_of_work,
        candidate_service=candidate_service,
        materializer=materializer,  # type: ignore[arg-type]
        enabled=enabled,
        interval_seconds=1.0,
        monotonic=monotonic,
    )


def _durable_counts(storage: Storage) -> tuple[int, int]:
    candidate_count = storage._conn.execute(  # noqa: SLF001 - durable test observation
        "SELECT COUNT(*) FROM evolution_candidates"
    ).fetchone()[0]
    artifact_count = storage._conn.execute(  # noqa: SLF001 - durable test observation
        "SELECT COUNT(*) FROM artifact_records"
    ).fetchone()[0]
    return candidate_count, artifact_count


def _artifact_files(root: Path) -> tuple[str, ...]:
    return tuple(sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()))


def _only_candidate(storage: Storage):
    row = storage._conn.execute(  # noqa: SLF001 - durable test observation
        "SELECT candidate_id FROM evolution_candidates"
    ).fetchone()
    assert row is not None
    candidate = EvolutionRepository().get_candidate(storage._conn, row["candidate_id"])  # noqa: SLF001
    assert candidate is not None
    return candidate


def test_disabled_switch_has_zero_side_effects(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
) -> None:
    materializer = _FakeMaterializer(_release("observed"), fail_on_use=True)
    scanner = _scanner(storage, candidate_service, materializer, enabled=False)
    statements: list[str] = []
    before = (_durable_counts(storage), _artifact_files(artifact_root))
    storage._conn.set_trace_callback(statements.append)  # noqa: SLF001

    try:
        assert scanner.scan_once() == 0
    finally:
        storage._conn.set_trace_callback(None)  # noqa: SLF001

    assert scanner.last_error_code is None
    assert scanner.last_candidate_id is None
    assert materializer.verified == []
    assert materializer.create_calls == 0
    assert (_durable_counts(storage), _artifact_files(artifact_root)) == before
    assert statements == []


def test_missing_baseline_reports_explicit_status_without_candidate(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
) -> None:
    materializer = _FakeMaterializer(_release("observed"))
    scanner = _scanner(storage, candidate_service, materializer)

    assert scanner.scan_once() == 0

    assert scanner.last_error_code == "executor_generation_baseline_unestablished"
    assert scanner.last_candidate_id is None
    assert materializer.verified == []
    assert materializer.create_calls == 0
    assert _durable_counts(storage) == (0, 0)
    assert _artifact_files(artifact_root) == ()


def test_identical_full_release_proposes_nothing(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
) -> None:
    baseline = _release("same")
    _install_active_baseline(storage, baseline)
    materializer = _FakeMaterializer(baseline)
    scanner = _scanner(storage, candidate_service, materializer)

    assert scanner.scan_once() == 0

    assert scanner.last_error_code is None
    assert scanner.last_candidate_id is None
    assert materializer.verified == [baseline]
    assert materializer.create_calls == 1
    assert _durable_counts(storage) == (0, 0)
    assert _artifact_files(artifact_root) == ()


def test_different_full_release_proposes_one_deterministic_executor_candidate(
    storage: Storage,
    candidate_service: CandidateService,
) -> None:
    baseline = _release("baseline")
    observed = _release("observed")
    assert baseline.cli_version == observed.cli_version
    assert baseline.package_digest != observed.package_digest
    _install_active_baseline(storage, baseline)
    scanner = _scanner(storage, candidate_service, _FakeMaterializer(observed))

    assert scanner.scan_once() == 1

    candidate = _only_candidate(storage)
    assert _durable_counts(storage) == (1, 3)
    assert candidate.kind is CandidateKind.EXECUTOR
    assert candidate.subject_key == PI_GENERATION_SCOPE
    assert candidate.lifecycle is CandidateLifecycle.PROPOSED
    assert candidate.candidate_id == scanner.last_candidate_id
    assert candidate.candidate_id.startswith("evolution-")
    assert len(candidate.candidate_id) == len("evolution-") + 64
    assert candidate.base.canonical_digest == canonical_sha256(baseline.model_dump(mode="json"))
    assert candidate.candidate.canonical_digest == canonical_sha256(
        observed.model_dump(mode="json")
    )


def test_rescan_and_restart_style_new_scanner_do_not_duplicate(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
) -> None:
    baseline = _release("baseline")
    observed = _release("observed")
    _install_active_baseline(storage, baseline)
    ticks = iter((0.0, 2.0))
    first = _scanner(
        storage,
        candidate_service,
        _FakeMaterializer(observed),
        monotonic=ticks.__next__,
    )

    assert first.scan_once() == 1
    candidate_id = first.last_candidate_id
    after_first = (_durable_counts(storage), _artifact_files(artifact_root))
    assert first.scan_once() == 0

    restarted = _scanner(storage, candidate_service, _FakeMaterializer(observed))
    assert restarted.scan_once() == 0

    assert candidate_id is not None
    assert _only_candidate(storage).candidate_id == candidate_id
    assert (_durable_counts(storage), _artifact_files(artifact_root)) == after_first
    assert after_first[0] == (1, 3)
    assert first.last_error_code is None
    assert restarted.last_error_code is None
    assert restarted.last_candidate_id is None


def test_same_challenger_against_a_new_active_base_creates_a_new_candidate(
    storage: Storage,
    candidate_service: CandidateService,
) -> None:
    first_base = _release("first-base")
    challenger = _release("challenger")
    _install_active_baseline(storage, first_base)
    assert (
        _scanner(
            storage,
            candidate_service,
            _FakeMaterializer(challenger),
        ).scan_once()
        == 1
    )

    second_base = _release("second-base")
    _install_active_baseline(storage, second_base)
    rescanned = _scanner(
        storage,
        candidate_service,
        _FakeMaterializer(challenger),
    )

    assert rescanned.scan_once() == 1
    rows = storage._conn.execute(  # noqa: SLF001 - durable test observation
        "SELECT candidate_id FROM evolution_candidates ORDER BY candidate_id"
    ).fetchall()
    assert len(rows) == 2
    candidates = tuple(
        EvolutionRepository().get_candidate(storage._conn, row["candidate_id"])  # noqa: SLF001
        for row in rows
    )
    assert all(candidate is not None for candidate in candidates)
    assert {
        candidate.base.canonical_digest for candidate in candidates if candidate is not None
    } == {
        canonical_sha256(first_base.model_dump(mode="json")),
        canonical_sha256(second_base.model_dump(mode="json")),
    }


def test_frozen_policy_creates_neither_artifacts_nor_candidate(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
) -> None:
    baseline = _release("baseline")
    observed = _release("observed")
    _install_active_baseline(storage, baseline)
    with storage.unit_of_work() as unit_of_work:
        EvolutionPolicyRepository().upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=PI_GENERATION_SCOPE,
                kind=CandidateKind.EXECUTOR,
                mode="frozen",
                max_canary_basis_points=0,
                version=1,
                updated_at=_NOW,
            ),
            expected_version=None,
        )
        unit_of_work.commit()
    before = (_durable_counts(storage), _artifact_files(artifact_root))
    scanner = _scanner(storage, candidate_service, _FakeMaterializer(observed))

    assert scanner.scan_once() == 0

    assert scanner.last_error_code == "executor_drift_subject_frozen"
    assert scanner.last_candidate_id is None
    assert (_durable_counts(storage), _artifact_files(artifact_root)) == before
    assert before == ((0, 0), ())


def test_keqing_get_status_never_runs_attached_scanner(
    storage: Storage,
    candidate_service: CandidateService,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _release("baseline")
    observed = _release("observed")
    _install_active_baseline(storage, baseline)
    materializer = _FakeMaterializer(observed)
    scanner = _scanner(storage, candidate_service, materializer)
    app = FastAPI()
    app.include_router(keqing_router)
    app.state.config_manager = SimpleNamespace(
        agent_config=SimpleNamespace(keqing_gateway_enabled=False)
    )
    app.state.storage = storage
    app.state.executor_drift_scanner = scanner
    monkeypatch.setattr("tianshu.gateway.keqing_api._detect_installed_version", lambda _name: None)
    before = (_durable_counts(storage), _artifact_files(artifact_root))

    with TestClient(app) as client:
        response = client.get("/keqing/status")

    assert response.status_code == 200
    assert materializer.verified == []
    assert materializer.create_calls == 0
    assert scanner.last_error_code is None
    assert scanner.last_candidate_id is None
    assert (_durable_counts(storage), _artifact_files(artifact_root)) == before
    assert before == ((0, 0), ())
