"""Connection-owned durable RunState repository contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import tianshu.storage.run_state_repo as repo_module
from tianshu.models.canonical import canonical_sha256
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedChatMessageV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
    ToolProposalV1,
)
from tianshu.storage import Storage


def _contracts():
    names = (
        "RunStateRepository",
        "RunStateConflict",
        "RunStateDecodeError",
        "RunStateSecretError",
    )
    missing = [name for name in names if not hasattr(repo_module, name)]
    assert missing == [], f"missing RunState repository contracts: {missing}"
    return tuple(getattr(repo_module, name) for name in names)


def _storage(database: str = ":memory:") -> Storage:
    storage = Storage(database)
    storage.init_db()
    if storage._conn.execute("SELECT 1 FROM edicts WHERE id = 'edict-1'").fetchone() is None:  # noqa: SLF001
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


def _state(arguments: dict[str, object] | None = None) -> RunStateV1:
    now = datetime(2026, 7, 15, 2, tzinfo=UTC)
    tool_arguments = arguments or {"path": "README.md"}
    usage = PersistedUsageSummaryV1(
        prompt_tokens=3,
        completion_tokens=5,
        total_tokens=8,
        cache_read_tokens=1,
        cost_cny=0.02,
        actual_model="demo-model",
        upstream_provider=None,
    )
    continuation = AgentContinuationV1(
        messages=(
            PersistedChatMessageV1(
                role="user",
                content="inspect",
                name=None,
                tool_call_id=None,
            ),
        ),
        pending_tool=ToolProposalV1(
            tool_call_id="call-1",
            tool_name="read_file",
            arguments=tool_arguments,
            arguments_hash=canonical_sha256(tool_arguments),
            tool_tier="0",
            policy_rule_id="readonly",
            proposed_at=now,
        ),
        iteration=1,
        usage=usage,
        checkpoint_ref="artifact:checkpoint-1",
        resolved_decision_id=None,
        side_effect_cursor=0,
    )
    return RunStateV1(
        memorial_id="memorial-1",
        edict_id="edict-1",
        phase=RunPhase.EXECUTING,
        continuation=continuation,
        checkpoint_ref="artifact:checkpoint-1",
        side_effect_cursor=0,
        version=1,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("operation", ("create", "cas"))
@pytest.mark.parametrize(
    ("phase", "pending_decision_id", "resolved_decision_id"),
    (
        (RunPhase.WAITING_DECISION, None, None),
        (RunPhase.WAITING_DECISION, "", None),
        (RunPhase.EXECUTING, "decision-pending", None),
        (RunPhase.EXECUTING, None, ""),
        (RunPhase.WAITING_DECISION, "decision-pending", "decision-resolved"),
    ),
)
def test_durable_decision_binding_invariants_reject_zero_write_create_and_cas(
    operation: str,
    phase: RunPhase,
    pending_decision_id: str | None,
    resolved_decision_id: str | None,
) -> None:
    _, conflict_type, *_ = _contracts()
    storage = _storage()
    original = _state()
    continuation = original.continuation.model_copy(
        update={
            "pending_decision_id": pending_decision_id,
            "resolved_decision_id": resolved_decision_id,
        }
    )
    candidate = original.model_copy(
        update={
            "phase": phase,
            "continuation": continuation,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )
    try:
        if operation == "cas":
            with storage.unit_of_work() as unit_of_work:
                storage.run_state_repo.create(unit_of_work.connection, original)
                unit_of_work.commit()

        with pytest.raises(conflict_type, match="decision binding"), storage.unit_of_work() as uow:
            if operation == "create":
                storage.run_state_repo.create(uow.connection, candidate)
            else:
                storage.run_state_repo.compare_and_swap(
                    uow.connection,
                    candidate.model_copy(update={"version": original.version}),
                    expected_version=original.version,
                )

        with storage.unit_of_work() as unit_of_work:
            durable = storage.run_state_repo.load(unit_of_work.connection, "memorial-1")
            unit_of_work.commit()
        assert durable == (original if operation == "cas" else None)
    finally:
        storage.close()


def test_storage_wires_repository_and_round_trip_survives_restart(tmp_path: Path) -> None:
    repository_type, *_ = _contracts()
    database = str(tmp_path / "run-state.sqlite3")
    storage = _storage(database)
    state = _state()
    try:
        assert isinstance(storage.run_state_repo, repository_type)
        with storage.unit_of_work() as uow:
            assert storage.run_state_repo.create(uow.connection, state) == state
            uow.commit()
    finally:
        storage.close()

    reopened = _storage(database)
    try:
        with reopened.unit_of_work() as uow:
            assert reopened.run_state_repo.load(uow.connection, "memorial-1") == state
    finally:
        reopened.close()


def test_uow_rollback_and_compare_and_swap_stale_writer() -> None:
    _, conflict_type, *_ = _contracts()
    storage = _storage()
    state = _state()
    try:
        with pytest.raises(RuntimeError, match="rollback sentinel"), storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, state)
            raise RuntimeError("rollback sentinel")
        with storage.unit_of_work() as uow:
            assert storage.run_state_repo.load(uow.connection, "memorial-1") is None
            storage.run_state_repo.create(uow.connection, state)
            uow.commit()

        changed = state.model_copy(
            update={
                "phase": RunPhase.EXECUTING,
                "updated_at": state.updated_at + timedelta(seconds=1),
            }
        )
        with storage.unit_of_work() as uow:
            saved = storage.run_state_repo.compare_and_swap(
                uow.connection, changed, expected_version=1
            )
            uow.commit()
        assert saved.version == 2
        assert saved.phase is RunPhase.EXECUTING
        with pytest.raises(conflict_type), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(uow.connection, changed, expected_version=1)
    finally:
        storage.close()


def test_create_and_cas_preserve_memorial_edict_identity_and_timestamp_order() -> None:
    _, conflict_type, *_ = _contracts()
    storage = _storage()
    state = _state()
    try:
        storage._conn.execute(  # noqa: SLF001 - repository fixture
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("edict-2", "other", "2026-07-15T00:00:00+00:00"),
        )
        storage._conn.commit()  # noqa: SLF001 - repository fixture
        with pytest.raises(conflict_type, match="memorial.*edict"), storage.unit_of_work() as uow:
            storage.run_state_repo.create(
                uow.connection, state.model_copy(update={"edict_id": "edict-2"})
            )

        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, state)
            uow.commit()
        with pytest.raises(conflict_type, match="edict_id"), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(
                uow.connection,
                state.model_copy(
                    update={
                        "edict_id": "edict-2",
                        "updated_at": state.updated_at + timedelta(seconds=1),
                    }
                ),
                expected_version=1,
            )
        with pytest.raises(conflict_type, match="updated_at"), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(
                uow.connection,
                state.model_copy(update={"updated_at": state.updated_at - timedelta(seconds=1)}),
                expected_version=1,
            )

        row = storage._conn.execute(  # noqa: SLF001 - identity contract
            "SELECT edict_id, schema_version, version FROM run_states WHERE memorial_id = ?",
            ("memorial-1",),
        ).fetchone()
        assert tuple(row) == ("edict-1", 1, 1)
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("assignment", "message"),
    (
        ("schema_version = 2", "schema_version"),
        ("continuation_kind = 'outer_loop'", "kind"),
    ),
)
def test_cas_decodes_and_rejects_corrupt_current_row(assignment: str, message: str) -> None:
    _, _, decode_error, _ = _contracts()
    storage = _storage()
    state = _state()
    try:
        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, state)
            uow.commit()
        storage._conn.execute("DROP TRIGGER IF EXISTS run_states_validate_update_v12")  # noqa: SLF001
        storage._conn.execute("PRAGMA ignore_check_constraints=ON")  # noqa: SLF001
        storage._conn.execute(  # noqa: SLF001 - corrupted historical row fixture
            f"UPDATE run_states SET {assignment} WHERE memorial_id = 'memorial-1'"
        )
        storage._conn.commit()  # noqa: SLF001 - corrupted historical row fixture

        changed = state.model_copy(
            update={
                "phase": RunPhase.EXECUTING,
                "updated_at": state.updated_at + timedelta(seconds=1),
            }
        )
        with pytest.raises(decode_error, match=message), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(uow.connection, changed, expected_version=1)
        assert (
            storage._conn.execute(  # noqa: SLF001 - zero-write contract
                "SELECT version FROM run_states WHERE memorial_id = 'memorial-1'"
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


def test_cas_rejects_created_at_rebinding() -> None:
    _, conflict_type, *_ = _contracts()
    storage = _storage()
    state = _state()
    try:
        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, state)
            uow.commit()
        rebound = state.model_copy(
            update={
                "created_at": state.created_at - timedelta(seconds=1),
                "updated_at": state.updated_at + timedelta(seconds=1),
            }
        )
        with pytest.raises(conflict_type, match="created_at"), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(uow.connection, rebound, expected_version=1)
    finally:
        storage.close()


def test_successful_cas_reloads_and_returns_durable_truth() -> None:
    storage = _storage()
    state = _state()
    try:
        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, state)
            uow.commit()
        storage._conn.execute(  # noqa: SLF001 - durable-reload proof
            """
            CREATE TRIGGER run_state_test_durable_truth
            AFTER UPDATE OF version ON run_states
            BEGIN
                UPDATE run_states SET phase = 'paused' WHERE memorial_id = NEW.memorial_id;
            END
            """
        )
        storage._conn.commit()  # noqa: SLF001 - durable-reload proof
        changed = state.model_copy(
            update={
                "phase": RunPhase.EXECUTING,
                "updated_at": state.updated_at + timedelta(seconds=1),
            }
        )
        with storage.unit_of_work() as uow:
            saved = storage.run_state_repo.compare_and_swap(
                uow.connection, changed, expected_version=1
            )
            durable = storage.run_state_repo.load(uow.connection, "memorial-1")
            uow.commit()
        assert saved == durable
        assert saved.phase is RunPhase.PAUSED
    finally:
        storage.close()


def test_load_rejects_unknown_schema_and_continuation_kind_mismatch() -> None:
    _, _, decode_error, _ = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, _state())
            uow.commit()
        storage._conn.execute("DROP TRIGGER run_states_validate_update_v12")  # noqa: SLF001
        storage._conn.execute("PRAGMA ignore_check_constraints=ON")  # noqa: SLF001
        storage._conn.execute(  # noqa: SLF001 - corrupted row fixture
            "UPDATE run_states SET schema_version = 2 WHERE memorial_id = 'memorial-1'"
        )
        storage._conn.commit()  # noqa: SLF001 - corrupted row fixture
        with storage.unit_of_work() as uow, pytest.raises(decode_error, match="schema_version"):
            storage.run_state_repo.load(uow.connection, "memorial-1")
        storage._conn.execute(  # noqa: SLF001 - corrupted row fixture
            "UPDATE run_states SET schema_version = 1, continuation_kind = 'outer_loop' "
            "WHERE memorial_id = 'memorial-1'"
        )
        storage._conn.commit()  # noqa: SLF001 - corrupted row fixture
        with storage.unit_of_work() as uow, pytest.raises(decode_error, match="kind"):
            storage.run_state_repo.load(uow.connection, "memorial-1")
    finally:
        storage.close()


_SENSITIVE_ARGUMENTS = (
    {"credentials": "opaque-credential"},
    {"headers": {"Authorization": "Bearer opaque-auth-token"}},
    {"cookie": "session=opaque-cookie"},
    {"db_url": "postgres://user:opaque-password@db.example/app"},
    {"client_secret": "opaque-client-secret"},
    {"refresh_token": "opaque-refresh-token"},
    {"credentials": {"nested": [{"value": "opaque-nested-secret"}]}},
    {"api_key": "[REDACTED] smuggled-raw-value"},
)

_SENSITIVE_ALIAS_KEYS = (
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


@pytest.mark.parametrize("operation", ("create", "cas"))
@pytest.mark.parametrize("arguments", _SENSITIVE_ARGUMENTS)
def test_sensitive_payloads_are_rejected_with_zero_create_or_cas_writes(
    operation: str, arguments: dict[str, object]
) -> None:
    *_, secret_error = _contracts()
    storage = _storage()
    try:
        original = _state()
        if operation == "cas":
            with storage.unit_of_work() as uow:
                storage.run_state_repo.create(uow.connection, original)
                uow.commit()

        with pytest.raises(secret_error, match="secret"), storage.unit_of_work() as uow:
            candidate = _state(arguments)
            if operation == "create":
                storage.run_state_repo.create(uow.connection, candidate)
            else:
                storage.run_state_repo.compare_and_swap(
                    uow.connection,
                    candidate.model_copy(
                        update={"updated_at": candidate.updated_at + timedelta(seconds=1)}
                    ),
                    expected_version=1,
                )

        with storage.unit_of_work() as uow:
            durable = storage.run_state_repo.load(uow.connection, "memorial-1")
        assert durable == (original if operation == "cas" else None)
        dump = "".join(storage._conn.iterdump())  # noqa: SLF001 - zero-write contract
        for value in _flatten_strings(arguments):
            assert value not in dump
    finally:
        storage.close()


@pytest.mark.parametrize("operation", ("create", "cas"))
@pytest.mark.parametrize("alias_key", _SENSITIVE_ALIAS_KEYS)
def test_sensitive_alias_variants_are_rejected_with_zero_create_or_cas_writes(
    operation: str,
    alias_key: str,
) -> None:
    *_, secret_error = _contracts()
    storage = _storage()
    original = _state()
    raw_value = f"opaque-{alias_key}-value"
    try:
        if operation == "cas":
            with storage.unit_of_work() as uow:
                storage.run_state_repo.create(uow.connection, original)
                uow.commit()

        with pytest.raises(secret_error, match="secret"), storage.unit_of_work() as uow:
            candidate = _state({alias_key: raw_value})
            if operation == "create":
                storage.run_state_repo.create(uow.connection, candidate)
            else:
                storage.run_state_repo.compare_and_swap(
                    uow.connection,
                    candidate.model_copy(
                        update={"updated_at": candidate.updated_at + timedelta(seconds=1)}
                    ),
                    expected_version=1,
                )

        with storage.unit_of_work() as uow:
            assert storage.run_state_repo.load(uow.connection, "memorial-1") == (
                original if operation == "cas" else None
            )
        assert raw_value not in "".join(storage._conn.iterdump())  # noqa: SLF001
    finally:
        storage.close()


def _flatten_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(item for nested in value.values() for item in _flatten_strings(nested))
    if isinstance(value, list):
        return tuple(item for nested in value for item in _flatten_strings(nested))
    return ()


def test_safe_secret_refs_redacted_markers_metrics_and_normal_keys_are_persistable() -> None:
    storage = _storage()
    arguments = {
        "api_key": "OPENAI_API_KEY",
        "refresh_token": "settings:eval_llm_api_key",
        "client_secret": "[REDACTED]",
        "token_count": 17,
        "prompt_tokens": 23.5,
        "tokenizer": "byte-pair",
    }
    state = _state(arguments)
    try:
        with storage.unit_of_work() as uow:
            assert storage.run_state_repo.create(uow.connection, state) == state
            uow.commit()
        with storage.unit_of_work() as uow:
            assert storage.run_state_repo.load(uow.connection, "memorial-1") == state
    finally:
        storage.close()


@pytest.mark.parametrize(
    "smuggled",
    (
        "OPENAI_API_KEY trailing",
        "gateway_test_secret_ref",
        "settings:Eval_LLM_API_KEY",
        "secret:legacy_ref",
        "[REDACTED API KEY] trailing",
    ),
)
def test_secret_reference_and_redacted_marker_must_match_the_full_value(smuggled: str) -> None:
    *_, secret_error = _contracts()
    storage = _storage()
    try:
        with pytest.raises(secret_error, match="secret"), storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, _state({"api_key": smuggled}))
        assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001
    finally:
        storage.close()
