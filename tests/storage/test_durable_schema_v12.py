"""Live v12 additive guards for durable Decision and RunState rows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    MigrationExecutionError,
    MigrationStateError,
    apply_migrations,
    pending_migrations,
)
from tianshu.storage.migrations import MIGRATIONS

_FROZEN_V1_TO_V11_DEFINITIONS = (
    (
        1,
        "0001_adopt_v042_baseline",
        "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6",
    ),
    (2, "0002_auth_tokens", "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"),
    (
        3,
        "0003_governance_contracts",
        "07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8",
    ),
    (
        4,
        "0004_workspace_foundation",
        "1c0a028e0ea16475b9de5eb0c843f81aa275ddf62c0aca3c067bf8408dd9bee5",
    ),
    (
        5,
        "0005_governed_apply_bindings",
        "c73294984096ea15e32d6ce80294f82323408cda12e82efea645ad8f35c5abc6",
    ),
    (
        6,
        "0006_seed_default_personas",
        "596e672919bbe16b111fe3793e183b17666c7c5cad588d5532d7b2875501fca1",
    ),
    (
        7,
        "0007_system_audit_events",
        "b24d3152f2b5aaa2d7dbf5776a5c865d336e025e861f8ca110e8be0c6a42e10b",
    ),
    (
        8,
        "0008_encrypt_mcp_secret_mappings",
        "f03ad9148472267b754f6e4f1f03cefc947795c2a6717e0b89206b38244706ad",
    ),
    (
        9,
        "0009_durable_edict_ingress",
        "114d0d4daab66202b32a4f9e4eb4290e2e06602ecf9465ce4d5beae03aac0a98",
    ),
    (
        10,
        "0010_telegram_seen_instance_identity",
        "d0587f036178e5f36e25277df16528925823905cd35d8bba30e7a3a8ab680f67",
    ),
    (
        11,
        "0011_decisions_run_state",
        "b5c8d33e52ba3132ebad7d5d730c9df7423a5c3ed86d1d404a06b590f48e075e",
    ),
)


def _insert_parent_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "preserve", "2026-07-15T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", "2026-07-15T00:00:00+00:00"),
    )
    connection.commit()


def _insert_request(
    connection: sqlite3.Connection,
    *,
    decision_request_id: str = "decision-1",
    payload_json: str = "{}",
) -> None:
    connection.execute(
        """
        INSERT INTO decision_requests (
            decision_request_id, schema_version, kind, edict_id, memorial_id,
            request_key, payload_json, payload_hash, requested_by, expires_at,
            status, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_request_id,
            1,
            "tool",
            "edict-1",
            "memorial-1",
            "tool-call:1",
            payload_json,
            "0" * 64,
            "user:operator",
            "2026-07-15T01:10:00+00:00",
            "pending",
            1,
            "2026-07-15T01:00:00+00:00",
            "2026-07-15T01:00:00+00:00",
        ),
    )


def _insert_resolution(
    connection: sqlite3.Connection,
    *,
    payload_json: str = "{}",
    reason: str = "reviewed",
) -> None:
    connection.execute(
        """
        INSERT INTO decision_resolutions (
            decision_request_id, action, reason, payload_json,
            actor_principal_id, actor_display_name, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "decision-1",
            "approve",
            reason,
            payload_json,
            "user:reviewer",
            "Reviewer",
            "2026-07-15T01:01:00+00:00",
        ),
    )


def _insert_run_state(
    connection: sqlite3.Connection,
    continuation: object,
    *,
    checkpoint_ref: str | None = None,
    side_effect_cursor: int = 0,
) -> None:
    kind = continuation.get("kind", "agent") if isinstance(continuation, dict) else "agent"
    connection.execute(
        """
        INSERT INTO run_states (
            memorial_id, edict_id, schema_version, phase, continuation_kind,
            continuation_json, checkpoint_ref, side_effect_cursor,
            version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "memorial-1",
            "edict-1",
            1,
            "executing",
            kind,
            json.dumps(continuation),
            checkpoint_ref,
            side_effect_cursor,
            1,
            "2026-07-15T01:00:00+00:00",
            "2026-07-15T01:00:00+00:00",
        ),
    )


def _valid_agent_continuation() -> dict[str, object]:
    return {
        "kind": "agent",
        "messages": [],
        "pending_tool": None,
        "iteration": 0,
        "usage": {},
        "checkpoint_ref": None,
        "resolved_decision_id": None,
        "side_effect_cursor": 0,
    }


