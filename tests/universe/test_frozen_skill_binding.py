"""Per-run frozen Skills view binding at the managed execution boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256

import pytest

from tests.universe.test_challenger_routing import (
    _router as _governed_router,
)
from tests.universe.test_challenger_routing import (
    _seed_canary,
    _seed_memorial,
)
from tests.universe.test_multi_subject_routing import _seed_canaries
from tianshu.application.run_dispatcher import AttemptAuthority, AttemptRunResult, RunDispatcher
from tianshu.evolution.runtime_context import current_evolution_runtime
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.attempt import AttemptDisposition
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.frozen_content import (
    FrozenContentViewsV1,
    FrozenSkillsViewV1,
    frozen_skills_view_digest,
)
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.skills.loader import (
    SkillsLoader,
    bind_frozen_content_views,
    current_frozen_content_views,
)
from tianshu.storage.system_snapshot_repo import (
    SystemSnapshotRepository,
    SystemSnapshotRepositoryDecodeError,
)
from tianshu.universe.router import (
    ChallengerRouter,
    FrozenContentViewUnavailable,
    GenerationBindingUnavailable,
)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _views(source_digest: str) -> FrozenContentViewsV1:
    return FrozenContentViewsV1(
        skills=FrozenSkillsViewV1(
            source_digest=source_digest,
            effective_digest=frozen_skills_view_digest(
                skills={},
                load_all_entries=(),
            ),
            skills={},
        )
    )


def _skill_package(name: str, body: str) -> dict[str, JsonValue]:
    return {
        "state": "present",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": f"---\nname: {name}\ndescription: evolved\n---\n{body}",
            }
        ],
    }


class _MutableViewFactory:
    def __init__(self, source_digest: str) -> None:
        self.source_digest = source_digest
        self.calls = 0

    def __call__(self) -> FrozenContentViewsV1:
        self.calls += 1
        return _views(self.source_digest)


def _snapshot_resolver(live_skills_digest: Callable[[], str]) -> SystemSnapshotResolver:
    def skills_digest() -> str:
        frozen = current_frozen_content_views()
        return frozen.skills.source_digest if frozen is not None else live_skills_digest()

    return SystemSnapshotResolver(
        kernel_facts=lambda: {
            "dependency_lock_hash": _digest("lock"),
            "tianshu_version": "test",
        },
        executor_digests=lambda: {},
        skills_digest=skills_digest,
        personas_digest=lambda: _digest("personas"),
        policy_rules_digest=lambda: _digest("policy-rules"),
        provider_profiles_digest=lambda: _digest("provider-profiles"),
    )


def _router(storage, factory: _MutableViewFactory, *, enforced: bool) -> ChallengerRouter:
    resolver = _snapshot_resolver(lambda: factory.source_digest)
    return ChallengerRouter(
        storage,
        snapshot_resolver=lambda: resolver,
        view_factory=factory,
        frozen_content_views=True,
        frozen_content_views_enforced=enforced,
    )


def _prebind(router: ChallengerRouter, storage, *, attempt_id: str) -> None:
    with storage.unit_of_work() as unit_of_work:
        router.prebind_runtime_current(
            unit_of_work,
            memorial_id="memorial-1",
            attempt_id=attempt_id,
        )
        unit_of_work.commit()


def test_off_mode_never_builds_or_changes_ambient_frozen_views(storage) -> None:
    calls = 0

    def view_factory() -> FrozenContentViewsV1:
        nonlocal calls
        calls += 1
        raise AssertionError("off mode must not build frozen views")

    router = ChallengerRouter(storage, view_factory=view_factory)
    _seed_memorial(storage)
    router.assign("memorial-1")
    outer = _views(_digest("outer"))

    with bind_frozen_content_views(outer):
        with router.bind_runtime("memorial-1", attempt_id="attempt-off"):
            assert current_frozen_content_views() is outer
        assert current_frozen_content_views() is outer

    assert calls == 0


def test_shadow_prebound_drift_audits_and_keeps_live_reads(storage, tmp_path) -> None:
    skill_dir = tmp_path / "builtin" / "nested-shadow"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: nested-shadow\ndescription: old\n---\nOLD",
        encoding="utf-8",
    )
    loader = SkillsLoader(tmp_path / "builtin")
    outer = FrozenContentViewsV1(skills=loader.freeze_view())
    factory_calls = 0

    def view_factory() -> FrozenContentViewsV1:
        nonlocal factory_calls
        factory_calls += 1
        assert current_frozen_content_views() is None
        return FrozenContentViewsV1(skills=loader.freeze_view())

    resolver = _snapshot_resolver(loader.content_digest)
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: resolver,
        view_factory=view_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=False,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")
    _prebind(router, storage, attempt_id="attempt-shadow")

    skill_file.write_text(
        "---\nname: nested-shadow\ndescription: new\n---\nNEW",
        encoding="utf-8",
    )
    with bind_frozen_content_views(outer):
        with router.bind_runtime("memorial-1", attempt_id="attempt-shadow"):
            assert current_frozen_content_views() is None
            assert loader.get_skill("nested-shadow")["content"] == "NEW"  # type: ignore[index]
        assert current_frozen_content_views() is outer
        assert loader.get_skill("nested-shadow")["content"] == "OLD"  # type: ignore[index]

    assert factory_calls == 2
    audit = storage._conn.execute(  # noqa: SLF001
        "SELECT outcome, reason_code, subject_kind, metadata_json "
        "FROM system_audit_events WHERE action='skills_view_drift'"
    ).fetchone()
    assert audit is not None
    assert tuple(audit) == ("succeeded", "skills_view_drift", "skills_view", "{}")
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_enforce_prebound_drift_persists_evidence_and_never_runs(storage) -> None:
    source_a = _digest("skills-a")
    source_b = _digest("skills-b")
    factory = _MutableViewFactory(source_a)
    router = _router(storage, factory, enforced=True)
    _seed_memorial(storage)
    router.assign("memorial-1")
    _prebind(router, storage, attempt_id="attempt-enforce")
    factory.source_digest = source_b

    runner_calls: list[AttemptAuthority] = []
    outcomes = []

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        runner_calls.append(authority)
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    class _UnusedRepository:
        def claim(self, **_kwargs: object) -> None:
            raise AssertionError("direct execution must not claim")

        def heartbeat(self, **_kwargs: object) -> bool:
            raise AssertionError("failed binding must not heartbeat")

        def complete(self, **_kwargs: object) -> bool:
            raise AssertionError("injected completer owns completion")

    authority = AttemptAuthority(
        attempt_id="attempt-enforce",
        memorial_id="memorial-1",
        owner_id="worker",
        fencing_token=1,
    )
    dispatcher = RunDispatcher(
        _UnusedRepository(),
        runner,
        owner_id="worker",
        challenger_router=router,
        completer=lambda actual, outcome: outcomes.append((actual, outcome)) or True,
    )

    await dispatcher._execute(authority)  # noqa: SLF001

    assert runner_calls == []
    assert len(outcomes) == 1
    assert outcomes[0][1].failure is not None
    assert outcomes[0][1].failure.code == "skills_view_unavailable"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_enforced_view_restores_outer_context_on_error_and_cancel(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = _router(storage, factory, enforced=True)
    _seed_memorial(storage)
    router.assign("memorial-1")
    outer = _views(_digest("outer"))

    with bind_frozen_content_views(outer):
        with (
            pytest.raises(RuntimeError, match="run failed"),
            router.bind_runtime("memorial-1", attempt_id="attempt-error"),
        ):
            assert current_frozen_content_views() is not outer
            raise RuntimeError("run failed")
        assert current_frozen_content_views() is outer

        entered = asyncio.Event()
        restored: list[FrozenContentViewsV1 | None] = []

        async def cancelled_run() -> None:
            try:
                with router.bind_runtime("memorial-1", attempt_id="attempt-cancel"):
                    assert current_frozen_content_views() is not outer
                    entered.set()
                    await asyncio.Future()
            except asyncio.CancelledError:
                restored.append(current_frozen_content_views())
                raise

        task = asyncio.create_task(cancelled_run())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert restored == [outer]
        assert current_frozen_content_views() is outer


def test_snapshot_disabled_does_not_report_false_skills_drift(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = ChallengerRouter(
        storage,
        view_factory=factory,
        frozen_content_views=True,
        frozen_content_views_enforced=False,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-no-snapshot"):
        assert current_evolution_runtime() is None

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_drift'"
        ).fetchone()[0]
        == 0
    )


def test_view_factory_failure_shadow_audits_and_continues_live(storage) -> None:
    calls = 0

    def failing_factory() -> FrozenContentViewsV1:
        nonlocal calls
        calls += 1
        raise RuntimeError("private failure detail")

    live_digest = _digest("live-skills")
    resolver = _snapshot_resolver(lambda: live_digest)
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: resolver,
        view_factory=failing_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=False,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-shadow-factory"):
        assert current_frozen_content_views() is None

    assert calls == 1
    audit = storage._conn.execute(  # noqa: SLF001
        "SELECT outcome, reason_code, subject_kind, metadata_json "
        "FROM system_audit_events WHERE action='skills_view_binding_failed'"
    ).fetchone()
    assert audit is not None
    assert tuple(audit) == (
        "failed",
        "skills_view_binding_failed",
        "skills_view",
        "{}",
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_system_bindings WHERE attempt_id='attempt-shadow-factory'"
        ).fetchone()[0]
        == 1
    )


def test_view_factory_failure_enforce_persists_audit_without_snapshot_binding(storage) -> None:
    def failing_factory() -> FrozenContentViewsV1:
        raise RuntimeError("private failure detail")

    resolver = _snapshot_resolver(lambda: _digest("live-skills"))
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: resolver,
        view_factory=failing_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")

    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-enforce-factory"),
    ):
        raise AssertionError("failed view factory must not enter runtime")

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_system_bindings"
        ).fetchone()[0]
        == 0
    )


def test_prebind_factory_failure_raises_after_audit_and_outbox_commit(storage) -> None:
    def failing_factory() -> FrozenContentViewsV1:
        raise RuntimeError("private failure detail")

    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: _snapshot_resolver(lambda: _digest("live-skills")),
        view_factory=failing_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")

    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        storage.unit_of_work() as unit_of_work,
    ):
        binding = router.prebind_runtime_current(
            unit_of_work,
            memorial_id="memorial-1",
            attempt_id="attempt-prebind-factory",
        )
        assert binding is None
        assert unit_of_work.has_post_commit_failure is True
        unit_of_work.commit()

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_system_bindings"
        ).fetchone()[0]
        == 0
    )


def test_prebind_drift_raises_after_drift_evidence_commit(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = _router(storage, factory, enforced=True)
    _seed_memorial(storage)
    router.assign("memorial-1")
    _prebind(router, storage, attempt_id="attempt-prebind-drift")
    factory.source_digest = _digest("skills-b")

    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        storage.unit_of_work() as unit_of_work,
    ):
        binding = router.prebind_runtime_current(
            unit_of_work,
            memorial_id="memorial-1",
            attempt_id="attempt-prebind-drift",
        )
        assert binding is not None
        assert unit_of_work.has_post_commit_failure is True
        unit_of_work.commit()

    audit = storage._conn.execute(  # noqa: SLF001
        "SELECT action, outcome FROM system_audit_events WHERE action='skills_view_drift'"
    ).fetchone()
    assert audit is not None
    assert tuple(audit) == ("skills_view_drift", "succeeded")
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )


def test_requirement_environment_drift_is_rejected_before_dispatch(
    storage,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill = builtin / "gated"
    skill.mkdir(parents=True)
    skill.joinpath("SKILL.md").write_text(
        "---\ndescription: gated\nmetadata:\n  openclaw:\n"
        "    requires:\n      env: [TIANSHU_P7_BIND_ENV]\n---\ngated-body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(builtin)
    resolver = _snapshot_resolver(loader.content_digest)
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: resolver,
        view_factory=lambda: FrozenContentViewsV1(skills=loader.freeze_view()),
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")
    monkeypatch.setenv("TIANSHU_P7_BIND_ENV", "enabled")
    _prebind(router, storage, attempt_id="attempt-requirement-drift")

    monkeypatch.delenv("TIANSHU_P7_BIND_ENV")
    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-requirement-drift"),
    ):
        raise AssertionError("requirements drift must not enter runtime")

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_drift'"
        ).fetchone()[0]
        == 1
    )


def _insert_snapshot_without_skills(storage, *, attempt_id: str) -> None:
    components = {"kernel": _digest("kernel-only")}
    snapshot = SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )
    with storage.unit_of_work() as unit_of_work:
        SystemSnapshotRepository().insert_binding(
            unit_of_work.connection,
            memorial_id="memorial-1",
            attempt_id=attempt_id,
            snapshot=snapshot,
        )
        unit_of_work.commit()


def test_shadow_snapshot_without_skills_identity_skips_comparison(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = _router(storage, factory, enforced=False)
    _seed_memorial(storage)
    router.assign("memorial-1")
    _insert_snapshot_without_skills(storage, attempt_id="attempt-shadow-missing-skills")

    with router.bind_runtime("memorial-1", attempt_id="attempt-shadow-missing-skills"):
        assert current_frozen_content_views() is None

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events "
            "WHERE action IN ('skills_view_drift', 'skills_view_binding_failed')"
        ).fetchone()[0]
        == 0
    )


def test_enforce_snapshot_without_skills_identity_is_binding_failure(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = _router(storage, factory, enforced=True)
    _seed_memorial(storage)
    router.assign("memorial-1")
    _insert_snapshot_without_skills(storage, attempt_id="attempt-enforce-missing-skills")

    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-enforce-missing-skills"),
    ):
        raise AssertionError("missing skills identity must not enter runtime")

    rows = storage._conn.execute(  # noqa: SLF001
        "SELECT action, outcome FROM system_audit_events"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("skills_view_binding_failed", "failed")]


def test_enforce_without_snapshot_identity_fails_closed_without_false_drift(storage) -> None:
    factory = _MutableViewFactory(_digest("skills-a"))
    router = ChallengerRouter(
        storage,
        view_factory=factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    _seed_memorial(storage)
    router.assign("memorial-1")

    with (
        pytest.raises(FrozenContentViewUnavailable, match="skills_view_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-missing-snapshot"),
    ):
        raise AssertionError("enforced view without snapshot identity must not run")

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_drift'"
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )


def test_enforce_corrupt_snapshot_binding_fails_closed(storage) -> None:
    class _CorruptSnapshotRepository(SystemSnapshotRepository):
        def get_binding(self, *_args: object, **_kwargs: object):
            raise SystemSnapshotRepositoryDecodeError("corrupt snapshot binding")

    factory = _MutableViewFactory(_digest("skills-a"))
    router = _router(storage, factory, enforced=True)
    _seed_memorial(storage)
    router.assign("memorial-1")
    router._snapshot_repository = _CorruptSnapshotRepository()  # noqa: SLF001

    with (
        pytest.raises(
            GenerationBindingUnavailable,
            match="generation_binding_unavailable",
        ),
        router.bind_runtime("memorial-1", attempt_id="attempt-corrupt-snapshot"),
    ):
        raise AssertionError("corrupt snapshot binding must not enter runtime")


def test_governed_freeze_sees_effective_provisional_overlay_once(storage, tmp_path) -> None:
    builtin_skill = tmp_path / "builtin" / "review-helper"
    builtin_skill.mkdir(parents=True)
    (builtin_skill / "SKILL.md").write_text(
        "---\nname: review-helper\ndescription: live\n---\nLIVE",
        encoding="utf-8",
    )
    champion = _skill_package("review-helper", "CHAMPION")
    challenger = _skill_package("review-helper", "CHALLENGER")
    payloads = {
        canonical_sha256(champion): champion,
        canonical_sha256(challenger): challenger,
    }

    def resolve(_connection, selected_ref, _overlay):
        return payloads[selected_ref.artifact_digest]

    loader = SkillsLoader(tmp_path / "builtin")
    captured = []
    captured_views = []

    def view_factory() -> FrozenContentViewsV1:
        captured.append(current_evolution_runtime())
        views = FrozenContentViewsV1(skills=loader.freeze_view())
        captured_views.append(views)
        return views

    resolver = _snapshot_resolver(loader.content_digest)
    _seed_canary(
        storage,
        base_payload=champion,
        candidate_payload=challenger,
    )
    _seed_memorial(storage)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"fixed-secret",
        bucket_calculator=lambda *_args: 0,
        payload_resolver=resolve,
        snapshot_resolver=lambda: resolver,
        view_factory=view_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-governed") as runtime:
        assert runtime is not None
        assert len(captured) == 1
        provisional = captured[0]
        assert provisional is not None
        assert provisional.overlay is not None
        assert provisional.overlay.kind is CandidateKind.SKILL
        assert provisional.overlay.subject_key == "skill:review-helper"
        frozen = current_frozen_content_views()
        assert frozen is captured_views[0]
        assert frozen.skills.skills["review-helper"].content == "CHALLENGER"
        assert runtime.system_snapshot is not None
        assert runtime.system_snapshot.components["skills"] == frozen.skills.source_digest

    assert loader.get_skill("review-helper")["content"] == "LIVE"  # type: ignore[index]


def test_multi_subject_freeze_captures_effective_and_absent_skills_once(
    storage,
    tmp_path,
) -> None:
    foo = _skill_package("foo", "FOO-EVOLVED")
    absent: dict[str, JsonValue] = {"state": "absent", "members": []}
    payloads = _seed_canaries(
        storage,
        ("candidate-foo", "skill:foo", "seed-foo"),
        ("candidate-bar", "skill:bar", "seed-bar"),
        challenger_payloads={"skill:foo": foo, "skill:bar": absent},
    )
    builtin = tmp_path / "builtin"
    for name in ("bar", "foo"):
        directory = builtin / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: live {name}\n---\n{name.upper()}-LIVE",
            encoding="utf-8",
        )
    loader = SkillsLoader(builtin)
    factory_calls = 0

    def resolve(_connection, selected_ref, _overlay):
        return payloads[selected_ref.artifact_digest]

    def view_factory() -> FrozenContentViewsV1:
        nonlocal factory_calls
        factory_calls += 1
        runtime = current_evolution_runtime()
        assert runtime is not None
        assert len(runtime.assignments) == 2
        return FrozenContentViewsV1(skills=loader.freeze_view())

    resolver = _snapshot_resolver(loader.content_digest)
    _seed_memorial(storage)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"p7-test-secret",
        bucket_calculator=lambda *_args: 0,
        payload_resolver=resolve,
        snapshot_resolver=lambda: resolver,
        view_factory=view_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-multi") as runtime:
        assert runtime is not None
        frozen = current_frozen_content_views()
        assert frozen is not None
        assert frozen.skills.skills["foo"].content == "FOO-EVOLVED"
        assert "bar" not in frozen.skills.skills
        assert runtime.system_snapshot is not None
        assert runtime.system_snapshot.components["skills"] == frozen.skills.source_digest

    assert factory_calls == 1
    assert loader.get_skill("foo")["content"] == "FOO-LIVE"  # type: ignore[index]
    assert loader.get_skill("bar")["content"] == "BAR-LIVE"  # type: ignore[index]


def test_legacy_frozen_run_clears_and_restores_outer_governed_runtime(storage) -> None:
    _seed_canary(storage)
    _seed_memorial(storage, memorial_id="memorial-governed")
    governed = _governed_router(storage, bucket_calculator=lambda *_args: 0)
    governed.assign("memorial-governed")

    captured = []

    def legacy_view_factory() -> FrozenContentViewsV1:
        captured.append(current_evolution_runtime())
        return _views(_digest("legacy-skills"))

    _seed_memorial(storage, memorial_id="memorial-legacy")
    legacy = ChallengerRouter(
        storage,
        routing_enabled=False,
        view_factory=legacy_view_factory,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    legacy.assign("memorial-legacy")

    with governed.bind_runtime("memorial-governed") as outer:
        assert outer is not None
        with legacy.bind_runtime("memorial-legacy") as runtime:
            assert runtime is None
            assert current_evolution_runtime() is None
        assert current_evolution_runtime() is outer

    assert captured == [None]
