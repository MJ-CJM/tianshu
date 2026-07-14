"""Focused contracts for the atomic Edict submission application service."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from tianshu.models import Edict
from tianshu.models.canonical import JsonValue, canonical_json_bytes
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage


def _application_types() -> tuple[type[Any], type[Any]]:
    from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand

    return EdictApplicationService, SubmitEdictCommand


@pytest.fixture
def storage() -> Iterator[Storage]:
    database = Storage(":memory:")
    database.init_db()
    try:
        yield database
    finally:
        database.close()


def _auth(principal_id: str = "principal-a") -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=principal_id,
            scopes=frozenset({"edicts:write"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id=f"correlation-{principal_id}",
    )


def _command(
    *,
    goal: str = "build the durable boundary",
    idempotency_key: str = "request-1",
    extra_payload: Mapping[str, JsonValue] | None = None,
    contract: RequestedGovernanceContractV1 | None = None,
) -> Any:
    _, command_type = _application_types()
    requested = contract or RequestedGovernanceContractV1(objective=ObjectiveV1(goal=goal))
    return command_type(
        edict=Edict(goal=goal),
        idempotency_key=idempotency_key,
        requested_contract=requested,
        extra_payload=extra_payload or {},
    )


def _service(storage: Storage) -> Any:
    service_type, _ = _application_types()
    return service_type(storage)


def test_first_submit_persists_one_complete_canonical_transaction(storage: Storage) -> None:
    secret = "Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
    command = _command(extra_payload={"channel": "api", "authorization": secret})

    result = _service(storage).submit(
        command,
        auth=_auth(),
        producer="gateway.api",
        correlation_id="correlation-submit",
    )

    assert result.deduplicated is False
    assert result.edict.id == command.edict.id
    assert result.edict.governance_contract == command.requested_contract
    assert result.memorial.edict_id == result.edict.id
    assert result.memorial.instruction == result.edict.goal
    assert len(result.request_hash) == 64
    assert {
        table: storage._conn.execute(  # noqa: SLF001 - atomic persistence proof
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        )
    } == {
        "edicts": 1,
        "requested_governance_contracts": 1,
        "memorials": 1,
        "outbox_events": 1,
        "submission_idempotency": 1,
    }

    outbox_row = storage._conn.execute(  # noqa: SLF001 - durable envelope proof
        "SELECT * FROM outbox_events WHERE event_id = ?", (result.event_id,)
    ).fetchone()
    assert outbox_row["event_type"] == "edict.submitted"
    assert outbox_row["edict_id"] == result.edict.id
    assert outbox_row["memorial_id"] == result.memorial.id
    assert outbox_row["status"] == "pending"
    assert outbox_row["attempt_count"] == 0
    assert secret not in outbox_row["payload_json"]
    assert '"authorization":"[REDACTED]"' in outbox_row["payload_json"]

    response_json = storage._conn.execute(  # noqa: SLF001 - exact response proof
        "SELECT response_json FROM submission_idempotency"
    ).fetchone()[0]
    response_payload = json.loads(response_json)
    assert response_json == canonical_json_bytes(response_payload).decode("utf-8")
    assert response_payload["edict"]["id"] == result.edict.id
    assert response_payload["memorial"]["id"] == result.memorial.id
    assert response_payload["event_id"] == result.event_id
    assert response_payload["request_hash"] == result.request_hash
    assert response_payload["deduplicated"] is False


@pytest.mark.parametrize(
    "denied_table",
    [
        "edicts",
        "requested_governance_contracts",
        "memorials",
        "outbox_events",
        "submission_idempotency",
    ],
)
def test_failure_at_each_write_boundary_rolls_back_every_record(
    storage: Storage,
    denied_table: str,
) -> None:
    def deny_one_insert(
        action_code: int,
        argument1: str | None,
        _argument2: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action_code == sqlite3.SQLITE_INSERT and argument1 == denied_table:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    storage._conn.set_authorizer(deny_one_insert)  # noqa: SLF001 - fault injection
    try:
        with pytest.raises(sqlite3.DatabaseError):
            _service(storage).submit(
                _command(),
                auth=_auth(),
                producer="test",
                correlation_id="correlation-rollback",
            )
    finally:
        storage._conn.set_authorizer(None)  # noqa: SLF001 - clear fault injection

    assert storage._conn.in_transaction is False  # noqa: SLF001
    for table in (
        "edicts",
        "requested_governance_contracts",
        "memorials",
        "outbox_events",
        "submission_idempotency",
    ):
        assert (
            storage._conn.execute(  # noqa: SLF001 - rollback proof
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            == 0
        )


def test_submit_never_opens_a_second_sqlite_connection(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_connection = storage._conn  # noqa: SLF001 - connection identity proof

    def reject_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("submission opened a second SQLite connection")

    monkeypatch.setattr(sqlite3, "connect", reject_connect)

    result = _service(storage).submit(
        _command(),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-same-connection",
    )

    assert storage._conn is live_connection  # noqa: SLF001
    assert result.deduplicated is False


def test_outbox_redacts_sensitive_fields_even_without_a_known_token_pattern(
    storage: Storage,
) -> None:
    authorization = "short-opaque-secret"
    password = "brief-password"
    environment_token = "opaque-env-token"

    result = _service(storage).submit(
        _command(
            extra_payload={
                "headers": {"Authorization": authorization},
                "password": password,
                "environment": {"TOKEN": environment_token},
            }
        ),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-redaction",
    )

    payload_json = storage._conn.execute(  # noqa: SLF001 - persisted redaction proof
        "SELECT payload_json FROM outbox_events WHERE event_id = ?",
        (result.event_id,),
    ).fetchone()[0]
    assert authorization not in payload_json
    assert password not in payload_json
    assert environment_token not in payload_json
    assert '"Authorization":"[REDACTED]"' in payload_json
    assert '"password":"[REDACTED]"' in payload_json
    assert '"environment":{"TOKEN":"[REDACTED]"}' in payload_json
