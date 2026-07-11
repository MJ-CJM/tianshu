"""Persistent opaque authentication token repository contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tianshu.storage import Storage


def _record(
    token_id: str,
    *,
    prefix: str | None = None,
    token_hash: str | None = None,
    token_type: str = "pat",
    family_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": token_id,
        "prefix": prefix or token_id,
        "token_hash": token_hash or (token_id[-1] * 64),
        "principal_id": "user:owner",
        "principal_kind": "human",
        "display_name": "Owner",
        "label": "test token",
        "scopes": ["admin", "api"],
        "token_type": token_type,
        "family_id": family_id,
        "created_at": "2026-07-11T00:00:00+00:00",
        "expires_at": "2026-08-11T00:00:00+00:00",
    }


def test_auth_token_migration_is_versioned_and_contains_no_plaintext_column(
    tmp_path: Path,
) -> None:
    db = tmp_path / "auth.db"
    storage = Storage(str(db))
    storage.init_db()
    columns = {
        row[1] for row in storage._conn.execute("PRAGMA table_info(auth_tokens)").fetchall()
    }
    ledger = storage._conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    storage.close()

    assert [(row[0], row[1]) for row in ledger] == [
        (1, "0001_adopt_v042_baseline"),
        (2, "0002_auth_tokens"),
    ]
    assert "token_hash" in columns
    assert "token" not in columns
    assert "secret" not in columns


def test_auth_tokens_persist_hash_metadata_and_revocation_across_reopen(
    tmp_path: Path,
) -> None:
    db = tmp_path / "auth.db"
    storage = Storage(str(db))
    storage.init_db()
    storage.save_auth_token(_record("token-a", token_hash="a" * 64))

    row = storage.get_auth_token_by_prefix("token-a")
    assert row is not None
    assert row["token_hash"] == "a" * 64
    assert row["scopes"] == ["admin", "api"]
    assert "token" not in row
    assert storage.revoke_auth_token("token-a", "2026-07-12T00:00:00+00:00") is True
    storage.close()

    reopened = Storage(str(db))
    reopened.init_db()
    revoked = reopened.get_auth_token_by_prefix("token-a")
    assert revoked is not None
    assert revoked["revoked_at"] == "2026-07-12T00:00:00+00:00"
    reopened.close()


def test_replacing_auth_token_revokes_old_and_inserts_new_atomically(storage: Storage) -> None:
    storage.save_auth_token(_record("token-a", token_hash="a" * 64))

    storage.replace_auth_token(
        "token-a",
        _record("token-b", token_hash="b" * 64),
        "2026-07-12T00:00:00+00:00",
    )

    old = storage.get_auth_token_by_prefix("token-a")
    new = storage.get_auth_token_by_prefix("token-b")
    assert old is not None and new is not None
    assert old["revoked_at"] == "2026-07-12T00:00:00+00:00"
    assert old["replaced_by"] == "token-b"
    assert new["revoked_at"] is None


def test_duplicate_auth_token_prefix_does_not_partially_replace(storage: Storage) -> None:
    storage.save_auth_token(_record("token-a", token_hash="a" * 64))
    storage.save_auth_token(_record("token-b", token_hash="b" * 64))

    try:
        storage.replace_auth_token(
            "token-a",
            _record("token-c", prefix="token-b", token_hash="c" * 64),
            "2026-07-12T00:00:00+00:00",
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate prefix must fail")

    old = storage.get_auth_token_by_prefix("token-a")
    assert old is not None
    assert old["revoked_at"] is None
    assert old["replaced_by"] is None
