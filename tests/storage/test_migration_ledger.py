"""Transactional schema migration ledger contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event, Lock, Thread

import pytest

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    MigrationDefinitionError,
    MigrationExecutionError,
    MigrationIntegrityError,
    MigrationStateError,
    MigrationTransactionError,
    apply_migrations,
    pending_migrations,
)

_CHECKSUM_A = "a" * 64
_CHECKSUM_B = "b" * 64
_CHECKSUM_C = "c" * 64


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _noop(_conn: MigrationConnection) -> None:
    return None


def _migration(
    version: int,
    *,
    name: str | None = None,
    checksum: str | None = None,
    upgrade: Callable[[MigrationConnection], None] = _noop,
) -> Migration:
    return Migration(
        version=version,
        name=name or f"migration_{version}",
        checksum=checksum or chr(96 + version) * 64,
        upgrade=upgrade,
    )


def _create_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _record_applied(
    connection: sqlite3.Connection,
    migration: Migration,
    *,
    name: str | None = None,
    checksum: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO schema_migrations(version, name, checksum, applied_at)
        VALUES (?, ?, ?, '2026-07-11T00:00:00+00:00')
        """,
        (migration.version, name or migration.name, checksum or migration.checksum),
    )
    connection.commit()


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
def test_migration_requires_a_positive_integer_version(version: object) -> None:
    with pytest.raises(MigrationDefinitionError, match="positive integer"):
        Migration(
            version=version,  # type: ignore[arg-type]
            name="valid_name",
            checksum=_CHECKSUM_A,
            upgrade=_noop,
        )


@pytest.mark.parametrize("name", ["", " ", "\n\t"])
def test_migration_requires_a_non_empty_name(name: str) -> None:
    with pytest.raises(MigrationDefinitionError, match="non-empty"):
        Migration(version=1, name=name, checksum=_CHECKSUM_A, upgrade=_noop)


@pytest.mark.parametrize(
    "checksum",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64],
)
def test_migration_requires_a_lowercase_sha256_checksum(checksum: str) -> None:
    with pytest.raises(MigrationDefinitionError, match="64 lowercase hexadecimal"):
        Migration(version=1, name="valid_name", checksum=checksum, upgrade=_noop)


@pytest.mark.parametrize(
    "migrations",
    [
        (_migration(2), _migration(1)),
        (_migration(1), _migration(1, name="another_name")),
    ],
)
def test_definitions_require_strictly_increasing_unique_versions(
    conn: sqlite3.Connection, migrations: tuple[Migration, ...]
) -> None:
    with pytest.raises(MigrationDefinitionError, match="strictly increasing"):
        pending_migrations(conn, migrations)


def test_definitions_require_unique_names(conn: sqlite3.Connection) -> None:
    migrations = (
        _migration(1, name="same_name", checksum=_CHECKSUM_A),
        _migration(2, name="same_name", checksum=_CHECKSUM_B),
    )

    with pytest.raises(MigrationDefinitionError, match="unique"):
        pending_migrations(conn, migrations)


def test_pending_without_ledger_returns_all_without_writing(
    conn: sqlite3.Connection,
) -> None:
    migrations = (_migration(1), _migration(2))
    before_tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    before_changes = conn.total_changes

    assert pending_migrations(conn, migrations) == migrations

    assert conn.total_changes == before_changes
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        is None
    )
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        == before_tables
    )


@pytest.mark.parametrize(
    "ledger_sql",
    [
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TRIGGER corrupt_migration_ledger AFTER INSERT ON schema_migrations BEGIN
            DELETE FROM schema_migrations;
        END;
        """,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE INDEX unexpected_ledger_index ON schema_migrations(applied_at);
        """,
    ],
)
def test_malformed_empty_ledger_is_rejected_without_writes(
    conn: sqlite3.Connection, ledger_sql: str
) -> None:
    migration = _migration(1)
    conn.executescript(ledger_sql)
    before_schema = conn.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    before_changes = conn.total_changes

    with pytest.raises(MigrationStateError, match="schema"):
        pending_migrations(conn, (migration,))
    with pytest.raises(MigrationStateError, match="schema"):
        apply_migrations(conn, (migration,))

    assert conn.total_changes == before_changes
    assert (
        conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        == before_schema
    )
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (0,)


