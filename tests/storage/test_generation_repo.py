"""Runtime generation repository lifecycle, integrity, and retention matrix."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import (
    GenerationRepository,
    GenerationRepositoryConflict,
    GenerationRepositoryDecodeError,
)
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_SCOPE = "executor:keqing:pi"
_NOW = datetime(2026, 8, 26, tzinfo=UTC)
_SNAPSHOT = "f" * 64


@pytest.fixture
def connection() -> sqlite3.Connection:
    value = sqlite3.connect(":memory:")
    value.row_factory = sqlite3.Row
    value.execute("PRAGMA foreign_keys=ON")
    apply_migrations(value, MIGRATIONS)
    value.commit()
    yield value
    value.close()


def _release(scope: str = _SCOPE, *, marker: str = "a") -> RuntimeReleaseV1:
    manifest = {
        "schema_version": "1",
        "manifest_id": f"pi-{marker}",
        "capabilities": [{"capability": "pause", "state": "enforced"}],
    }
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": scope,
        "manifest": manifest,
        "manifest_hash": canonical_sha256(manifest),
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": f"/opt/tianshu/bin/pi-{marker}",
        "binary_digest": marker * 64,
        "package_name": "@earendil-works/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": chr(ord(marker) + 1) * 64,
        "single_argv_shape": "pi-single-v1",
        "session_argv_shape": "pi-session-v1",
        "pi_wire_version": 3,
        "materializer_id": "pi-release",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def _staged(
    release: RuntimeReleaseV1,
    marker: str,
    *,
    seconds: int = 0,
) -> RuntimeGenerationV1:
    created_at = _NOW + timedelta(seconds=seconds)
    return RuntimeGenerationV1(
        generation_id="rg-" + marker * 32,
        scope=release.scope,
        release_digest=release.release_digest,
        state=RuntimeGenerationState.STAGED,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _insert_release_and_ready(
    connection: sqlite3.Connection,
    repository: GenerationRepository,
    release: RuntimeReleaseV1,
    marker: str,
    *,
    seconds: int,
) -> RuntimeGenerationV1:
    repository.insert_release(connection, release, first_seen_at=_NOW)
    generation = repository.insert_staged(
        connection,
        _staged(release, marker, seconds=seconds),
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=generation.scope,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=1,
        updated_at=_NOW + timedelta(seconds=seconds + 1),
    )
    return repository.transition_pre_activation(
        connection,
        scope=generation.scope,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=2,
        updated_at=_NOW + timedelta(seconds=seconds + 2),
    )


def _insert_edict_and_memorial(
    connection: sqlite3.Connection,
    *,
    edict_id: str,
    memorial_id: str,
    status: str,
    schedule: dict[str, object],
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO edicts (id, goal, status, created_at, schedule_json)
        VALUES (?, 'generation retention', ?, ?, ?)
        """,
        (
            edict_id,
            status,
            created_at.isoformat(),
            json.dumps(schedule, separators=(",", ":"), sort_keys=True),
        ),
    )
    connection.execute(
        """
        INSERT INTO memorials (id, edict_id, status, created_at, dag_node_id)
        VALUES (?, ?, 'pending', ?, NULL)
        """,
        (memorial_id, edict_id, created_at.isoformat()),
    )


def _insert_snapshot_binding(
    connection: sqlite3.Connection,
    *,
    memorial_id: str,
    attempt_id: str,
    generation_ids: tuple[str, ...],
    created_at: datetime,
) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM system_snapshots WHERE snapshot_digest=?",
            (_SNAPSHOT,),
        ).fetchone()
        is None
    ):
        connection.execute(
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            (_SNAPSHOT, _NOW.isoformat()),
        )
    connection.execute(
        """
        INSERT INTO run_system_bindings (
            memorial_id, attempt_id, snapshot_digest, generation_ids_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            memorial_id,
            attempt_id,
            _SNAPSHOT,
            json.dumps(list(generation_ids), separators=(",", ":")),
            created_at.isoformat(),
        ),
    )


def _insert_generation_binding(
    connection: sqlite3.Connection,
    *,
    memorial_id: str,
    attempt_id: str,
    generation_ids: tuple[str, ...],
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO run_generation_bindings (
            memorial_id, attempt_id, state, generation_ids_json, created_at
        ) VALUES (?, ?, 'bound', ?, ?)
        """,
        (
            memorial_id,
            attempt_id,
            json.dumps(list(generation_ids), separators=(",", ":")),
            created_at.isoformat(),
        ),
    )


