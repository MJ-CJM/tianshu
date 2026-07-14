"""Atomic, idempotent Edict submission application boundary."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from tianshu.models import Edict, EventEnvelope, Memorial, TaskStatus
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.governance_contract import RequestedGovernanceContractV1
from tianshu.models.principal import AuthContext
from tianshu.security.redact import redact_text
from tianshu.storage.edict_repo import _insert_edict
from tianshu.storage.memorial_repo import _insert_memorial
from tianshu.storage.outbox_repo import (
    OutboxRepository,
    SubmissionIdempotencyRecord,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class _SubmissionStorage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...

    def get_edict(self, edict_id: str) -> Edict | None: ...

    def get_memorial(self, memorial_id: str) -> Memorial | None: ...


_SENSITIVE_COMPACT_KEY_ALIASES = frozenset(
    {
        "authorization",
        "authorizationheader",
        "accesstoken",
        "apikey",
        "apisecret",
        "authtoken",
        "cookie",
        "credential",
        "credentials",
        "databaseurl",
        "dbpassword",
        "dburl",
        "masterkey",
        "password",
        "passwd",
        "privatekey",
        "proxyauthorization",
        "refreshtoken",
        "redisurl",
        "secret",
        "secretkey",
        "setcookie",
        "token",
        "xapikey",
        "xauthtoken",
    }
)
_SENSITIVE_COMPACT_KEY_ANCHORS = frozenset(
    {
        "accesstoken",
        "apikey",
        "apisecret",
        "authorization",
        "authtoken",
        "clientsecret",
        "cookievalue",
        "credential",
        "databaseurl",
        "dbpassword",
        "dburl",
        "masterkey",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "redisurl",
        "secretkey",
        "secretvalue",
        "setcookie",
        "tokenvalue",
    }
)
_SENSITIVE_KEY_TOKEN_SEQUENCES = (
    ("api", "key"),
    ("database", "url"),
    ("db", "url"),
    ("master", "key"),
    ("private", "key"),
    ("redis", "url"),
)
_TOKEN_METRIC_COMPACT_KEY_ALIASES = frozenset(
    {
        "completiontokencount",
        "completiontokens",
        "inputtokencount",
        "inputtokens",
        "maxtokencount",
        "maxtokens",
        "outputtokencount",
        "outputtokens",
        "prompttokencount",
        "prompttokens",
        "tokenbudget",
        "tokencount",
        "totaltokencount",
        "totaltokens",
    }
)
_ENVIRONMENT_COMPACT_KEY_ALIASES = frozenset(
    {"env", "environment", "environmentvalues", "environmentvariables", "envvars"}
)
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class SubmitEdictCommand:
    edict: Edict
    idempotency_key: str
    requested_contract: RequestedGovernanceContractV1
    extra_payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SubmitEdictResult:
    edict: Edict
    memorial: Memorial
    event_id: str
    request_hash: str
    deduplicated: bool


class IdempotencyConflict(RuntimeError):
    def __init__(self, principal_id: str, idempotency_key: str, existing_edict_id: str) -> None:
        self.principal_id = principal_id
        self.idempotency_key = idempotency_key
        self.existing_edict_id = existing_edict_id
        super().__init__(
            f"idempotency key conflicts with existing Edict {existing_edict_id} "
            f"for principal {principal_id}"
        )


class EdictApplicationService:
    def __init__(self, storage: _SubmissionStorage) -> None:
        self._storage = storage
        self._outbox = OutboxRepository()

    def submit(
        self,
        command: SubmitEdictCommand,
        *,
        auth: AuthContext,
        producer: str,
        correlation_id: str,
    ) -> SubmitEdictResult:
        validate_idempotency_key(command.idempotency_key)
        request_hash = _request_hash(command)
        principal_id = auth.principal.id
        return self._submit_once(
            command,
            principal_id=principal_id,
            producer=producer,
            correlation_id=correlation_id,
            request_hash=request_hash,
        )

    def _submit_once(
        self,
        command: SubmitEdictCommand,
        *,
        principal_id: str,
        producer: str,
        correlation_id: str,
        request_hash: str,
    ) -> SubmitEdictResult:
        with self._storage.unit_of_work() as unit_of_work:
            conn = unit_of_work.connection
            existing = self._outbox.get_submission(
                conn,
                principal_id=principal_id,
                idempotency_key=command.idempotency_key,
            )
            if existing is not None:
                return self._resolve_existing(conn, existing, request_hash)

            edict = _edict_for_submission(command)
            memorial = Memorial(
                edict_id=edict.id,
                instruction=edict.goal,
                status=TaskStatus.SUBMITTED,
            )
            event = EventEnvelope(
                event_type="edict.submitted",
                edict_id=edict.id,
                memorial_id=memorial.id,
                producer=producer,
                payload=_event_payload(command, correlation_id=correlation_id),
            )
            result = SubmitEdictResult(
                edict=edict,
                memorial=memorial,
                event_id=event.event_id,
                request_hash=request_hash,
                deduplicated=False,
            )
            response_json = _serialize_replay_identity(result)

            _insert_edict(conn, edict)
            _insert_memorial(conn, memorial)
            self._outbox.add(conn, event)
            self._outbox.add_submission(
                conn,
                SubmissionIdempotencyRecord(
                    principal_id=principal_id,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    edict_id=edict.id,
                    memorial_id=memorial.id,
                    event_id=event.event_id,
                    response_json=response_json,
                    created_at=event.timestamp.isoformat(),
                ),
            )
            unit_of_work.commit()
            return result

    def _resolve_existing(
        self,
        conn: sqlite3.Connection,
        existing: SubmissionIdempotencyRecord,
        request_hash: str,
    ) -> SubmitEdictResult:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(
                existing.principal_id,
                existing.idempotency_key,
                existing.edict_id,
            )
        payload = json.loads(existing.response_json)
        identity = _replay_identity(payload)
        if identity != (
            existing.edict_id,
            existing.memorial_id,
            existing.event_id,
            existing.request_hash,
        ):
            raise ValueError("stored idempotency response does not match its durable identity")
        outbox_event = self._outbox.get(conn, existing.event_id)
        if (
            outbox_event is None
            or outbox_event.event_type != "edict.submitted"
            or outbox_event.aggregate_type != "edict"
            or outbox_event.edict_id != existing.edict_id
            or outbox_event.memorial_id != existing.memorial_id
        ):
            raise ValueError("stored idempotency response has an invalid durable outbox identity")

        durable_edict = self._storage.get_edict(existing.edict_id)
        durable_memorial = self._storage.get_memorial(existing.memorial_id)
        if durable_edict is None or durable_memorial is None:
            raise ValueError("stored idempotency response references missing durable identity")
        if durable_memorial.edict_id != durable_edict.id:
            raise ValueError("stored idempotency response has an invalid durable identity relation")
        return SubmitEdictResult(
            edict=durable_edict,
            memorial=durable_memorial,
            event_id=existing.event_id,
            request_hash=existing.request_hash,
            deduplicated=True,
        )


def validate_idempotency_key(idempotency_key: str) -> None:
    if not 1 <= len(idempotency_key) <= 200:
        raise ValueError("idempotency key must contain 1 to 200 characters")
    if any(unicodedata.category(character) == "Cc" for character in idempotency_key):
        raise ValueError("idempotency key must not contain control characters")


def _request_hash(command: SubmitEdictCommand) -> str:
    edict_payload = command.edict.model_dump(
        mode="json",
        exclude={"id", "created_at", "idempotency_key", "governance_contract"},
        exclude_none=False,
    )
    return canonical_sha256(
        {
            "edict": edict_payload,
            "extra_payload": dict(command.extra_payload),
            "requested_contract_hash": command.requested_contract.content_hash,
        }
    )


def _edict_for_submission(command: SubmitEdictCommand) -> Edict:
    payload = command.edict.model_dump(mode="python")
    payload.update(
        {
            "governance_contract": command.requested_contract,
            "idempotency_key": command.idempotency_key,
        }
    )
    return Edict.model_validate(payload)


def _event_payload(
    command: SubmitEdictCommand,
    *,
    correlation_id: str,
) -> dict[str, JsonValue]:
    payload = dict(command.extra_payload)
    payload.update({"correlation_id": correlation_id, "goal": command.edict.goal})
    return cast(dict[str, JsonValue], _redact_durable_mapping(payload))


def _payload_key_tokens(key: str) -> tuple[str, ...]:
    separated = _CAMEL_CASE_BOUNDARY.sub("_", key)
    normalized = _NON_ALPHANUMERIC.sub("_", separated.casefold())
    return tuple(token for token in normalized.split("_") if token)


def _contains_key_token_sequence(
    key_tokens: tuple[str, ...],
    sequence: tuple[str, ...],
) -> bool:
    width = len(sequence)
    return any(
        key_tokens[index : index + width] == sequence
        for index in range(len(key_tokens) - width + 1)
    )


def _is_json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_sensitive_payload_key(
    key_tokens: tuple[str, ...],
    compact_key: str,
    value: object,
) -> bool:
    if compact_key in _TOKEN_METRIC_COMPACT_KEY_ALIASES:
        return not _is_json_number(value)
    if any(anchor in compact_key for anchor in _SENSITIVE_COMPACT_KEY_ANCHORS):
        return True
    if any(token in _SENSITIVE_COMPACT_KEY_ALIASES for token in key_tokens):
        return True
    return any(
        _contains_key_token_sequence(key_tokens, sequence)
        for sequence in _SENSITIVE_KEY_TOKEN_SEQUENCES
    )


def _redact_durable_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        key_tokens = _payload_key_tokens(key)
        compact_key = "".join(key_tokens)
        if compact_key in _ENVIRONMENT_COMPACT_KEY_ALIASES:
            redacted[key] = (
                {str(environment_key): "[REDACTED]" for environment_key in value}
                if isinstance(value, Mapping)
                else "[REDACTED]"
            )
        elif _is_sensitive_payload_key(key_tokens, compact_key, value):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = _redact_durable_value(value)
    return redacted


def _redact_durable_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return _redact_durable_mapping({str(key): item for key, item in value.items()})
    if isinstance(value, list):
        return [_redact_durable_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_durable_value(item) for item in value]
    return value


def _serialize_replay_identity(result: SubmitEdictResult) -> str:
    return canonical_json_bytes(
        {
            "edict_id": result.edict.id,
            "event_id": result.event_id,
            "memorial_id": result.memorial.id,
            "request_hash": result.request_hash,
        }
    ).decode("utf-8")


def _replay_identity(
    payload: object,
) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict) or set(payload) != {
        "edict_id",
        "memorial_id",
        "event_id",
        "request_hash",
    }:
        raise ValueError("stored idempotency response must be identity-only")
    identity = (
        payload["edict_id"],
        payload["memorial_id"],
        payload["event_id"],
        payload["request_hash"],
    )
    if not all(isinstance(value, str) for value in identity):
        raise ValueError("stored idempotency response has non-string durable identity")
    return cast(tuple[str, str, str, str], identity)


__all__ = [
    "EdictApplicationService",
    "IdempotencyConflict",
    "SubmitEdictCommand",
    "SubmitEdictResult",
]