def _valid_outer_loop_continuation() -> dict[str, object]:
    return {
        "kind": "outer_loop",
        "level": "L0",
        "iteration": 0,
        "best_output": None,
        "feedback": None,
        "steer": None,
        "history": [],
        "same_issue_streak": 0,
        "last_critic_issue_class": None,
        "l1_rounds_used": 0,
        "l2_rounds_used": 0,
        "consultation_advice": None,
        "usage": {},
        "total_cost_cny": "0",
        "checkpoint_ref": None,
        "resolved_decision_id": None,
        "side_effect_cursor": 0,
    }


def _without_key(payload: dict[str, object], key: str) -> dict[str, object]:
    return {name: value for name, value in payload.items() if name != key}


def test_live_migration_tail_is_v12_without_drifting_v1_to_v11() -> None:
    assert tuple((item.version, item.name, item.checksum) for item in MIGRATIONS[:11]) == (
        _FROZEN_V1_TO_V11_DEFINITIONS
    )
    assert tuple(item.version for item in MIGRATIONS[:12]) == tuple(range(1, 13))
    assert (MIGRATIONS[11].version, MIGRATIONS[11].name) == (
        12,
        "0012_decision_run_state_guards",
    )


def test_v12_requires_decision_payload_json_objects() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        apply_migrations(connection, MIGRATIONS)
        _insert_parent_rows(connection)

        with pytest.raises(sqlite3.IntegrityError, match="payload.*object"):
            _insert_request(connection, payload_json="[]")
        _insert_request(connection)
        with pytest.raises(sqlite3.IntegrityError, match="payload.*object"):
            _insert_resolution(connection, payload_json='"scalar"')
    finally:
        connection.close()


@pytest.mark.parametrize(
    "continuation",
    (
        [],
        {"kind": "agent"},
        {"kind": "outer_loop", "history": []},
        _without_key(_valid_agent_continuation(), "kind"),
        _without_key(_valid_outer_loop_continuation(), "level"),
    ),
)
def test_v12_requires_typed_continuation_top_level_shape(continuation: object) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        apply_migrations(connection, MIGRATIONS)
        _insert_parent_rows(connection)
        with pytest.raises(sqlite3.IntegrityError, match="continuation.*shape"):
            _insert_run_state(connection, continuation)
    finally:
        connection.close()


def test_v12_rejects_insert_or_replace_with_recursive_triggers_disabled() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=OFF")
    try:
        apply_migrations(connection, MIGRATIONS)
        _insert_parent_rows(connection)
        _insert_request(connection)
        _insert_resolution(connection)
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_resolutions (
                    decision_request_id, action, reason, payload_json,
                    actor_principal_id, actor_display_name, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "decision-1",
                    "approve",
                    "replaced",
                    "{}",
                    "user:reviewer",
                    "Reviewer",
                    "2026-07-15T01:02:00+00:00",
                ),
            )
        assert connection.execute(
            "SELECT reason FROM decision_resolutions WHERE decision_request_id = 'decision-1'"
        ).fetchone() == ("reviewed",)
    finally:
        connection.close()


