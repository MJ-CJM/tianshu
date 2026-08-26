from __future__ import annotations

from collections.abc import Iterator

import pytest

import tianshu.evolution.process_snapshot as process_snapshot_module
from tianshu.evolution.process_snapshot import (
    ProcessSnapshotBootstrap,
    ProcessSnapshotDriftError,
    ProcessSnapshotTargetUnavailable,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import (
    PROCESS_GENERATION_SCOPE,
    RuntimeGenerationState,
)
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.generation_repo import (
    GenerationActivationResult,
    GenerationRepository,
    GenerationRepositoryDecodeError,
)
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository


class _Resolver:
    def __init__(self, snapshot: SystemSnapshotV1) -> None:
        self.snapshot = snapshot

    def resolve(self) -> SystemSnapshotV1:
        return self.snapshot


class _FailingActivationRepository(GenerationRepository):
    def activate(self, *args: object, **kwargs: object) -> GenerationActivationResult:
        del args, kwargs
        raise RuntimeError("activation fault")


def _snapshot(**components: str) -> SystemSnapshotV1:
    material = dict(components)
    return SystemSnapshotV1(
        components=material,
        digest=canonical_sha256(material),
    )


def _ids(*characters: str) -> Iterator[str]:
    return iter(f"rg-{character * 32}" for character in characters)


def _bootstrap(
    storage,
    snapshot: SystemSnapshotV1,
    *,
    ids: Iterator[str],
    strict: bool = False,
    target: str | None = None,
    repository: GenerationRepository | None = None,
) -> ProcessSnapshotBootstrap:
    return ProcessSnapshotBootstrap(
        unit_of_work_factory=storage.unit_of_work,
        resolver=_Resolver(snapshot),
        strict=strict,
        target_digest=target,
        repository=repository,
        generation_id_factory=lambda: next(ids),
    )


def _pointer(storage):
    with storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    return pointer


def _generations(storage):
    with storage.unit_of_work() as unit_of_work:
        generations = GenerationRepository().list_by_scope(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    return generations


def test_first_boot_and_one_hundred_identical_restarts_are_idempotent(storage) -> None:
    snapshot = _snapshot(kernel="a" * 64, skills="b" * 64)
    ids = _ids("1", "2")
    bootstrap = _bootstrap(storage, snapshot, ids=ids)

    first = bootstrap.initialize()
    for _ in range(100):
        replay = bootstrap.initialize()
        assert replay.action == "unchanged"
        assert replay.active_generation_id == first.active_generation_id

    generations = _generations(storage)
    assert len(generations) == 1
    assert generations[0].state is RuntimeGenerationState.ACTIVE
    pointer = _pointer(storage)
    assert pointer is not None
    assert pointer.active_generation_id == pointer.last_good_generation_id
    with storage.unit_of_work() as unit_of_work:
        release = GenerationRepository().get_process_release(
            unit_of_work.connection,
            release_digest=snapshot.digest,
        )
        journal = GenerationRepository().list_journal(
            unit_of_work.connection,
            generation_id=generations[0].generation_id,
        )
        unit_of_work.commit()
    assert release == snapshot
    assert tuple(entry.to_state for entry in journal) == (
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
        RuntimeGenerationState.ACTIVE,
    )


def test_a_to_b_to_c_retains_only_active_and_last_good(storage) -> None:
    ids = _ids("1", "2", "3")
    snapshot_a = _snapshot(kernel="a" * 64)
    snapshot_b = _snapshot(kernel="b" * 64)
    snapshot_c = _snapshot(kernel="c" * 64)

    report_a = _bootstrap(storage, snapshot_a, ids=ids).initialize()
    report_b = _bootstrap(storage, snapshot_b, ids=ids).initialize()
    states_after_b = {item.generation_id: item.state for item in _generations(storage)}
    assert states_after_b[report_a.active_generation_id] is RuntimeGenerationState.DRAINING
    assert states_after_b[report_b.active_generation_id] is RuntimeGenerationState.ACTIVE

    report_c = _bootstrap(storage, snapshot_c, ids=ids).initialize()
    states_after_c = {item.generation_id: item.state for item in _generations(storage)}
    assert states_after_c[report_a.active_generation_id] is RuntimeGenerationState.DISPOSED
    assert states_after_c[report_b.active_generation_id] is RuntimeGenerationState.DRAINING
    assert states_after_c[report_c.active_generation_id] is RuntimeGenerationState.ACTIVE
    pointer = _pointer(storage)
    assert pointer is not None
    assert pointer.active_generation_id == report_c.active_generation_id
    assert pointer.last_good_generation_id == report_b.active_generation_id


def test_returning_to_retained_last_good_uses_repository_rollback(storage) -> None:
    ids = _ids("1", "2", "3")
    snapshot_a = _snapshot(kernel="a" * 64)
    snapshot_b = _snapshot(kernel="b" * 64)
    report_a = _bootstrap(storage, snapshot_a, ids=ids).initialize()
    report_b = _bootstrap(storage, snapshot_b, ids=ids).initialize()

    rolled_back = _bootstrap(storage, snapshot_a, ids=ids).initialize()

    assert rolled_back.action == "rolled_back"
    assert rolled_back.active_generation_id == report_a.active_generation_id
    assert rolled_back.last_good_generation_id == report_a.active_generation_id
    states = {item.generation_id: item.state for item in _generations(storage)}
    assert states[report_a.active_generation_id] is RuntimeGenerationState.ACTIVE
    assert states[report_b.active_generation_id] is RuntimeGenerationState.DISPOSED


def test_strict_drift_reports_component_names_and_writes_nothing(storage) -> None:
    ids = _ids("1", "2")
    snapshot_a = _snapshot(kernel="a" * 64, provider_profiles="1" * 64)
    snapshot_b = _snapshot(kernel="a" * 64, provider_profiles="2" * 64)
    first = _bootstrap(storage, snapshot_a, ids=ids).initialize()

    with pytest.raises(ProcessSnapshotDriftError) as raised:
        _bootstrap(storage, snapshot_b, ids=ids, strict=True).initialize()

    assert raised.value.target_digest == snapshot_a.digest
    assert raised.value.actual_digest == snapshot_b.digest
    assert raised.value.last_good_digest == snapshot_a.digest
    assert raised.value.differing_components == ("provider_profiles",)
    assert _pointer(storage).active_generation_id == first.active_generation_id
    assert len(_generations(storage)) == 1
    with storage.unit_of_work() as unit_of_work:
        assert (
            SystemSnapshotRepository().get_snapshot(
                unit_of_work.connection,
                snapshot_b.digest,
            )
            is None
        )
        unit_of_work.commit()


def test_non_strict_drift_audits_and_advances_actual_snapshot(storage) -> None:
    ids = _ids("1", "2")
    snapshot_a = _snapshot(kernel="a" * 64, provider_profiles="1" * 64)
    snapshot_b = _snapshot(kernel="a" * 64, provider_profiles="2" * 64)
    _bootstrap(storage, snapshot_a, ids=ids).initialize()

    report = _bootstrap(storage, snapshot_b, ids=ids).initialize()

    assert report.action == "advanced"
    assert report.drifted is True
    assert report.target_digest == snapshot_a.digest
    assert report.differing_components == ("provider_profiles",)
    assert _pointer(storage).active_generation_id == report.active_generation_id
    with storage.unit_of_work() as unit_of_work:
        actions = unit_of_work.connection.execute(
            "SELECT action, subject_digest FROM system_audit_events ORDER BY sequence"
        ).fetchall()
        unit_of_work.commit()
    assert [(row["action"], row["subject_digest"]) for row in actions] == [
        ("system_snapshot_drift", snapshot_b.digest)
    ]


def test_non_strict_drift_audit_failure_is_best_effort_and_atomic(
    storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _ids("1", "2")
    snapshot_a = _snapshot(kernel="a" * 64, provider_profiles="1" * 64)
    snapshot_b = _snapshot(kernel="a" * 64, provider_profiles="2" * 64)
    _bootstrap(storage, snapshot_a, ids=ids).initialize()
    append_audit = process_snapshot_module._append_system_audit_unlocked

    def fail_after_audit_write(connection, request):
        append_audit(connection, request)
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(
        process_snapshot_module,
        "_append_system_audit_unlocked",
        fail_after_audit_write,
    )

    report = _bootstrap(storage, snapshot_b, ids=ids).initialize()

    assert report.action == "advanced"
    assert report.drifted is True
    pointer = _pointer(storage)
    assert pointer is not None
    assert pointer.active_generation_id == report.active_generation_id
    with storage.unit_of_work() as unit_of_work:
        assert (
            SystemSnapshotRepository().get_snapshot(
                unit_of_work.connection,
                snapshot_b.digest,
            )
            == snapshot_b
        )
        audit_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM system_audit_events"
        ).fetchone()[0]
        unit_of_work.commit()
    assert audit_count == 0


def test_unknown_explicit_target_fails_without_inventing_component_diff(storage) -> None:
    current = _snapshot(kernel="a" * 64)
    unknown = "f" * 64

    with pytest.raises(ProcessSnapshotTargetUnavailable) as raised:
        _bootstrap(storage, current, ids=_ids("1"), target=unknown).initialize()

    assert raised.value.target_digest == unknown
    assert _pointer(storage) is None
    assert _generations(storage) == ()


def test_corrupt_active_pointer_state_fails_closed_without_bootstrap_writes(storage) -> None:
    snapshot_a = _snapshot(kernel="a" * 64)
    snapshot_b = _snapshot(kernel="b" * 64)
    report = _bootstrap(storage, snapshot_a, ids=_ids("1", "2")).initialize()
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            "UPDATE runtime_generations SET state='draining' WHERE generation_id=?",
            (report.active_generation_id,),
        )
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in (
                "system_snapshots",
                "runtime_generation_releases",
                "runtime_generations",
                "runtime_generation_journal",
            )
        }
        unit_of_work.commit()

    with pytest.raises(
        GenerationRepositoryDecodeError,
        match="process active generation is not active",
    ):
        _bootstrap(storage, snapshot_b, ids=_ids("2")).initialize()

    with storage.unit_of_work() as unit_of_work:
        after = {
            table: unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608
            ).fetchone()[0]
            for table in before
        }
        unit_of_work.commit()
    assert after == before