def _insert_claimable_attempt(
    connection: sqlite3.Connection,
    *,
    memorial_id: str,
    attempt_id: str,
    created_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO execution_attempts (
            attempt_id, schema_version, memorial_id, attempt_no, status,
            available_at, max_attempts, version, created_at, updated_at
        ) VALUES (?, 1, ?, 1, 'claimable', ?, 1, 1, ?, ?)
        """,
        (
            attempt_id,
            memorial_id,
            created_at.isoformat(),
            created_at.isoformat(),
            created_at.isoformat(),
        ),
    )


def test_writes_require_transaction_and_exact_replay_preserves_identity(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()

    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        repository.insert_release(connection, release)

    connection.execute("BEGIN IMMEDIATE")
    repository.insert_release(connection, release, first_seen_at=_NOW)
    generation = _staged(release, "1")
    first = repository.insert_staged(connection, generation)
    assert repository.insert_release(connection, release) == release
    assert repository.insert_staged(connection, generation) == first
    drifted = generation.model_copy(update={"updated_at": _NOW + timedelta(seconds=1)})
    with pytest.raises(GenerationRepositoryConflict, match="identity is immutable"):
        repository.insert_staged(connection, drifted)
    connection.rollback()


def test_record_retired_atomically_appends_redacted_audit_and_outbox(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    connection.execute("BEGIN IMMEDIATE")
    _insert_edict_and_memorial(
        connection,
        edict_id="edict-retired",
        memorial_id="memorial-retired",
        status="closed",
        schedule={},
        created_at=_NOW,
    )

    assert repository.record_retired(
        connection,
        memorial_id="memorial-retired",
        attempt_id="attempt-retired",
    )

    audit = connection.execute(
        """
        SELECT correlation_id, action, outcome, reason_code, subject_kind,
               subject_digest, metadata_json
        FROM system_audit_events
        """
    ).fetchone()
    event = connection.execute(
        """
        SELECT event_type, memorial_id, producer, correlation_id, payload_json
        FROM outbox_events WHERE event_type='generation_retired'
        """
    ).fetchone()
    assert audit is not None
    assert event is not None
    assert audit["correlation_id"] == event["correlation_id"]
    assert audit["action"] == audit["reason_code"] == "generation_retired"
    assert audit["outcome"] == "failed"
    assert audit["subject_kind"] == "runtime_generation_binding"
    assert len(audit["subject_digest"]) == 64
    assert audit["metadata_json"] == "{}"
    assert event["memorial_id"] == "memorial-retired"
    assert event["producer"] == "generation_repository"
    assert json.loads(event["payload_json"]) == {
        "attempt_id": "attempt-retired",
        "correlation_id": event["correlation_id"],
    }
    connection.rollback()


def test_record_retired_fault_rolls_back_only_its_best_effort_savepoint(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        CREATE TEMP TRIGGER reject_generation_retired_audit
        BEFORE INSERT ON system_audit_events
        WHEN NEW.action = 'generation_retired'
        BEGIN
            SELECT RAISE(ABORT, 'injected generation audit failure');
        END
        """
    )

    assert not repository.record_retired(
        connection,
        memorial_id="memorial-retired",
        attempt_id="attempt-retired",
    )
    assert connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM system_audit_events").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0
    connection.execute(
        "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
        (_SNAPSHOT, _NOW.isoformat()),
    )
    assert connection.execute("SELECT COUNT(*) FROM system_snapshots").fetchone()[0] == 1
    connection.rollback()


def test_generation_and_journal_writes_rollback_together_on_sql_fault(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    repository.insert_release(connection, release, first_seen_at=_NOW)
    generation = _staged(release, "1")
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_journal_insert
        BEFORE INSERT ON runtime_generation_journal BEGIN
            SELECT RAISE(ABORT, 'injected journal failure');
        END
        """
    )
    with pytest.raises(GenerationRepositoryConflict, match="journal conflict"):
        repository.insert_staged(connection, generation)
    assert (
        repository.get_generation(
            connection,
            scope=generation.scope,
            generation_id=generation.generation_id,
        )
        is None
    )

    connection.execute("DROP TRIGGER fail_generation_journal_insert")
    repository.insert_staged(connection, generation)
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_journal_insert
        BEFORE INSERT ON runtime_generation_journal BEGIN
            SELECT RAISE(ABORT, 'injected journal failure');
        END
        """
    )
    with pytest.raises(GenerationRepositoryConflict, match="journal conflict"):
        repository.transition_pre_activation(
            connection,
            scope=generation.scope,
            generation_id=generation.generation_id,
            target_state=RuntimeGenerationState.WARMING,
            expected_version=1,
            updated_at=_NOW + timedelta(seconds=1),
        )
    durable = repository.get_generation(
        connection,
        scope=generation.scope,
        generation_id=generation.generation_id,
    )
    assert durable == generation
    assert len(repository.list_journal(connection, generation_id=generation.generation_id)) == 1
    connection.rollback()


