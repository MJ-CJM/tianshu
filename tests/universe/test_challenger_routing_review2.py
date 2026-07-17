"""Focused Task 5 review-two regressions for attribution and corrupt assignments."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.universe.test_challenger_routing import (
    _auth,
    _candidate,
    _candidate_service,
    _digest,
    _seed_canary,
    _seed_memorial,
)
from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import AdapterError
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.evolution.runtime_context import current_evolution_runtime
from tianshu.models import Edict
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateVersionRefV1
from tianshu.models.governance_contract import LegacyEdictGovernanceMapper
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1, RunAssignmentV1
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.universe.router import ChallengerRouter, EvolutionRuntimeUnavailable


def _skill_package(name: str, *, state: str = "present") -> dict[str, object]:
    return {
        "name": name,
        "state": state,
        "trust_source": "community",
        "members": (
            []
            if state == "absent"
            else [
                {
                    "path": "SKILL.md",
                    "kind": "file",
                    "content": f"---\nname: {name}\ndescription: governed\n---\n\n{name}",
                }
            ]
        ),
    }


def _drop_assignment_update_guard(storage) -> None:
    storage._conn.execute("DROP TRIGGER run_evolution_assignments_no_update")  # noqa: SLF001
    storage._conn.commit()  # noqa: SLF001


@pytest.mark.parametrize("state", ["present", "absent"])
@pytest.mark.parametrize("arm", ["champion", "challenger"])
def test_skill_subject_package_mismatch_rolls_back_assignment_uow(
    storage,
    tmp_path: Path,
    state: str,
    arm: str,
) -> None:
    claimed = _skill_package("claimed-name")
    mismatched = _skill_package("real-name", state=state)
    champion = mismatched if arm == "champion" else claimed
    challenger = mismatched if arm == "challenger" else claimed
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    for payload in (champion, challenger):
        artifacts.put_bytes(
            canonical_json_bytes(payload),
            media_type="application/vnd.tianshu.evolution.skill+json",
            redaction="governed_candidate",
        )
    _seed_canary(
        storage,
        subject_key="skill:claimed-name",
        base_payload=champion,
        candidate_payload=challenger,
        allocation=1_000,
    )
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 1_000 if arm == "champion" else 0,
        payload_resolver=service.resolve_effective_payload_current,
    )
    edict = Edict(id=f"edict-{arm}-{state}", goal="route", submitter="principal-1")
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key=f"routing-{arm}-{state}",
        requested_contract=LegacyEdictGovernanceMapper.from_edict(edict),
        extra_payload={},
    )

    with pytest.raises(ValueError, match="candidate_overlay_unavailable"):
        EdictApplicationService(storage, challenger_router=router).submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="routing-test",
        )

    for table in ("edicts", "memorials", "run_evolution_assignments", "outbox_events"):
        assert storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608, SLF001


def test_skill_subject_binding_requires_canonical_prefix(storage, tmp_path: Path) -> None:
    package = _skill_package("review-helper")
    digest = canonical_sha256(package)
    adapter = SkillCandidateAdapter(
        ArtifactStore(
            tmp_path / "artifacts",
            storage.artifact_repo,
            storage.unit_of_work,
            max_object_bytes=1024 * 1024,
            max_total_bytes=10 * 1024 * 1024,
        ),
        live_root=tmp_path / "live-skills",
    )
    overlay = EffectiveEvolutionOverlayV1(
        assignment_id="assignment-1",
        kind=CandidateKind.SKILL,
        subject_key="review-helper",
        artifact_digest=digest,
        canonical_digest=digest,
    )

    with pytest.raises(AdapterError, match="subject"):
        adapter.resolve_effective_payload(package, overlay=overlay)


def test_absent_skill_subject_binding_rejects_noncanonical_package_name(
    storage,
    tmp_path: Path,
) -> None:
    package = _skill_package("Review Helper", state="absent")
    digest = canonical_sha256(package)
    adapter = SkillCandidateAdapter(
        ArtifactStore(
            tmp_path / "artifacts",
            storage.artifact_repo,
            storage.unit_of_work,
            max_object_bytes=1024 * 1024,
            max_total_bytes=10 * 1024 * 1024,
        ),
        live_root=tmp_path / "live-skills",
    )
    overlay = EffectiveEvolutionOverlayV1(
        assignment_id="assignment-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:Review Helper",
        artifact_digest=digest,
        canonical_digest=digest,
    )

    with pytest.raises(AdapterError, match="validation"):
        adapter.resolve_effective_payload(package, overlay=overlay)


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "UPDATE run_evolution_assignments SET assignment_hash='" + "0" * 64 + "'",
        "UPDATE run_evolution_assignments SET bucket=bucket + 1",
    ],
    ids=["bad-hash", "conflicting-column"],
)
def test_bind_runtime_normalizes_deterministic_assignment_decode_corruption(
    storage,
    tamper_sql: str,
) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 0,
        payload_resolver=lambda _connection, _selected_ref, _overlay: {"marker": "challenger"},
    )
    router.assign("memorial-1")
    _drop_assignment_update_guard(storage)
    storage._conn.execute(tamper_sql)  # noqa: SLF001
    storage._conn.commit()  # noqa: SLF001

    with (
        pytest.raises(EvolutionRuntimeUnavailable, match="run_assignment_unavailable"),
        router.bind_runtime("memorial-1"),
    ):
        pytest.fail("corrupt assignment must not bind")


def test_bind_runtime_normalizes_candidate_attribution_corruption(storage) -> None:
    first = _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 0,
        payload_resolver=lambda _connection, _selected_ref, _overlay: {"marker": "challenger"},
    )
    assignment = router.assign("memorial-1")
    assert isinstance(assignment, RunAssignmentV1)
    other_base = CandidateVersionRefV1(
        version="other-base",
        artifact_digest=_digest("other-base"),
        canonical_digest=_digest("other-base"),
    )
    other_selected = CandidateVersionRefV1(
        version="other-candidate",
        artifact_digest=_digest("other-candidate"),
        canonical_digest=_digest("other-candidate"),
    )
    repository = EvolutionRepository()
    _drop_assignment_update_guard(storage)
    with storage.unit_of_work() as unit_of_work:
        repository.insert_candidate(
            unit_of_work.connection,
            _candidate(
                kind=first.kind,
                subject_key=first.subject_key,
                base=other_base,
                selected=other_selected,
                candidate_id="candidate-2",
            ),
        )
        corrupted = assignment.model_copy(update={"candidate_id": "candidate-2"})
        unit_of_work.connection.execute(
            """UPDATE run_evolution_assignments
               SET candidate_id=?, assignment_json=?, assignment_hash=?
               WHERE memorial_id=?""",
            (
                corrupted.candidate_id,
                canonical_json_bytes(corrupted).decode(),
                canonical_sha256(corrupted),
                corrupted.memorial_id,
            ),
        )
        unit_of_work.commit()

    with (
        pytest.raises(EvolutionRuntimeUnavailable, match="run_assignment_unavailable"),
        router.bind_runtime("memorial-1"),
    ):
        pytest.fail("misattributed assignment must not bind")


def test_bind_runtime_rejects_persisted_skill_subject_attribution_drift(
    storage,
    tmp_path: Path,
) -> None:
    package = _skill_package("review-helper")
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    artifacts.put_bytes(
        canonical_json_bytes(package),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    candidate = _seed_canary(
        storage,
        subject_key="skill:review-helper",
        base_payload=package,
        candidate_payload=package,
        allocation=1_000,
    )
    _seed_memorial(storage)
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 0,
        payload_resolver=service.resolve_effective_payload_current,
    )
    router.assign("memorial-1")
    drifted_contract = candidate.evolution_contract.model_copy(
        update={"subject_key": "skill:drifted-name"}
    )
    storage._conn.execute(  # noqa: SLF001
        """UPDATE evolution_candidates
           SET subject_key=?, evolution_contract_json=?, evolution_contract_hash=?
           WHERE candidate_id=?""",
        (
            "skill:drifted-name",
            canonical_json_bytes(drifted_contract).decode(),
            canonical_sha256(drifted_contract),
            candidate.candidate_id,
        ),
    )
    storage._conn.commit()  # noqa: SLF001

    with (
        pytest.raises(EvolutionRuntimeUnavailable, match="run_assignment_unavailable"),
        router.bind_runtime("memorial-1"),
    ):
        pytest.fail("drifted subject attribution must not bind")
    assert current_evolution_runtime() is None


def test_bind_runtime_does_not_normalize_transient_sqlite_errors(
    storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = ChallengerRouter(storage)

    def fail_transiently(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(router._repository, "get_assignment", fail_transiently)  # noqa: SLF001

    with (
        pytest.raises(sqlite3.OperationalError, match="database is busy"),
        router.bind_runtime("memorial-1"),
    ):
        pytest.fail("transient sqlite failure must propagate")
