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
    assert response_payload["edict_id"] == result.edict.id
    assert response_payload["memorial_id"] == result.memorial.id
    assert response_payload["event_id"] == result.event_id
    assert response_payload["request_hash"] == result.request_hash


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


def test_outbox_redacts_refresh_token_and_credentials_key_variants(storage: Storage) -> None:
    refresh_token = "opaque-refresh-value-67c02"
    client_credentials = "opaque-client-credentials-a91ed"
    oauth_credentials = "opaque-oauth-credentials-4bd32"

    result = _service(storage).submit(
        _command(
            extra_payload={
                "refresh_token": refresh_token,
                "clientCredentials": {"value": client_credentials},
                "oauth_credentials": oauth_credentials,
            }
        ),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-credential-variants",
    )

    payload_json = storage._conn.execute(  # noqa: SLF001 - persisted redaction proof
        "SELECT payload_json FROM outbox_events WHERE event_id = ?",
        (result.event_id,),
    ).fetchone()[0]
    assert refresh_token not in payload_json
    assert client_credentials not in payload_json
    assert oauth_credentials not in payload_json
    assert '"refresh_token":"[REDACTED]"' in payload_json
    assert '"clientCredentials":"[REDACTED]"' in payload_json
    assert '"oauth_credentials":"[REDACTED]"' in payload_json


def test_idempotency_blob_stores_only_safe_identity_and_replays_exact_models(
    storage: Storage,
) -> None:
    _, command_type = _application_types()
    metadata_password = "metadata-password-sentinel-5c943"
    requested_contract = RequestedGovernanceContractV1(objective=ObjectiveV1(goal="safe replay"))
    command = command_type(
        edict=Edict(
            goal="safe replay",
            metadata={"password": metadata_password, "visible": "preserved"},
        ),
        idempotency_key="safe-replay-key",
        requested_contract=requested_contract,
        extra_payload={},
    )
    service = _service(storage)

    first = service.submit(
        command,
        auth=_auth(),
        producer="test",
        correlation_id="correlation-safe-replay",
    )
    response_json = storage._conn.execute(  # noqa: SLF001 - durable blob proof
        "SELECT response_json FROM submission_idempotency"
    ).fetchone()[0]
    second = service.submit(
        command,
        auth=_auth(),
        producer="test",
        correlation_id="correlation-safe-replay",
    )

    assert metadata_password not in response_json
    assert set(json.loads(response_json)) == {
        "edict_id",
        "event_id",
        "memorial_id",
        "request_hash",
    }
    assert second.deduplicated is True
    assert second.edict == first.edict
    assert second.memorial == first.memorial
    assert second.event_id == first.event_id


def test_unrelated_integrity_error_is_not_reclassified_as_idempotent_dedupe(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _command()
    service = _service(storage)
    service.submit(
        command,
        auth=_auth(),
        producer="test",
        correlation_id="correlation-first",
    )

    def fail_with_unrelated_integrity(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.IntegrityError("CHECK constraint failed: unrelated_table")

    monkeypatch.setattr(service, "_submit_once", fail_with_unrelated_integrity)

    with pytest.raises(sqlite3.IntegrityError, match="unrelated_table"):
        service.submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="correlation-retry",
        )
