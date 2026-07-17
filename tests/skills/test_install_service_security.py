"""Security and architecture contracts for the governed skill write entry."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.candidate_service import CandidateLiveAuthorities, CandidateService
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    EvolutionContractV1,
    GateName,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.skills.install_service import (
    ProposeSkillCommand,
    SkillInstallAuthorizationError,
    SkillInstallService,
)
from tianshu.storage.facade import Storage

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)


_WRITE_ENTRY_MODULES = (
    "src/tianshu/gateway/skills_api.py",
    "src/tianshu/tools/skill_tools.py",
    "src/tianshu/skills/reviewer.py",
    "src/tianshu/skills/curator.py",
    "src/tianshu/skills/curator_lifecycle.py",
)
_FORBIDDEN_MUTATIONS = {
    "archive_skill",
    "create_skill",
    "delete_skill",
    "patch_skill",
    "save_skill",
    "write_skill_file",
    "remove_skill_file",
}


def test_legacy_installer_public_entry_cannot_materialize_live_skills() -> None:
    module = "src/tianshu/skills/installer.py"
    tree = ast.parse(Path(module).read_text(encoding="utf-8"), filename=module)
    installer = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SkillInstaller"
    )
    install = next(
        node
        for node in installer.body
        if isinstance(node, ast.FunctionDef) and node.name == "install"
    )
    live_materializers = {
        node.func.attr
        for node in ast.walk(install)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"replace", "rename", "move", "copy", "copytree"}
    }
    assert live_materializers == set()


@pytest.mark.parametrize("module", _WRITE_ENTRY_MODULES)
def test_skill_write_entry_has_no_direct_loader_mutation(module: str) -> None:
    tree = ast.parse(Path(module).read_text(encoding="utf-8"), filename=module)
    direct = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _FORBIDDEN_MUTATIONS
    }
    assert direct == set()


@pytest.mark.parametrize(
    "path",
    ("../SKILL.md", "/SKILL.md", "C:/SKILL.md", "a\\SKILL.md", "x\u202ey/SKILL.md"),
)
def test_propose_command_rejects_unsafe_member_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ProposeSkillCommand(
            command_id="command-1",
            name="safe-skill",
            version="1",
            source_channel="api",
            members=({"path": path, "kind": "file", "content": "---\nname: safe-skill\n---"},),
            evidence_bundle_ids=(),
            restore_point_ref="skill:safe-skill:base",
        )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    active = Storage(str(tmp_path / "tianshu.db"))
    active.init_db()
    yield active
    active.close()


@pytest.fixture
def install_service(tmp_path: Path, storage: Storage) -> SkillInstallService:
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: NOW,
    )
    authorities = CandidateLiveAuthorities(
        memory_root=tmp_path / "live-memory",
        skill_target=tmp_path / "live-skills",
        policy_root=tmp_path / "live-policy",
        persona_root=tmp_path / "live-persona",
        code_worktree=tmp_path / "live-code",
    )
    candidates = CandidateService(
        storage,
        artifacts,
        live_authorities=authorities,
        clock=lambda: NOW,
    )

    def contract(name: str) -> EvolutionContractV1:
        return EvolutionContractV1(
            kind=CandidateKind.SKILL,
            subject_key=f"skill:{name}",
            governance_contract_hash="1" * 64,
            required_gates=tuple(GateName),
            regression_policy_artifact_digest="2" * 64,
            sample_policy_artifact_digest="3" * 64,
            budget_policy_artifact_digest="4" * 64,
            minimum_canary_samples=10,
            max_canary_allocation_basis_points=500,
            rollback_slo_seconds=30,
        )

    return SkillInstallService(candidates, storage, contract_factory=contract)


def _auth(*, scopes: frozenset[str] = frozenset({"api"})) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="principal-api",
            kind=PrincipalKind.HUMAN,
            display_name="API Reviewer",
            scopes=scopes,
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id="correlation-skill-propose",
    )


def _other_auth() -> AuthContext:
    return _auth().model_copy(
        update={
            "principal": _auth().principal.model_copy(
                update={"id": "principal-other", "display_name": "Other Reviewer"}
            )
        }
    )


def _command(
    *,
    source_channel: str = "api",
    members: tuple[dict[str, object], ...] | None = None,
) -> ProposeSkillCommand:
    base_content = "---\nname: safe-skill\ndescription: Safe base\n---\n\nBase."
    candidate_content = "---\nname: safe-skill\ndescription: Safe candidate\n---\n\nCandidate."
    return ProposeSkillCommand(
        command_id="command-skill-1",
        name="safe-skill",
        version="candidate-v1",
        base_version="champion-v1",
        source_channel=source_channel,
        base_members=({"path": "SKILL.md", "kind": "file", "content": base_content},),
        members=(
            members
            if members is not None
            else ({"path": "SKILL.md", "kind": "file", "content": candidate_content},)
        ),
        evidence_bundle_ids=(),
        restore_point_ref="skill:safe-skill:champion-v1",
    )


def test_propose_and_stage_derive_principal_and_never_write_live(
    install_service: SkillInstallService, storage: Storage, tmp_path: Path
) -> None:
    auth = _auth()

    candidate = install_service.propose(_command(), auth=auth)
    staged = install_service.stage(candidate.candidate_id, auth=auth)

    assert candidate.provenance.actor_principal_id == auth.principal.id
    assert candidate.provenance.actor_display_name == auth.principal.display_name
    assert candidate.lifecycle is CandidateLifecycle.PROPOSED
    assert staged.lifecycle is CandidateLifecycle.STAGED
    assert not (tmp_path / "live-skills" / "safe-skill").exists()
    with storage.unit_of_work() as unit_of_work:
        audit_actions = [
            row[0]
            for row in unit_of_work.connection.execute(
                "SELECT action FROM system_audit_events ORDER BY sequence"
            ).fetchall()
        ]
        outbox_types = [
            row[0]
            for row in unit_of_work.connection.execute(
                "SELECT event_type FROM outbox_events ORDER BY occurred_at, event_id"
            ).fetchall()
        ]
        unit_of_work.commit()
    assert audit_actions == ["skill.candidate.proposed", "skill.candidate.staged"]
    assert sorted(outbox_types) == ["skill.candidate.proposed", "skill.candidate.staged"]


@pytest.mark.parametrize(
    ("auth", "command"),
    (
        (_auth(scopes=frozenset()), _command()),
        (_auth(), _command(source_channel="cli")),
    ),
)
def test_propose_rejects_missing_scope_or_forged_channel(
    install_service: SkillInstallService,
    storage: Storage,
    auth: AuthContext,
    command: ProposeSkillCommand,
) -> None:
    with pytest.raises(SkillInstallAuthorizationError):
        install_service.propose(command, auth=auth)

    with storage.unit_of_work() as unit_of_work:
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[
                0
            ]
            == 0
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM system_audit_events").fetchone()[
                0
            ]
            == 0
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0
        )
        unit_of_work.commit()


@pytest.mark.parametrize(
    "members",
    (
        ({"path": "SKILL.md", "kind": "symlink_file", "content": None},),
        ({"path": "SKILL.md", "kind": "file", "content": "x" * (4 * 1024 * 1024 + 1)},),
        ({"path": "README.md", "kind": "file", "content": "missing metadata"},),
        (
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": "---\nname: different-skill\n---\n",
            },
        ),
    ),
)
def test_propose_security_failure_creates_no_candidate_or_live_file(
    install_service: SkillInstallService,
    storage: Storage,
    tmp_path: Path,
    members: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(ValueError):
        install_service.propose(_command(members=members), auth=_auth())

    assert not (tmp_path / "live-skills" / "safe-skill").exists()
    with storage.unit_of_work() as unit_of_work:
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[
                0
            ]
            == 0
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM artifact_records").fetchone()[0]
            == 0
        )
        unit_of_work.commit()


def test_stage_rejects_cross_principal_bola(
    install_service: SkillInstallService,
) -> None:
    candidate = install_service.propose(_command(), auth=_auth())

    with pytest.raises(SkillInstallAuthorizationError, match="principal mismatch"):
        install_service.stage(candidate.candidate_id, auth=_other_auth())


def test_retries_are_idempotent_and_do_not_duplicate_audit_or_outbox(
    install_service: SkillInstallService, storage: Storage
) -> None:
    first = install_service.propose(_command(), auth=_auth())
    second = install_service.propose(_command(), auth=_auth())
    staged = install_service.stage(first.candidate_id, auth=_auth())
    replayed = install_service.stage(first.candidate_id, auth=_auth())

    assert second == first
    assert replayed == staged
    with storage.unit_of_work() as unit_of_work:
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[
                0
            ]
            == 1
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM system_audit_events").fetchone()[
                0
            ]
            == 2
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 2
        )
        unit_of_work.commit()


def test_audit_outbox_failure_rolls_back_candidate_and_artifacts(
    install_service: SkillInstallService,
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected skill outbox failure")

    monkeypatch.setattr(install_service._outbox, "add", fail_outbox)
    with pytest.raises(RuntimeError, match="injected skill outbox failure"):
        install_service.propose(_command(), auth=_auth())

    with storage.unit_of_work() as unit_of_work:
        for table in (
            "artifact_records",
            "evolution_candidates",
            "evolution_lifecycle_journal",
            "system_audit_events",
            "outbox_events",
        ):
            assert (
                unit_of_work.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed table allowlist
                ).fetchone()[0]
                == 0
            )
        unit_of_work.commit()