def test_activate_then_rollback_is_one_pointer_cas_and_preserves_last_good(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)

    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    assert initial.pointer.active_generation_id == first.generation_id
    assert initial.pointer.last_good_generation_id == first.generation_id
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    assert switched.draining is not None
    assert switched.draining.generation_id == first.generation_id
    assert switched.pointer.active_generation_id == second.generation_id
    assert switched.pointer.last_good_generation_id == first.generation_id

    rolled_back = repository.rollback_to_last_good(
        connection,
        scope=_SCOPE,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=14),
    )
    assert rolled_back.activated.generation_id == first.generation_id
    assert rolled_back.activated.state is RuntimeGenerationState.ACTIVE
    assert rolled_back.draining.generation_id == second.generation_id
    assert rolled_back.pointer.active_generation_id == first.generation_id
    assert rolled_back.pointer.last_good_generation_id == first.generation_id
    assert rolled_back.pointer.version == 3
    assert [
        entry.to_state
        for entry in repository.list_journal(connection, generation_id=first.generation_id)
    ] == [
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
        RuntimeGenerationState.ACTIVE,
        RuntimeGenerationState.DRAINING,
        RuntimeGenerationState.ACTIVE,
    ]
    connection.rollback()


def test_activate_and_rollback_sql_faults_restore_every_row_to_pre_call_state(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_pointer_update
        BEFORE UPDATE ON generation_pointers BEGIN
            SELECT RAISE(ABORT, 'injected pointer failure');
        END
        """
    )

    with pytest.raises(GenerationRepositoryConflict, match="atomic generation activation"):
        repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=second.generation_id,
            expected_generation_version=second.version,
            expected_pointer_version=initial.pointer.version,
            updated_at=_NOW + timedelta(seconds=13),
        )
    assert (
        repository.get_generation(connection, scope=_SCOPE, generation_id=first.generation_id).state
        is RuntimeGenerationState.ACTIVE
    )
    assert (
        repository.get_generation(
            connection, scope=_SCOPE, generation_id=second.generation_id
        ).state
        is RuntimeGenerationState.READY
    )
    assert repository.get_pointer(connection, scope=_SCOPE) == initial.pointer
    assert len(repository.list_journal(connection, generation_id=first.generation_id)) == 4
    assert len(repository.list_journal(connection, generation_id=second.generation_id)) == 3

    connection.execute("DROP TRIGGER fail_generation_pointer_update")
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_pointer_update
        BEFORE UPDATE ON generation_pointers BEGIN
            SELECT RAISE(ABORT, 'injected pointer failure');
        END
        """
    )
    with pytest.raises(GenerationRepositoryConflict, match="atomic generation rollback"):
        repository.rollback_to_last_good(
            connection,
            scope=_SCOPE,
            expected_pointer_version=switched.pointer.version,
            updated_at=_NOW + timedelta(seconds=14),
        )
    assert (
        repository.get_generation(connection, scope=_SCOPE, generation_id=first.generation_id).state
        is RuntimeGenerationState.DRAINING
    )
    assert (
        repository.get_generation(
            connection, scope=_SCOPE, generation_id=second.generation_id
        ).state
        is RuntimeGenerationState.ACTIVE
    )
    assert repository.get_pointer(connection, scope=_SCOPE) == switched.pointer
    connection.rollback()


