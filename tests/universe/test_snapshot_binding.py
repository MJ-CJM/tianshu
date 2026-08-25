"""Runtime SystemSnapshot binding at the managed execution boundary."""

from __future__ import annotations

from hashlib import sha256
from typing import cast

from tests.universe.test_challenger_routing import (
    _router,
    _seed_canary,
    _seed_memorial,
)
from tianshu.evolution.runtime_context import (
    current_evolution_runtime,
    current_run_binding,
)
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.run_assignment import RunAssignmentV1
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository
from tianshu.universe.router import ChallengerRouter


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _snapshot_resolver() -> SystemSnapshotResolver:
    return SystemSnapshotResolver(
        kernel_facts=lambda: {
            "dependency_lock_hash": _digest("lock"),
            "tianshu_version": "test",
        },
        executor_digests=lambda: {"test": _digest("executor")},
        skills_digest=lambda: _digest("skills"),
        personas_digest=lambda: _digest("personas"),
        policy_rules_digest=lambda: _digest("policy-rules"),
        provider_profiles_digest=lambda: _digest("provider-profiles"),
    )


def test_legacy_run_persists_and_binds_snapshot_before_yield(storage) -> None:
    _seed_memorial(storage)
    resolver = _snapshot_resolver()
    router = ChallengerRouter(storage, snapshot_resolver=lambda: resolver)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-1") as runtime:
        assert runtime is None
        assert current_evolution_runtime() is None
        binding_context = current_run_binding()
        assert binding_context is not None
        assert binding_context.attempt_id == "attempt-1"
        assert binding_context.generation_ids == ()
        assert binding_context.system_snapshot == resolver.resolve()
        with storage.unit_of_work() as unit_of_work:
            durable = SystemSnapshotRepository().get_binding(
                unit_of_work.connection,
                memorial_id="memorial-1",
                attempt_id="attempt-1",
            )
            unit_of_work.commit()
        assert durable is not None
        assert durable.snapshot is not None
        assert durable.snapshot == binding_context.system_snapshot

    assert current_run_binding() is None
    assert current_evolution_runtime() is None


def test_governed_run_shares_one_snapshot_across_both_runtime_contexts(storage) -> None:
    _seed_canary(storage)
    _seed_memorial(storage)
    resolver = _snapshot_resolver()
    router = _router(
        storage,
        bucket_calculator=lambda *_args: 0,
        snapshot_resolver=lambda: resolver,
    )
    assignment = router.assign("memorial-1")
    assert isinstance(assignment, RunAssignmentV1)

    with router.bind_runtime("memorial-1", attempt_id="attempt-governed") as runtime:
        assert runtime is not None
        binding_context = current_run_binding()
        assert binding_context is not None
        assert current_evolution_runtime() is runtime
        assert runtime.system_snapshot is binding_context.system_snapshot
        assert runtime.system_snapshot is not None
        assert "evolution_overlay" in runtime.system_snapshot.components

    assert current_run_binding() is None
    assert current_evolution_runtime() is None


def test_disabled_or_unbound_snapshot_path_preserves_previous_context_behavior(storage) -> None:
    _seed_memorial(storage)
    getter_calls = 0

    def resolver_getter() -> SystemSnapshotResolver | None:
        nonlocal getter_calls
        getter_calls += 1
        return None

    router = ChallengerRouter(storage, snapshot_resolver=resolver_getter)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-disabled"):
        assert current_run_binding() is None
    assert getter_calls == 1

    with router.bind_runtime("memorial-1"):
        assert current_run_binding() is None
    assert getter_calls == 1
    assert storage._conn.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 0  # noqa: SLF001


def test_resolver_failure_is_audited_without_binding_or_runtime_context(storage) -> None:
    _seed_memorial(storage)

    class _FailingResolver:
        def resolve_for_run(self, *_args: object) -> None:
            raise RuntimeError("/private/secret/api-key")

    resolver = cast(SystemSnapshotResolver, _FailingResolver())
    router = ChallengerRouter(storage, snapshot_resolver=lambda: resolver)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-failed"):
        assert current_run_binding() is None

    assert storage._conn.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 0  # noqa: SLF001
    audit = storage._conn.execute(  # noqa: SLF001
        "SELECT action, reason_code, metadata_json FROM system_audit_events "
        "WHERE action='system_snapshot_binding_failed'"
    ).fetchone()
    assert audit is not None
    assert tuple(audit) == (
        "system_snapshot_binding_failed",
        "system_snapshot_binding_failed",
        "{}",
    )


def test_repository_failure_rolls_back_snapshot_and_preserves_governed_runtime(storage) -> None:
    _seed_canary(storage)
    _seed_memorial(storage)
    resolver = _snapshot_resolver()
    router = _router(
        storage,
        bucket_calculator=lambda *_args: 0,
        snapshot_resolver=lambda: resolver,
    )
    router.assign("memorial-1")
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """
            CREATE TRIGGER reject_runtime_system_binding
            BEFORE INSERT ON run_system_bindings BEGIN
                SELECT RAISE(ABORT, 'injected binding failure');
            END
            """
        )
        unit_of_work.commit()

    with router.bind_runtime("memorial-1", attempt_id="attempt-repo-failed") as runtime:
        assert runtime is not None
        assert runtime.system_snapshot is None
        assert current_evolution_runtime() is runtime
        assert current_run_binding() is None

    assert storage._conn.execute("SELECT COUNT(*) FROM system_snapshots").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 0  # noqa: SLF001
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='system_snapshot_binding_failed'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='system_snapshot_binding_failed'"
        ).fetchone()[0]
        == 1
    )
