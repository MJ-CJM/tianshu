"""Immutable system snapshot repository and shadow-write contracts."""

from __future__ import annotations

import hashlib
import json

import pytest

from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.evolution_repo import EvolutionAssignmentConflict
from tianshu.storage.system_snapshot_repo import (
    SystemSnapshotRepository,
    SystemSnapshotRepositoryDecodeError,
)


def _snapshot(component_digest: str) -> SystemSnapshotV1:
    components = {"kernel": component_digest}
    return SystemSnapshotV1(components=components, digest=canonical_sha256(components))


def _seed_memorial(storage, *, suffix: str = "1") -> Memorial:
    edict = Edict(goal=f"snapshot-{suffix}")
    memorial = Memorial(
        edict_id=edict.id,
        status=TaskStatus.COMPLETED,
        result="ok",
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    return memorial


def test_insert_snapshot_and_binding_are_insert_once_and_conflicts_fail_closed(storage) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_snapshot(connection, snapshot)
        repository.insert_snapshot(connection, snapshot)
        first = repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=snapshot,
            generation_ids=("generation-1",),
        )
        replay = repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=snapshot,
            generation_ids=("generation-1",),
        )
        assert first.inserted is True
        assert first.drifted is False
        assert replay.inserted is False
        assert replay.binding == first.binding
        assert connection.execute("SELECT COUNT(*) FROM system_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 1

        with pytest.raises(EvolutionAssignmentConflict):
            repository.insert_binding(
                connection,
                memorial_id=memorial.id,
                attempt_id="attempt-1",
                snapshot=snapshot,
                generation_ids=("generation-2",),
            )
        assert connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 1
        unit_of_work.commit()


def test_insert_binding_records_one_durable_drift_audit_and_outbox_event(storage) -> None:
    memorial = _seed_memorial(storage)
    first_snapshot = _snapshot("a" * 64)
    second_snapshot = _snapshot("b" * 64)
    repository = SystemSnapshotRepository()

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=first_snapshot,
        )
        result = repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-2",
            snapshot=second_snapshot,
        )
        replay = repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-2",
            snapshot=second_snapshot,
        )

        assert result.inserted is True
        assert result.drifted is True
        assert result.previous_snapshot_digest == first_snapshot.digest
        assert replay.inserted is False
        assert replay.drifted is False
        audit = connection.execute(
            "SELECT action, outcome, reason_code, metadata_json "
            "FROM system_audit_events WHERE action='system_snapshot_drift'"
        ).fetchall()
        assert [tuple(row) for row in audit] == [
            ("system_snapshot_drift", "succeeded", "system_snapshot_drift", "{}")
        ]
        outbox = connection.execute(
            "SELECT event_type, payload_json FROM outbox_events "
            "WHERE event_type='system_snapshot_drift'"
        ).fetchall()
        assert len(outbox) == 1
        payload = json.loads(outbox[0]["payload_json"])
        assert payload["attempt_id"] == "attempt-2"
        assert payload["snapshot_digest"] == second_snapshot.digest
        assert payload["previous_snapshot_digest"] == first_snapshot.digest
        unit_of_work.commit()