def test_corrupt_active_journal_fails_closed_without_bootstrap_writes(storage) -> None:
    snapshot = _snapshot(kernel="a" * 64)
    report = _bootstrap(storage, snapshot, ids=_ids("1", "2")).initialize()
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute("DROP TRIGGER runtime_generation_journal_no_delete")
        connection.execute(
            """
            DELETE FROM runtime_generation_journal
            WHERE generation_id = ? AND generation_version = 2
            """,
            (report.active_generation_id,),
        )
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in (
                "system_snapshots",
                "runtime_generation_releases",
                "runtime_generations",
                "runtime_generation_journal",
                "generation_pointers",
                "system_audit_events",
            )
        }
        unit_of_work.commit()

    with pytest.raises(
        GenerationRepositoryDecodeError,
        match="journal is not contiguous",
    ):
        _bootstrap(storage, snapshot, ids=_ids("2")).initialize()

    with storage.unit_of_work() as unit_of_work:
        after = {
            table: unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608
            ).fetchone()[0]
            for table in before
        }
        unit_of_work.commit()
    assert after == before


def test_activation_fault_rolls_back_snapshot_release_generation_and_audit(storage) -> None:
    snapshot = _snapshot(kernel="a" * 64)
    repository = _FailingActivationRepository()

    with pytest.raises(RuntimeError, match="activation fault"):
        _bootstrap(
            storage,
            snapshot,
            ids=_ids("1"),
            repository=repository,
        ).initialize()

    with storage.unit_of_work() as unit_of_work:
        counts = {
            table: unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed test table names
            ).fetchone()[0]
            for table in (
                "system_snapshots",
                "runtime_generation_releases",
                "runtime_generations",
                "runtime_generation_journal",
                "generation_pointers",
                "system_audit_events",
            )
        }
        unit_of_work.commit()
    assert set(counts.values()) == {0}
