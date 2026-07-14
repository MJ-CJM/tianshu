"""Durable Edict submission idempotency and conflict behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tianshu.models import Edict
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage


def _application_types() -> tuple[type[Any], type[Any], type[BaseException]]:
    from tianshu.application.edicts import (
        EdictApplicationService,
        IdempotencyConflict,
        SubmitEdictCommand,
    )

    return EdictApplicationService, SubmitEdictCommand, IdempotencyConflict


def _auth(principal_id: str) -> AuthContext:
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
    goal: str,
    key: str,
    *,
    payload: dict[str, object] | None = None,
    contract: RequestedGovernanceContractV1 | None = None,
) -> Any:
    _, command_type, _ = _application_types()
    requested_contract = contract or RequestedGovernanceContractV1(objective=ObjectiveV1(goal=goal))
    return command_type(
        edict=Edict(goal=goal),
        idempotency_key=key,
        requested_contract=requested_contract,
        extra_payload=payload or {},
    )


def _service(storage: Storage) -> Any:
    service_type, _, _ = _application_types()
    return service_type(storage)


def _submit(storage: Storage, command: Any, principal_id: str = "principal-a") -> Any:
    return _service(storage).submit(
        command,
        auth=_auth(principal_id),
        producer="integration",
        correlation_id="correlation-idempotency",
    )


def test_same_principal_key_and_canonical_hash_returns_original_response() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        first = _submit(storage, _command("same request", "opaque-key", payload={"b": 2, "a": 1}))
        second = _submit(
            storage,
            _command("same request", "opaque-key", payload={"a": 1, "b": 2}),
        )

        assert first.deduplicated is False
        assert second.deduplicated is True
        assert second.request_hash == first.request_hash
        assert second.edict.id == first.edict.id
        assert second.memorial.id == first.memorial.id
        assert second.event_id == first.event_id
        assert storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0] == 1  # noqa: SLF001
        assert storage._conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 1  # noqa: SLF001
    finally:
        storage.close()


def test_idempotency_key_does_not_participate_in_the_request_hash() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        first = _submit(storage, _command("same request", "first-key", payload={"stable": True}))
        second = _submit(
            storage,
            _command("same request", "second-key", payload={"stable": True}),
        )

        assert first.deduplicated is False
        assert second.deduplicated is False
        assert first.request_hash == second.request_hash
    finally:
        storage.close()


def test_same_principal_and_key_with_different_hash_raises_stable_conflict() -> None:
    service_type, _, conflict_type = _application_types()
    storage = Storage(":memory:")
    storage.init_db()
    try:
        first = _submit(storage, _command("first request", "shared-key"))

        with pytest.raises(conflict_type) as raised:
            service_type(storage).submit(
                _command("different request", "shared-key"),
                auth=_auth("principal-a"),
                producer="integration",
                correlation_id="correlation-conflict",
            )

        assert raised.value.principal_id == "principal-a"  # type: ignore[attr-defined]
        assert raised.value.idempotency_key == "shared-key"  # type: ignore[attr-defined]
        assert raised.value.existing_edict_id == first.edict.id  # type: ignore[attr-defined]
        assert storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0] == 1  # noqa: SLF001
    finally:
        storage.close()


def test_legacy_response_with_mismatched_memorial_edict_relation_is_rejected() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        command = _command("tamper proof", "tamper-key")
        first = _submit(storage, command)
        legacy_response = {
            "deduplicated": False,
            "edict": first.edict.model_dump(mode="json", exclude_none=False),
            "event_id": first.event_id,
            "memorial": first.memorial.model_dump(mode="json", exclude_none=False),
            "request_hash": first.request_hash,
        }
        legacy_response["memorial"]["edict_id"] = "tampered-edict-id"
        storage._conn.execute(  # noqa: SLF001 - durable tamper injection
            "UPDATE submission_idempotency SET response_json = ?",
            (canonical_json_bytes(legacy_response).decode("utf-8"),),
        )
        storage._conn.commit()  # noqa: SLF001 - durable tamper injection

        with pytest.raises(ValueError, match="durable identity"):
            _submit(storage, command)
    finally:
        storage.close()


def test_safe_replay_rejects_tampered_memorial_edict_relation() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        command = _command("durable relation", "memorial-relation-key")
        first = _submit(storage, command)
        other_edict = Edict(goal="different durable parent")
        storage.save_edict(other_edict)
        storage._conn.execute(  # noqa: SLF001 - durable tamper injection
            "UPDATE memorials SET edict_id = ? WHERE id = ?",
            (other_edict.id, first.memorial.id),
        )
        storage._conn.commit()  # noqa: SLF001 - durable tamper injection

        with pytest.raises(ValueError, match="durable identity relation"):
            _submit(storage, command)
    finally:
        storage.close()


def test_safe_replay_rejects_tampered_outbox_aggregate_binding() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        command = _command("durable outbox relation", "outbox-relation-key")
        first = _submit(storage, command)
        other_edict = Edict(goal="different outbox parent")
        storage.save_edict(other_edict)
        storage._conn.execute(  # noqa: SLF001 - durable tamper injection
            "UPDATE outbox_events SET edict_id = ? WHERE event_id = ?",
            (other_edict.id, first.event_id),
        )
        storage._conn.commit()  # noqa: SLF001 - durable tamper injection

        with pytest.raises(ValueError, match="durable outbox identity"):
            _submit(storage, command)
    finally:
        storage.close()


def test_requested_contract_hash_participates_in_the_request_hash() -> None:
    _, _, conflict_type = _application_types()
    storage = Storage(":memory:")
    storage.init_db()
    try:
        first_contract = RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="same Edict", context="first contract")
        )
        second_contract = RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="same Edict", context="second contract")
        )
        _submit(
            storage,
            _command(
                "same Edict",
                "contract-key",
                payload={"stable": True},
                contract=first_contract,
            ),
        )

        with pytest.raises(conflict_type):
            _submit(
                storage,
                _command(
                    "same Edict",
                    "contract-key",
                    payload={"stable": True},
                    contract=second_contract,
                ),
            )

        assert first_contract.content_hash != second_contract.content_hash
    finally:
        storage.close()


def test_same_key_is_independent_for_different_principals() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        first = _submit(storage, _command("independent", "same-key"), "principal-a")
        second = _submit(storage, _command("independent", "same-key"), "principal-b")

        assert first.deduplicated is False
        assert second.deduplicated is False
        assert first.edict.id != second.edict.id
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM submission_idempotency"
            ).fetchone()[0]
            == 2
        )
    finally:
        storage.close()


def test_idempotency_keys_are_opaque_and_compared_byte_for_byte() -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        results = [_submit(storage, _command("opaque", key)) for key in ("Key", "key", " Key ")]

        assert all(result.deduplicated is False for result in results)
        assert len({result.edict.id for result in results}) == 3
    finally:
        storage.close()


@pytest.mark.parametrize("key", ["", "x" * 201, "line\nbreak", "c1\u0085control"])
def test_idempotency_key_rejects_invalid_length_or_controls(key: str) -> None:
    storage = Storage(":memory:")
    storage.init_db()
    try:
        with pytest.raises(ValueError, match="idempotency key"):
            _submit(storage, _command("invalid key", key))
        assert storage._conn.execute("SELECT COUNT(*) FROM edicts").fetchone()[0] == 0  # noqa: SLF001
    finally:
        storage.close()


def test_idempotent_response_survives_storage_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "durable.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    first = _submit(first_storage, _command("restart", "restart-key"))
    first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    try:
        second = _submit(second_storage, _command("restart", "restart-key"))

        assert second.deduplicated is True
        assert (second.edict.id, second.memorial.id, second.event_id) == (
            first.edict.id,
            first.memorial.id,
            first.event_id,
        )
    finally:
        second_storage.close()


def test_concurrent_unique_race_resolves_to_new_plus_deduplicated(tmp_path: Path) -> None:
    database_path = tmp_path / "race.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    second_storage = Storage(str(database_path))
    second_storage.init_db()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _submit,
                    storage,
                    _command("racing request", "race-key", payload={"stable": True}),
                )
                for storage in (first_storage, second_storage)
            ]
            results = [future.result() for future in futures]

        assert sorted(result.deduplicated for result in results) == [False, True]
        assert len({result.edict.id for result in results}) == 1
        assert len({result.memorial.id for result in results}) == 1
        assert len({result.event_id for result in results}) == 1
    finally:
        second_storage.close()
        first_storage.close()


def test_concurrent_same_key_with_different_hash_resolves_to_conflict(tmp_path: Path) -> None:
    _, _, conflict_type = _application_types()
    database_path = tmp_path / "race-conflict.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    second_storage = Storage(str(database_path))
    second_storage.init_db()

    def submit_or_capture(storage: Storage, goal: str) -> Any:
        try:
            return _submit(storage, _command(goal, "race-conflict-key"))
        except conflict_type as error:
            return error

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(submit_or_capture, first_storage, "first racing hash"),
                executor.submit(submit_or_capture, second_storage, "second racing hash"),
            ]
            outcomes = [future.result() for future in futures]

        new_results = [outcome for outcome in outcomes if not isinstance(outcome, conflict_type)]
        conflicts = [outcome for outcome in outcomes if isinstance(outcome, conflict_type)]
        assert len(new_results) == 1
        assert new_results[0].deduplicated is False
        assert len(conflicts) == 1
        assert conflicts[0].existing_edict_id == new_results[0].edict.id
    finally:
        second_storage.close()
        first_storage.close()
