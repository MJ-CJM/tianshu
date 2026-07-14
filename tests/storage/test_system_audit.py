"""Immutable, tamper-evident system audit storage contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage import Storage
from tianshu.storage.system_audit_repo import SystemAuditIntegrityError

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_GENESIS_HASH = "0" * 64
_MAX_PAGE_SIZE = 1000


@pytest.fixture
def audit_storage(tmp_path: Path) -> Iterator[tuple[Storage, Path]]:
    database = tmp_path / "system-audit.sqlite3"
    storage = Storage(str(database))
    storage.init_db()
    yield storage, database
    storage.close()


def _request(
    *,
    action: str = "auth.token.issued",
    metadata: dict[str, str | int | bool | None] | None = None,
) -> AppendSystemAuditRequest:
    return AppendSystemAuditRequest(
        correlation_id="correlation-1",
        actor_digest=_DIGEST_A,
        action=action,
        outcome="succeeded",
        reason_code="policy_allowed",
        subject_kind="auth_token",
        subject_digest=_DIGEST_B,
        metadata=metadata or {},
    )


def _canonical_row_hash(row: sqlite3.Row) -> str:
    payload = dict(row)
    payload.pop("event_hash")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _drop_update_trigger(storage: Storage) -> None:
    storage._conn.execute("DROP TRIGGER system_audit_events_no_update")


def test_append_builds_genesis_and_contiguous_exact_canonical_hash_chain(
    audit_storage: tuple[Storage, Path],
) -> None:
    storage, _ = audit_storage

    first = storage.append_system_audit(
        _request(metadata={"token_type": "pat", "scope_count": 2})
    )
    second = storage.append_system_audit(
        _request(
            action="estop.engaged",
            metadata={"kill_all": True, "network_kill": False, "frozen_tool_count": 1},
        )
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert first.previous_hash == _GENESIS_HASH
    assert second.previous_hash == first.event_hash
    assert first.schema_version == second.schema_version == 1
    assert first.created_at.tzinfo is not None
    with pytest.raises(ValidationError, match="frozen"):
        first.sequence = 99  # type: ignore[misc]

    rows = storage._conn.execute(
        "SELECT * FROM system_audit_events ORDER BY sequence"
    ).fetchall()
    assert len(rows) == 2
    assert [row["sequence"] for row in rows] == [1, 2]
    assert all(row["event_hash"] == _canonical_row_hash(row) for row in rows)
    assert [event.event_hash for event in storage.list_system_audit()] == [
        first.event_hash,
        second.event_hash,
    ]


def test_metadata_is_action_specific_fail_closed_and_never_persists_secret(
    audit_storage: tuple[Storage, Path],
) -> None:
    storage, database = audit_storage
    secret_sentinel = "SECRET_SENTINEL_MUST_NOT_REACH_SQLITE"

    with pytest.raises(ValidationError, match="metadata keys are not allowed"):
        _request(metadata={"token": secret_sentinel})
    with pytest.raises(ValidationError, match="metadata keys are not allowed"):
        _request(action="estop.engaged", metadata={"token_type": "pat"})

    event = storage.append_system_audit(
        _request(metadata={"token_type": "pat", "scope_count": 2})
    )
    row = storage._conn.execute(
        "SELECT metadata_json FROM system_audit_events WHERE sequence = ?",
        (event.sequence,),
    ).fetchone()
    assert row["metadata_json"] == '{"scope_count":2,"token_type":"pat"}'
    assert secret_sentinel not in row["metadata_json"]
    for path in (database, database.with_name(f"{database.name}-wal")):
        if path.exists():
            assert secret_sentinel.encode("utf-8") not in path.read_bytes()


def test_page_validates_predecessor_anchor_before_returning_events(
    audit_storage: tuple[Storage, Path],
) -> None:
    storage, _ = audit_storage
    storage.append_system_audit(_request(metadata={"token_type": "pat"}))
    second = storage.append_system_audit(_request(metadata={"token_type": "access"}))
    storage.append_system_audit(_request(metadata={"token_type": "refresh"}))

    page = storage.list_system_audit(after=1, limit=1)
    assert [event.sequence for event in page] == [2]
    assert page[0].event_hash == second.event_hash

    _drop_update_trigger(storage)
    storage._conn.execute(
        "UPDATE system_audit_events SET event_hash = ? WHERE sequence = 1",
        ("f" * 64,),
    )
    storage._conn.commit()

    with pytest.raises(
        SystemAuditIntegrityError,
        match=r"event_hash_mismatch at sequence 1$",
    ):
        storage.list_system_audit(after=1, limit=1)


def test_update_and_delete_triggers_unconditionally_reject_changes(
    audit_storage: tuple[Storage, Path],
) -> None:
    storage, _ = audit_storage
    event = storage.append_system_audit(_request())

    with pytest.raises(sqlite3.IntegrityError, match="system audit events are append-only"):
        storage._conn.execute(
            "UPDATE system_audit_events SET action = action WHERE sequence = ?",
            (event.sequence,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="system audit events are append-only"):
        storage._conn.execute(
            "DELETE FROM system_audit_events WHERE sequence = ?",
            (event.sequence,),
        )
    storage._conn.rollback()

    assert storage._conn.execute("SELECT COUNT(*) FROM system_audit_events").fetchone()[0] == 1


def test_verify_and_export_fail_stably_without_partial_data_after_tamper(
    audit_storage: tuple[Storage, Path],
) -> None:
    storage, _ = audit_storage
    first = storage.append_system_audit(_request(metadata={"token_type": "pat"}))
    second = storage.append_system_audit(_request(metadata={"token_type": "access"}))

    verification = storage.verify_system_audit()
    assert verification.verified is True
    assert verification.event_count == 2
    assert verification.start_sequence == 1
    assert verification.end_sequence == 2
    assert verification.terminal_hash == second.event_hash
    assert verification.failure_sequence is None
    assert verification.reason_code == "verified"

    exported = storage.export_system_audit()
    assert exported.start_sequence == 1
    assert exported.end_sequence == 2
    assert exported.terminal_hash == second.event_hash
    assert [event.event_hash for event in exported.events] == [
        first.event_hash,
        second.event_hash,
    ]

    _drop_update_trigger(storage)
    storage._conn.execute(
        "UPDATE system_audit_events SET metadata_json = '{}' WHERE sequence = 2"
    )
    storage._conn.commit()

    failed = storage.verify_system_audit()
    assert failed.verified is False
    assert failed.event_count == 1
    assert failed.failure_sequence == 2
    assert failed.reason_code == "event_hash_mismatch"
    with pytest.raises(
        SystemAuditIntegrityError,
        match=r"event_hash_mismatch at sequence 2$",
    ):
        storage.export_system_audit()


@pytest.mark.parametrize(
    ("after", "limit"),
    [(-1, 1), (0, 0), (0, -1), (0, _MAX_PAGE_SIZE + 1)],
)
def test_list_rejects_invalid_or_unbounded_page_arguments(
    audit_storage: tuple[Storage, Path], after: int, limit: int
) -> None:
    storage, _ = audit_storage

    with pytest.raises(ValueError):
        storage.list_system_audit(after=after, limit=limit)
