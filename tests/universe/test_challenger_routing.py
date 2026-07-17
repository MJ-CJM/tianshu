"""Durable challenger routing selects real immutable per-run overlays."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.models import Edict, Memorial
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
    RoutingPolicyV1,
)
from tianshu.models.governance_contract import LegacyEdictGovernanceMapper
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1, RunAssignmentV1
from tianshu.skills.loader import SkillsLoader
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.universe.router import ChallengerRouter, allocation_bucket, selects_challenger

NOW = datetime(2026, 7, 18, 9, tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Owner",
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.WEB,
        correlation_id="routing-test",
    )


def _default_artifact_reader(_connection, digest: str) -> bytes:
    payloads = ({"marker": "champion"}, {"marker": "challenger"})
    encoded = {canonical_sha256(payload): canonical_json_bytes(payload) for payload in payloads}
    try:
        return encoded[digest]
    except KeyError as exc:
        raise LookupError("test artifact not found") from exc


def _router(storage, **kwargs) -> ChallengerRouter:
    return ChallengerRouter(storage, artifact_reader=_default_artifact_reader, **kwargs)


def _contract(kind: CandidateKind, subject_key: str) -> EvolutionContractV1:
    return EvolutionContractV1(
        kind=kind,
        subject_key=subject_key,
        governance_contract_hash=_digest("governance"),
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.EVIDENCE),
        regression_policy_artifact_digest=_digest("regression"),
        sample_policy_artifact_digest=_digest("sample"),
        budget_policy_artifact_digest=_digest("budget"),
        minimum_canary_samples=1,
        max_canary_allocation_basis_points=1_000,
        rollback_slo_seconds=30,
    )


def _candidate(
    *,
    kind: CandidateKind,
    subject_key: str,
    base: CandidateVersionRefV1,
    selected: CandidateVersionRefV1,
    candidate_id: str = "candidate-1",
) -> EvolutionCandidateV1:
    contract = _contract(kind, subject_key)
    return EvolutionCandidateV1(
        candidate_id=candidate_id,
        kind=kind,
        subject_key=subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.REVIEWER,
            source_uri_redacted="review://candidate-1",
            source_digest=selected.canonical_digest,
            actor_principal_id="principal-1",
            actor_display_name="Owner",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="routing-test",
            producer_version="1",
            received_at=NOW,
        ),
        base=base,
        candidate=selected,
        diff_artifact_digest=_digest("diff"),
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=1,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="restore-1",
            adapter_name=kind.value,
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _seed_canary(
    storage,
    *,
    kind: CandidateKind = CandidateKind.SKILL,
    subject_key: str = "skill:review-helper",
    base_payload: dict[str, object] | None = None,
    candidate_payload: dict[str, object] | None = None,
    allocation: int = 1_000,
    seed_id: str = "seed-v1",
) -> EvolutionCandidateV1:
    base_payload = base_payload or {"marker": "champion"}
    candidate_payload = candidate_payload or {"marker": "challenger"}
    base = CandidateVersionRefV1(
        version="champion-v1",
        artifact_digest=canonical_sha256(base_payload),
        canonical_digest=canonical_sha256(base_payload),
    )
    selected = CandidateVersionRefV1(
        version="candidate-v1",
        artifact_digest=canonical_sha256(candidate_payload),
        canonical_digest=canonical_sha256(candidate_payload),
    )
    repository = EvolutionRepository()
    routing = RoutingPolicyV1(
        allocation_basis_points=allocation,
        allocation_seed_id=seed_id,
        routing_version=1,
    )
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        current = repository.insert_candidate(
            connection,
            _candidate(
                kind=kind,
                subject_key=subject_key,
                base=base,
                selected=selected,
            ),
        )
        for lifecycle in (
            CandidateLifecycle.STAGED,
            CandidateLifecycle.EVALUATING,
            CandidateLifecycle.READY,
            CandidateLifecycle.CANARY,
        ):
            current = repository.save_candidate(
                connection,
                current.model_copy(
                    update={
                        "lifecycle": lifecycle,
                        "routing": routing if lifecycle is CandidateLifecycle.CANARY else None,
                        "updated_at": current.updated_at + timedelta(seconds=1),
                    }
                ),
                expected_version=current.version,
            )
        payload = routing.model_dump(mode="json")
        connection.execute(
            """INSERT INTO evolution_routing_allocations (
                   candidate_id, routing_version, allocation_basis_points,
                   allocation_seed_id, routing_json, routing_hash, version,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                current.candidate_id,
                routing.routing_version,
                routing.allocation_basis_points,
                routing.allocation_seed_id,
                canonical_json_bytes(payload).decode(),
                canonical_sha256(payload),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        unit_of_work.commit()
    return current


def _seed_memorial(storage, memorial_id: str = "memorial-1") -> None:
    storage.save_edict(Edict(id=f"edict-{memorial_id}", goal="route", submitter="principal-1"))
    storage.save_memorial(Memorial(id=memorial_id, edict_id=f"edict-{memorial_id}"))


def test_assignment_contracts_are_strict_frozen_and_timezone_aware() -> None:
    ref = CandidateVersionRefV1(
        version="v1",
        artifact_digest=_digest("artifact"),
        canonical_digest=_digest("canonical"),
    )
    assignment = RunAssignmentV1(
        assignment_id="assignment-1",
        memorial_id="memorial-1",
        candidate_id=None,
        champion_ref=ref,
        selected_ref=ref,
        routing_version=1,
        bucket=0,
        created_at=NOW,
    )
    overlay = EffectiveEvolutionOverlayV1(
        assignment_id=assignment.assignment_id,
        kind=None,
        subject_key=None,
        artifact_digest=ref.artifact_digest,
        canonical_digest=ref.canonical_digest,
    )

    with pytest.raises(ValidationError, match="Extra inputs"):
        RunAssignmentV1.model_validate({**assignment.model_dump(), "arm": "champion"})
    with pytest.raises(ValidationError, match="frozen"):
        assignment.bucket = 1  # type: ignore[misc]
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunAssignmentV1.model_validate(
            {**assignment.model_dump(), "created_at": NOW.replace(tzinfo=None)}
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        EffectiveEvolutionOverlayV1.model_validate(
            {**overlay.model_dump(), "host_path": "/tmp/leak"}
        )


def test_bucket_uses_exact_hmac_algorithm_and_allocation_boundaries(storage) -> None:
    assert allocation_bucket("memorial-1", "seed-v1", b"fixed-secret") == 2944
    _seed_canary(storage, allocation=1_000)

    challenger_router = _router(storage, bucket_calculator=lambda *_: 999)
    champion_router = _router(storage, bucket_calculator=lambda *_: 1_000)
    _seed_memorial(storage, "memorial-challenger")
    _seed_memorial(storage, "memorial-champion")

    challenger = challenger_router.assign("memorial-challenger")
    champion = champion_router.assign("memorial-champion")

    assert challenger.candidate_id == "candidate-1"
    assert challenger.selected_ref != challenger.champion_ref
    assert champion.candidate_id == "candidate-1"
    assert champion.selected_ref == champion.champion_ref


@pytest.mark.parametrize("allocation", [0, 1_000])
def test_zero_is_always_champion_and_positive_boundary_selects_challenger(
    storage,
    allocation: int,
) -> None:
    _seed_canary(storage, allocation=allocation)
    _seed_memorial(storage)
    assignment = _router(storage, bucket_calculator=lambda *_: 0).assign("memorial-1")
    assert (assignment.selected_ref != assignment.champion_ref) is (allocation == 1_000)
    assert selects_challenger(bucket=9_999, allocation_basis_points=10_000)
    assert not selects_challenger(bucket=0, allocation_basis_points=0)


def test_restart_and_routing_rotation_never_reroute_existing_assignment(storage) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    first = _router(
        storage,
        allocation_secret=b"old",
        bucket_calculator=lambda *_: 0,
    ).assign("memorial-1")

    restarted = _router(
        storage,
        allocation_secret=b"rotated",
        bucket_calculator=lambda *_: 9_999,
    )
    replay = restarted.assign("memorial-1")

    assert replay == first
    assert canonical_json_bytes(replay) == canonical_json_bytes(first)


def test_concurrent_assignment_produces_one_immutable_row(storage) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    router = _router(storage, bucket_calculator=lambda *_: 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assignments = tuple(pool.map(lambda _: router.assign("memorial-1"), range(20)))

    assert len(set(assignments)) == 1
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        == 1
    )


def test_selected_skill_overlay_changes_real_loader_behavior(storage, tmp_path: Path) -> None:
    champion_package = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "community",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: review-helper\ndescription: review\n---\n\nCHAMPION",
            }
        ],
    }
    challenger_package = {
        **champion_package,
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: review-helper\ndescription: review\n---\n\nCHALLENGER-SENTINEL",
            }
        ],
    }
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    artifacts.put_bytes(
        canonical_json_bytes(champion_package),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    artifacts.put_bytes(
        canonical_json_bytes(challenger_package),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    _seed_canary(
        storage,
        base_payload=champion_package,
        candidate_payload=challenger_package,
        allocation=1_000,
    )
    _seed_memorial(storage)
    router = ChallengerRouter(
        storage,
        artifact_reader=artifacts.get_bytes_current,
        bucket_calculator=lambda *_: 0,
    )
    loader = SkillsLoader(tmp_path / "builtin")

    assignment = router.assign("memorial-1")
    with router.bind_runtime("memorial-1"):
        resolved = loader.get_skill("review-helper")

    assert assignment.selected_ref.artifact_digest != assignment.champion_ref.artifact_digest
    assert resolved is not None
    assert "CHALLENGER-SENTINEL" in resolved["content"]


def test_all_adapters_share_the_authoritative_overlay_resolution_boundary(
    storage,
    tmp_path: Path,
) -> None:
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    adapters_and_payloads = (
        (
            MemoryCandidateAdapter(artifacts),
            CandidateKind.MEMORY,
            "memory:entry-1",
            {
                "id": "entry-1",
                "persona_id": "persona-1",
                "content": "candidate memory",
                "created_at": NOW.isoformat(),
            },
        ),
        (
            SkillCandidateAdapter(artifacts, live_root=tmp_path / "live-skills"),
            CandidateKind.SKILL,
            "skill:review-helper",
            {
                "name": "review-helper",
                "trust_source": "workspace",
                "members": [
                    {
                        "path": "SKILL.md",
                        "kind": "file",
                        "content": (
                            "---\nname: review-helper\ndescription: Review safely\n---"
                            "\n\nUse evidence."
                        ),
                    }
                ],
            },
        ),
        (
            PolicyCandidateAdapter(artifacts),
            CandidateKind.POLICY,
            "policy:workspace",
            {
                "workspace": {
                    "source_id": "workspace-main",
                    "base_revision": "1" * 40,
                    "staging_mode": "isolated",
                    "apply_mode": "governed",
                    "require_clean_source": True,
                },
                "recovery": {
                    "require_restore_point": True,
                    "failure_cleanup": "required",
                    "rollback_on_apply_failure": True,
                },
            },
        ),
        (
            PersonaCandidateAdapter(artifacts),
            CandidateKind.PERSONA,
            "persona:reviewer",
            {
                "id": "reviewer",
                "name": "Reviewer",
                "department": "court",
                "soul_path": "SOUL.md",
                "role_path": "ROLE.md",
                "memory_path": "MEMORY.md",
            },
        ),
        (
            CodeCandidateAdapter(artifacts),
            CandidateKind.CODE,
            "code:workspace",
            {
                "id": "change-set-1",
                "lease_id": "lease-1",
                "restore_point_id": "restore-1",
                "source_repository_id": "repo-1",
                "base_revision": "1" * 40,
                "sequence": 1,
                "changes": [
                    {
                        "kind": "modify",
                        "old_path": "src/tianshu/example.py",
                        "new_path": "src/tianshu/example.py",
                        "old_oid": "2" * 40,
                        "new_oid": "3" * 40,
                        "old_mode": "100644",
                        "new_mode": "100644",
                        "old_size": 10,
                        "new_size": 12,
                        "binary": False,
                    }
                ],
                "created_at": NOW.isoformat(),
            },
        ),
    )
    for adapter, kind, subject_key, candidate_payload in adapters_and_payloads:
        normalized = adapter._normalize_domain(candidate_payload)  # noqa: SLF001
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id="assignment-1",
            kind=kind,
            subject_key=subject_key,
            artifact_digest=canonical_sha256(normalized),
            canonical_digest=canonical_sha256(normalized),
        )
        assert (
            adapter.resolve_effective_payload(
                {"marker": "champion"},
                overlay=overlay,
                candidate_payload=candidate_payload,
            )
            == normalized
        )


def test_assignment_failure_rolls_back_memorial_edict_and_outbox(storage) -> None:
    def fail_before_insert(_assignment: RunAssignmentV1) -> None:
        raise RuntimeError("injected assignment failure")

    router = ChallengerRouter(storage, before_insert=fail_before_insert)
    edict = Edict(id="edict-1", goal="route", submitter="principal-1")
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key="routing-atomicity",
        requested_contract=LegacyEdictGovernanceMapper.from_edict(edict),
        extra_payload={},
    )

    with pytest.raises(RuntimeError, match="injected assignment failure"):
        EdictApplicationService(storage, challenger_router=router).submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="routing-test",
        )

    for table in (
        "edicts",
        "memorials",
        "run_evolution_assignments",
        "outbox_events",
    ):
        assert storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608, SLF001


def test_unavailable_selected_overlay_fails_before_submission_side_effects(storage) -> None:
    _seed_canary(storage, allocation=1_000)
    router = ChallengerRouter(storage, bucket_calculator=lambda *_: 0)
    edict = Edict(id="edict-unavailable", goal="route", submitter="principal-1")
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key="routing-overlay-unavailable",
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


def test_assignment_json_is_evidence_ready_and_contains_routing_attribution(storage) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage)
    router = _router(storage, bucket_calculator=lambda *_: 0)
    assignment = router.assign("memorial-1")
    evidence = router.evidence_for("memorial-1")

    assert evidence.assignment == assignment
    assert evidence.overlay.assignment_id == assignment.assignment_id
    assert evidence.overlay.artifact_digest == assignment.selected_ref.artifact_digest
    assert evidence.routing_version == assignment.routing_version
    assert evidence.candidate_id == assignment.candidate_id
    assert json.loads(canonical_json_bytes(evidence))["assignment"]["bucket"] == assignment.bucket
