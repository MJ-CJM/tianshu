"""Connection-owned durable Decision repository contracts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import tianshu.storage.decision_repo as repo_module
from tianshu.models.canonical import canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
)
from tianshu.storage import Storage


def _contracts():
    names = (
        "DecisionRepository",
        "DecisionIdentityConflict",
        "DecisionStateConflict",
        "DecisionDecodeError",
    )
    missing = [name for name in names if not hasattr(repo_module, name)]
    assert missing == [], f"missing Decision repository contracts: {missing}"
    return tuple(getattr(repo_module, name) for name in names)


def _storage() -> Storage:
    storage = Storage(":memory:")
    storage.init_db()
    storage._conn.execute(  # noqa: SLF001 - repository fixture
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "goal", "2026-07-15T00:00:00+00:00"),
    )
    storage._conn.execute(  # noqa: SLF001 - repository fixture
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", "2026-07-15T00:00:00+00:00"),
    )
    storage._conn.commit()  # noqa: SLF001 - repository fixture
    return storage


def _request(**updates: object) -> DecisionRequestV1:
    now = datetime(2026, 7, 15, 1, tzinfo=UTC)
    payload = updates.pop("payload", {"tool_name": "read_file", "arguments": {"path": "x"}})
    values = {
        "decision_request_id": "decision-1",
        "kind": DecisionKind.TOOL,
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "request_key": "tool-call:1",
        "payload": payload,
        "payload_hash": canonical_sha256(payload),
        "requested_by": "user:operator",
        "expires_at": now + timedelta(minutes=10),
        "status": DecisionStatus.PENDING,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return DecisionRequestV1(**values)


def _resolution(**updates: object) -> DecisionResolutionV1:
    values = {
        "decision_request_id": "decision-1",
        "action": "approve",
        "reason": "reviewed",
        "payload": {"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        "actor_principal_id": "user:reviewer",
        "actor_display_name": "Reviewer",
        "resolved_at": datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
    }
    values.update(updates)
    return DecisionResolutionV1(**values)


def test_storage_wires_stateless_repository_and_uow_owns_rollback() -> None:
    repository_type, *_ = _contracts()
    storage = _storage()
    try:
        assert isinstance(storage.decision_repo, repository_type)
        with pytest.raises(RuntimeError, match="rollback sentinel"), storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            raise RuntimeError("rollback sentinel")
        with storage.unit_of_work() as uow:
            assert storage.decision_repo.get(uow.connection, "decision-1") is None
    finally:
        storage.close()


def test_request_identity_is_idempotent_only_for_the_same_payload_hash() -> None:
    _, identity_conflict, *_ = _contracts()
    storage = _storage()
    try:
        request = _request()
        with storage.unit_of_work() as uow:
            assert storage.decision_repo.add_or_get(uow.connection, request) == request
            uow.commit()
        duplicate = _request(decision_request_id="decision-retry")
        with storage.unit_of_work() as uow:
            assert storage.decision_repo.add_or_get(uow.connection, duplicate) == request
            uow.commit()
        changed_payload = {"tool_name": "write_file", "arguments": {"path": "x"}}
        conflict = _request(
            decision_request_id="decision-conflict",
            payload=changed_payload,
            payload_hash=canonical_sha256(changed_payload),
        )
        with pytest.raises(identity_conflict), storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, conflict)
    finally:
        storage.close()


def test_request_rejects_memorial_from_a_different_edict() -> None:
    _, identity_conflict, *_ = _contracts()
    storage = _storage()
    try:
        storage._conn.execute(  # noqa: SLF001 - repository fixture
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("edict-2", "other", "2026-07-15T00:00:00+00:00"),
        )
        storage._conn.commit()  # noqa: SLF001 - repository fixture
        with (
            pytest.raises(identity_conflict, match="memorial.*edict"),
            storage.unit_of_work() as uow,
        ):
            storage.decision_repo.add_or_get(
                uow.connection,
                _request(edict_id="edict-2", decision_request_id="decision-cross-edict"),
            )
    finally:
        storage.close()


def test_resolution_round_trip_uses_cas_and_database_immutability() -> None:
    _, _, state_conflict, _ = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            uow.commit()
        with storage.unit_of_work() as uow:
            record = storage.decision_repo.resolve(
                uow.connection, _resolution(), expected_version=1
            )
            uow.commit()
        assert record.request.status is DecisionStatus.RESOLVED
        assert record.request.version == 2
        assert record.resolution == _resolution()

        with pytest.raises(state_conflict), storage.unit_of_work() as uow:
            storage.decision_repo.resolve(uow.connection, _resolution(), expected_version=1)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            storage._conn.execute(  # noqa: SLF001 - database immutability contract
                "UPDATE decision_resolutions SET reason = 'changed' WHERE decision_request_id = ?",
                ("decision-1",),
            )
    finally:
        storage.close()


def test_resolution_action_is_bound_to_request_kind_and_decode_is_strict() -> None:
    *_, decode_error = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            uow.commit()
        with pytest.raises(ValueError, match="unsupported action"), storage.unit_of_work() as uow:
            storage.decision_repo.resolve(
                uow.connection,
                _resolution(action="amend", payload={"schema_version": 1, "amendment": "x"}),
                expected_version=1,
            )

        storage._conn.execute("PRAGMA ignore_check_constraints=ON")  # noqa: SLF001
        storage._conn.execute(  # noqa: SLF001 - corrupted row fixture
            "UPDATE decision_requests SET schema_version = 2 WHERE decision_request_id = ?",
            ("decision-1",),
        )
        storage._conn.commit()  # noqa: SLF001 - corrupted row fixture
        with storage.unit_of_work() as uow, pytest.raises(decode_error):
            storage.decision_repo.get(uow.connection, "decision-1")
    finally:
        storage.close()