def test_pending_returns_only_suffix_after_exact_applied_prefix(
    conn: sqlite3.Connection,
) -> None:
    migrations = (
        _migration(1, checksum=_CHECKSUM_A),
        _migration(3, checksum=_CHECKSUM_B),
        _migration(7, checksum=_CHECKSUM_C),
    )
    _create_ledger(conn)
    _record_applied(conn, migrations[0])
    _record_applied(conn, migrations[1])

    assert pending_migrations(conn, migrations) == (migrations[2],)


def test_pending_rejects_an_unknown_applied_version(conn: sqlite3.Connection) -> None:
    migrations = (_migration(1), _migration(2))
    unknown = _migration(99, checksum="f" * 64)
    _create_ledger(conn)
    _record_applied(conn, migrations[0])
    _record_applied(conn, unknown)

    with pytest.raises(MigrationStateError, match="unknown applied migration"):
        pending_migrations(conn, migrations)


def test_pending_rejects_a_gap_in_the_applied_prefix(conn: sqlite3.Connection) -> None:
    migrations = (_migration(1), _migration(2))
    _create_ledger(conn)
    _record_applied(conn, migrations[1])

    with pytest.raises(MigrationStateError, match="prefix"):
        pending_migrations(conn, migrations)


@pytest.mark.parametrize(
    ("field", "override", "message"),
    [
        ("name", "renamed_migration", "name drift"),
        ("checksum", "f" * 64, "checksum drift"),
    ],
)
def test_pending_rejects_applied_definition_drift(
    conn: sqlite3.Connection, field: str, override: str, message: str
) -> None:
    migration = _migration(1)
    _create_ledger(conn)
    _record_applied(
        conn,
        migration,
        name=override if field == "name" else None,
        checksum=override if field == "checksum" else None,
    )

    with pytest.raises(MigrationStateError, match=message):
        pending_migrations(conn, (migration,))


def test_apply_runs_upgrade_checks_and_ledger_insert_in_one_immediate_transaction(
    conn: sqlite3.Connection,
) -> None:
    trace: list[str] = []
    conn.set_trace_callback(trace.append)

    def upgrade(connection: MigrationConnection) -> None:
        assert connection.in_transaction
        connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO example(value) VALUES ('kept')")

    migration = _migration(1, upgrade=upgrade)

    assert apply_migrations(conn, (migration,)) == (1,)
    assert conn.execute("SELECT value FROM example").fetchone() == ("kept",)
    assert conn.in_transaction is False
    assert [row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")] == [
        "version",
        "name",
        "checksum",
        "applied_at",
    ]
    applied = conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations"
    ).fetchone()
    assert applied[:3] == (1, migration.name, migration.checksum)
    assert isinstance(applied[3], str)
    assert applied[3]

    statements = [statement.strip().upper() for statement in trace]
    begin_index = statements.index("BEGIN IMMEDIATE")
    upgrade_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE TABLE EXAMPLE")
    )
    foreign_key_check_index = statements.index("PRAGMA FOREIGN_KEY_CHECK")
    quick_check_index = statements.index("PRAGMA QUICK_CHECK")
    ledger_insert_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("INSERT INTO SCHEMA_MIGRATIONS")
    )
    commit_index = statements.index("COMMIT")
    assert (
        begin_index
        < upgrade_index
        < foreign_key_check_index
        < quick_check_index
        < ledger_insert_index
        < commit_index
    )


def test_first_upgrade_failure_rolls_back_ddl_dml_and_ledger(
    conn: sqlite3.Connection,
) -> None:
    class UpgradeFailed(RuntimeError):
        pass

    def upgrade(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE doomed (value TEXT NOT NULL)")
        connection.execute("INSERT INTO doomed VALUES ('must roll back')")
        raise UpgradeFailed("boom")

    with pytest.raises(MigrationExecutionError, match="migration_1") as error:
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))

    assert isinstance(error.value.__cause__, UpgradeFailed)
    assert conn.in_transaction is False
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'doomed'").fetchone() is None
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        is None
    )


