"""Same-connection SQLite transaction ownership for application services."""

from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Literal, Protocol, Self


class _ReentrantLock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class SqliteUnitOfWork:
    """Own one explicit transaction on an existing Storage connection."""

    def __init__(self, connection: sqlite3.Connection, lock: _ReentrantLock) -> None:
        self._connection = connection
        self._lock = lock
        self._entered = False
        self._completed = False

    @property
    def connection(self) -> sqlite3.Connection:
        if not self._entered:
            raise RuntimeError("unit of work is not active")
        return self._connection

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("nested unit of work is not supported")
        self._lock.acquire()
        try:
            if self._connection.in_transaction:
                raise RuntimeError("nested SQLite transaction is not supported")
            self._connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        self._entered = True
        self._completed = False
        return self

    def commit(self) -> None:
        self._require_active()
        self._connection.commit()
        self._completed = True

    def rollback(self) -> None:
        self._require_active()
        if self._connection.in_transaction:
            self._connection.rollback()
        self._completed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, exc, traceback
        try:
            if not self._completed or self._connection.in_transaction:
                self._connection.rollback()
        finally:
            self._entered = False
            self._lock.release()
        return False

    def _require_active(self) -> None:
        if not self._entered or self._completed:
            raise RuntimeError("unit of work is not active")


__all__ = ["SqliteUnitOfWork"]