def test_v12_rejects_request_unique_identity_replacement_with_a_new_primary_id() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=OFF")
    try:
        apply_migrations(connection, MIGRATIONS)
        _insert_parent_rows(connection)
        _insert_request(connection)
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_requests (
                    decision_request_id, schema_version, kind, edict_id, memorial_id,
                    request_key, payload_json, payload_hash, requested_by, expires_at,
                    status, version, created_at, updated_at
                )
                SELECT ?, schema_version, kind, edict_id, memorial_id,
                       request_key, payload_json, payload_hash, requested_by, expires_at,
                       status, version, created_at, updated_at
                FROM decision_requests
                WHERE decision_request_id = ?
                """,
                ("decision-replacement", "decision-1"),
            )
        assert connection.execute(
            "SELECT decision_request_id FROM decision_requests"
        ).fetchall() == [("decision-1",)]
    finally:
        connection.close()


def test_v12_requires_run_state_column_and_continuation_mirrors_on_insert_and_update() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        apply_migrations(connection, MIGRATIONS)
        _insert_parent_rows(connection)
        with pytest.raises(sqlite3.IntegrityError, match="continuation.*shape"):
            _insert_run_state(
                connection,
                _valid_agent_continuation() | {"side_effect_cursor": 1},
                side_effect_cursor=0,
            )

        _insert_run_state(connection, _valid_agent_continuation())
        with pytest.raises(sqlite3.IntegrityError, match="continuation.*shape"):
            connection.execute(
                "UPDATE run_states SET checkpoint_ref = 'artifact:other' "
                "WHERE memorial_id = 'memorial-1'"
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "seed_invalid_row",
    (
        lambda connection: _insert_request(connection, payload_json="[]"),
        lambda connection: (
            _insert_request(connection),
            _insert_resolution(connection, payload_json="null"),
        ),
        lambda connection: _insert_run_state(connection, {"kind": "agent"}),
        lambda connection: _insert_run_state(
            connection, _without_key(_valid_agent_continuation(), "kind")
        ),
        lambda connection: _insert_run_state(
            connection, _without_key(_valid_outer_loop_continuation(), "level")
        ),
        lambda connection: _insert_run_state(
            connection,
            _valid_agent_continuation() | {"side_effect_cursor": 1},
            side_effect_cursor=0,
        ),
    ),
)
def test_v11_to_v12_rejects_invalid_existing_rows_atomically(seed_invalid_row) -> None:  # type: ignore[no-untyped-def]
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:11]) == tuple(range(1, 12))
        _insert_parent_rows(connection)
        seed_invalid_row(connection)
        connection.commit()

        with pytest.raises(MigrationExecutionError, match="0012_decision_run_state_guards"):
            apply_migrations(connection, MIGRATIONS)

        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (11,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%_v12'"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_valid_v11_rows_upgrade_to_v12_and_are_preserved() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert apply_migrations(connection, MIGRATIONS[:11]) == tuple(range(1, 12))
        _insert_parent_rows(connection)
        _insert_request(connection)
        _insert_resolution(connection)
        _insert_run_state(connection, _valid_agent_continuation())
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS[:12]) == (12,)
        assert connection.execute("SELECT payload_json FROM decision_requests").fetchall() == [
            ("{}",)
        ]
        assert connection.execute("SELECT continuation_kind FROM run_states").fetchall() == [
            ("agent",)
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_v12_failure_rolls_back_guards_and_ledger() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert apply_migrations(connection, MIGRATIONS[:11]) == tuple(range(1, 12))
        migration = MIGRATIONS[11]

        def fail_after_upgrade(active: MigrationConnection) -> None:
            migration.upgrade(active)
            raise RuntimeError("stop after v12")

        failing = Migration(
            version=migration.version,
            name=migration.name,
            checksum=migration.checksum,
            upgrade=fail_after_upgrade,
        )
        with pytest.raises(MigrationExecutionError, match=migration.name):
            apply_migrations(connection, (*MIGRATIONS[:11], failing))

        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (11,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%_v12'"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_applied_v12_checksum_drift_is_rejected_without_writes() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert apply_migrations(connection, MIGRATIONS[:12]) == tuple(range(1, 13))
        drifted = (*MIGRATIONS[:11], replace(MIGRATIONS[11], checksum="f" * 64))
        before = connection.total_changes
        with pytest.raises(MigrationStateError, match="checksum drift"):
            pending_migrations(connection, drifted)
        assert connection.total_changes == before
    finally:
        connection.close()


def test_concurrent_v12_upgrade_executes_callback_once(tmp_path: Path) -> None:
    database = tmp_path / "v12-concurrent.sqlite3"
    setup = sqlite3.connect(database)
    try:
        assert apply_migrations(setup, MIGRATIONS[:11]) == tuple(range(1, 12))
    finally:
        setup.close()

    connections = (
        sqlite3.connect(database, timeout=5, check_same_thread=False),
        sqlite3.connect(database, timeout=5, check_same_thread=False),
    )
    migration = MIGRATIONS[11]
    callback_entered = Event()
    release_callback = Event()
    calls_lock = Lock()
    callback_calls = 0
    results: list[tuple[int, ...]] = []
    errors: list[BaseException] = []

    def controlled_upgrade(active: MigrationConnection) -> None:
        nonlocal callback_calls
        with calls_lock:
            callback_calls += 1
        callback_entered.set()
        if not release_callback.wait(timeout=5):
            raise TimeoutError("test did not release v12 migration")
        migration.upgrade(active)

    controlled = replace(migration, upgrade=controlled_upgrade)

    def run(connection: sqlite3.Connection) -> None:
        try:
            results.append(apply_migrations(connection, (*MIGRATIONS[:11], controlled)))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=run, args=(connection,)) for connection in connections]
    try:
        threads[0].start()
        assert callback_entered.wait(timeout=5)
        threads[1].start()
        release_callback.set()
        for thread in threads:
            thread.join(timeout=5)

        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(results) == [(), (12,)]
        assert callback_calls == 1
        assert connections[0].execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 12"
        ).fetchone() == (1,)
    finally:
        release_callback.set()
        for thread in threads:
            thread.join(timeout=5)
        for connection in connections:
            connection.close()
