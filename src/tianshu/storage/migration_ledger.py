"""Versioned, transactional SQLite schema migration ledger.

Migration callbacks receive the active connection but must not control its
transaction.  In particular, ``commit()``, ``rollback()``, explicit transaction
SQL, and ``executescript()`` are rejected while a callback is running.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

_CHECKSUM_PATTERN = re.compile(r"[0-9a-f]{64}")
_LEDGER_TABLE = "schema_migrations"
_TRANSACTION_STATEMENTS = {
    "BEGIN",
    "COMMIT",
    "END",
    "RELEASE",
    "ROLLBACK",
    "SAVEPOINT",
}
_CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""
_SQL_TOKEN_PATTERN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]"
    r"|!=|<>|<=|>=|==|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]"
)


def _sql_tokens(sql: str) -> tuple[str, ...]:
    return tuple(
        token.upper() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) else token
        for token in _SQL_TOKEN_PATTERN.findall(sql)
    )


def _expected_ledger_tokens() -> tuple[str, ...]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(_CREATE_LEDGER_SQL)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (_LEDGER_TABLE,),
        ).fetchone()
        assert row is not None and row[0] is not None
        return _sql_tokens(str(row[0]))
    finally:
        conn.close()


_EXPECTED_LEDGER_TOKENS = _expected_ledger_tokens()
_EXPECTED_LEDGER_COLUMNS = (
    ("version", "INTEGER", 0, None, 1, 0),
    ("name", "TEXT", 1, None, 0, 0),
    ("checksum", "TEXT", 1, None, 0, 0),
    ("applied_at", "TEXT", 1, None, 0, 0),
)


def _first_sql_keyword(sql: str) -> str | None:
    remaining = sql
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith(";"):
            remaining = remaining[1:]
            continue
        if remaining.startswith("--"):
            newline = remaining.find("\n")
            if newline == -1:
                return None
            remaining = remaining[newline + 1 :]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end == -1:
                return None
            remaining = remaining[comment_end + 2 :]
            continue
        break

    match = re.match(r"[A-Za-z]+", remaining)
    return match.group(0).upper() if match is not None else None


def _reject_transaction_sql(sql: str) -> None:
    keyword = _first_sql_keyword(sql)
    if keyword in _TRANSACTION_STATEMENTS:
        raise MigrationTransactionError(
            f"migration callbacks must not control transactions (attempted {keyword})"
        )


class MigrationCursor:
    """Read-only cursor result exposed to migration callbacks."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self.__cursor.lastrowid

    def fetchone(self) -> Any | None:
        return self.__cursor.fetchone()

    def fetchmany(self, size: int | None = None) -> list[Any]:
        if size is None:
            return self.__cursor.fetchmany()
        return self.__cursor.fetchmany(size)

    def fetchall(self) -> list[Any]:
        return self.__cursor.fetchall()

    def __iter__(self) -> Iterator[Any]:
        yield from self.__cursor