def test_generation_id_validation_is_scope_aware_canonical_and_fail_closed(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    alpha = _release("executor:a", marker="a")
    zulu = _release("executor:z", marker="c")
    connection.execute("BEGIN IMMEDIATE")
    repository.insert_release(connection, alpha, first_seen_at=_NOW)
    repository.insert_release(connection, zulu, first_seen_at=_NOW)
    first = repository.insert_staged(connection, _staged(alpha, "1"))
    second = repository.insert_staged(connection, _staged(zulu, "2"))
    canonical_ids = (first.generation_id, second.generation_id)

    assert repository.validate_generation_ids(
        connection,
        canonical_ids,
        expected_scopes=(zulu.scope, alpha.scope),
    ) == (first, second)
    with pytest.raises(GenerationRepositoryConflict, match="canonical scope order"):
        repository.validate_generation_ids(connection, tuple(reversed(canonical_ids)))
    with pytest.raises(GenerationRepositoryConflict, match="duplicate identities"):
        repository.validate_generation_ids(connection, (first.generation_id, first.generation_id))
    with pytest.raises(GenerationRepositoryConflict, match="expected scopes"):
        repository.validate_generation_ids(
            connection,
            canonical_ids,
            expected_scopes=(alpha.scope,),
        )
    repository.transition_pre_activation(
        connection,
        scope=zulu.scope,
        generation_id=second.generation_id,
        target_state=RuntimeGenerationState.FAILED,
        expected_version=1,
        updated_at=_NOW + timedelta(seconds=1),
    )
    with pytest.raises(GenerationRepositoryConflict, match="unusable"):
        repository.validate_generation_ids(connection, canonical_ids)
    connection.execute(
        """
        UPDATE runtime_generations
        SET state='disposed', version=2, activated_at=?, updated_at=?
        WHERE generation_id=?
        """,
        (
            (_NOW + timedelta(seconds=1)).isoformat(),
            (_NOW + timedelta(seconds=1)).isoformat(),
            first.generation_id,
        ),
    )
    with pytest.raises(GenerationRepositoryDecodeError, match="journal tail"):
        repository.validate_generation_ids(connection, (first.generation_id,))
    connection.rollback()


def test_retention_uses_pointer_exact_attempt_and_latest_open_non_recurring_root(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    third = _insert_release_and_ready(connection, repository, release, "3", seconds=20)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    final = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=third.generation_id,
        expected_generation_version=third.version,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=23),
    )
    assert first.generation_id not in {
        final.pointer.active_generation_id,
        final.pointer.last_good_generation_id,
    }
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=second.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=24),
        )
        is None
    )

    _insert_edict_and_memorial(
        connection,
        edict_id="closed-mismatch",
        memorial_id="memorial-mismatch",
        status="closed",
        schedule={},
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="memorial-mismatch",
        attempt_id="binding-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_claimable_attempt(
        connection,
        memorial_id="memorial-mismatch",
        attempt_id="ledger-attempt",
        created_at=_NOW + timedelta(seconds=30),
    )
    # A replacement attempt has no exact binding until dispatch.  Retain the
    # memorial's latest binding across that gap so infrastructure retries do
    # not lose their pinned generation.
    assert first.generation_id in repository.retained_generation_ids(connection)
    connection.execute("DELETE FROM execution_attempts WHERE attempt_id='ledger-attempt'")
    assert first.generation_id not in repository.retained_generation_ids(connection)

    _insert_edict_and_memorial(
        connection,
        edict_id="closed-exact",
        memorial_id="memorial-exact",
        status="closed",
        schedule={},
        created_at=_NOW + timedelta(seconds=31),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="memorial-exact",
        attempt_id="attempt-exact",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=31),
    )
    _insert_claimable_attempt(
        connection,
        memorial_id="memorial-exact",
        attempt_id="attempt-exact",
        created_at=_NOW + timedelta(seconds=31),
    )
    assert first.generation_id in repository.retained_generation_ids(connection)
    connection.execute("DELETE FROM execution_attempts WHERE attempt_id='attempt-exact'")

    _insert_edict_and_memorial(
        connection,
        edict_id="open-immediate",
        memorial_id="root-old",
        status="open",
        schedule={"type": "immediate"},
        created_at=_NOW + timedelta(seconds=32),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="root-old",
        attempt_id="root-old-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=32),
    )
    assert first.generation_id in repository.retained_generation_ids(connection)
    connection.execute(
        """
        INSERT INTO memorials (id, edict_id, status, created_at, dag_node_id)
        VALUES ('root-new', 'open-immediate', 'pending', ?, NULL)
        """,
        ((_NOW + timedelta(seconds=33)).isoformat(),),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="root-new",
        attempt_id="root-new-attempt",
        generation_ids=(),
        created_at=_NOW + timedelta(seconds=33),
    )
    assert first.generation_id not in repository.retained_generation_ids(connection)

    _insert_edict_and_memorial(
        connection,
        edict_id="open-cron",
        memorial_id="cron-root",
        status="open",
        schedule={"type": "cron", "cron": "0 * * * *"},
        created_at=_NOW + timedelta(seconds=34),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="cron-root",
        attempt_id="cron-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=34),
    )
    assert first.generation_id in repository.retained_generation_ids(connection)
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=first.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=35),
        )
        is None
    )
    connection.execute("UPDATE edicts SET status='closed' WHERE id='open-cron'")
    assert first.generation_id not in repository.retained_generation_ids(connection)
    disposed = repository.dispose_if_unreferenced(
        connection,
        scope=_SCOPE,
        generation_id=first.generation_id,
        expected_version=5,
        updated_at=_NOW + timedelta(seconds=36),
    )
    assert disposed is not None
    assert disposed.state is RuntimeGenerationState.DISPOSED
    connection.rollback()