def test_try_insert_binding_rolls_back_half_write_and_emits_redacted_failure(storage) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()
    injected_secret = "/private/secret/config api_key=do-not-persist"

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            f"""
            CREATE TRIGGER reject_snapshot_binding
            BEFORE INSERT ON run_system_bindings BEGIN
                SELECT RAISE(ABORT, '{injected_secret}');
            END
            """
        )
        result = repository.try_insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-failed",
            snapshot=snapshot,
        )

        assert result is None
        assert connection.execute("SELECT COUNT(*) FROM system_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 0
        audit = connection.execute(
            "SELECT action, outcome, reason_code, subject_digest, metadata_json "
            "FROM system_audit_events WHERE action='system_snapshot_binding_failed'"
        ).fetchone()
        assert audit is not None
        assert tuple(audit[:3]) == (
            "system_snapshot_binding_failed",
            "failed",
            "system_snapshot_binding_failed",
        )
        assert audit["subject_digest"] == snapshot.digest
        assert audit["metadata_json"] == "{}"
        [outbox] = connection.execute(
            "SELECT payload_json FROM outbox_events "
            "WHERE event_type='system_snapshot_binding_failed'"
        ).fetchall()
        assert injected_secret not in audit["metadata_json"]
        assert injected_secret not in outbox["payload_json"]
        unit_of_work.commit()


def test_record_event_without_snapshot_digest_is_durable_and_uses_stable_identity(storage) -> None:
    memorial = _seed_memorial(storage)
    repository = SystemSnapshotRepository()

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        assert repository.record_event(
            connection,
            action="system_snapshot_binding_failed",
            memorial_id=memorial.id,
            attempt_id="attempt-resolver-failed",
        )
        audit = connection.execute(
            "SELECT action, outcome, reason_code, subject_digest "
            "FROM system_audit_events WHERE action='system_snapshot_binding_failed'"
        ).fetchone()
        assert audit is not None
        expected_digest = hashlib.sha256(
            f"{memorial.id}:attempt-resolver-failed".encode()
        ).hexdigest()
        assert tuple(audit) == (
            "system_snapshot_binding_failed",
            "failed",
            "system_snapshot_binding_failed",
            expected_digest,
        )
        outbox = connection.execute(
            "SELECT payload_json FROM outbox_events "
            "WHERE event_type='system_snapshot_binding_failed'"
        ).fetchone()
        assert outbox is not None
        payload = json.loads(outbox["payload_json"])
        assert payload["attempt_id"] == "attempt-resolver-failed"
        assert "snapshot_digest" not in payload
        unit_of_work.commit()


def test_event_outbox_failure_rolls_back_audit_and_never_blocks_shadow_write(storage) -> None:
    memorial = _seed_memorial(storage)
    repository = SystemSnapshotRepository()
    injected_secret = "/private/secret/settings.toml token=do-not-persist"

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            f"""
            CREATE TRIGGER reject_snapshot_outbox
            BEFORE INSERT ON outbox_events
            WHEN NEW.event_type='system_snapshot_binding_failed' BEGIN
                SELECT RAISE(ABORT, '{injected_secret}');
            END
            """
        )
        assert not repository.record_event(
            connection,
            action="system_snapshot_binding_failed",
            memorial_id=memorial.id,
            attempt_id="attempt-1",
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM system_audit_events "
                "WHERE action='system_snapshot_binding_failed'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE event_type='system_snapshot_binding_failed'"
            ).fetchone()[0]
            == 0
        )
        unit_of_work.commit()


def test_drift_outbox_failure_does_not_undo_the_durable_binding(storage) -> None:
    memorial = _seed_memorial(storage)
    repository = SystemSnapshotRepository()
    first_snapshot = _snapshot("a" * 64)
    second_snapshot = _snapshot("b" * 64)

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=first_snapshot,
        )
        connection.execute(
            """
            CREATE TRIGGER reject_snapshot_drift_outbox
            BEFORE INSERT ON outbox_events
            WHEN NEW.event_type='system_snapshot_drift' BEGIN
                SELECT RAISE(ABORT, 'redacted injected failure');
            END
            """
        )

        result = repository.insert_binding(
            connection,
            memorial_id=memorial.id,
            attempt_id="attempt-2",
            snapshot=second_snapshot,
        )

        assert result.inserted is True
        assert result.drifted is True
        assert connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM system_audit_events WHERE action='system_snapshot_drift'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='system_snapshot_drift'"
            ).fetchone()[0]
            == 0
        )
        unit_of_work.commit()


