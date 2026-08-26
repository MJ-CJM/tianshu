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
from tianshu.evolution.candidate_service import CandidateLiveAuthorities, CandidateService
from tianshu.evolution.runtime_context import current_evolution_runtime
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
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
)
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.skills.loader import SkillsLoader
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryDecodeError,
)
from tianshu.tools.skill_tools import _skill_list, _skill_view
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
    def resolve(connection, selected_ref, overlay):
        del connection, overlay
        return json.loads(_default_artifact_reader(None, selected_ref.artifact_digest))

    kwargs.setdefault("allocation_secret", b"fixed-secret")
    kwargs.setdefault("payload_resolver", resolve)
    return ChallengerRouter(storage, **kwargs)


def _candidate_service(storage, artifacts: ArtifactStore, root: Path) -> CandidateService:
    return CandidateService(
        storage,
        artifacts,
        live_authorities=CandidateLiveAuthorities(
            memory_root=root / "memory",
            skill_target=root / "skills",
            policy_root=root / "policies",
            persona_root=root / "personas",
            code_worktree=root / "code",
            executor_root=root / "runtime-releases",
        ),
    )


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
        candidate_id="candidate-1",
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
    assert allocation_bucket("memorial-1", "seed-v1", b"fixed-secret") == 4941
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


def test_bucket_identity_is_unambiguous_for_colons_and_unicode() -> None:
    secret = b"fixed-secret"

    assert allocation_bucket("b:c", "a", secret) != allocation_bucket("c", "a:b", secret)
    assert allocation_bucket("奏折:一", "种子:甲", secret) == 4685


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


def test_governed_assignment_inheritance_copies_routing_without_rebucketing(storage) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage, "memorial-parent")
    _seed_memorial(storage, "memorial-child")
    router = _router(storage, bucket_calculator=lambda *_: 0)
    parent = router.assign("memorial-parent")

    def reject_rebucket(*_args) -> int:
        raise AssertionError("inherited assignments must not be re-bucketed")

    inheriting = _router(storage, bucket_calculator=reject_rebucket)
    with storage.unit_of_work() as unit_of_work:
        child = inheriting.assign_current(
            unit_of_work,
            memorial_id="memorial-child",
            inherit_from_memorial_id="memorial-parent",
            created_at=NOW + timedelta(seconds=1),
        )
        unit_of_work.commit()

    assert isinstance(parent, RunAssignmentV1)
    assert isinstance(child, RunAssignmentV1)
    assert child.assignment_id != parent.assignment_id
    assert child.memorial_id == "memorial-child"
    assert child.created_at == NOW + timedelta(seconds=1)
    assert child.candidate_id == parent.candidate_id
    assert child.champion_ref == parent.champion_ref
    assert child.selected_ref == parent.selected_ref
    assert child.routing_version == parent.routing_version
    assert child.bucket == parent.bucket
    parent_overlay = router.overlay_for("memorial-parent")
    child_overlay = router.overlay_for("memorial-child")
    assert parent_overlay is not None
    assert child_overlay == parent_overlay.model_copy(update={"assignment_id": child.assignment_id})


def test_legacy_assignment_inheritance_preserves_mode_with_new_identity(storage) -> None:
    _seed_memorial(storage, "legacy-parent")
    _seed_memorial(storage, "legacy-child")
    router = ChallengerRouter(storage)
    parent = router.assign("legacy-parent")

    with storage.unit_of_work() as unit_of_work:
        child = router.assign_current(
            unit_of_work,
            memorial_id="legacy-child",
            inherit_from_memorial_id="legacy-parent",
            created_at=NOW + timedelta(seconds=1),
        )
        unit_of_work.commit()

    assert isinstance(parent, LegacyRunAssignmentV1)
    assert isinstance(child, LegacyRunAssignmentV1)
    assert child.mode == parent.mode
    assert child.assignment_id != parent.assignment_id
    assert child.memorial_id == "legacy-child"
    assert child.created_at == NOW + timedelta(seconds=1)


def test_historical_parent_without_assignment_inherits_as_legacy_without_rebucketing(
    storage,
) -> None:
    _seed_canary(storage, allocation=1_000)
    _seed_memorial(storage, "historical-parent")
    _seed_memorial(storage, "historical-child")

    def reject_rebucket(*_args) -> int:
        raise AssertionError("historical legacy continuity must not be re-bucketed")

    router = _router(storage, bucket_calculator=reject_rebucket)
    with storage.unit_of_work() as unit_of_work:
        child = router.assign_current(
            unit_of_work,
            memorial_id="historical-child",
            inherit_from_memorial_id="historical-parent",
            created_at=NOW + timedelta(seconds=1),
        )
        unit_of_work.commit()

    assert isinstance(child, LegacyRunAssignmentV1)
    assert child.memorial_id == "historical-child"
    assert child.created_at == NOW + timedelta(seconds=1)
    assert router.get("historical-parent") is None
    assert router.get("historical-child") == child


