"""Five-domain staging matrix for governed evolution candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution import (
    CandidateProposalV1,
    CandidateService,
    CandidateSourceV1,
    ProvenanceInputV1,
)
from tianshu.evolution.adapters.base import (
    AdapterKindMismatch,
    AdapterOperationUnavailable,
)
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    EvolutionContractV1,
    GateName,
)
from tianshu.storage.facade import Storage

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _sources() -> dict[CandidateKind, tuple[dict[str, object], dict[str, object]]]:
    memory_base = {
        "id": "memory-1",
        "persona_id": "ducha",
        "category": "insight",
        "content": "Prefer exact evidence bindings.",
        "source": "reflection",
        "confidence": 0.8,
        "entity_refs": [],
        "created_at": NOW.isoformat(),
        "access_level": "private",
    }
    skill_base = {
        "name": "review-helper",
        "content": "---\nname: review-helper\ndescription: Review safely\n---\n\nUse evidence.",
        "trust_source": "workspace",
    }
    policy_base = {
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
    }
    persona_base = {
        "id": "ducha-reviewer",
        "name": "Reviewer",
        "department": "ducha",
        "soul_path": "personas/ducha/SOUL.md",
        "role_path": "personas/ducha/ROLE.md",
        "memory_path": "personas/ducha/MEMORY.md",
        "tools_allowed": ["grep"],
        "tools_denied": [],
        "skills_allowed": ["review-helper"],
        "tool_tier_max": 1,
        "can_delegate": False,
        "memory_global_read": False,
        "delegates_to": [],
    }
    code_base = {
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
    }
    return {
        CandidateKind.MEMORY: (memory_base, {**memory_base, "confidence": 0.9}),
        CandidateKind.SKILL: (
            skill_base,
            {**skill_base, "content": skill_base["content"] + "\nReport blockers."},
        ),
        CandidateKind.POLICY: (
            policy_base,
            {
                **policy_base,
                "recovery": {**policy_base["recovery"], "failure_cleanup": "best_effort"},
            },
        ),
        CandidateKind.PERSONA: (
            persona_base,
            {**persona_base, "tools_allowed": ["grep", "lsp"]},
        ),
        CandidateKind.CODE: (
            code_base,
            {**code_base, "id": "change-set-2", "sequence": 2},
        ),
    }


ADAPTERS = {
    CandidateKind.MEMORY: MemoryCandidateAdapter,
    CandidateKind.SKILL: SkillCandidateAdapter,
    CandidateKind.POLICY: PolicyCandidateAdapter,
    CandidateKind.PERSONA: PersonaCandidateAdapter,
    CandidateKind.CODE: CodeCandidateAdapter,
}


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    active = Storage(str(tmp_path / "tianshu.db"))
    active.init_db()
    yield active
    active.close()


@pytest.fixture
def artifacts(tmp_path: Path, storage: Storage) -> ArtifactStore:
    return ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: NOW,
    )


def _proposal(kind: CandidateKind) -> CandidateProposalV1:
    base, candidate = _sources()[kind]
    subject_key = f"{kind.value}:review-target"
    contract = EvolutionContractV1(
        kind=kind,
        subject_key=subject_key,
        governance_contract_hash=DIGEST_A,
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.EVIDENCE),
        regression_policy_artifact_digest=DIGEST_B,
        sample_policy_artifact_digest=DIGEST_C,
        budget_policy_artifact_digest=DIGEST_D,
        minimum_canary_samples=10,
        max_canary_allocation_basis_points=500,
        rollback_slo_seconds=30,
    )
    return CandidateProposalV1(
        command_id=f"command-{kind.value}-1",
        kind=kind,
        subject_key=subject_key,
        base=CandidateSourceV1(version="champion-v1", payload=base),
        candidate=CandidateSourceV1(version="candidate-v1", payload=candidate),
        evolution_contract=contract,
        provenance=ProvenanceInputV1(
            source_channel=CandidateSourceChannel.REVIEWER,
            source_uri_redacted=f"review://{kind.value}-bundle",
            actor_principal_id="principal-reviewer",
            actor_display_name="Reviewer",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="candidate-test",
            producer_version="1.0",
        ),
        evidence_bundle_ids=(f"evidence-{kind.value}",),
        restore_point_ref=f"restore-{kind.value}-1",
    )


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_propose_binds_all_canonical_inputs_and_builds_diff(
    kind: CandidateKind, storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(kind)
    service = CandidateService(storage, artifacts, clock=lambda: NOW)

    candidate = service.propose(proposal)

    assert candidate.kind is kind
    assert candidate.lifecycle is CandidateLifecycle.PROPOSED
    assert candidate.routing is None
    assert candidate.provenance.source_digest == canonical_sha256(proposal.candidate.payload)
    assert candidate.base.canonical_digest == canonical_sha256(proposal.base.payload)
    assert candidate.candidate.canonical_digest == canonical_sha256(proposal.candidate.payload)
    assert candidate.evolution_contract_hash == canonical_sha256(proposal.evolution_contract)
    assert candidate.evidence_bundle_ids == proposal.evidence_bundle_ids
    assert candidate.rollback.adapter_name == kind.value
    diff = json.loads(artifacts.get_bytes(candidate.diff_artifact_digest))
    assert diff == {
        "base_canonical_digest": candidate.base.canonical_digest,
        "candidate_canonical_digest": candidate.candidate.canonical_digest,
        "evolution_contract_hash": candidate.evolution_contract_hash,
        "kind": kind.value,
        "source_digest": candidate.provenance.source_digest,
        "subject_key": candidate.subject_key,
    }


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_wrong_adapter_fails_closed(kind: CandidateKind, artifacts: ArtifactStore) -> None:
    wrong_kind = next(
        candidate_kind for candidate_kind in CandidateKind if candidate_kind is not kind
    )
    adapter = ADAPTERS[kind](artifacts)

    with pytest.raises(AdapterKindMismatch, match="adapter kind"):
        adapter.validate_source(_proposal(wrong_kind))


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_adapter_activation_and_rollback_are_explicitly_unavailable(
    kind: CandidateKind, storage: Storage, artifacts: ArtifactStore
) -> None:
    candidate = CandidateService(storage, artifacts, clock=lambda: NOW).propose(_proposal(kind))
    adapter = ADAPTERS[kind](artifacts)

    with pytest.raises(AdapterOperationUnavailable, match="promotion service"):
        adapter.activate(candidate)
    with pytest.raises(AdapterOperationUnavailable, match="promotion service"):
        adapter.rollback(candidate)
    assert candidate.evolution_contract.automatic_promotion_allowed is False


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_stage_is_restart_safe_idempotent_and_has_no_live_effect(
    kind: CandidateKind, storage: Storage, artifacts: ArtifactStore, tmp_path: Path
) -> None:
    proposal = _proposal(kind)
    live_sentinel = tmp_path / "live-resource"
    live_sentinel.write_text("champion", encoding="utf-8")
    first_service = CandidateService(storage, artifacts, clock=lambda: NOW)
    proposed = first_service.propose(proposal)

    first = first_service.stage(proposed.candidate_id)
    restarted = CandidateService(storage, artifacts, clock=lambda: NOW)
    second = restarted.stage(proposed.candidate_id)
    repeated = restarted.propose(proposal)

    assert first == second
    assert first.lifecycle is CandidateLifecycle.STAGED
    assert first.candidate_id == proposed.candidate_id
    assert repeated == first.candidate
    assert live_sentinel.read_text(encoding="utf-8") == "champion"
    assert storage._conn.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0] == 1
    assert (
        storage._conn.execute("SELECT COUNT(*) FROM evolution_lifecycle_journal").fetchone()[0] == 2
    )