def test_generation_only_attempt_marker_retains_draining_material(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    third = _insert_release_and_ready(connection, repository, release, "3", seconds=20)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=third.generation_id,
        expected_generation_version=third.version,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=23),
    )
    _insert_edict_and_memorial(
        connection,
        edict_id="generation-only",
        memorial_id="generation-only-root",
        status="closed",
        schedule={},
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_claimable_attempt(
        connection,
        memorial_id="generation-only-root",
        attempt_id="generation-only-attempt",
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_generation_binding(
        connection,
        memorial_id="generation-only-root",
        attempt_id="generation-only-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=30),
    )

    assert first.generation_id in repository.retained_generation_ids(connection)
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=first.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=31),
        )
        is None
    )
    connection.execute("DELETE FROM execution_attempts WHERE attempt_id='generation-only-attempt'")
    assert first.generation_id not in repository.retained_generation_ids(connection)
    connection.rollback()


def test_parent_retry_attempt_retains_parent_pin_before_dispatch(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    third = _insert_release_and_ready(connection, repository, release, "3", seconds=20)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=third.generation_id,
        expected_generation_version=third.version,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=23),
    )
    _insert_edict_and_memorial(
        connection,
        edict_id="cron-retry",
        memorial_id="cron-parent",
        status="open",
        schedule={"type": "cron", "cron": "0 * * * *"},
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="cron-parent",
        attempt_id="cron-parent-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=30),
    )
    connection.execute(
        """
        INSERT INTO memorials (
            id, edict_id, status, created_at, parent_memorial_id, dag_node_id
        ) VALUES (?, ?, 'pending', ?, ?, NULL)
        """,
        (
            "cron-retry-root",
            "cron-retry",
            (_NOW + timedelta(seconds=31)).isoformat(),
            "cron-parent",
        ),
    )
    _insert_claimable_attempt(
        connection,
        memorial_id="cron-retry-root",
        attempt_id="cron-retry-attempt",
        created_at=_NOW + timedelta(seconds=31),
    )

    assert first.generation_id in repository.retained_generation_ids(connection)
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=first.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=32),
        )
        is None
    )

    connection.execute("DELETE FROM execution_attempts WHERE attempt_id='cron-retry-attempt'")
    assert first.generation_id in repository.retained_generation_ids(connection)
    connection.execute("UPDATE edicts SET status='closed' WHERE id='cron-retry'")
    assert first.generation_id not in repository.retained_generation_ids(connection)
    disposed = repository.dispose_if_unreferenced(
        connection,
        scope=_SCOPE,
        generation_id=first.generation_id,
        expected_version=5,
        updated_at=_NOW + timedelta(seconds=33),
    )
    assert disposed is not None
    assert disposed.state is RuntimeGenerationState.DISPOSED
    connection.rollback()


def test_completed_periodic_followup_lineage_retains_pin_until_edict_closes(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    third = _insert_release_and_ready(connection, repository, release, "3", seconds=20)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=third.generation_id,
        expected_generation_version=third.version,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=23),
    )
    _insert_edict_and_memorial(
        connection,
        edict_id="cron-followup",
        memorial_id="cron-fire",
        status="open",
        schedule={"type": "cron", "cron": "0 * * * *"},
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="cron-fire",
        attempt_id="cron-fire-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=30),
    )
    assert first.generation_id in repository.retained_generation_ids(connection)

    connection.execute(
        """
        INSERT INTO memorials (
            id, edict_id, status, created_at, parent_memorial_id, dag_node_id
        ) VALUES (?, ?, 'completed', ?, ?, NULL)
        """,
        (
            "cron-followup-root",
            "cron-followup",
            (_NOW + timedelta(seconds=31)).isoformat(),
            "cron-fire",
        ),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="cron-followup-root",
        attempt_id="cron-followup-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=31),
    )

    assert first.generation_id in repository.retained_generation_ids(connection)
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=first.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=32),
        )
        is None
    )

    connection.execute("UPDATE edicts SET status='closed' WHERE id='cron-followup'")
    assert first.generation_id not in repository.retained_generation_ids(connection)
    disposed = repository.dispose_if_unreferenced(
        connection,
        scope=_SCOPE,
        generation_id=first.generation_id,
        expected_version=5,
        updated_at=_NOW + timedelta(seconds=33),
    )
    assert disposed is not None
    assert disposed.state is RuntimeGenerationState.DISPOSED
    connection.rollback()