class MigrationConnection:
    """Restricted connection facade that keeps transaction ownership internal."""

    __slots__ = ("__connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.__connection = connection

    @property
    def in_transaction(self) -> bool:
        return self.__connection.in_transaction

    def execute(
        self,
        sql: str,
        parameters: Sequence[Any] | Mapping[str, Any] = (),
        /,
    ) -> MigrationCursor:
        _reject_transaction_sql(sql)
        return MigrationCursor(self.__connection.execute(sql, parameters))

    def executemany(
        self,
        sql: str,
        parameters: Iterable[Sequence[Any] | Mapping[str, Any]],
        /,
    ) -> MigrationCursor:
        _reject_transaction_sql(sql)
        return MigrationCursor(self.__connection.executemany(sql, parameters))

    def commit(self) -> NoReturn:
        raise MigrationTransactionError("migration callbacks must not control transactions")

    def rollback(self) -> NoReturn:
        raise MigrationTransactionError("migration callbacks must not control transactions")

    def executescript(self, _sql_script: str, /) -> NoReturn:
        raise MigrationTransactionError("migration callbacks must not control transactions")


class MigrationError(RuntimeError):
    """Base error for migration definition, state, or execution failures."""


class MigrationDefinitionError(MigrationError):
    """A migration definition or definition sequence is invalid."""


class MigrationStateError(MigrationError):
    """The persisted ledger does not match the declared migration sequence."""


class MigrationTransactionError(MigrationError):
    """A caller or callback violated migration transaction ownership."""


class MigrationExecutionError(MigrationError):
    """A migration callback or ledger write failed."""


class MigrationIntegrityError(MigrationExecutionError):
    """SQLite integrity checks failed before a migration could be committed."""


@dataclass(frozen=True)
class Migration:
    """One immutable SQLite schema migration."""

    version: int
    name: str
    checksum: str
    upgrade: Callable[[MigrationConnection], None]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise MigrationDefinitionError("migration version must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise MigrationDefinitionError("migration name must be non-empty")
        if not isinstance(self.checksum, str) or _CHECKSUM_PATTERN.fullmatch(self.checksum) is None:
            raise MigrationDefinitionError(
                "migration checksum must contain exactly 64 lowercase hexadecimal characters"
            )
        if not callable(self.upgrade):
            raise MigrationDefinitionError("migration upgrade must be callable")


@dataclass(frozen=True)
class _AppliedMigration:
    version: int
    name: str
    checksum: str


def _validated_definitions(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
    definitions = tuple(migrations)
    previous_version = 0
    names: set[str] = set()

    for migration in definitions:
        if not isinstance(migration, Migration):
            raise MigrationDefinitionError("all migration definitions must be Migration instances")
        if migration.version <= previous_version:
            raise MigrationDefinitionError(
                "migration versions must be unique and strictly increasing"
            )
        if migration.name in names:
            raise MigrationDefinitionError("migration names must be unique")
        previous_version = migration.version
        names.add(migration.name)

    return definitions


def _ledger_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT type, sql FROM sqlite_master WHERE name = ?",
        (_LEDGER_TABLE,),
    ).fetchone()
    if row is None:
        return False
    if str(row[0]) != "table" or row[1] is None:
        raise MigrationStateError(f"{_LEDGER_TABLE} schema object must be a table")

    columns = tuple(
        (str(item[1]), str(item[2]).upper(), int(item[3]), item[4], int(item[5]), int(item[6]))
        for item in conn.execute(f"PRAGMA table_xinfo({_LEDGER_TABLE})").fetchall()
    )
    indexes = conn.execute(f"PRAGMA index_list({_LEDGER_TABLE})").fetchall()
    unique_name_index = indexes[0] if len(indexes) == 1 else None
    index_columns = (
        conn.execute(f'PRAGMA index_xinfo("{unique_name_index[1]}")').fetchall()
        if unique_name_index is not None
        else ()
    )
    key_columns = tuple(
        (str(item[2]), int(item[3]), str(item[4])) for item in index_columns if int(item[5]) == 1
    )
    table_row = next(
        (
            item
            for item in conn.execute("PRAGMA table_list").fetchall()
            if str(item[0]) == "main" and str(item[1]) == _LEDGER_TABLE
        ),
        None,
    )
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",
        (_LEDGER_TABLE,),
    ).fetchall()
    canonical_index = (
        unique_name_index is not None
        and int(unique_name_index[2]) == 1
        and str(unique_name_index[3]) == "u"
        and int(unique_name_index[4]) == 0
        and key_columns == (("name", 0, "BINARY"),)
    )
    canonical_options = (
        table_row is not None
        and str(table_row[2]) == "table"
        and int(table_row[4]) == 0
        and int(table_row[5]) == 0
    )
    if (
        _sql_tokens(str(row[1])) != _EXPECTED_LEDGER_TOKENS
        or columns != _EXPECTED_LEDGER_COLUMNS
        or not canonical_index
        or conn.execute(f"PRAGMA foreign_key_list({_LEDGER_TABLE})").fetchall()
        or not canonical_options
        or triggers
    ):
        raise MigrationStateError(f"{_LEDGER_TABLE} schema does not match the canonical ledger")
    return True


def _read_applied(conn: sqlite3.Connection) -> tuple[_AppliedMigration, ...]:
    if not _ledger_exists(conn):
        return ()
    rows = conn.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return tuple(_AppliedMigration(int(row[0]), str(row[1]), str(row[2])) for row in rows)


def _pending_from_definitions(
    conn: sqlite3.Connection, definitions: tuple[Migration, ...]
) -> tuple[Migration, ...]:
    applied = _read_applied(conn)
    versions = {migration.version for migration in definitions}

    for index, recorded in enumerate(applied):
        if recorded.version not in versions:
            raise MigrationStateError(
                f"unknown applied migration version {recorded.version} in {_LEDGER_TABLE}"
            )
        if index >= len(definitions) or recorded.version != definitions[index].version:
            raise MigrationStateError("applied migrations must be an exact definition prefix")

        declared = definitions[index]
        if recorded.name != declared.name:
            raise MigrationStateError(
                f"migration {declared.version} name drift: "
                f"ledger has {recorded.name!r}, definition has {declared.name!r}"
            )
        if recorded.checksum != declared.checksum:
            raise MigrationStateError(
                f"migration {declared.version} checksum drift: "
                f"ledger has {recorded.checksum!r}, definition has {declared.checksum!r}"
            )

    return definitions[len(applied) :]


def pending_migrations(
    conn: sqlite3.Connection, migrations: Iterable[Migration]
) -> tuple[Migration, ...]:
    """Return the unapplied definition suffix without modifying the database."""

    definitions = _validated_definitions(migrations)
    return _pending_from_definitions(conn, definitions)


def _run_upgrade(conn: sqlite3.Connection, migration: Migration) -> None:
    migration.upgrade(MigrationConnection(conn))
    if not conn.in_transaction:
        raise MigrationTransactionError(
            f"migration callback {migration.name!r} ended the owned transaction"
        )


def _check_integrity(conn: sqlite3.Connection, migration: Migration) -> None:
    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise MigrationIntegrityError(
            f"migration {migration.name!r} failed foreign_key_check: {foreign_key_errors!r}"
        )

    quick_check = conn.execute("PRAGMA quick_check").fetchall()
    if not quick_check or any(str(row[0]).lower() != "ok" for row in quick_check):
        raise MigrationIntegrityError(
            f"migration {migration.name!r} failed quick_check: {quick_check!r}"
        )


def apply_migrations(conn: sqlite3.Connection, migrations: Iterable[Migration]) -> tuple[int, ...]:
    """Apply each pending migration in its own immediate, atomic transaction."""

    if conn.in_transaction:
        raise MigrationTransactionError("cannot apply migrations inside an existing transaction")

    definitions = _validated_definitions(migrations)
    if not _pending_from_definitions(conn, definitions):
        return ()

    applied_versions: list[int] = []

    while True:
        migration: Migration | None = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            locked_pending = _pending_from_definitions(conn, definitions)
            if not locked_pending:
                conn.rollback()
                return tuple(applied_versions)

            migration = locked_pending[0]
            conn.execute(_CREATE_LEDGER_SQL)
            _run_upgrade(conn, migration)
            _check_integrity(conn, migration)
            conn.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        except BaseException as exc:
            if conn.in_transaction:
                conn.rollback()
            if isinstance(exc, MigrationError):
                raise
            if isinstance(exc, Exception):
                context = (
                    f"migration {migration.version} ({migration.name})"
                    if migration is not None
                    else "migration transaction"
                )
                raise MigrationExecutionError(f"{context} failed") from exc
            raise
        assert migration is not None
        applied_versions.append(migration.version)


def ledger_exists(conn: sqlite3.Connection) -> bool:
    """Return True when the migration ledger table exists, validating its shape."""

    return _ledger_exists(conn)


def adopt_migrations(conn: sqlite3.Connection, migrations: Iterable[Migration]) -> tuple[int, ...]:
    """Record every migration as applied without executing any upgrade callback.

    Used for pre-ledger databases whose schema already satisfies the complete
    migration sequence; the caller is responsible for proving that equivalence
    before adopting. The full ledger is written in one immediate transaction.
    """

    if conn.in_transaction:
        raise MigrationTransactionError("cannot adopt migrations inside an existing transaction")

    definitions = _validated_definitions(migrations)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _ledger_exists(conn):
            # A concurrent starter already recorded the ledger; nothing to adopt.
            conn.rollback()
            return ()
        conn.execute(_CREATE_LEDGER_SQL)
        adopted_at = datetime.now(UTC).isoformat()
        for migration in definitions:
            conn.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (migration.version, migration.name, migration.checksum, adopted_at),
            )
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise MigrationIntegrityError(
                f"migration adoption failed foreign_key_check: {foreign_key_errors!r}"
            )
        quick_check = conn.execute("PRAGMA quick_check").fetchall()
        if not quick_check or any(str(row[0]).lower() != "ok" for row in quick_check):
            raise MigrationIntegrityError(f"migration adoption failed quick_check: {quick_check!r}")
        conn.commit()
    except BaseException as exc:
        if conn.in_transaction:
            conn.rollback()
        if isinstance(exc, MigrationError):
            raise
        if isinstance(exc, Exception):
            raise MigrationExecutionError("migration adoption failed") from exc
        raise
    return tuple(migration.version for migration in definitions)


__all__ = [
    "Migration",
    "MigrationConnection",
    "MigrationCursor",
    "MigrationDefinitionError",
    "MigrationError",
    "MigrationExecutionError",
    "MigrationIntegrityError",
    "MigrationStateError",
    "MigrationTransactionError",
    "adopt_migrations",
    "apply_migrations",
    "ledger_exists",
    "pending_migrations",
]