def test_each_pending_migration_has_its_own_atomic_transaction(
    conn: sqlite3.Connection,
) -> None:
    def first(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE durable (value TEXT NOT NULL)")
        connection.execute("INSERT INTO durable VALUES ('kept')")

    def second(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE discarded (value TEXT NOT NULL)")
        raise RuntimeError("second failed")

    migrations = (
        _migration(1, checksum=_CHECKSUM_A, upgrade=first),
        _migration(2, checksum=_CHECKSUM_B, upgrade=second),
    )

    with pytest.raises(MigrationExecutionError, match="migration_2"):
        apply_migrations(conn, migrations)

    assert conn.execute("SELECT value FROM durable").fetchone() == ("kept",)
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'discarded'").fetchone() is None
    )
    assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]


def test_apply_does_not_repeat_an_applied_migration(conn: sqlite3.Connection) -> None:
    calls = 0

    def upgrade(connection: MigrationConnection) -> None:
        nonlocal calls
        calls += 1
        connection.execute("CREATE TABLE once_only (id INTEGER PRIMARY KEY)")

    migration = _migration(1, upgrade=upgrade)

    assert apply_migrations(conn, (migration,)) == (1,)
    assert apply_migrations(conn, (migration,)) == ()
    assert calls == 1


def test_concurrent_apply_rechecks_ledger_after_acquiring_the_write_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    connection_a = sqlite3.connect(database, timeout=5, check_same_thread=False)
    connection_b = sqlite3.connect(database, timeout=5, check_same_thread=False)
    callback_entered = Event()
    release_callback = Event()
    connection_b_attempted_begin = Event()
    calls_lock = Lock()
    callback_calls = 0
    results: dict[str, tuple[int, ...]] = {}
    errors: dict[str, BaseException] = {}

    connection_b.set_trace_callback(
        lambda statement: (
            connection_b_attempted_begin.set()
            if statement.strip().upper() == "BEGIN IMMEDIATE"
            else None
        )
    )

    def upgrade(connection: MigrationConnection) -> None:
        nonlocal callback_calls
        with calls_lock:
            callback_calls += 1
            call_number = callback_calls
        if call_number == 1:
            callback_entered.set()
            if not release_callback.wait(timeout=5):
                raise TimeoutError("test did not release the first migration callback")
        connection.execute("CREATE TABLE applied_once (value TEXT NOT NULL)")
        connection.execute("INSERT INTO applied_once VALUES ('one callback')")

    migration = _migration(1, upgrade=upgrade)

    def run(label: str, connection: sqlite3.Connection) -> None:
        try:
            results[label] = apply_migrations(connection, (migration,))
        except BaseException as exc:
            errors[label] = exc

    thread_a = Thread(target=run, args=("a", connection_a))
    thread_b = Thread(target=run, args=("b", connection_b))
    try:
        thread_a.start()
        assert callback_entered.wait(timeout=5)
        thread_b.start()
        assert connection_b_attempted_begin.wait(timeout=5)
        assert callback_calls == 1
        release_callback.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)

        assert not thread_a.is_alive()
        assert not thread_b.is_alive()
        assert errors == {}
        assert results == {"a": (1,), "b": ()}
        assert callback_calls == 1
        assert connection_a.execute("SELECT value FROM applied_once").fetchall() == [
            ("one callback",)
        ]
        assert connection_a.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
    finally:
        release_callback.set()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
        connection_a.close()
        connection_b.close()


def test_foreign_key_check_failure_rolls_back_and_refuses_ledger_entry(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.commit()

    def upgrade(connection: MigrationConnection) -> None:
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")

    with pytest.raises(MigrationIntegrityError, match="foreign_key_check"):
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))

    assert conn.execute("SELECT * FROM child").fetchall() == []
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        is None
    )


class _FailingQuickCheckConnection(sqlite3.Connection):
    def execute(self, sql: str, parameters: tuple[object, ...] = (), /) -> sqlite3.Cursor:
        if sql.strip().upper() == "PRAGMA QUICK_CHECK":
            return super().execute("SELECT 'database disk image is malformed'")
        return super().execute(sql, parameters)


