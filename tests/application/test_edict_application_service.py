"""Focused contracts for the atomic Edict submission application service."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from tianshu.models import Edict
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.canonical import JsonValue, canonical_json_bytes
from tianshu.models.edict import (
    EdictRuntime,
    EdictSchedule,
    LongRunningScheduleError,
    PolicyProfilePayload,
)
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage
from tianshu.storage.edict_repo import InvalidAllowedPathGlob
from tianshu.tools.policy_profile import BUILTIN_TEMPLATES

_BUILTIN_RELATIVE_ALLOWED_PATH_GLOBS = sorted(
    {
        path_glob
        for template in BUILTIN_TEMPLATES.values()
        for path_glob in template.allowed_paths
        if not path_glob.startswith("/")
    }
)


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


def _allowed_paths_command(
    *allowed_paths: str,
    template_name: str | None = None,
    idempotency_key: str = "allowed-paths-request",
) -> Any:
    _, command_type = _application_types()
    goal = "validate allowed path admission"
    return command_type(
        edict=Edict(
            goal=goal,
            runtime=EdictRuntime(
                policy_profile=PolicyProfilePayload(
                    allowed_paths=list(allowed_paths),
                    template_name=template_name,
                )
            ),
        ),
        idempotency_key=idempotency_key,
        requested_contract=RequestedGovernanceContractV1(objective=ObjectiveV1(goal=goal)),
        extra_payload={},
    )


def _submission_table_counts(storage: Storage) -> dict[str, int]:
    return {
        table: storage._conn.execute(  # noqa: SLF001 - atomic admission proof
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        )
    }


@pytest.mark.parametrize(
    "bad_path",
    [
        "/**",
        "/*",
        "/",
        "docs/**",
        "~/secrets/**",
        "/Users/example/.ssh/**",
        " /tmp/shared/**",
        "/tmp/shared/** ",
        " **/*",
        "**/* ",
    ],
)
def test_allowed_paths_admission_rejects_before_any_submission_write(
    storage: Storage,
    bad_path: str,
) -> None:
    with pytest.raises(InvalidAllowedPathGlob) as caught:
        _service(storage).submit(
            _allowed_paths_command(bad_path),
            auth=_auth(),
            producer="test",
            correlation_id="correlation-invalid-path",
        )

    assert caught.value.path_glob == bad_path
    assert caught.value.reason
    counts = _submission_table_counts(storage)
    assert counts == dict.fromkeys(counts, 0)


@pytest.mark.parametrize("allowed_path", ["/tmp/shared/**", *_BUILTIN_RELATIVE_ALLOWED_PATH_GLOBS])
def test_allowed_paths_admission_accepts_absolute_and_exact_builtin_globs(
    storage: Storage,
    allowed_path: str,
) -> None:
    result = _service(storage).submit(
        _allowed_paths_command(allowed_path),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-valid-path",
    )

    assert result.deduplicated is False
    counts = _submission_table_counts(storage)
    assert counts == dict.fromkeys(counts, 1)


def test_builtin_relative_exemption_source_is_not_empty() -> None:
    assert _BUILTIN_RELATIVE_ALLOWED_PATH_GLOBS


def test_builtin_name_does_not_exempt_an_undeclared_relative_glob(storage: Storage) -> None:
    with pytest.raises(InvalidAllowedPathGlob):
        _service(storage).submit(
            _allowed_paths_command("docs/**", template_name="refactor-in-place"),
            auth=_auth(),
            producer="test",
            correlation_id="correlation-forged-template",
        )


def test_storage_save_edict_cannot_bypass_allowed_paths_admission(storage: Storage) -> None:
    command = _allowed_paths_command("/**")

    with pytest.raises(InvalidAllowedPathGlob):
        storage.save_edict(command.edict)

    assert storage.list_edicts()[1] == 0
    assert (
        storage._conn.execute(  # noqa: SLF001 - direct persistence boundary proof
            "SELECT COUNT(*) FROM requested_governance_contracts"
        ).fetchone()[0]
        == 0
    )


def test_existing_submission_replay_precedes_new_admission_validation(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tianshu.storage import edict_repo

    service = _service(storage)
    command = _allowed_paths_command("/**")
    with monkeypatch.context() as historical:
        historical.setattr(edict_repo, "_validate_allowed_paths_admission", lambda _edict: None)
        service.submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="correlation-first",
        )

    replay = service.submit(
        command,
        auth=_auth(),
        producer="test",
        correlation_id="correlation-replay",
    )

    assert replay.deduplicated is True
    assert replay.edict.runtime.policy_profile is not None
    assert replay.edict.runtime.policy_profile.allowed_paths == ["/**"]


def test_idempotency_conflict_precedes_invalid_fresh_payload(storage: Storage) -> None:
    service = _service(storage)
    valid = _allowed_paths_command("/tmp/shared/**")
    service.submit(
        valid,
        auth=_auth(),
        producer="test",
        correlation_id="correlation-first",
    )

    from tianshu.application.edicts import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        service.submit(
            _allowed_paths_command("/**", idempotency_key=valid.idempotency_key),
            auth=_auth(),
            producer="test",
            correlation_id="correlation-conflict",
        )


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


def test_application_boundary_rejects_recurring_long_running_submission(
    storage: Storage,
) -> None:
    _, command_type = _application_types()
    goal = "unsafe recurring long task"
    command = command_type(
        edict=Edict(
            goal=goal,
            acceptance=AcceptanceCriteria(),
            execution_profile="checkpointed",
            schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
        ),
        idempotency_key="recurring-long-task",
        requested_contract=RequestedGovernanceContractV1(objective=ObjectiveV1(goal=goal)),
        extra_payload={},
    )

    with pytest.raises(
        LongRunningScheduleError,
        match="recurring schedules do not support long-running tasks",
    ):
        _service(storage).submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="correlation-recurring-long-task",
        )

    assert storage.list_edicts()[1] == 0


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


def test_outbox_redacts_compact_camel_and_snake_credential_aliases(storage: Storage) -> None:
    api_key = "opaque-api-key-8f31d"
    compact_refresh_token = "opaque-compact-refresh-value-67c02"
    camel_refresh_token = "opaque-camel-refresh-value-d44b7"
    snake_refresh_token = "opaque-snake-refresh-value-c29a1"
    client_credentials = "opaque-client-credentials-a91ed"
    oauth_credentials = "opaque-oauth-credentials-4bd32"
    qualified_credentials = {
        "token_value": "opaque-qualified-token-a1d04",
        "tokenValue": "opaque-qualified-camel-token-8ab29",
        "api_key_value": "opaque-qualified-api-key-f24c1",
        "api-key-value": "opaque-qualified-kebab-api-key-9ec32",
        "client_secret_value": "opaque-qualified-client-secret-6da78",
        "password_value": "opaque-qualified-password-b593e",
        "credential_value": "opaque-qualified-credential-30ec7",
        "apikeyvalue": "opaque-compact-qualified-api-key-d10f4",
        "passwordvalue": "opaque-compact-qualified-password-5fc21",
        "refreshtokenvalue": "opaque-compact-qualified-refresh-token-49ae8",
        "credentialvalue": "opaque-compact-qualified-credential-7b62d",
        "clientsecretvalue": "opaque-compact-qualified-client-secret-38c5a",
        "tokenvalue": "opaque-compact-qualified-token-405d3",
        "secretvalue": "opaque-compact-qualified-secret-f830a",
        "cookievalue": "opaque-compact-qualified-cookie-2d7b1",
    }

    result = _service(storage).submit(
        _command(
            extra_payload={
                "apikey": api_key,
                "refreshtoken": compact_refresh_token,
                "refreshToken": camel_refresh_token,
                "refresh_token": snake_refresh_token,
                "clientCredentials": {"value": client_credentials},
                "oauth_credentials": oauth_credentials,
                **qualified_credentials,
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
    assert api_key not in payload_json
    assert compact_refresh_token not in payload_json
    assert camel_refresh_token not in payload_json
    assert snake_refresh_token not in payload_json
    assert client_credentials not in payload_json
    assert oauth_credentials not in payload_json
    assert all(secret not in payload_json for secret in qualified_credentials.values())
    assert '"apikey":"[REDACTED]"' in payload_json
    assert '"refreshtoken":"[REDACTED]"' in payload_json
    assert '"refreshToken":"[REDACTED]"' in payload_json
    assert '"refresh_token":"[REDACTED]"' in payload_json
    assert '"clientCredentials":"[REDACTED]"' in payload_json
    assert '"oauth_credentials":"[REDACTED]"' in payload_json
    payload = json.loads(payload_json)
    assert all(payload[key] == "[REDACTED]" for key in qualified_credentials)


def test_outbox_redacts_run_state_sensitive_alias_variants_with_shared_rules(
    storage: Storage,
) -> None:
    aliases = (
        "bearer",
        "bearer_token",
        "bearerToken",
        "bearertoken",
        "access_key",
        "accessKey",
        "accesskey",
        "session_key",
        "sessionKey",
        "sessionkey",
        "bot_token",
        "botToken",
        "bottoken",
        "webhook_secret",
        "webhookSecret",
        "webhooksecret",
        "session_token",
        "sessionToken",
        "sessiontoken",
        "id_token",
        "idToken",
        "idtoken",
    )
    sensitive_values = {alias: f"opaque-{alias}-value" for alias in aliases}
    result = _service(storage).submit(
        _command(extra_payload=sensitive_values),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-shared-sensitive-aliases",
    )

    payload = json.loads(
        storage._conn.execute(  # noqa: SLF001 - persisted redaction proof
            "SELECT payload_json FROM outbox_events WHERE event_id = ?",
            (result.event_id,),
        ).fetchone()[0]
    )
    assert all(payload[alias] == "[REDACTED]" for alias in aliases)
    assert all(value not in json.dumps(payload) for value in sensitive_values.values())


def test_outbox_preserves_non_secret_token_metrics(storage: Storage) -> None:
    result = _service(storage).submit(
        _command(
            extra_payload={
                "token_budget": 4096,
                "tokenCount": 17,
                "nested": {
                    "token_count": 3,
                    "prompt_tokens": 101,
                    "completionTokens": 202,
                    "input_tokens": 303,
                    "outputTokens": 404,
                    "total_tokens": 505.5,
                    "max_tokens": 606,
                },
            }
        ),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-token-metrics",
    )

    payload = json.loads(
        storage._conn.execute(  # noqa: SLF001 - persisted preservation proof
            "SELECT payload_json FROM outbox_events WHERE event_id = ?",
            (result.event_id,),
        ).fetchone()[0]
    )
    assert payload["token_budget"] == 4096
    assert payload["tokenCount"] == 17
    assert payload["nested"] == {
        "completionTokens": 202,
        "input_tokens": 303,
        "max_tokens": 606,
        "outputTokens": 404,
        "prompt_tokens": 101,
        "token_count": 3,
        "total_tokens": 505.5,
    }


def test_outbox_redacts_non_numeric_values_under_token_metric_keys(storage: Storage) -> None:
    budget_string = "opaque-budget-string-51c9d"
    count_string = "opaque-count-string-b7e4f"
    result = _service(storage).submit(
        _command(
            extra_payload={
                "token_budget": budget_string,
                "tokenCount": count_string,
                "nested": {"prompt_tokens": True},
            }
        ),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-invalid-token-metrics",
    )

    payload_json = storage._conn.execute(  # noqa: SLF001 - persisted redaction proof
        "SELECT payload_json FROM outbox_events WHERE event_id = ?",
        (result.event_id,),
    ).fetchone()[0]
    assert budget_string not in payload_json
    assert count_string not in payload_json
    payload = json.loads(payload_json)
    assert payload["token_budget"] == "[REDACTED]"
    assert payload["tokenCount"] == "[REDACTED]"
    assert payload["nested"]["prompt_tokens"] == "[REDACTED]"


def test_outbox_preserves_non_credential_compact_substrings(storage: Storage) -> None:
    result = _service(storage).submit(
        _command(
            extra_payload={
                "tokenizerValue": "byte-pair",
                "secretary_name": "Ada",
                "cookiecutterTemplate": "service",
            }
        ),
        auth=_auth(),
        producer="test",
        correlation_id="correlation-compact-non-credentials",
    )

    payload = json.loads(
        storage._conn.execute(  # noqa: SLF001 - persisted preservation proof
            "SELECT payload_json FROM outbox_events WHERE event_id = ?",
            (result.event_id,),
        ).fetchone()[0]
    )
    assert payload["tokenizerValue"] == "byte-pair"
    assert payload["secretary_name"] == "Ada"
    assert payload["cookiecutterTemplate"] == "service"


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


def test_duplicate_edict_id_integrity_error_propagates_and_rolls_back(storage: Storage) -> None:
    command = _command()
    storage.save_edict(command.edict)

    with pytest.raises(sqlite3.IntegrityError, match="edicts.id"):
        _service(storage).submit(
            command,
            auth=_auth(),
            producer="test",
            correlation_id="correlation-duplicate-edict",
        )

    assert storage._conn.in_transaction is False  # noqa: SLF001 - rollback proof
    assert storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0] == 1  # noqa: SLF001
    assert (  # noqa: SLF001 - pre-existing contract preserved
        storage._conn.execute("SELECT COUNT(*) FROM requested_governance_contracts").fetchone()[0]
        == 1
    )
    for table in ("memorials", "outbox_events", "submission_idempotency"):
        assert storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: SLF001
