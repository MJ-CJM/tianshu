"""Exact executor-generation routing for governed canaries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from tests.universe.test_challenger_routing import _router, _seed_canary, _seed_memorial
from tests.universe.test_snapshot_binding import _snapshot_resolver
from tianshu.evolution.runtime_context import current_run_binding
from tianshu.models import Edict, Memorial
from tianshu.models.evolution_candidate import CandidateKind, EvolutionCandidateV1
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityStatus,
    ExecutorGenerationAuthorityV1,
    new_pending_executor_generation_authority,
    transition_executor_generation_authority,
)
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionAssignmentConflict
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityDecodeError,
)
from tianshu.universe.router import (
    ChallengerRouter,
    GenerationBindingUnavailable,
    GenerationRetired,
)

_SCOPE = "executor:keqing:pi"
_BASE_GENERATION_ID = "rg-" + "1" * 32
_CANDIDATE_GENERATION_ID = "rg-" + "2" * 32
_NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


_BASE_RELEASE_DIGEST = _digest("base-release")
_CANDIDATE_RELEASE_DIGEST = _digest("candidate-release")


@dataclass(frozen=True, slots=True)
class _Bundle:
    release_digest: str


@dataclass(frozen=True, slots=True)
class _Selection:
    generation_ids: tuple[str, ...]
    by_scope: dict[str, str]
    executor_manifest_digests: dict[str, str]
    bundles: dict[str, _Bundle]


@dataclass
class _Controller:
    release_by_generation: dict[str, str] = field(
        default_factory=lambda: {
            _BASE_GENERATION_ID: _BASE_RELEASE_DIGEST,
            _CANDIDATE_GENERATION_ID: _CANDIDATE_RELEASE_DIGEST,
        }
    )
    returned_generation_id: str | None = None
    scope_generation_id: str | None = None
    calls: list[tuple[str, str, tuple[str, ...], bool, bool]] = field(default_factory=list)

    def resolve_for_binding_current(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...] = (),
        inherit_pinned: bool = False,
        allow_ready: bool = False,
    ) -> _Selection:
        del connection
        self.calls.append((memorial_id, attempt_id, pinned_ids, inherit_pinned, allow_ready))
        generation_ids = (
            (self.returned_generation_id,)
            if self.returned_generation_id is not None
            else pinned_ids or (_BASE_GENERATION_ID,)
        )
        generation_id = generation_ids[0]
        if generation_id == _CANDIDATE_GENERATION_ID and not allow_ready:
            raise RuntimeError("READY generation requires explicit authority")
        release_digest = self.release_by_generation[generation_id]
        return _Selection(
            generation_ids=generation_ids,
            by_scope={_SCOPE: self.scope_generation_id or generation_id},
            executor_manifest_digests={"keqing:pi": _digest(f"manifest:{generation_id}")},
            bundles={_SCOPE: _Bundle(release_digest=release_digest)},
        )

    def release_binding(self, attempt_id: str) -> bool:
        del attempt_id
        return True


@dataclass
class _AuthorityResolver:
    current: ExecutorGenerationAuthorityV1 | None
    generation: ExecutorGenerationAuthorityV1 | None

    def get_current(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None:
        del connection, candidate_id
        return self.current

    def get_by_generation(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None:
        del connection, generation_id
        return self.generation


class _UnexpectedAuthorityResolver:
    def get_current(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("champion routing must not read challenger authority")

    def get_by_generation(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("champion routing must not read challenger authority")


class _CorruptAuthorityResolver:
    def get_current(self, *_args: object, **_kwargs: object) -> None:
        raise ExecutorGenerationAuthorityDecodeError(
            "start-canary intent entry_hash does not match entry_json"
        )

    def get_by_generation(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("corrupt current authority must stop routing")


def _authority(
    candidate: EvolutionCandidateV1,
    *,
    candidate_id: str | None = None,
    candidate_artifact_digest: str | None = None,
    release_digest: str = _CANDIDATE_RELEASE_DIGEST,
) -> ExecutorGenerationAuthorityV1:
    pending = new_pending_executor_generation_authority(
        candidate_id=candidate_id or candidate.candidate_id,
        candidate_version=candidate.version - 1,
        candidate_artifact_digest=(
            candidate_artifact_digest or candidate.candidate.artifact_digest
        ),
        candidate_canonical_digest=candidate.candidate.canonical_digest,
        release_digest=release_digest,
        scope=_SCOPE,
        generation_id=_CANDIDATE_GENERATION_ID,
        base_generation_id=_BASE_GENERATION_ID,
        base_release_digest=_BASE_RELEASE_DIGEST,
        promotion_journal_id=_digest("promotion-journal"),
        start_command_key="executor-start-canary",
        now=_NOW,
    )
    return transition_executor_generation_authority(
        pending,
        ExecutorGenerationAuthorityStatus.AUTHORIZED,
        now=_NOW + timedelta(seconds=1),
    )


def _revoked(authority: ExecutorGenerationAuthorityV1) -> ExecutorGenerationAuthorityV1:
    return transition_executor_generation_authority(
        authority,
        ExecutorGenerationAuthorityStatus.REVOKED,
        now=authority.updated_at + timedelta(seconds=1),
        revocation_reason="test_revoked",
    )


def _governed_router(
    storage,
    *,
    controller: _Controller,
    resolver: object,
    bucket: int = 0,
) -> ChallengerRouter:
    return _router(
        storage,
        bucket_calculator=lambda *_args: bucket,
        snapshot_resolver=lambda: _snapshot_resolver(),
        generation_controller=lambda: controller,
        executor_generation_authority_resolver=lambda: resolver,
    )


def _seed_child(storage, *, parent_memorial_id: str) -> None:
    storage.save_edict(Edict(id="edict-child", goal="child", submitter="principal-1"))
    storage.save_memorial(
        Memorial(
            id="child",
            edict_id="edict-child",
            parent_memorial_id=parent_memorial_id,
        )
    )


def _seed_executor_canary(storage, *, allocation: int) -> EvolutionCandidateV1:
    with storage.unit_of_work() as unit_of_work:
        EvolutionPolicyRepository().upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=_SCOPE,
                kind=CandidateKind.EXECUTOR,
                mode="canary",
                max_canary_basis_points=1_000,
                version=1,
                updated_at=_NOW,
            ),
            expected_version=None,
        )
        unit_of_work.commit()
    return _seed_canary(
        storage,
        kind=CandidateKind.EXECUTOR,
        subject_key=_SCOPE,
        allocation=allocation,
    )


def test_executor_challenger_binds_only_authorized_ready_generation(storage) -> None:
    candidate = _seed_executor_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    authority = _authority(candidate)
    controller = _Controller()
    router = _governed_router(
        storage,
        controller=controller,
        resolver=_AuthorityResolver(authority, authority),
    )
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-challenger"):
        binding = current_run_binding()
        assert binding is not None
        assert binding.generation_ids == (_CANDIDATE_GENERATION_ID,)

    assert controller.calls == [
        (
            "memorial-1",
            "attempt-challenger",
            (_CANDIDATE_GENERATION_ID,),
            False,
            True,
        )
    ]


def test_executor_champion_uses_active_without_reading_candidate_authority(storage) -> None:
    _seed_executor_canary(storage, allocation=0)
    _seed_memorial(storage)
    controller = _Controller()
    router = _governed_router(
        storage,
        controller=controller,
        resolver=_UnexpectedAuthorityResolver(),
    )
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-champion"):
        binding = current_run_binding()
        assert binding is not None
        assert binding.generation_ids == (_BASE_GENERATION_ID,)

    assert controller.calls == [("memorial-1", "attempt-champion", (), False, False)]


@pytest.mark.parametrize(
    "failure_mode",
    [
        "missing",
        "candidate_digest",
        "ambiguous",
        "revoked",
        "generation_id",
        "scope_binding",
        "release_digest",
        "corrupt_journal",
    ],
)
def test_executor_challenger_authority_faults_fail_closed(
    storage,
    failure_mode: str,
) -> None:
    candidate = _seed_executor_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    authority = _authority(candidate)
    current: ExecutorGenerationAuthorityV1 | None = authority
    generation: ExecutorGenerationAuthorityV1 | None = authority
    controller = _Controller()
    if failure_mode == "missing":
        current = None
        generation = None
    elif failure_mode == "candidate_digest":
        current = _authority(candidate, candidate_artifact_digest=_digest("wrong-candidate"))
        generation = current
    elif failure_mode == "ambiguous":
        generation = _authority(candidate, candidate_id="candidate-other")
    elif failure_mode == "revoked":
        current = _revoked(authority)
        generation = current
    elif failure_mode == "generation_id":
        controller.returned_generation_id = _BASE_GENERATION_ID
    elif failure_mode == "scope_binding":
        controller.scope_generation_id = _BASE_GENERATION_ID
    elif failure_mode == "release_digest":
        controller.release_by_generation[_CANDIDATE_GENERATION_ID] = _digest("wrong-release")
    resolver: object = (
        _CorruptAuthorityResolver()
        if failure_mode == "corrupt_journal"
        else _AuthorityResolver(current, generation)
    )
    router = _governed_router(
        storage,
        controller=controller,
        resolver=resolver,
    )
    router.assign("memorial-1")

    with (
        pytest.raises(GenerationBindingUnavailable, match="generation_binding_unavailable"),
        router.bind_runtime("memorial-1", attempt_id=f"attempt-{failure_mode}"),
    ):
        pytest.fail("invalid executor authority must not enter runtime")

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_system_bindings WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        == 0
    )


def test_executor_ready_generation_continuity_requires_exact_authority(storage) -> None:
    candidate = _seed_executor_canary(storage, allocation=1_000)
    _seed_memorial(storage, "parent")
    _seed_child(storage, parent_memorial_id="parent")
    authority = _authority(candidate)
    resolver = _AuthorityResolver(authority, authority)
    controller = _Controller()
    router = _governed_router(storage, controller=controller, resolver=resolver)
    router.assign("parent")
    with router.bind_runtime("parent", attempt_id="attempt-parent"):
        pass
    with storage.unit_of_work() as unit_of_work:
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )
        unit_of_work.commit()

    with router.bind_runtime("child", attempt_id="attempt-child"):
        binding = current_run_binding()
        assert binding is not None
        assert binding.generation_ids == (_CANDIDATE_GENERATION_ID,)

    assert controller.calls[-1] == (
        "child",
        "attempt-child",
        (_CANDIDATE_GENERATION_ID,),
        False,
        True,
    )


@pytest.mark.parametrize("failure_mode", ["missing", "candidate_digest", "ambiguous", "revoked"])
def test_executor_followup_rejects_invalid_challenger_authority(
    storage,
    failure_mode: str,
) -> None:
    candidate = _seed_executor_canary(storage, allocation=1_000)
    _seed_memorial(storage, "parent")
    _seed_child(storage, parent_memorial_id="parent")
    authority = _authority(candidate)
    resolver = _AuthorityResolver(authority, authority)
    controller = _Controller()
    router = _governed_router(storage, controller=controller, resolver=resolver)
    router.assign("parent")
    if failure_mode == "missing":
        resolver.current = None
        resolver.generation = None
    elif failure_mode == "candidate_digest":
        resolver.current = _authority(
            candidate,
            candidate_artifact_digest=_digest("wrong-candidate"),
        )
        resolver.generation = resolver.current
    elif failure_mode == "ambiguous":
        resolver.generation = _authority(candidate, candidate_id="candidate-other")
    elif failure_mode == "revoked":
        resolver.current = _revoked(authority)
        resolver.generation = resolver.current

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(
            EvolutionAssignmentConflict,
            match="executor challenger continuity authority is unavailable",
        ),
    ):
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )


@pytest.mark.parametrize("failure_mode", ["revoked", "release_digest"])
def test_executor_exact_ready_replay_revalidates_authority_and_release(
    storage,
    failure_mode: str,
) -> None:
    candidate = _seed_executor_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    authority = _authority(candidate)
    resolver = _AuthorityResolver(authority, authority)
    controller = _Controller()
    router = _governed_router(storage, controller=controller, resolver=resolver)
    router.assign("memorial-1")
    with router.bind_runtime("memorial-1", attempt_id="attempt-exact"):
        pass
    if failure_mode == "revoked":
        resolver.current = _revoked(authority)
        resolver.generation = resolver.current
    else:
        controller.release_by_generation[_CANDIDATE_GENERATION_ID] = _digest("wrong-release")

    with (
        pytest.raises(GenerationRetired, match="generation_retired"),
        router.bind_runtime("memorial-1", attempt_id="attempt-exact"),
    ):
        pytest.fail("invalid exact READY continuity must not enter runtime")