def test_nested_retry_attempt_retains_nearest_bound_ancestor_pin(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    third = _insert_release_and_ready(connection, repository, release, "3", seconds=20)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=third.generation_id,
        expected_generation_version=third.version,
        expected_pointer_version=switched.pointer.version,
        updated_at=_NOW + timedelta(seconds=23),
    )
    _insert_edict_and_memorial(
        connection,
        edict_id="nested-retry",
        memorial_id="bound-ancestor",
        status="open",
        schedule={"type": "cron", "cron": "0 * * * *"},
        created_at=_NOW + timedelta(seconds=30),
    )
    _insert_snapshot_binding(
        connection,
        memorial_id="bound-ancestor",
        attempt_id="bound-ancestor-attempt",
        generation_ids=(first.generation_id,),
        created_at=_NOW + timedelta(seconds=30),
    )
    connection.execute(
        """
        INSERT INTO memorials (
            id, edict_id, status, created_at, parent_memorial_id, dag_node_id
        ) VALUES (?, ?, 'failed', ?, ?, NULL), (?, ?, 'pending', ?, ?, NULL)
        """,
        (
            "unbound-parent",
            "nested-retry",
            (_NOW + timedelta(seconds=31)).isoformat(),
            "bound-ancestor",
            "retry-child",
            "nested-retry",
            (_NOW + timedelta(seconds=32)).isoformat(),
            "unbound-parent",
        ),
    )
    _insert_claimable_attempt(
        connection,
        memorial_id="retry-child",
        attempt_id="retry-child-attempt",
        created_at=_NOW + timedelta(seconds=32),
    )

    assert first.generation_id in repository.retained_generation_ids(connection)
    assert (
        repository.dispose_if_unreferenced(
            connection,
            scope=_SCOPE,
            generation_id=first.generation_id,
            expected_version=5,
            updated_at=_NOW + timedelta(seconds=33),
        )
        is None
    )

    connection.execute("DELETE FROM execution_attempts WHERE attempt_id='retry-child-attempt'")
    assert first.generation_id in repository.retained_generation_ids(connection)
    connection.execute("UPDATE edicts SET status='closed' WHERE id='nested-retry'")
    assert first.generation_id not in repository.retained_generation_ids(connection)
    connection.rollback()


def test_release_and_journal_reads_reject_noncanonical_or_broken_durable_rows(
    connection: sqlite3.Connection,
) -> None:
    repository = GenerationRepository()
    release = _release()
    connection.execute("BEGIN IMMEDIATE")
    generation = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    connection.execute("DROP TRIGGER runtime_generation_releases_no_update")
    connection.execute("UPDATE runtime_generation_releases SET release_json=' ' || release_json")
    with pytest.raises(GenerationRepositoryDecodeError, match="canonical JSON"):
        repository.get_release(
            connection,
            scope=release.scope,
            release_digest=release.release_digest,
        )

    connection.execute("DROP TRIGGER runtime_generation_journal_no_delete")
    connection.execute(
        "DELETE FROM runtime_generation_journal WHERE generation_id=? AND generation_version=2",
        (generation.generation_id,),
    )
    with pytest.raises(GenerationRepositoryDecodeError, match="not contiguous"):
        repository.list_journal(connection, generation_id=generation.generation_id)
    connection.rollback()


_FAULT_FIRST_GENERATION_ID = "rg-" + "1" * 32
_FAULT_SECOND_GENERATION_ID = "rg-" + "2" * 32


def _runtime_generation_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    return (
        tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT release_digest, schema_version, scope, release_json, first_seen_at
                FROM runtime_generation_releases
                ORDER BY release_digest
                """
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT generation_id, schema_version, scope, release_digest, state,
                       version, created_at, activated_at, updated_at
                FROM runtime_generations
                ORDER BY generation_id
                """
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT journal_id, generation_id, generation_version, from_state,
                       to_state, entry_json, entry_hash, created_at
                FROM runtime_generation_journal
                ORDER BY generation_id, generation_version
                """
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT scope, active_generation_id, last_good_generation_id,
                       version, updated_at
                FROM generation_pointers
                ORDER BY scope
                """
            )
        ),
    )


