"""Five-domain staging matrix for governed evolution candidates."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

import tianshu.evolution.candidate_service as candidate_service_module
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution import (
    CandidateProposalV1,
    CandidateService,
    CandidateSourceV1,
    ProvenanceInputV1,
)
from tianshu.evolution.adapters.base import (
    AdapterError,
    AdapterKindMismatch,
    AdapterOperationUnavailable,
)
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.memory.models import MemoryEntry
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    EvolutionContractV1,
    GateName,
)
from tianshu.storage.evolution_repo import EvolutionRepositoryConflict
from tianshu.storage.facade import Storage
from tianshu.storage.unit_of_work import SqliteUnitOfWork

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
        "trust_source": "workspace",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    "---\nname: review-helper\ndescription: Review safely\n---\n\nUse evidence."
                ),
            }
        ],
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
            {
                **skill_base,
                "members": [
                    {
                        **skill_base["members"][0],
                        "content": skill_base["members"][0]["content"] + "\nReport blockers.",
                    }
                ],
            },
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
    base_materialized = json.loads(artifacts.get_bytes(candidate.base.artifact_digest))
    candidate_materialized = json.loads(artifacts.get_bytes(candidate.candidate.artifact_digest))
    assert candidate.provenance.source_digest == canonical_sha256(candidate_materialized)
    assert candidate.base.canonical_digest == canonical_sha256(base_materialized)
    assert candidate.candidate.canonical_digest == canonical_sha256(candidate_materialized)
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


def test_propose_insert_failure_leaves_no_artifact_orphan(
    storage: Storage,
    artifacts: ArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = CandidateService(storage, artifacts, clock=lambda: NOW)

    def fail_insert(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected candidate insert failure")

    monkeypatch.setattr(service._repository, "insert_candidate", fail_insert)

    with pytest.raises(RuntimeError, match="injected candidate insert failure"):
        service.propose(_proposal(CandidateKind.MEMORY))

    assert storage._conn.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0] == 0
    assert storage._conn.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0] == 0
    assert [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()] == []


def test_memory_materializes_normalized_domain_value(
    storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(CandidateKind.MEMORY)
    raw = {**proposal.candidate.payload, "confidence": "0.9"}
    proposal = proposal.model_copy(
        update={"candidate": CandidateSourceV1(version="candidate-v1", payload=raw)}
    )

    candidate = CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)
    materialized = json.loads(artifacts.get_bytes(candidate.candidate.artifact_digest))

    assert materialized["confidence"] == 0.9
    assert isinstance(materialized["confidence"], float)


def test_persona_rejects_host_absolute_paths_without_echoing_them(
    storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(CandidateKind.PERSONA)
    private_path = "/Users/reviewer/private/personas/SOUL.md"
    raw = {**proposal.candidate.payload, "soul_path": private_path}
    proposal = proposal.model_copy(
        update={"candidate": CandidateSourceV1(version="candidate-v1", payload=raw)}
    )

    with pytest.raises(AdapterError) as captured:
        CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)

    assert private_path not in str(captured.value)


def test_skill_accepts_a_complete_canonical_package(
    storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(CandidateKind.SKILL)
    package = {
        "name": "review-helper",
        "trust_source": "workspace",
        "members": [
            {
                "path": "scripts/check.sh",
                "kind": "file",
                "content": "#!/bin/sh\necho checked\n",
            },
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    "---\nname: review-helper\ndescription: Review safely\n---\n\nUse evidence."
                ),
            },
        ],
    }
    proposal = proposal.model_copy(
        update={
            "base": CandidateSourceV1(version="champion-v1", payload=package),
            "candidate": CandidateSourceV1(version="candidate-v1", payload=package),
        }
    )

    candidate = CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)
    materialized = json.loads(artifacts.get_bytes(candidate.candidate.artifact_digest))

    assert [member["path"] for member in materialized["members"]] == [
        "SKILL.md",
        "scripts/check.sh",
    ]


def _artifact_state(storage: Storage, root: Path) -> tuple[int, tuple[str, ...]]:
    rows = storage._conn.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0]
    files = tuple(sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()))
    return rows, files


def test_invalid_envelope_rolls_back_all_proposal_artifacts(
    storage: Storage,
    artifacts: ArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_envelope(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ValueError("injected envelope failure")

    monkeypatch.setattr(candidate_service_module, "EvolutionCandidateV1", fail_envelope)

    with pytest.raises(ValueError, match="injected envelope failure"):
        CandidateService(storage, artifacts, clock=lambda: NOW).propose(
            _proposal(CandidateKind.MEMORY)
        )

    assert storage._conn.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0] == 0
    assert _artifact_state(storage, tmp_path / "artifacts") == (0, ())


@pytest.mark.parametrize("failure", ["cas", "commit"])
def test_stage_failure_rolls_back_receipt_and_keeps_proposed(
    failure: str,
    storage: Storage,
    artifacts: ArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = CandidateService(storage, artifacts, clock=lambda: NOW)
    proposed = service.propose(_proposal(CandidateKind.CODE))
    before = _artifact_state(storage, tmp_path / "artifacts")

    if failure == "cas":

        def fail_save(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise EvolutionRepositoryConflict("injected stage CAS failure")

        monkeypatch.setattr(service._repository, "save_candidate", fail_save)
    else:
        original_commit = SqliteUnitOfWork.commit
        attempts = 0

        def fail_first_commit(unit_of_work: SqliteUnitOfWork) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected stage commit failure")
            original_commit(unit_of_work)

        monkeypatch.setattr(SqliteUnitOfWork, "commit", fail_first_commit)

    with pytest.raises((EvolutionRepositoryConflict, RuntimeError), match="injected stage"):
        service.stage(proposed.candidate_id)

    durable = service._repository.get_candidate(storage._conn, proposed.candidate_id)
    assert durable is not None and durable.lifecycle is CandidateLifecycle.PROPOSED
    assert _artifact_state(storage, tmp_path / "artifacts") == before


def test_stage_failure_does_not_delete_preexisting_shared_receipt(
    storage: Storage,
    artifacts: ArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = CandidateService(storage, artifacts, clock=lambda: NOW)
    proposed = service.propose(_proposal(CandidateKind.CODE))
    adapter = CodeCandidateAdapter(artifacts)
    staged_envelope = proposed.model_copy(update={"lifecycle": CandidateLifecycle.STAGED})
    shared = adapter.stage(staged_envelope).staged_artifact
    shared_path = tmp_path / "artifacts" / shared.digest[:2] / shared.digest

    def fail_save(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise EvolutionRepositoryConflict("injected stage CAS failure")

    monkeypatch.setattr(service._repository, "save_candidate", fail_save)

    with pytest.raises(EvolutionRepositoryConflict, match="injected stage CAS failure"):
        service.stage(proposed.candidate_id)

    assert shared_path.is_file()
    assert artifacts.get_bytes(shared.digest)


@pytest.mark.parametrize(
    "members",
    [
        ({"path": "../escape", "kind": "file", "content": "x"},),
        ({"path": "/absolute", "kind": "file", "content": "x"},),
        (
            {"path": "SKILL.md", "kind": "file", "content": "x"},
            {"path": "SKILL.md", "kind": "file", "content": "x"},
        ),
        ({"path": "link", "kind": "symlink_file", "content": None},),
        ({"path": "link", "kind": "symlink_directory", "content": None},),
        ({"path": "README.md", "kind": "file", "content": "missing"},),
        (
            {"path": "SKILL.md", "kind": "file", "content": "x"},
            {"path": "nested/SKILL.md", "kind": "file", "content": "x"},
        ),
        (
            {
                "path": "nested/deep/SKILL.md",
                "kind": "file",
                "content": "---\nname: review-helper\ndescription: x\n---\nbody",
            },
        ),
        (
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: other\ndescription: x\n---\nbody",
            },
        ),
        (
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: review-helper\n---\nbody",
            },
        ),
    ],
    ids=[
        "traversal",
        "absolute",
        "duplicate",
        "symlink-file",
        "symlink-directory",
        "missing-skill-md",
        "multiple-skill-md",
        "non-root-skill-md",
        "name-mismatch",
        "invalid-metadata",
    ],
)
def test_skill_package_security_boundaries_fail_closed(
    members: tuple[dict[str, object], ...], storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(CandidateKind.SKILL)
    package = {
        "name": "review-helper",
        "trust_source": "workspace",
        "members": list(members),
    }
    proposal = proposal.model_copy(
        update={
            "base": CandidateSourceV1(version="champion-v1", payload=package),
            "candidate": CandidateSourceV1(version="candidate-v1", payload=package),
        }
    )

    with pytest.raises(AdapterError, match="skill source validation failed"):
        CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)


def test_skill_package_rejects_oversize_member(storage: Storage, artifacts: ArtifactStore) -> None:
    proposal = _proposal(CandidateKind.SKILL)
    package = {
        "name": "review-helper",
        "trust_source": "workspace",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "x" * (4 * 1024 * 1024 + 1),
            }
        ],
    }
    proposal = proposal.model_copy(
        update={
            "base": CandidateSourceV1(version="champion-v1", payload=package),
            "candidate": CandidateSourceV1(version="candidate-v1", payload=package),
        }
    )

    with pytest.raises(AdapterError, match="skill source validation failed"):
        CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_domain_source_extras_fail_closed_without_secret_echo(
    kind: CandidateKind, storage: Storage, artifacts: ArtifactStore
) -> None:
    proposal = _proposal(kind)
    secret = "token=sk-abcdefghijklmnopqrstuvwxyz012345"
    payload = {**proposal.candidate.payload, "operator_note": secret}
    proposal = proposal.model_copy(
        update={"candidate": CandidateSourceV1(version="candidate-v1", payload=payload)}
    )

    with pytest.raises(AdapterError) as captured:
        CandidateService(storage, artifacts, clock=lambda: NOW).propose(proposal)

    assert secret not in str(captured.value)


def _open_candidate_runtime(
    database: Path, artifact_root: Path
) -> tuple[Storage, ArtifactStore, CandidateService]:
    storage = Storage(str(database))
    storage.init_db()
    artifacts = ArtifactStore(
        artifact_root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=8 * 1024 * 1024,
        max_total_bytes=32 * 1024 * 1024,
        clock=lambda: NOW,
    )
    return storage, artifacts, CandidateService(storage, artifacts, clock=lambda: NOW)


def _tree_digest(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _prepare_live_resource(kind: CandidateKind, storage: Storage, root: Path) -> None:
    root.mkdir(parents=True)
    if kind is CandidateKind.MEMORY:
        storage.save_memory_entry(
            MemoryEntry(
                id="live-memory",
                persona_id="ducha",
                category="insight",
                content="live champion memory",
                source="reflection",
                created_at=NOW,
            )
        )
        return
    if kind is CandidateKind.SKILL:
        skill = root / "skills" / "review-helper"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: review-helper\ndescription: live\n---\nlive champion skill",
            encoding="utf-8",
        )
    elif kind is CandidateKind.POLICY:
        (root / "policy.json").write_text(
            json.dumps(_sources()[kind][0], sort_keys=True), encoding="utf-8"
        )
    elif kind is CandidateKind.PERSONA:
        persona = root / "personas" / "ducha-reviewer"
        persona.mkdir(parents=True)
        (persona / "SOUL.md").write_text("live soul", encoding="utf-8")
        (persona / "ROLE.md").write_text("live role", encoding="utf-8")
        storage.save_persona(
            {
                "id": "live-persona",
                "name": "Live Persona",
                "department": "ducha",
                "soul_path": "personas/ducha/SOUL.md",
                "role_path": "personas/ducha/ROLE.md",
            }
        )
    else:
        worktree = root / "worktree"
        (worktree / ".git").mkdir(parents=True)
        (worktree / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (worktree / "app.py").write_text("print('live champion')\n", encoding="utf-8")


def _live_resource_digest(kind: CandidateKind, storage: Storage, root: Path) -> str:
    if kind is CandidateKind.MEMORY:
        return canonical_sha256(
            [entry.model_dump(mode="json") for entry in storage.list_memory_by_persona("ducha")]
        )
    if kind is CandidateKind.PERSONA:
        return canonical_sha256(
            {"row": storage.get_persona("live-persona"), "files": _tree_digest(root)}
        )
    return _tree_digest(root)


@pytest.mark.parametrize("kind", tuple(CandidateKind))
def test_real_live_resources_remain_unchanged_across_reopen_and_retry(
    kind: CandidateKind, tmp_path: Path
) -> None:
    database = tmp_path / f"{kind.value}.db"
    artifact_root = tmp_path / f"{kind.value}-artifacts"
    live_root = tmp_path / f"{kind.value}-live"
    storage, _artifacts, service = _open_candidate_runtime(database, artifact_root)
    _prepare_live_resource(kind, storage, live_root)
    before = _live_resource_digest(kind, storage, live_root)
    proposed = service.propose(_proposal(kind))
    staged = service.stage(proposed.candidate_id)
    storage.close()

    reopened, reopened_artifacts, restarted = _open_candidate_runtime(database, artifact_root)
    repeated = restarted.propose(_proposal(kind))
    restaged = restarted.stage(proposed.candidate_id)

    assert repeated == staged.candidate
    assert restaged == staged
    assert _live_resource_digest(kind, reopened, live_root) == before
    assert reopened._conn.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0] == 1
    assert (
        reopened._conn.execute("SELECT COUNT(*) FROM evolution_lifecycle_journal").fetchone()[0]
        == 2
    )
    refs = {
        staged.candidate.base.artifact_digest,
        staged.candidate.candidate.artifact_digest,
        staged.candidate.diff_artifact_digest,
        staged.staged_artifact.digest,
    }
    assert len(refs) == 4
    assert reopened._conn.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0] == 4
    assert all(reopened_artifacts.verify(digest) for digest in refs)
    assert len([path for path in artifact_root.rglob("*") if path.is_file()]) == 4
    reopened.close()


def test_two_independent_connections_converge_on_one_candidate_and_stage(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.db"
    artifact_root = tmp_path / "artifacts"
    first_storage, _first_artifacts, first = _open_candidate_runtime(database, artifact_root)
    second_storage, _second_artifacts, second = _open_candidate_runtime(database, artifact_root)
    proposal = _proposal(CandidateKind.CODE)

    with ThreadPoolExecutor(max_workers=2) as executor:
        proposed = tuple(executor.map(lambda service: service.propose(proposal), (first, second)))
        staged = tuple(
            executor.map(
                lambda service: service.stage(proposed[0].candidate_id),
                (first, second),
            )
        )

    assert proposed[0] == proposed[1]
    assert staged[0] == staged[1]
    assert (
        first_storage._conn.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0] == 1
    )
    assert (
        first_storage._conn.execute("SELECT COUNT(*) FROM evolution_lifecycle_journal").fetchone()[
            0
        ]
        == 2
    )
    assert first_storage._conn.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0] == 4
    first_storage.close()
    second_storage.close()


def _identity_variants(proposal: CandidateProposalV1) -> tuple[CandidateProposalV1, ...]:
    base_payload = {**proposal.base.payload, "confidence": 0.7}
    candidate_payload = {**proposal.candidate.payload, "confidence": 0.6}
    contract = proposal.evolution_contract.model_copy(update={"minimum_canary_samples": 11})
    subject_contract = proposal.evolution_contract.model_copy(
        update={"subject_key": "memory:other"}
    )
    return (
        proposal.model_copy(
            update={
                "provenance": proposal.provenance.model_copy(
                    update={"actor_principal_id": "principal-other"}
                )
            }
        ),
        proposal.model_copy(
            update={
                "provenance": proposal.provenance.model_copy(update={"producer_version": "2.0"})
            }
        ),
        proposal.model_copy(
            update={"base": CandidateSourceV1(version="champion-v1", payload=base_payload)}
        ),
        proposal.model_copy(
            update={
                "candidate": CandidateSourceV1(version="candidate-v1", payload=candidate_payload)
            }
        ),
        proposal.model_copy(update={"evolution_contract": contract}),
        proposal.model_copy(
            update={"subject_key": "memory:other", "evolution_contract": subject_contract}
        ),
    )


def test_candidate_identity_binds_principal_provenance_sources_contract_and_subject(
    storage: Storage, artifacts: ArtifactStore
) -> None:
    service = CandidateService(storage, artifacts, clock=lambda: NOW)
    proposal = _proposal(CandidateKind.MEMORY)
    original = service.propose(proposal)
    repeated = service.propose(proposal)
    variants = tuple(service.propose(variant) for variant in _identity_variants(proposal))

    assert original.candidate_id == repeated.candidate_id
    assert len({original.candidate_id, *(candidate.candidate_id for candidate in variants)}) == 7
    assert variants[0].candidate.canonical_digest == original.candidate.canonical_digest