def test_assignment_inheritance_fails_closed_for_missing_or_corrupt_parent(storage) -> None:
    _seed_memorial(storage, "missing-child")
    router = ChallengerRouter(storage)
    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(LookupError, match="parent run assignment"),
    ):
        router.assign_current(
            unit_of_work,
            memorial_id="missing-child",
            inherit_from_memorial_id="missing-parent",
        )

    _seed_memorial(storage, "corrupt-parent")
    _seed_memorial(storage, "corrupt-child")
    router.assign("corrupt-parent")
    storage._conn.execute(  # noqa: SLF001 - durable corruption injection
        "DROP TRIGGER run_evolution_assignments_no_update"
    )
    storage._conn.execute(  # noqa: SLF001 - durable corruption injection
        "UPDATE run_evolution_assignments SET assignment_hash=? WHERE memorial_id=?",
        (_digest("corrupt"), "corrupt-parent"),
    )
    storage._conn.commit()  # noqa: SLF001 - durable corruption injection

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryDecodeError, match="hash"),
    ):
        router.assign_current(
            unit_of_work,
            memorial_id="corrupt-child",
            inherit_from_memorial_id="corrupt-parent",
        )
    assert router.get("corrupt-child") is None


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
    subject_rows = storage._conn.execute(  # noqa: SLF001
        """SELECT assignment_set_size FROM run_subject_assignments
           WHERE memorial_id='memorial-1'"""
    ).fetchall()
    assert [row["assignment_set_size"] for row in subject_rows] == [1]


@pytest.mark.asyncio
async def test_selected_skill_overlay_changes_real_loader_behavior(storage, tmp_path: Path) -> None:
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
                "content": (
                    "---\nname: review-helper\ndescription: challenger review\n"
                    "metadata:\n  openclaw:\n    always: true\n---\n\nCHALLENGER-SENTINEL"
                ),
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
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        payload_resolver=service.resolve_effective_payload_current,
        bucket_calculator=lambda *_: 0,
    )
    loader = SkillsLoader(tmp_path / "builtin")

    assignment = router.assign("memorial-1")
    with router.bind_runtime("memorial-1"):
        resolved = loader.get_skill("review-helper")
        metadata = loader.list_all_metadata()
        index = loader.load_index()
        always = loader.load_always()
        all_skills = loader.load_all()
        listed = await _skill_list(loader)
        viewed = await _skill_view(loader, "review-helper")
        prompt = await PromptBuilder(
            tmp_path / "personas",
            loader,
            memory_dir=tmp_path / "memory",
        ).build(Edict(id="edict-prompt", goal="inspect", submitter="principal-1"))

    assert assignment.selected_ref.artifact_digest != assignment.champion_ref.artifact_digest
    assert resolved is not None
    assert "CHALLENGER-SENTINEL" in resolved["content"]
    assert metadata == [
        {
            "name": "review-helper",
            "description": "challenger review",
            "source": "evolution-overlay",
            "always": True,
            "tool_tier": None,
            "path": "",
            "content_length": len("CHALLENGER-SENTINEL"),
        }
    ]
    assert "review-helper: challenger review" in index
    assert "CHALLENGER-SENTINEL" in always and "CHALLENGER-SENTINEL" in all_skills
    assert json.loads(listed.content)[0]["source"] == "evolution-overlay"
    assert "CHALLENGER-SENTINEL" in viewed.content
    assert "review-helper: challenger review" in prompt
    assert "CHALLENGER-SENTINEL" in prompt
    assert loader.get_skill("review-helper") is None


@pytest.mark.asyncio
async def test_absent_skill_overlay_is_hidden_everywhere_and_restores_live(
    storage,
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "skills"
    live_skill = live_root / "review-helper"
    live_skill.mkdir(parents=True)
    (live_skill / "SKILL.md").write_text(
        "---\nname: review-helper\ndescription: live\n---\n\nLIVE-SKILL",
        encoding="utf-8",
    )
    champion = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: review-helper\ndescription: live\n---\n\nLIVE-SKILL",
            }
        ],
    }
    absent = {
        "name": "review-helper",
        "state": "absent",
        "trust_source": "workspace",
        "members": [],
    }
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    for payload in (champion, absent):
        artifacts.put_bytes(
            canonical_json_bytes(payload),
            media_type="application/vnd.tianshu.evolution.skill+json",
            redaction="governed_candidate",
        )
    _seed_canary(storage, base_payload=champion, candidate_payload=absent, allocation=1_000)
    _seed_memorial(storage)
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        payload_resolver=service.resolve_effective_payload_current,
        bucket_calculator=lambda *_: 0,
    )
    loader = SkillsLoader(tmp_path / "builtin", user_dir=live_root)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1"):
        assert loader.list_all_metadata() == []
        assert loader.load_index() == ""
        assert loader.load_always() == ""
        assert loader.load_all() == ""
        assert loader.get_skill("review-helper") is None
        assert json.loads((await _skill_list(loader)).content) == []
        assert (await _skill_view(loader, "review-helper")).is_error

    assert "LIVE-SKILL" in loader.get_skill("review-helper")["content"]