def _arm_generation_write_fault(connection: sqlite3.Connection, point: str) -> None:
    connection.execute("CREATE TEMP TABLE generation_write_fault (point TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO generation_write_fault (point) VALUES (?)", (point,))
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_runtime_generation_update
        BEFORE UPDATE ON runtime_generations
        WHEN (SELECT point FROM generation_write_fault) =
             'generation_update:' || OLD.generation_id || ':' || NEW.state
        BEGIN
            SELECT RAISE(ABORT, 'injected generation write fault');
        END
        """
    )
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_runtime_generation_journal_insert
        BEFORE INSERT ON runtime_generation_journal
        WHEN (SELECT point FROM generation_write_fault) =
             'journal_insert:' || NEW.generation_id || ':' || NEW.generation_version
        BEGIN
            SELECT RAISE(ABORT, 'injected generation write fault');
        END
        """
    )
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_pointer_insert
        BEFORE INSERT ON generation_pointers
        WHEN (SELECT point FROM generation_write_fault) =
             'pointer_insert:' || NEW.scope
        BEGIN
            SELECT RAISE(ABORT, 'injected generation write fault');
        END
        """
    )
    connection.execute(
        """
        CREATE TEMP TRIGGER fail_generation_pointer_update
        BEFORE UPDATE ON generation_pointers
        WHEN (SELECT point FROM generation_write_fault) =
             'pointer_update:' || NEW.scope
        BEGIN
            SELECT RAISE(ABORT, 'injected generation write fault');
        END
        """
    )


@pytest.mark.parametrize(
    "fault_point",
    (
        f"generation_update:{_FAULT_FIRST_GENERATION_ID}:active",
        f"journal_insert:{_FAULT_FIRST_GENERATION_ID}:4",
        f"pointer_insert:{_SCOPE}",
    ),
    ids=("target-update", "target-journal", "pointer-insert"),
)
def test_initial_activate_fault_injection_restores_complete_pre_call_state(
    connection: sqlite3.Connection,
    fault_point: str,
) -> None:
    repository = GenerationRepository()
    connection.execute("BEGIN IMMEDIATE")
    ready = _insert_release_and_ready(connection, repository, _release(), "1", seconds=0)
    before = _runtime_generation_snapshot(connection)
    _arm_generation_write_fault(connection, fault_point)

    with pytest.raises(GenerationRepositoryConflict):
        repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=ready.generation_id,
            expected_generation_version=ready.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        )

    assert _runtime_generation_snapshot(connection) == before
    connection.rollback()


@pytest.mark.parametrize(
    "fault_point",
    (
        f"generation_update:{_FAULT_FIRST_GENERATION_ID}:draining",
        f"journal_insert:{_FAULT_FIRST_GENERATION_ID}:5",
        f"generation_update:{_FAULT_SECOND_GENERATION_ID}:active",
        f"journal_insert:{_FAULT_SECOND_GENERATION_ID}:4",
        f"pointer_update:{_SCOPE}",
    ),
    ids=(
        "old-active-update",
        "old-active-journal",
        "target-update",
        "target-journal",
        "pointer-update",
    ),
)
def test_switch_activate_fault_injection_restores_complete_pre_call_state(
    connection: sqlite3.Connection,
    fault_point: str,
) -> None:
    repository = GenerationRepository()
    connection.execute("BEGIN IMMEDIATE")
    release = _release()
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    before = _runtime_generation_snapshot(connection)
    _arm_generation_write_fault(connection, fault_point)

    with pytest.raises(GenerationRepositoryConflict):
        repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=second.generation_id,
            expected_generation_version=second.version,
            expected_pointer_version=initial.pointer.version,
            updated_at=_NOW + timedelta(seconds=13),
        )

    assert _runtime_generation_snapshot(connection) == before
    connection.rollback()


@pytest.mark.parametrize(
    "fault_point",
    (
        f"generation_update:{_FAULT_SECOND_GENERATION_ID}:draining",
        f"journal_insert:{_FAULT_SECOND_GENERATION_ID}:5",
        f"generation_update:{_FAULT_FIRST_GENERATION_ID}:active",
        f"journal_insert:{_FAULT_FIRST_GENERATION_ID}:6",
        f"pointer_update:{_SCOPE}",
    ),
    ids=(
        "current-active-update",
        "current-active-journal",
        "last-good-update",
        "last-good-journal",
        "pointer-update",
    ),
)
def test_rollback_fault_injection_restores_complete_pre_call_state(
    connection: sqlite3.Connection,
    fault_point: str,
) -> None:
    repository = GenerationRepository()
    connection.execute("BEGIN IMMEDIATE")
    release = _release()
    first = _insert_release_and_ready(connection, repository, release, "1", seconds=0)
    second = _insert_release_and_ready(connection, repository, release, "2", seconds=10)
    initial = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=first.generation_id,
        expected_generation_version=first.version,
        expected_pointer_version=None,
        updated_at=_NOW + timedelta(seconds=3),
    )
    switched = repository.activate(
        connection,
        scope=_SCOPE,
        target_generation_id=second.generation_id,
        expected_generation_version=second.version,
        expected_pointer_version=initial.pointer.version,
        updated_at=_NOW + timedelta(seconds=13),
    )
    before = _runtime_generation_snapshot(connection)
    _arm_generation_write_fault(connection, fault_point)

    with pytest.raises(GenerationRepositoryConflict):
        repository.rollback_to_last_good(
            connection,
            scope=_SCOPE,
            expected_pointer_version=switched.pointer.version,
            updated_at=_NOW + timedelta(seconds=14),
        )

    assert _runtime_generation_snapshot(connection) == before
    connection.rollback()


def _visible_generation_state(connection: sqlite3.Connection) -> tuple[object, ...]:
    generations = tuple(
        tuple(row)
        for row in connection.execute(
            """
            SELECT generation_id, state, version,
                   (
                       SELECT COUNT(*)
                       FROM runtime_generation_journal AS journal
                       WHERE journal.generation_id = generation.generation_id
                   ) AS journal_count,
                   (
                       SELECT generation_version
                       FROM runtime_generation_journal AS journal
                       WHERE journal.generation_id = generation.generation_id
                       ORDER BY generation_version DESC
                       LIMIT 1
                   ) AS journal_version,
                   (
                       SELECT to_state
                       FROM runtime_generation_journal AS journal
                       WHERE journal.generation_id = generation.generation_id
                       ORDER BY generation_version DESC
                       LIMIT 1
                   ) AS journal_state
            FROM runtime_generations AS generation
            WHERE scope = ?
            ORDER BY generation_id
            """,
            (_SCOPE,),
        )
    )
    pointer_row = connection.execute(
        """
        SELECT active_generation_id, last_good_generation_id, version
        FROM generation_pointers
        WHERE scope = ?
        """,
        (_SCOPE,),
    ).fetchone()
    return generations, tuple(pointer_row) if pointer_row is not None else None


def test_independent_reader_sees_complete_old_then_new_activation_state(tmp_path) -> None:
    database_path = tmp_path / "generation-visibility.sqlite3"
    writer = sqlite3.connect(database_path)
    writer.row_factory = sqlite3.Row
    writer.execute("PRAGMA foreign_keys=ON")
    apply_migrations(writer, MIGRATIONS)
    writer.commit()
    reader: sqlite3.Connection | None = None

    try:
        repository = GenerationRepository()
        writer.execute("BEGIN IMMEDIATE")
        release = _release()
        first = _insert_release_and_ready(writer, repository, release, "1", seconds=0)
        second = _insert_release_and_ready(writer, repository, release, "2", seconds=10)
        initial = repository.activate(
            writer,
            scope=_SCOPE,
            target_generation_id=first.generation_id,
            expected_generation_version=first.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        )
        writer.commit()

        reader = sqlite3.connect(database_path, timeout=0.1)
        reader.row_factory = sqlite3.Row
        reader.execute("PRAGMA foreign_keys=ON")
        old_state = (
            (
                (_FAULT_FIRST_GENERATION_ID, "active", 4, 4, 4, "active"),
                (_FAULT_SECOND_GENERATION_ID, "ready", 3, 3, 3, "ready"),
            ),
            (_FAULT_FIRST_GENERATION_ID, _FAULT_FIRST_GENERATION_ID, 1),
        )
        new_state = (
            (
                (_FAULT_FIRST_GENERATION_ID, "draining", 5, 5, 5, "draining"),
                (_FAULT_SECOND_GENERATION_ID, "active", 4, 4, 4, "active"),
            ),
            (_FAULT_SECOND_GENERATION_ID, _FAULT_FIRST_GENERATION_ID, 2),
        )
        assert _visible_generation_state(reader) == old_state

        writer.execute("BEGIN IMMEDIATE")
        repository.activate(
            writer,
            scope=_SCOPE,
            target_generation_id=second.generation_id,
            expected_generation_version=second.version,
            expected_pointer_version=initial.pointer.version,
            updated_at=_NOW + timedelta(seconds=13),
        )
        assert _visible_generation_state(writer) == new_state
        assert _visible_generation_state(reader) == old_state

        reader.commit()
        writer.commit()
        assert _visible_generation_state(reader) == new_state
    finally:
        if writer.in_transaction:
            writer.rollback()
        if reader is not None:
            reader.close()
        writer.close()
