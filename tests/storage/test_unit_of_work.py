from __future__ import annotations

import sqlite3
from threading import RLock

import pytest

from tianshu.storage.unit_of_work import SqliteUnitOfWork


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    return connection


def test_registered_failure_is_raised_only_after_commit_is_durable() -> None:
    connection = _connection()
    unit_of_work = SqliteUnitOfWork(connection, RLock())

    with pytest.raises(RuntimeError, match="fail closed"), unit_of_work:
        connection.execute("INSERT INTO values_table (value) VALUES ('durable')")
        unit_of_work.fail_after_commit(RuntimeError("fail closed"))
        assert unit_of_work.has_post_commit_failure is True
        unit_of_work.commit()

    assert connection.execute("SELECT value FROM values_table").fetchall() == [("durable",)]


def test_rollback_discards_registered_failure_and_transaction() -> None:
    connection = _connection()
    unit_of_work = SqliteUnitOfWork(connection, RLock())

    with unit_of_work:
        connection.execute("INSERT INTO values_table (value) VALUES ('discarded')")
        unit_of_work.fail_after_commit(RuntimeError("must not escape"))
        unit_of_work.rollback()

    assert connection.execute("SELECT value FROM values_table").fetchall() == []