def test_binding_reads_fail_closed_and_last_order_uses_created_at_then_attempt_id(storage) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()

    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_snapshot(connection, snapshot)
        rows = (
            (memorial.id, "attempt-old", snapshot.digest, "[]", "2026-08-24T00:00:00+00:00"),
            (memorial.id, "attempt-a", snapshot.digest, "[]", "2026-08-25T00:00:00+00:00"),
            (memorial.id, "attempt-z", snapshot.digest, "[]", "2026-08-25T00:00:00+00:00"),
        )
        connection.executemany(
            "INSERT INTO run_system_bindings VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        last = repository.get_last_binding(connection, memorial.id)
        assert last is not None
        assert last.attempt_id == "attempt-z"
        assert last.snapshot == snapshot

        connection.execute("DROP TRIGGER run_system_bindings_no_update")
        connection.execute(
            "UPDATE run_system_bindings SET generation_ids_json='[ \"generation-1\" ]' "
            "WHERE memorial_id=? AND attempt_id='attempt-z'",
            (memorial.id,),
        )
        with pytest.raises(SystemSnapshotRepositoryDecodeError, match="canonical JSON"):
            repository.get_last_binding(connection, memorial.id)
        unit_of_work.rollback()


@pytest.mark.parametrize(
    ("components_json", "expected_message"),
    [
        ('{ "kernel": "' + "a" * 64 + '" }', "canonical JSON"),
        ('{"kernel":"' + "b" * 64 + '"}', "violates the v1 contract"),
    ],
    ids=["noncanonical-json", "digest-mismatch"],
)
def test_snapshot_reads_reject_noncanonical_components_and_digest_mismatch(
    storage,
    components_json: str,
    expected_message: str,
) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_binding(
            unit_of_work.connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=snapshot,
        )
        unit_of_work.commit()

    with storage._conn:
        storage._conn.execute("DROP TRIGGER system_snapshots_no_update")
        storage._conn.execute(
            "UPDATE system_snapshots SET components_json=? WHERE snapshot_digest=?",
            (components_json, snapshot.digest),
        )
    with pytest.raises(SystemSnapshotRepositoryDecodeError, match=expected_message):
        repository.get_binding(
            storage._conn,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
        )


def test_binding_read_rejects_generation_array_with_non_string_item(storage) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_binding(
            unit_of_work.connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=snapshot,
        )
        unit_of_work.commit()

    with storage._conn:
        storage._conn.execute("DROP TRIGGER run_system_bindings_no_update")
        storage._conn.execute(
            "UPDATE run_system_bindings SET generation_ids_json='[1]' "
            "WHERE memorial_id=? AND attempt_id='attempt-1'",
            (memorial.id,),
        )
    with pytest.raises(SystemSnapshotRepositoryDecodeError, match="array of strings"):
        repository.get_binding(
            storage._conn,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
        )


def test_binding_read_rejects_a_missing_snapshot_row(storage) -> None:
    memorial = _seed_memorial(storage)
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_binding(
            unit_of_work.connection,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
            snapshot=snapshot,
        )
        unit_of_work.commit()

    storage._conn.execute("PRAGMA foreign_keys=OFF")
    with storage._conn:
        storage._conn.execute("DROP TRIGGER system_snapshots_no_delete")
        storage._conn.execute(
            "DELETE FROM system_snapshots WHERE snapshot_digest=?",
            (snapshot.digest,),
        )
    with pytest.raises(SystemSnapshotRepositoryDecodeError, match="components_json is not text"):
        repository.get_binding(
            storage._conn,
            memorial_id=memorial.id,
            attempt_id="attempt-1",
        )


def test_insert_binding_requires_a_caller_owned_transaction(storage) -> None:
    snapshot = _snapshot("a" * 64)
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        SystemSnapshotRepository().insert_binding(
            storage._conn,
            memorial_id="memorial-1",
            attempt_id="attempt-1",
            snapshot=snapshot,
        )


def test_snapshot_components_are_written_as_canonical_json(storage) -> None:
    snapshot = _snapshot("a" * 64)
    repository = SystemSnapshotRepository()
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_snapshot(connection, snapshot)
        row = connection.execute(
            "SELECT components_json FROM system_snapshots WHERE snapshot_digest=?",
            (snapshot.digest,),
        ).fetchone()
        assert row is not None
        assert row["components_json"] == canonical_json_bytes(snapshot.components).decode()
        unit_of_work.commit()
