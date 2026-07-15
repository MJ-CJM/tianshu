"""Connection-owned durable Decision repository contracts."""

from __future__ import annotations

import json
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
                uow.connection,
                _resolution(),
                expected_version=1,
                now=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
            )
            uow.commit()
        assert record.request.status is DecisionStatus.RESOLVED
        assert record.request.version == 2
        assert record.resolution == _resolution()

        with pytest.raises(state_conflict), storage.unit_of_work() as uow:
            storage.decision_repo.resolve(
                uow.connection,
                _resolution(),
                expected_version=1,
                now=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
            )
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
                now=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
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


def test_list_pending_filters_kind_and_due_order_without_opening_a_transaction() -> None:
    storage = _storage()
    try:
        requests = (
            _request(
                decision_request_id="decision-later",
                request_key="tool-call:later",
                created_at=datetime(2026, 7, 15, 1, 2, tzinfo=UTC),
                updated_at=datetime(2026, 7, 15, 1, 2, tzinfo=UTC),
                expires_at=datetime(2026, 7, 15, 1, 12, tzinfo=UTC),
            ),
            _request(
                decision_request_id="decision-plan",
                kind=DecisionKind.PLAN_REVIEW,
                request_key="plan:1",
                created_at=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
                expires_at=datetime(2026, 7, 15, 1, 11, tzinfo=UTC),
            ),
            _request(decision_request_id="decision-first", request_key="tool-call:first"),
        )
        with storage.unit_of_work() as uow:
            for request in requests:
                storage.decision_repo.add_or_get(uow.connection, request)
            uow.commit()

        assert [
            request.decision_request_id
            for request in storage.decision_repo.list_pending(
                storage._conn,  # noqa: SLF001 - connection-level repository contract
                kind=DecisionKind.TOOL,
            )
        ] == ["decision-first", "decision-later"]
        assert [
            request.decision_request_id
            for request in storage.decision_repo.list_due(
                storage._conn,  # noqa: SLF001 - connection-level repository contract
                now=datetime(2026, 7, 15, 1, 11, 30, tzinfo=UTC),
                limit=10,
            )
        ] == ["decision-first", "decision-plan"]
    finally:
        storage.close()


def test_resolve_requires_exact_version_and_unexpired_pending_status() -> None:
    _, _, state_conflict, _ = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            uow.commit()

        for expected_version, now, reason_code in (
            (2, datetime(2026, 7, 15, 1, 1, tzinfo=UTC), "stale_version"),
            (1, datetime(2026, 7, 15, 1, 10, tzinfo=UTC), "deadline_elapsed"),
        ):
            with pytest.raises(state_conflict) as error, storage.unit_of_work() as uow:
                storage.decision_repo.resolve(
                    uow.connection,
                    _resolution(),
                    expected_version=expected_version,
                    now=now,
                )
            assert error.value.reason_code == reason_code

        record = storage.decision_repo.get(storage._conn, "decision-1")  # noqa: SLF001
        assert record is not None
        assert record.request.status is DecisionStatus.PENDING
        assert record.resolution is None
    finally:
        storage.close()


def test_expire_uses_pending_version_and_deadline_cas() -> None:
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            uow.commit()
        now = datetime(2026, 7, 15, 1, 10, tzinfo=UTC)

        with storage.unit_of_work() as uow:
            expired = storage.decision_repo.expire(
                uow.connection,
                "decision-1",
                expected_version=1,
                now=now,
            )
            uow.commit()
        assert expired is not None
        assert expired.status is DecisionStatus.EXPIRED
        assert expired.version == 2

        with storage.unit_of_work() as uow:
            assert (
                storage.decision_repo.expire(
                    uow.connection,
                    "decision-1",
                    expected_version=1,
                    now=now,
                )
                is None
            )
            uow.commit()
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("status", "action", "payload"),
    (
        (
            "resolved",
            "amend",
            {"schema_version": 1, "amendment": "not valid for a tool decision"},
        ),
        (
            "pending",
            "approve",
            {"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        ),
    ),
)
def test_get_fails_closed_on_raw_resolution_state_or_action_corruption(
    status: str,
    action: str,
    payload: dict[str, object],
) -> None:
    *_, decode_error = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.decision_repo.add_or_get(uow.connection, _request())
            uow.commit()
        storage._conn.execute(  # noqa: SLF001 - raw corrupted row fixture
            "UPDATE decision_requests SET status = ? WHERE decision_request_id = ?",
            (status, "decision-1"),
        )
        storage._conn.execute(  # noqa: SLF001 - raw corrupted row fixture
            """
            INSERT INTO decision_resolutions (
                decision_request_id, action, reason, payload_json,
                actor_principal_id, actor_display_name, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "decision-1",
                action,
                "raw corruption",
                json.dumps(payload),
                "user:reviewer",
                "Reviewer",
                "2026-07-15T01:01:00+00:00",
            ),
        )
        storage._conn.commit()  # noqa: SLF001 - raw corrupted row fixture

        with storage.unit_of_work() as uow, pytest.raises(decode_error):
            storage.decision_repo.get(uow.connection, "decision-1")
    finally:
        storage.close()
