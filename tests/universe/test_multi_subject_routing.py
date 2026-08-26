"""P4b acceptance coverage for per-subject routing and continuity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tests.evolution.test_promotion_fail_closed import _ready, _service, _start_command
from tests.universe.test_challenger_routing import _candidate, _seed_memorial
from tianshu.evolution.promotion import PromoteCommand, RollbackCommand
from tianshu.evolution.runtime_context import EvolutionRuntimeContext, runtime_subject_key
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateVersionRefV1,
    RoutingPolicyV1,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_assignment import LegacyRunAssignmentV1, RunAssignmentV1
from tianshu.skills.loader import SkillsLoader
from tianshu.storage.evolution_repo import EvolutionAssignmentConflict, EvolutionRepository
from tianshu.universe.router import (
    ChallengerRouter,
    EvolutionRuntimeUnavailable,
    allocation_bucket,
    selects_challenger,
)

NOW = datetime(2026, 8, 26, 9, tzinfo=UTC)


def _skill_package(name: str, body: str, *, always: bool = False) -> dict[str, JsonValue]:
    always_metadata = (
        f"\nmetadata:\n  openclaw:\n    always: {str(always).lower()}" if always else ""
    )
    return {
        "state": "present",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    f"---\nname: {name}\ndescription: {name} test skill"
                    f"{always_metadata}\n---\n{body}"
                ),
            }
        ],
    }


def _seed_canaries(
    storage,
    *subjects: tuple[str, str, str],
    challenger_payloads: dict[str, dict[str, JsonValue]] | None = None,
) -> dict[str, dict[str, JsonValue]]:
    """Seed (candidate_id, subject_key, seed_id) skill canaries and return payloads."""

    repository = EvolutionRepository()
    payloads: dict[str, dict[str, JsonValue]] = {}
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        for candidate_id, subject_key, seed_id in subjects:
            name = subject_key.removeprefix("skill:")
            champion = _skill_package(name, f"{name} champion")
            challenger = (challenger_payloads or {}).get(
                subject_key,
                _skill_package(name, f"{name} challenger"),
            )
            base = CandidateVersionRefV1(
                version=f"{name}-champion-v1",
                artifact_digest=canonical_sha256(champion),
                canonical_digest=canonical_sha256(champion),
            )
            selected = CandidateVersionRefV1(
                version=f"{name}-candidate-v1",
                artifact_digest=canonical_sha256(challenger),
                canonical_digest=canonical_sha256(challenger),
            )
            payloads[base.artifact_digest] = champion
            payloads[selected.artifact_digest] = challenger
            current = repository.insert_candidate(
                connection,
                _candidate(
                    kind=CandidateKind.SKILL,
                    subject_key=subject_key,
                    base=base,
                    selected=selected,
                    candidate_id=candidate_id,
                ),
            )
            routing = RoutingPolicyV1(
                allocation_basis_points=1_000,
                allocation_seed_id=seed_id,
                routing_version=1,
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
            routing_payload = routing.model_dump(mode="json")
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
                    canonical_json_bytes(routing_payload).decode(),
                    canonical_sha256(routing_payload),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        unit_of_work.commit()
    return payloads


def _router(
    storage,
    payloads,
    *,
    buckets=None,
    routing_enabled: bool = True,
    resolve_calls: list[str] | None = None,
    snapshot_resolver: SystemSnapshotResolver | None = None,
):
    bucket_by_seed = buckets or {}

    def resolve(_connection, selected_ref, _overlay):
        if resolve_calls is not None:
            resolve_calls.append(selected_ref.artifact_digest)
        try:
            return payloads[selected_ref.artifact_digest]
        except KeyError as exc:
            raise LookupError("test payload unavailable") from exc

    return ChallengerRouter(
        storage,
        routing_enabled=routing_enabled,
        allocation_secret=b"p4b-test-secret",
        bucket_calculator=lambda _memorial, seed, _secret: bucket_by_seed.get(seed, 0),
        payload_resolver=resolve,
        snapshot_resolver=(lambda: snapshot_resolver),
        clock=lambda: NOW,
    )


def _assignment_set(storage, memorial_id: str):
    with storage.unit_of_work() as unit_of_work:
        result = EvolutionRepository().get_assignment_set(unit_of_work.connection, memorial_id)
        unit_of_work.commit()
    return result


def _archive_candidate(storage, candidate_id: str, from_lifecycle: CandidateLifecycle) -> None:
    paths = {
        CandidateLifecycle.PROMOTED: (CandidateLifecycle.PROMOTED, CandidateLifecycle.ARCHIVED),
        CandidateLifecycle.ROLLED_BACK: (
            CandidateLifecycle.ROLLBACK_PENDING,
            CandidateLifecycle.ROLLED_BACK,
            CandidateLifecycle.ARCHIVED,
        ),
        CandidateLifecycle.REJECTED: (CandidateLifecycle.REJECTED, CandidateLifecycle.ARCHIVED),
    }
    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        current = repository.get_candidate(unit_of_work.connection, candidate_id)
        assert current is not None and current.lifecycle is CandidateLifecycle.CANARY
        for lifecycle in paths[from_lifecycle]:
            current = repository.save_candidate(
                unit_of_work.connection,
                current.model_copy(
                    update={
                        "lifecycle": lifecycle,
                        "routing": None,
                        "updated_at": current.updated_at + timedelta(seconds=1),
                    }
                ),
                expected_version=current.version,
            )
        unit_of_work.commit()


def test_two_subjects_route_independently_and_runtime_has_no_primary_subject(
    storage, tmp_path
) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    _seed_memorial(storage)
    router = _router(
        storage,
        payloads,
        buckets={"seed-foo": 999, "seed-bar": 1_000},
    )

    projection = router.assign("memorial-1")
    assert isinstance(projection, LegacyRunAssignmentV1)
    assignment_set = _assignment_set(storage, "memorial-1")
    assert assignment_set is not None
    assert [item.subject_key for item in assignment_set.assignments] == ["skill:bar", "skill:foo"]
    selected = {item.subject_key: item for item in assignment_set.assignments}
    assert selected["skill:foo"].selected_ref != selected["skill:foo"].champion_ref
    assert selected["skill:bar"].selected_ref == selected["skill:bar"].champion_ref

    restarted = _router(
        storage,
        payloads,
        buckets={"seed-foo": 9_999, "seed-bar": 0},
    )
    assert restarted.assign("memorial-1") == projection
    assert _assignment_set(storage, "memorial-1") == assignment_set

    with restarted.bind_runtime("memorial-1") as runtime:
        assert runtime is not None
        assert runtime.assignment is None
        assert runtime.overlay is None
        assert runtime.selected_payload is None
        assert runtime.assignments == assignment_set.assignments
        assert set(runtime.overlays) == {
            runtime_subject_key(CandidateKind.SKILL, "skill:bar"),
            runtime_subject_key(CandidateKind.SKILL, "skill:foo"),
        }
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        rendered = SkillsLoader(builtin_dir=builtin).load_all()
        assert "foo challenger" in rendered
        assert "bar champion" in rendered


def test_multi_subject_prebind_and_same_attempt_runtime_replay_one_snapshot(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    _seed_memorial(storage)
    snapshot_resolver = SystemSnapshotResolver(
        kernel_facts=lambda: {"version": "p4b-prebind"},
        executor_digests=lambda: {"keqing:pi": "1" * 64},
        skills_digest=lambda: "2" * 64,
        personas_digest=lambda: "3" * 64,
        policy_rules_digest=lambda: "4" * 64,
        provider_profiles_digest=lambda: "5" * 64,
    )
    router = _router(storage, payloads, snapshot_resolver=snapshot_resolver)
    router.assign("memorial-1")

    with storage.unit_of_work() as unit_of_work:
        prebound = router.prebind_runtime_current(
            unit_of_work,
            memorial_id="memorial-1",
            attempt_id="attempt-prebound-multi",
        )
        unit_of_work.commit()

    assert prebound is not None
    assert "evolution_overlay_set" in prebound.system_snapshot.components
    assert "evolution_overlay" not in prebound.system_snapshot.components
    with router.bind_runtime(
        "memorial-1",
        attempt_id="attempt-prebound-multi",
    ) as runtime:
        assert runtime is not None
        assert len(runtime.assignments) == 2
        assert runtime.system_snapshot == prebound.system_snapshot
    with storage.unit_of_work() as unit_of_work:
        binding_count = unit_of_work.connection.execute(
            """SELECT COUNT(*) FROM run_system_bindings
               WHERE memorial_id='memorial-1' AND attempt_id='attempt-prebound-multi'"""
        ).fetchone()[0]
        unit_of_work.commit()
    assert binding_count == 1


def test_multi_skill_runtime_covers_absence_metadata_always_and_context_restore(
    storage,
    tmp_path,
) -> None:
    foo = _skill_package("foo", "foo evolved", always=True)
    absent: dict[str, JsonValue] = {"state": "absent", "members": []}
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
        challenger_payloads={"skill:foo": foo, "skill:bar": absent},
    )
    _seed_memorial(storage)
    router = _router(storage, payloads)
    router.assign("memorial-1")
    builtin = tmp_path / "builtin"
    for name in ("bar", "foo"):
        directory = builtin / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: builtin {name}\n---\n{name} builtin",
            encoding="utf-8",
        )
    loader = SkillsLoader(builtin_dir=builtin)

    with router.bind_runtime("memorial-1") as runtime:
        assert runtime is not None
        assert loader.get_skill("foo")["content"] == "foo evolved"  # type: ignore[index]
        assert loader.get_skill("bar") is None
        metadata = {item["name"]: item for item in loader.list_all_metadata()}
        assert metadata["foo"]["source"] == "evolution-overlay"
        assert "bar" not in metadata
        assert "foo test skill" in loader.load_index()
        assert "bar" not in loader.load_index()
        always = loader.load_always()
        assert "foo evolved" in always
        assert loader.load_always(["bar"]) == ""
        rendered = loader.load_all()
        assert "foo evolved" in rendered
        assert "foo builtin" not in rendered
        assert "bar builtin" not in rendered

        foo_key = runtime_subject_key(CandidateKind.SKILL, "skill:foo")
        members = runtime.payloads[foo_key]["members"]
        assert isinstance(members, list)
        with pytest.raises(TypeError, match="immutable"):
            members.append({"path": "tampered"})
        with pytest.raises(TypeError, match="immutable"):
            runtime.payloads[foo_key]["state"] = "absent"

        dict.__setitem__(runtime.payloads[foo_key], "state", "absent")
        with pytest.raises(RuntimeError, match="provenance mismatch"):
            loader.load_all()

    assert loader.get_skill("foo")["content"] == "foo builtin"  # type: ignore[index]
    assert loader.get_skill("bar")["content"] == "bar builtin"  # type: ignore[index]


def test_runtime_context_rejects_multi_overlays_without_durable_assignments(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    _seed_memorial(storage)
    router = _router(storage, payloads)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1") as runtime:
        assert runtime is not None
        with pytest.raises(ValidationError, match="durable subject assignments"):
            EvolutionRuntimeContext(
                assignment=None,
                overlay=None,
                selected_payload=None,
                assignments=(),
                overlays=dict(runtime.overlays),
                payloads={key: dict(value) for key, value in runtime.payloads.items()},
                system_snapshot=None,
            )


def test_runtime_context_rejects_duplicate_durable_subject_assignments(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    _seed_memorial(storage)
    router = _router(storage, payloads)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1") as runtime:
        assert runtime is not None
        subject = runtime.assignments[0]
        key = runtime_subject_key(subject.kind, subject.subject_key)
        with pytest.raises(ValidationError, match="subject assignments must be unique"):
            EvolutionRuntimeContext(
                assignment=None,
                overlay=None,
                selected_payload=None,
                assignments=(subject, subject),
                overlays={key: runtime.overlays[key]},
                payloads={key: dict(runtime.payloads[key])},
                system_snapshot=None,
            )


def test_multiple_always_on_runtime_skills_have_a_deterministic_prompt_order(
    storage,
    tmp_path,
) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-zeta", "skill:zeta", "seed-zeta"),
        ("candidate-alpha", "skill:alpha", "seed-alpha"),
        challenger_payloads={
            "skill:zeta": _skill_package("zeta", "zeta evolved", always=True),
            "skill:alpha": _skill_package("alpha", "alpha evolved", always=True),
        },
    )
    _seed_memorial(storage)
    router = _router(storage, payloads)
    router.assign("memorial-1")
    builtin = tmp_path / "builtin"
    builtin.mkdir()

    with router.bind_runtime("memorial-1"):
        prompt = SkillsLoader(builtin_dir=builtin).load_always()

    assert prompt.index("## Skill: alpha") < prompt.index("## Skill: zeta")


def test_single_subject_shadow_is_equivalent_to_the_legacy_authority(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
    )
    _seed_memorial(storage)
    router = _router(storage, payloads, buckets={"seed-foo": 999})

    legacy = router.assign("memorial-1")
    assert isinstance(legacy, RunAssignmentV1)
    with storage.unit_of_work() as unit_of_work:
        loaded = EvolutionRepository().get_assignment(unit_of_work.connection, "memorial-1")
        shadow = EvolutionRepository().get_assignment_set(unit_of_work.connection, "memorial-1")
        unit_of_work.commit()
    assert loaded is not None and shadow is not None
    durable, overlay = loaded
    assert durable == legacy and overlay is not None
    assert len(shadow.assignments) == 1
    subject = shadow.assignments[0]
    assert subject.assignment_id != durable.assignment_id
    assert (
        subject.memorial_id,
        subject.candidate_id,
        subject.champion_ref,
        subject.selected_ref,
        subject.routing_version,
        subject.bucket,
        subject.created_at,
    ) == (
        durable.memorial_id,
        durable.candidate_id,
        durable.champion_ref,
        durable.selected_ref,
        durable.routing_version,
        durable.bucket,
        durable.created_at,
    )
    assert (overlay.kind, overlay.subject_key) == (subject.kind, subject.subject_key)


def test_assignment_write_is_atomic_when_any_subject_cannot_bind(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    _seed_memorial(storage)
    payloads.pop(
        next(digest for digest, payload in payloads.items() if "bar challenger" in str(payload))
    )
    router = _router(storage, payloads)

    with pytest.raises(EvolutionRuntimeUnavailable, match="candidate_overlay_unavailable"):
        router.assign("memorial-1")

    with storage.unit_of_work() as unit_of_work:
        old_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        new_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        unit_of_work.commit()
    assert (old_count, new_count) == (0, 0)


def test_global_routing_kill_switch_only_blocks_new_assignments(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
    )
    for memorial_id in ("existing", "disabled-new", "enabled-new"):
        _seed_memorial(storage, memorial_id)
    enabled = _router(storage, payloads)
    existing = enabled.assign("existing")
    assert isinstance(existing, RunAssignmentV1)

    disabled = _router(storage, payloads, routing_enabled=False)
    assert disabled.assign("existing") == existing
    with disabled.bind_runtime("existing") as runtime:
        assert runtime is not None and runtime.assignment == existing
    disabled_new = disabled.assign("disabled-new")
    assert isinstance(disabled_new, LegacyRunAssignmentV1)
    assert _assignment_set(storage, "disabled-new") is None

    enabled_new = enabled.assign("enabled-new")
    assert isinstance(enabled_new, RunAssignmentV1)
    assert _assignment_set(storage, "enabled-new") is not None
    assert disabled.assign("disabled-new") == disabled_new


def test_disabled_routing_preserves_multi_subject_followup_continuity(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    for memorial_id in ("parent", "child"):
        _seed_memorial(storage, memorial_id)
    enabled = _router(storage, payloads)
    enabled.assign("parent")
    parent = _assignment_set(storage, "parent")
    assert parent is not None

    def reject_rebucket(_memorial_id: str, _seed_id: str, _secret: bytes) -> int:
        raise AssertionError("follow-up continuity must not re-bucket")

    disabled = ChallengerRouter(
        storage,
        routing_enabled=False,
        allocation_secret=b"p4b-disabled-continuity-secret",
        bucket_calculator=reject_rebucket,
        payload_resolver=lambda _connection, selected_ref, _overlay: payloads[
            selected_ref.artifact_digest
        ],
        clock=lambda: NOW,
    )
    with storage.unit_of_work() as unit_of_work:
        disabled.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )
        unit_of_work.commit()

    child = _assignment_set(storage, "child")
    assert child is not None
    parent_by_subject = {item.subject_key: item for item in parent.assignments}
    child_by_subject = {item.subject_key: item for item in child.assignments}
    assert set(parent_by_subject) == set(child_by_subject) == {"skill:foo", "skill:bar"}
    for subject_key, parent_assignment in parent_by_subject.items():
        child_assignment = child_by_subject[subject_key]
        assert child_assignment.assignment_id != parent_assignment.assignment_id
        assert (
            child_assignment.candidate_id,
            child_assignment.champion_ref,
            child_assignment.selected_ref,
            child_assignment.routing_version,
            child_assignment.bucket,
        ) == (
            parent_assignment.candidate_id,
            parent_assignment.champion_ref,
            parent_assignment.selected_ref,
            parent_assignment.routing_version,
            parent_assignment.bucket,
        )


def test_followups_keep_each_subject_sticky_then_converge_to_champion(storage) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
    )
    for memorial_id in ("parent", "child", "grandchild", "settled"):
        _seed_memorial(storage, memorial_id)
    router = _router(storage, payloads)
    router.assign("parent")
    parent = _assignment_set(storage, "parent")
    assert parent is not None
    assert all(item.selected_ref != item.champion_ref for item in parent.assignments)

    with storage.unit_of_work() as unit_of_work:
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )
        unit_of_work.commit()
    child = _assignment_set(storage, "child")
    assert child is not None
    assert [item.selected_ref for item in child.assignments] == [
        item.selected_ref for item in parent.assignments
    ]
    assert [item.bucket for item in child.assignments] == [
        item.bucket for item in parent.assignments
    ]

    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        for item in child.assignments:
            assert item.candidate_id is not None
            candidate = repository.get_candidate(unit_of_work.connection, item.candidate_id)
            assert candidate is not None
            repository.save_candidate(
                unit_of_work.connection,
                candidate.model_copy(
                    update={
                        "lifecycle": CandidateLifecycle.READY,
                        "routing": None,
                        "updated_at": candidate.updated_at + timedelta(seconds=1),
                    }
                ),
                expected_version=candidate.version,
            )
        router.assign_current(
            unit_of_work,
            memorial_id="grandchild",
            inherit_from_memorial_id="child",
        )
        unit_of_work.commit()

    grandchild = _assignment_set(storage, "grandchild")
    assert grandchild is not None
    assert all(item.selected_ref == item.champion_ref for item in grandchild.assignments)
    with storage.unit_of_work() as unit_of_work:
        convergence_count = unit_of_work.connection.execute(
            """SELECT COUNT(*) FROM system_audit_events
               WHERE action='evolution_continuity_converged'"""
        ).fetchone()[0]
        router.assign_current(
            unit_of_work,
            memorial_id="settled",
            inherit_from_memorial_id="grandchild",
        )
        unit_of_work.commit()
    assert convergence_count == 2
    with storage.unit_of_work() as unit_of_work:
        assert (
            unit_of_work.connection.execute(
                """SELECT COUNT(*) FROM system_audit_events
               WHERE action='evolution_continuity_converged'"""
            ).fetchone()[0]
            == 2
        )
        unit_of_work.commit()


def test_followup_after_real_promotion_converges_to_the_promoted_candidate(storage) -> None:
    candidate = _ready(storage, CandidateKind.SKILL)
    service, _gates, _adapter = _service(storage, candidate)
    auth = AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Owner",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="p4b-promoted-continuity",
    )
    canary = service.start_canary(
        candidate.candidate_id,
        _start_command(candidate, key="p4b-promote-start"),
        auth=auth,
    )
    for memorial_id in ("parent", "child"):
        _seed_memorial(storage, memorial_id)
    router = _router(
        storage,
        {
            candidate.base.artifact_digest: {},
            candidate.candidate.artifact_digest: {},
        },
        buckets={"seed-1": 0},
    )
    router.assign("parent")
    parent = _assignment_set(storage, "parent")
    assert parent is not None
    assert parent.assignments[0].selected_ref == candidate.candidate

    promoted = service.promote(
        candidate.candidate_id,
        PromoteCommand(
            expected_version=canary.candidate_version,
            idempotency_key="p4b-promote-complete",
            reason="promote the verified P4b candidate",
        ),
        auth=auth,
    )
    assert promoted.lifecycle is CandidateLifecycle.PROMOTED
    with storage.unit_of_work() as unit_of_work:
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )
        unit_of_work.commit()

    child = _assignment_set(storage, "child")
    assert child is not None
    assert child.assignments[0].selected_ref == candidate.candidate
    assert child.assignments[0].selected_ref != child.assignments[0].champion_ref


@pytest.mark.parametrize(
    ("from_lifecycle", "selects_candidate"),
    [
        (CandidateLifecycle.PROMOTED, True),
        (CandidateLifecycle.ROLLED_BACK, False),
        (CandidateLifecycle.REJECTED, False),
    ],
)
def test_archived_followup_uses_verified_terminal_provenance(
    storage,
    from_lifecycle: CandidateLifecycle,
    selects_candidate: bool,
) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
    )
    for memorial_id in ("parent", "child"):
        _seed_memorial(storage, memorial_id)
    router = _router(storage, payloads)
    router.assign("parent")
    _archive_candidate(storage, "candidate-foo", from_lifecycle)
    with storage.unit_of_work() as unit_of_work:
        repository = EvolutionRepository()
        archived = repository.get_candidate(unit_of_work.connection, "candidate-foo")
        assert archived is not None
        expected_ref = archived.candidate if selects_candidate else archived.base
        repository.save_candidate(
            unit_of_work.connection,
            archived.model_copy(update={"updated_at": archived.updated_at + timedelta(seconds=1)}),
            expected_version=archived.version,
        )
        unit_of_work.commit()

    with storage.unit_of_work() as unit_of_work:
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )
        unit_of_work.commit()

    child = _assignment_set(storage, "child")
    assert child is not None
    assignment = child.assignments[0]
    assert assignment.selected_ref == expected_ref


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "UPDATE evolution_lifecycle_journal SET entry_hash='" + "0" * 64 + "' "
        "WHERE candidate_id='candidate-foo' AND to_lifecycle='archived'",
        "UPDATE evolution_lifecycle_journal SET from_lifecycle='promoted' "
        "WHERE candidate_id='candidate-foo' AND to_lifecycle='archived'",
    ],
)
def test_archived_followup_rejects_lifecycle_journal_tampering(
    storage,
    tamper_sql: str,
) -> None:
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
    )
    for memorial_id in ("parent", "child"):
        _seed_memorial(storage, memorial_id)
    router = _router(storage, payloads)
    router.assign("parent")
    _archive_candidate(storage, "candidate-foo", CandidateLifecycle.ROLLED_BACK)
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute("DROP TRIGGER evolution_lifecycle_journal_no_update")
        unit_of_work.connection.execute(tamper_sql)
        unit_of_work.commit()

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionAssignmentConflict, match="continuity provenance conflicts"),
    ):
        router.assign_current(
            unit_of_work,
            memorial_id="child",
            inherit_from_memorial_id="parent",
        )


def test_rollback_of_one_subject_does_not_change_the_other_subject(storage) -> None:
    memory = _ready(storage, CandidateKind.MEMORY)
    skill = _ready(storage, CandidateKind.SKILL)
    memory_service, _memory_gates, memory_adapter = _service(storage, memory)
    skill_service, _skill_gates, _skill_adapter = _service(storage, skill)
    repository = EvolutionRepository()
    auth = AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Owner",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="p4b-independent-rollback",
    )
    memory_canary = memory_service.start_canary(
        memory.candidate_id,
        _start_command(memory, key="start-memory"),
        auth=auth,
    )
    skill_service.start_canary(
        skill.candidate_id,
        _start_command(skill, key="start-skill"),
        auth=auth,
    )

    memory_service.rollback(
        memory.candidate_id,
        RollbackCommand(
            expected_version=memory_canary.candidate_version,
            idempotency_key="rollback-memory-only",
            reason="P4b independent subject rollback",
        ),
        auth=auth,
    )

    assert memory_adapter.saw_zero_before_restore is True
    with storage.unit_of_work() as unit_of_work:
        allocations = {
            row["candidate_id"]: row["allocation_basis_points"]
            for row in unit_of_work.connection.execute(
                """SELECT candidate_id, allocation_basis_points
                   FROM evolution_routing_allocations"""
            ).fetchall()
        }
        routable = repository.get_routable_candidates(unit_of_work.connection)
        unit_of_work.commit()
    assert allocations == {"candidate-memory": 0, "candidate-skill": 250}
    assert [candidate.candidate_id for candidate in routable] == ["candidate-skill"]


def test_each_subject_uses_an_independent_real_hmac_distribution() -> None:
    secret = b"p4b-distribution-secret"
    sample_size = 10_000
    foo = tuple(
        selects_challenger(
            bucket=allocation_bucket(f"memorial-{index}", "seed-foo", secret),
            allocation_basis_points=1_000,
        )
        for index in range(sample_size)
    )
    bar = tuple(
        selects_challenger(
            bucket=allocation_bucket(f"memorial-{index}", "seed-bar", secret),
            allocation_basis_points=1_000,
        )
        for index in range(sample_size)
    )

    assert 850 <= sum(foo) <= 1_150
    assert 850 <= sum(bar) <= 1_150
    assert sum(left != right for left, right in zip(foo, bar, strict=True)) > 1_000


def test_kind_is_part_of_every_runtime_and_durable_subject_identity() -> None:
    assert runtime_subject_key(CandidateKind.SKILL, "shared") != runtime_subject_key(
        CandidateKind.POLICY,
        "shared",
    )
    assert ChallengerRouter._subject_assignment_id(  # noqa: SLF001
        "memorial-1",
        CandidateKind.SKILL,
        "shared",
    ) != ChallengerRouter._subject_assignment_id(  # noqa: SLF001
        "memorial-1",
        CandidateKind.POLICY,
        "shared",
    )


def test_sixty_four_subject_hot_path_is_strictly_linear(storage) -> None:
    subjects = tuple(
        (f"candidate-{index}", f"skill:s{index:02d}", f"seed-{index}") for index in range(64)
    )
    payloads = _seed_canaries(storage, *subjects)
    _seed_memorial(storage)
    calls: list[str] = []
    router = _router(storage, payloads, resolve_calls=calls)

    router.assign("memorial-1")
    assert len(calls) == 64
    with storage.unit_of_work() as unit_of_work:
        seals = unit_of_work.connection.execute(
            """SELECT DISTINCT assignment_set_hash, assignment_set_size
               FROM run_subject_assignments WHERE memorial_id='memorial-1'"""
        ).fetchall()
        unit_of_work.commit()
    assert len(seals) == 1
    assert seals[0]["assignment_set_size"] == 64
    assert len(seals[0]["assignment_set_hash"]) == 64
    calls.clear()
    with router.bind_runtime("memorial-1") as runtime:
        assert runtime is not None and len(runtime.assignments) == 64
    assert len(calls) == 64


def test_sixty_five_subjects_are_rejected_before_any_assignment_write(storage) -> None:
    subjects = tuple(
        (f"candidate-{index}", f"skill:s{index:02d}", f"seed-{index}") for index in range(65)
    )
    payloads = _seed_canaries(storage, *subjects)
    _seed_memorial(storage)
    calls: list[str] = []
    router = _router(storage, payloads, resolve_calls=calls)

    with pytest.raises(EvolutionAssignmentConflict, match="exceeds 64"):
        router.assign("memorial-1")

    assert calls == []
    with storage.unit_of_work() as unit_of_work:
        counts = tuple(
            unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE memorial_id='memorial-1'"
            ).fetchone()[0]
            for table in ("run_evolution_assignments", "run_subject_assignments")
        )
        unit_of_work.commit()
    assert counts == (0, 0)