def test_quick_check_failure_rolls_back_and_refuses_ledger_entry() -> None:
    connection = sqlite3.connect(":memory:", factory=_FailingQuickCheckConnection)

    def upgrade(active_connection: MigrationConnection) -> None:
        active_connection.execute("CREATE TABLE doomed (id INTEGER PRIMARY KEY)")

    try:
        with pytest.raises(MigrationIntegrityError, match="quick_check"):
            apply_migrations(connection, (_migration(1, upgrade=upgrade),))

        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name = 'doomed'").fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_callback_receives_a_restricted_connection_without_a_raw_cursor(
    conn: sqlite3.Connection,
) -> None:
    def upgrade(connection: MigrationConnection) -> None:
        assert isinstance(connection, MigrationConnection)
        assert not isinstance(connection, sqlite3.Connection)
        assert not hasattr(connection, "connection")
        assert not hasattr(connection, "cursor")

        result = connection.execute("SELECT 1")
        assert result.fetchone() == (1,)
        assert not hasattr(result, "connection")
        assert not hasattr(result, "execute")
        connection.execute("CREATE TABLE through_facade (id INTEGER PRIMARY KEY)")

    assert apply_migrations(conn, (_migration(1, upgrade=upgrade),)) == (1,)


def test_cursor_iteration_does_not_leak_raw_connection_and_failure_rolls_back(
    conn: sqlite3.Connection,
) -> None:
    class CallbackFailed(RuntimeError):
        pass

    def upgrade(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE iteration_rollback (id INTEGER PRIMARY KEY)")
        iterator = iter(connection.execute("SELECT 1"))
        assert not hasattr(iterator, "connection")
        assert not hasattr(iterator, "execute")
        assert next(iterator) == (1,)
        assert list(iterator) == []
        raise CallbackFailed("rollback after safe iteration")

    with pytest.raises(MigrationExecutionError) as error:
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))

    assert isinstance(error.value.__cause__, CallbackFailed)
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'iteration_rollback'").fetchone()
        is None
    )
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        is None
    )


def test_caller_authorizer_remains_active_during_and_after_migration(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("CREATE TABLE protected (id INTEGER PRIMARY KEY)")
    conn.commit()
    denied_drops: list[str | None] = []

    def deny_drops(
        action_code: int,
        argument1: str | None,
        _argument2: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action_code == sqlite3.SQLITE_DROP_TABLE:
            denied_drops.append(argument1)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_drops)

    def upgrade(connection: MigrationConnection) -> None:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("DROP TABLE protected")
        connection.execute("CREATE TABLE authorizer_kept (id INTEGER PRIMARY KEY)")

    assert apply_migrations(conn, (_migration(1, upgrade=upgrade),)) == (1,)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute("DROP TABLE authorizer_kept")
    assert denied_drops == ["protected", "authorizer_kept"]


@pytest.mark.parametrize(
    "statement",
    [
        "BEGIN IMMEDIATE",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT callback_owned",
        "RELEASE callback_owned",
        "-- leading comment\nCOMMIT",
    ],
)
def test_callback_explicit_transaction_sql_is_rejected(
    conn: sqlite3.Connection, statement: str
) -> None:
    def upgrade(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE before_explicit_control (id INTEGER PRIMARY KEY)")
        connection.execute(statement)

    with pytest.raises(MigrationTransactionError, match="must not control transactions"):
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))

    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'before_explicit_control'"
        ).fetchone()
        is None
    )


def test_callback_executemany_rejects_transaction_control(conn: sqlite3.Connection) -> None:
    def upgrade(connection: MigrationConnection) -> None:
        connection.executemany("ROLLBACK", [()])

    with pytest.raises(MigrationTransactionError, match="must not control transactions"):
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))


@pytest.mark.parametrize("operation", ["commit", "executescript"])
def test_callback_transaction_control_is_rejected_and_rolled_back(
    conn: sqlite3.Connection, operation: str
) -> None:
    def upgrade(connection: MigrationConnection) -> None:
        connection.execute("CREATE TABLE before_forbidden_control (id INTEGER PRIMARY KEY)")
        if operation == "commit":
            connection.commit()
        else:
            connection.executescript("CREATE TABLE script_table (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationTransactionError, match="must not control transactions"):
        apply_migrations(conn, (_migration(1, upgrade=upgrade),))

    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'before_forbidden_control'"
        ).fetchone()
        is None
    )
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'script_table'").fetchone()
        is None
    )
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        is None
    )


def test_apply_fails_closed_inside_an_existing_transaction(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")

    with pytest.raises(MigrationTransactionError, match="existing transaction"):
        apply_migrations(conn, (_migration(1),))

    assert conn.in_transaction is True
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()
        is None
    )
    conn.rollback()
