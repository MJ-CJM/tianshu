"""Slice 3B DecisionService authority, atomicity, and expiry contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models.decision import (
    DecisionKind,
    DecisionStatus,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 15, 8, tzinfo=UTC)


def _auth(
    principal_id: str = "user:requester",
    display_name: str = "Requester",
    correlation_id: str = "decision-correlation",
) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=display_name,
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id=correlation_id,
        remote_addr="203.0.113.10",
    )


def _seed(storage: Storage) -> None:
    storage._conn.execute(  # noqa: SLF001 - durable service fixture
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "goal", _NOW.isoformat()),
    )
    storage._conn.execute(  # noqa: SLF001 - durable service fixture
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", _NOW.isoformat()),
    )
    storage._conn.commit()  # noqa: SLF001 - durable service fixture


@pytest.fixture
def decision_storage(tmp_path) -> Storage:
    storage = Storage(str(tmp_path / "decision-service.db"))
    storage.init_db()
    _seed(storage)
    yield storage
    storage.close()


def _request_command(**updates: object) -> RequestDecisionCommand:
    values = {
        "kind": DecisionKind.TOOL,
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "request_key": "tool-call:1",
        "payload": {"tool_name": "read_file", "arguments": {"path": "README.md"}},
        "expires_at": _NOW + timedelta(minutes=10),
    }
    values.update(updates)
    return RequestDecisionCommand(**values)


def _resolve_command(**updates: object) -> ResolveDecisionCommand:
    values = {
        "action": "approve",
        "reason": "reviewed against policy",
        "payload": {"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        "expected_version": 1,
    }
    values.update(updates)
    return ResolveDecisionCommand(**values)


def _service(storage: Storage, clock: list[datetime]):
    from tianshu.governance.decision_service import DecisionService

    return DecisionService(storage, clock=lambda: clock[0])


def test_request_uses_auth_identity_deduplicates_and_audits_payload_conflict(
    decision_storage: Storage,
) -> None:
    from tianshu.governance.decision_service import DecisionConflict

    clock = [_NOW]
    service = _service(decision_storage, clock)
    original = service.request(_request_command(), auth=_auth())
    duplicate = service.request(
        _request_command(),
        auth=_auth("user:other", "Other", "decision-request-retry"),
    )

    assert duplicate == original
    assert original.requested_by == "user:requester"
    assert service.get(original.decision_request_id).request == original
    assert service.list_pending(kind=DecisionKind.TOOL) == [original]

    changed = _request_command(
        payload={"tool_name": "write_file", "arguments": {"path": "README.md"}}
    )
    with pytest.raises(DecisionConflict) as error:
        service.request(
            changed,
            auth=_auth("user:other", "Other", "decision-request-conflict"),
        )
    assert error.value.code == "decision_identity_conflict"

    [denial] = [
        event
        for event in decision_storage.list_system_audit()
        if event.action == "decision.request.denied"
    ]
    assert denial.correlation_id == "decision-request-conflict"
    assert denial.actor_digest == hashlib.sha256(b"user:other").hexdigest()
    assert denial.reason_code == "decision_identity_conflict"
    assert "user:other" not in repr(denial)


def test_resolve_commits_resolution_transition_and_secret_free_outbox_once(
    decision_storage: Storage,
) -> None:
    clock = [_NOW]
    service = _service(decision_storage, clock)
    requested = service.request(_request_command(), auth=_auth())
    clock[0] += timedelta(minutes=1)
    secret = "OUTBOX_MUST_NOT_CONTAIN_THIS_REASON"
    resolver = _auth("user:reviewer", "Reviewer", "decision-resolve-correlation")

    resolution = service.resolve(
        requested.decision_request_id,
        _resolve_command(reason=secret),
        auth=resolver,
    )

    assert resolution.actor_principal_id == "user:reviewer"
    assert resolution.actor_display_name == "Reviewer"
    record = service.get(requested.decision_request_id)
    assert record is not None
    assert record.request.status is DecisionStatus.RESOLVED
    assert record.request.version == 2
    assert record.resolution == resolution

    rows = decision_storage._conn.execute(  # noqa: SLF001 - atomic outbox assertion
        "SELECT event_type, producer, payload_json FROM outbox_events"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "decision.resolved"
    assert rows[0]["producer"] == "governance.decision_service.v1"
    assert json.loads(rows[0]["payload_json"]) == {
        "action": "approve",
        "correlation_id": "decision-resolve-correlation",
        "decision_request_id": requested.decision_request_id,
        "kind": "tool",
        "request_version": 2,
        "schema_version": 1,
    }
    assert secret not in rows[0]["payload_json"]
    assert "user:reviewer" not in rows[0]["payload_json"]
    assert "203.0.113.10" not in rows[0]["payload_json"]


@pytest.mark.parametrize(
    ("setup", "command", "expected_code"),
    (
        ("stale", _resolve_command(expected_version=2), "decision_stale"),
        ("late", _resolve_command(), "decision_expired"),
        ("cancelled", _resolve_command(), "decision_cancelled"),
        ("resolved", _resolve_command(), "decision_already_resolved"),
    ),
)
def test_rejected_resolution_never_creates_second_resolution_or_outbox(
    decision_storage: Storage,
    setup: str,
    command: ResolveDecisionCommand,
    expected_code: str,
) -> None:
    from tianshu.governance.decision_service import DecisionConflict

    clock = [_NOW]
    service = _service(decision_storage, clock)
    requested = service.request(_request_command(), auth=_auth())
    if setup == "late":
        clock[0] = requested.expires_at
    elif setup == "cancelled":
        decision_storage._conn.execute(  # noqa: SLF001 - cancelled authority fixture
            "UPDATE decision_requests SET status = 'cancelled', version = 2 WHERE decision_request_id = ?",
            (requested.decision_request_id,),
        )
        decision_storage._conn.commit()  # noqa: SLF001
    elif setup == "resolved":
        clock[0] += timedelta(minutes=1)
        service.resolve(requested.decision_request_id, _resolve_command(), auth=_auth())

    before_resolution_count = decision_storage._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM decision_resolutions"
    ).fetchone()[0]
    before_outbox_count = decision_storage._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM outbox_events"
    ).fetchone()[0]

    with pytest.raises(DecisionConflict) as error:
        service.resolve(
            requested.decision_request_id,
            command,
            auth=_auth("user:loser", "Loser", f"resolve-{setup}"),
        )

    assert error.value.code == expected_code
    assert (
        decision_storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_resolutions"
        ).fetchone()[0]
        == before_resolution_count
    )
    assert (
        decision_storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == before_outbox_count
    )
    denials = [
        event
        for event in decision_storage.list_system_audit()
        if event.action == "decision.resolve.denied"
    ]
    assert len(denials) == 1
    assert denials[0].reason_code == expected_code


def test_malformed_kind_action_payload_is_rejected_before_authority_changes(
    decision_storage: Storage,
) -> None:
    from tianshu.governance.decision_service import DecisionValidationError

    clock = [_NOW]
    service = _service(decision_storage, clock)
    requested = service.request(_request_command(), auth=_auth())

    with pytest.raises(DecisionValidationError) as error:
        service.resolve(
            requested.decision_request_id,
            _resolve_command(action="amend", payload={"schema_version": 1, "amendment": "x"}),
            auth=_auth("user:reviewer", "Reviewer"),
        )

    assert error.value.code == "invalid_decision_resolution"
    record = service.get(requested.decision_request_id)
    assert record is not None
    assert record.request.status is DecisionStatus.PENDING
    assert record.resolution is None
    assert (
        decision_storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )


def test_expire_due_emits_exactly_one_event_per_cas_winner(
    decision_storage: Storage,
) -> None:
    clock = [_NOW]
    service = _service(decision_storage, clock)
    first = service.request(_request_command(), auth=_auth())
    future = service.request(
        _request_command(request_key="tool-call:future", expires_at=_NOW + timedelta(hours=1)),
        auth=_auth(),
    )
    clock[0] = first.expires_at

    assert service.expire_due(limit=100) == 1
    assert service.expire_due(limit=100) == 0
    expired = service.get(first.decision_request_id)
    pending = service.get(future.decision_request_id)
    assert expired is not None and expired.request.status is DecisionStatus.EXPIRED
    assert pending is not None and pending.request.status is DecisionStatus.PENDING

    [row] = decision_storage._conn.execute(  # noqa: SLF001
        "SELECT event_type, producer, payload_json FROM outbox_events"
    ).fetchall()
    assert row["event_type"] == "decision.expired"
    assert row["producer"] == "governance.decision_service.v1"
    assert json.loads(row["payload_json"]) == {
        "decision_request_id": first.decision_request_id,
        "kind": "tool",
        "request_version": 2,
        "schema_version": 1,
    }


def test_outbox_failure_rolls_back_resolution_and_request_transition(
    decision_storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [_NOW]
    service = _service(decision_storage, clock)
    requested = service.request(_request_command(), auth=_auth())
    clock[0] += timedelta(minutes=1)

    def fail_outbox(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("outbox failure sentinel")

    monkeypatch.setattr(service._outbox, "add", fail_outbox)  # noqa: SLF001
    with pytest.raises(RuntimeError, match="outbox failure sentinel"):
        service.resolve(requested.decision_request_id, _resolve_command(), auth=_auth())

    record = service.get(requested.decision_request_id)
    assert record is not None
    assert record.request.status is DecisionStatus.PENDING
    assert record.resolution is None
    assert (
        decision_storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )
