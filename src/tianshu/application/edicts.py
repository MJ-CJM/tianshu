"""Atomic, idempotent Edict submission application boundary."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

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


_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "authorization",
        "access-token",
        "api-key",
        "api-secret",
        "auth-token",
        "cookie",
        "database-url",
        "db-password",
        "db-url",
        "master-key",
        "password",
        "private-key",
        "proxy-authorization",
        "redis-url",
        "secret",
        "secret-key",
        "set-cookie",
        "token",
        "x-api-key",
        "x-auth-token",
    }
)


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
        _validate_idempotency_key(command.idempotency_key)
        request_hash = _request_hash(command)
        principal_id = auth.principal.id
        try:
            return self._submit_once(
                command,
                principal_id=principal_id,
                producer=producer,
                correlation_id=correlation_id,
                request_hash=request_hash,
            )
        except sqlite3.IntegrityError:
            existing = self._load_existing(principal_id, command.idempotency_key)
            if existing is None:
                raise
            return _resolve_existing(existing, request_hash)

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
                return _resolve_existing(existing, request_hash)

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
            response_json = _serialize_result(result)

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

    def _load_existing(
        self,
        principal_id: str,
        idempotency_key: str,
    ) -> SubmissionIdempotencyRecord | None:
        with self._storage.unit_of_work() as unit_of_work:
            return self._outbox.get_submission(
                unit_of_work.connection,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
            )


def _validate_idempotency_key(idempotency_key: str) -> None:
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
    return _redact_payload_mapping(payload)


def _redact_payload_mapping(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    redacted: dict[str, JsonValue] = {}
    for key, value in payload.items():
        normalized_key = key.casefold().replace("_", "-")
        if normalized_key in _SENSITIVE_PAYLOAD_KEYS:
            redacted[key] = "[REDACTED]"
        elif normalized_key in {"env", "environment"} and isinstance(value, dict):
            redacted[key] = {environment_key: "[REDACTED]" for environment_key in value}
        else:
            redacted[key] = _redact_payload_value(value)
    return redacted


def _redact_payload_value(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return _redact_payload_mapping(value)
    if isinstance(value, list):
        return [_redact_payload_value(item) for item in value]
    return value


def _serialize_result(result: SubmitEdictResult) -> str:
    return canonical_json_bytes(
        {
            "deduplicated": result.deduplicated,
            "edict": result.edict.model_dump(mode="json", exclude_none=False),
            "event_id": result.event_id,
            "memorial": result.memorial.model_dump(mode="json", exclude_none=False),
            "request_hash": result.request_hash,
        }
    ).decode("utf-8")


def _resolve_existing(
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
    result = SubmitEdictResult(
        edict=Edict.model_validate(payload["edict"]),
        memorial=Memorial.model_validate(payload["memorial"]),
        event_id=payload["event_id"],
        request_hash=payload["request_hash"],
        deduplicated=True,
    )
    if (
        result.edict.id != existing.edict_id
        or result.memorial.id != existing.memorial_id
        or result.event_id != existing.event_id
        or result.request_hash != existing.request_hash
    ):
        raise ValueError("stored idempotency response does not match its durable identity")
    return result


__all__ = [
    "EdictApplicationService",
    "IdempotencyConflict",
    "SubmitEdictCommand",
    "SubmitEdictResult",
]
