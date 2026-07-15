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
        phase=RunPhase.WAITING_DECISION,
        continuation=continuation,
        checkpoint_ref="artifact:checkpoint-1",
        side_effect_cursor=0,
        version=1,
        created_at=now,
        updated_at=now,
    )


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


def test_load_rejects_unknown_schema_and_continuation_kind_mismatch() -> None:
    _, _, decode_error, _ = _contracts()
    storage = _storage()
    try:
        with storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, _state())
            uow.commit()
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


def test_secret_sentinel_is_rejected_before_any_raw_state_is_written() -> None:
    *_, secret_error = _contracts()
    storage = _storage()
    secret = "RUN_STATE_SECRET_SENTINEL"
    try:
        with pytest.raises(secret_error, match="secret"), storage.unit_of_work() as uow:
            storage.run_state_repo.create(uow.connection, _state({"api_key": secret}))
        assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001
        database_bytes = "".join(storage._conn.iterdump())  # noqa: SLF001 - sentinel contract
        assert secret not in database_bytes
    finally:
        storage.close()