def test_candidate_champion_replays_frozen_selected_payload_after_live_mutation(
    storage,
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "skills"
    skill_root = live_root / "review-helper"
    skill_root.mkdir(parents=True)
    live_file = skill_root / "SKILL.md"
    live_file.write_text(
        "---\nname: review-helper\ndescription: live\n---\n\nLIVE-BEFORE",
        encoding="utf-8",
    )
    champion_package = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    "---\nname: review-helper\ndescription: frozen champion\n"
                    "metadata:\n  openclaw:\n    always: true\n---\n\nCHAMPION-SNAPSHOT"
                ),
            }
        ],
    }
    challenger_package = {
        **champion_package,
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    "---\nname: review-helper\ndescription: challenger\n---\n\nCHALLENGER-SNAPSHOT"
                ),
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
    for payload in (champion_package, challenger_package):
        artifacts.put_bytes(
            canonical_json_bytes(payload),
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
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 1_000,
        payload_resolver=service.resolve_effective_payload_current,
    )
    loader = SkillsLoader(tmp_path / "builtin", user_dir=live_root)
    assignment = router.assign("memorial-1")
    live_file.write_text(
        "---\nname: review-helper\ndescription: live\n---\n\nLIVE-MUTATED",
        encoding="utf-8",
    )
    restarted = ChallengerRouter(
        storage,
        allocation_secret=b"rotated-secret",
        bucket_calculator=lambda *_: 0,
        payload_resolver=service.resolve_effective_payload_current,
    )

    with restarted.bind_runtime("memorial-1") as runtime:
        frozen = loader.get_skill("review-helper")

    assert assignment.selected_ref == assignment.champion_ref
    assert runtime.overlay.kind is CandidateKind.SKILL
    assert runtime.overlay.subject_key == "skill:review-helper"
    assert runtime.selected_payload is not None
    assert frozen is not None and "CHAMPION-SNAPSHOT" in frozen["content"]
    assert "LIVE-MUTATED" in loader.get_skill("review-helper")["content"]
    with (
        pytest.raises(RuntimeError, match="consumer failed"),
        restarted.bind_runtime("memorial-1") as rebound,
    ):
        assert current_evolution_runtime() is rebound
        raise RuntimeError("consumer failed")
    assert current_evolution_runtime() is None


def test_no_canary_persists_truthful_legacy_marker_without_overlay(storage) -> None:
    from tianshu.models.run_assignment import LegacyRunAssignmentV1

    _seed_memorial(storage)
    router = ChallengerRouter(storage, allocation_secret=b"fixed-secret")

    assignment = router.assign("memorial-1")

    assert isinstance(assignment, LegacyRunAssignmentV1)
    assert assignment.mode == "legacy_unmanaged"
    assert router.overlay_for("memorial-1") is None
    _seed_canary(storage, allocation=1_000)
    assert router.assign("memorial-1") == assignment


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
        assert adapter.resolve_effective_payload(candidate_payload, overlay=overlay) == normalized


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


def test_wrong_candidate_artifact_metadata_rolls_back_submission(storage, tmp_path: Path) -> None:
    champion = {"marker": "champion"}
    challenger = {"marker": "challenger"}
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=10 * 1024 * 1024,
    )
    artifacts.put_bytes(
        canonical_json_bytes(champion),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    artifacts.put_bytes(
        canonical_json_bytes(challenger),
        media_type="application/json",
        redaction="governed_candidate",
    )
    _seed_canary(
        storage,
        base_payload=champion,
        candidate_payload=challenger,
        allocation=1_000,
    )
    service = _candidate_service(storage, artifacts, tmp_path)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_: 0,
        payload_resolver=service.resolve_effective_payload_current,
    )
    edict = Edict(id="edict-wrong-media", goal="route", submitter="principal-1")
    command = SubmitEdictCommand(
        edict=edict,
        idempotency_key="routing-wrong-media",
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
