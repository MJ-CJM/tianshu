"""MCP persisted env/header mappings are encrypted by migration v8."""

from __future__ import annotations

import json
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tianshu.secrets import vault as vault_module
from tianshu.secrets.vault import SecretVault, reset_vault
from tianshu.storage import Storage
from tianshu.storage import _base as storage_base
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS
from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    merge_overrides,
)

_MIGRATION_NAME = "0008_encrypt_mcp_secret_mappings"
_DURABLE_INGRESS_MIGRATION_NAME = "0009_durable_edict_ingress"
_TELEGRAM_SEEN_IDENTITY_MIGRATION_NAME = "0010_telegram_seen_instance_identity"
_DECISIONS_RUN_STATE_MIGRATION_NAME = "0011_decisions_run_state"
_DECISION_RUN_STATE_GUARDS_MIGRATION_NAME = "0012_decision_run_state_guards"
_GOVERNED_APPLY_DECISION_BINDING_MIGRATION_NAME = "0013_governed_apply_decision_binding"
_EXECUTION_ATTEMPT_LEDGER_MIGRATION_NAME = "0014_execution_attempt_ledger"
_SIDE_EFFECT_JOURNAL_MIGRATION_NAME = "0015_side_effect_journal"
_ARTIFACTS_EVIDENCE_MIGRATION_NAME = "0016_artifacts_evidence"
_INTERNAL_NOTIFICATION_DELIVERY_MIGRATION_NAME = "0017_internal_notification_delivery"
_GOVERNED_EVOLUTION_CANDIDATES_MIGRATION_NAME = "0018_governed_evolution_candidates"
_ENV_SENTINEL = "mcp-env-sentinel-7c92f5"
_HEADER_SENTINEL = "mcp-header-sentinel-1ad843"


@pytest.fixture(autouse=True)
def _isolated_vault(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    reset_vault()
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    yield
    reset_vault()


@pytest.fixture
def master_key() -> str:
    return Fernet.generate_key().decode()


def _v7_migrations():  # type: ignore[no-untyped-def]
    return tuple(migration for migration in MIGRATIONS if migration.version <= 7)


def _create_v7_database(path: Path, rows: list[dict[str, Any]]) -> None:
    with closing(sqlite3.connect(path)) as connection:
        apply_migrations(connection, _v7_migrations())
        for row in rows:
            values = {
                "name": row["name"],
                "enabled": None,
                "env_json": None,
                "tools_include_json": None,
                "tools_exclude_json": None,
                "transport": None,
                "command": None,
                "args_json": None,
                "url": None,
                "headers_json": None,
                "default_tier": None,
                "timeout": None,
                "connect_timeout": None,
                "tool_overrides_json": None,
                "updated_at": "2026-07-14T00:00:00+00:00",
                **row,
            }
            connection.execute(
                """
                INSERT INTO mcp_server_overrides (
                    name, enabled, env_json, tools_include_json, tools_exclude_json,
                    transport, command, args_json, url, headers_json,
                    default_tier, timeout, connect_timeout, tool_overrides_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values[column] for column in values),
            )
        connection.commit()


def _legacy_state(path: Path) -> tuple[tuple[Any, ...], ...]:
    with closing(sqlite3.connect(path)) as connection:
        columns = tuple(
            tuple(row) for row in connection.execute("PRAGMA table_info(mcp_server_overrides)")
        )
        rows = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM mcp_server_overrides ORDER BY name"
            ).fetchall()
        )
        ledger = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
    return columns, rows, ledger


def _legacy_sensitive_backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.pre-migration-*.legacy-sensitive.bak"))


def _cause(error: pytest.ExceptionInfo[MigrationExecutionError]) -> BaseException:
    cause = error.value.__cause__
    assert cause is not None
    return cause


def test_migration_history_preserves_v7_through_v10() -> None:
    assert [(migration.version, migration.name) for migration in MIGRATIONS[6:10]] == [
        (7, "0007_system_audit_events"),
        (8, _MIGRATION_NAME),
        (9, _DURABLE_INGRESS_MIGRATION_NAME),
        (10, _TELEGRAM_SEEN_IDENTITY_MIGRATION_NAME),
    ]


def test_canonical_mapping_codec_sorts_compacts_and_preserves_utf8(master_key: str) -> None:
    vault = SecretVault(master_key)
    encrypt = vault_module.encrypt_canonical_mapping
    decrypt = vault_module.decrypt_canonical_mapping

    ciphertext = encrypt(vault, {"z": "last", "a": "秘密"})

    assert vault.decrypt(ciphertext) == '{"a":"秘密","z":"last"}'
    assert decrypt(vault, ciphertext) == {"a": "秘密", "z": "last"}


@pytest.mark.parametrize("payload", ["null", "[]", '{"TOKEN":7}', '{"TOKEN":true}'])
def test_canonical_mapping_codec_rejects_non_string_mappings(master_key: str, payload: str) -> None:
    vault = SecretVault(master_key)
    decrypt = vault_module.decrypt_canonical_mapping

    with pytest.raises(ValueError, match="^MCP secret mapping is invalid$"):
        decrypt(vault, vault.encrypt(payload))


def test_canonical_mapping_codec_rejects_wrong_key_without_disclosing_secret(
    master_key: str,
) -> None:
    encrypt = vault_module.encrypt_canonical_mapping
    decrypt = vault_module.decrypt_canonical_mapping
    ciphertext = encrypt(SecretVault(master_key), {"TOKEN": _ENV_SENTINEL})

    with pytest.raises(ValueError, match="^credential decryption failed$") as error:
        decrypt(SecretVault(Fernet.generate_key().decode()), ciphertext)

    assert _ENV_SENTINEL not in str(error.value)


def test_v8_migrates_null_empty_env_headers_and_preserves_non_secret_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "mcp.sqlite3"
    _create_v7_database(
        database_path,
        [
            {"name": "null-mappings"},
            {"name": "empty-mappings", "env_json": "{}", "headers_json": "{}"},
            {
                "name": "env-only",
                "env_json": json.dumps({"TOKEN": _ENV_SENTINEL, "ALPHA": "一"}),
                "transport": "stdio",
                "command": "npx",
                "args_json": '["-y","server"]',
            },
            {
                "name": "headers-only",
                "headers_json": json.dumps({"Authorization": f"Bearer {_HEADER_SENTINEL}"}),
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
            },
            {
                "name": "db-only",
                "enabled": 0,
                "env_json": json.dumps({"DB_SECRET": _ENV_SENTINEL}),
                "tools_include_json": '["read"]',
                "tools_exclude_json": '["write"]',
                "transport": "stdio",
                "command": "uvx",
                "args_json": '["db-server"]',
                "default_tier": 2,
                "timeout": 90,
                "connect_timeout": 15,
                "tool_overrides_json": '{"read":1}',
                "updated_at": "2026-07-14T01:02:03+00:00",
            },
        ],
    )
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)

    storage = Storage(str(database_path))
    storage.init_db()
    try:
        assert storage._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        rows = {row["name"]: row for row in storage.list_mcp_overrides()}
        assert rows["null-mappings"]["env"] is None
        assert rows["null-mappings"]["headers"] is None
        assert rows["empty-mappings"]["env"] == {}
        assert rows["empty-mappings"]["headers"] == {}
        assert rows["env-only"]["env"] == {"TOKEN": _ENV_SENTINEL, "ALPHA": "一"}
        assert rows["env-only"]["headers"] is None
        assert rows["headers-only"]["env"] is None
        assert rows["headers-only"]["headers"] == {"Authorization": f"Bearer {_HEADER_SENTINEL}"}
        assert rows["db-only"] == {
            "name": "db-only",
            "enabled": False,
            "env": {"DB_SECRET": _ENV_SENTINEL},
            "tools_include": ["read"],
            "tools_exclude": ["write"],
            "transport": "stdio",
            "command": "uvx",
            "args": ["db-server"],
            "url": None,
            "headers": None,
            "default_tier": 2,
            "timeout": 90,
            "connect_timeout": 15,
            "tool_overrides": {"read": 1},
        }

        columns = {
            row[1] for row in storage._conn.execute("PRAGMA table_info(mcp_server_overrides)")
        }
        assert {"env_json", "headers_json"}.isdisjoint(columns)
        assert {
            "env_ciphertext",
            "env_keys_json",
            "headers_ciphertext",
            "header_keys_json",
        } <= columns
        raw_empty = storage._conn.execute(
            """
            SELECT env_ciphertext, env_keys_json, headers_ciphertext, header_keys_json
            FROM mcp_server_overrides WHERE name = 'empty-mappings'
            """
        ).fetchone()
        assert raw_empty[0] is not None
        assert raw_empty[1] == "[]"
        assert raw_empty[2] is not None
        assert raw_empty[3] == "[]"
        raw_env = storage._conn.execute(
            "SELECT env_keys_json FROM mcp_server_overrides WHERE name = 'env-only'"
        ).fetchone()
        assert raw_env[0] == '["ALPHA","TOKEN"]'
        for active_file in (database_path, Path(f"{database_path}-wal")):
            if active_file.exists():
                content = active_file.read_bytes()
                assert _ENV_SENTINEL.encode() not in content
                assert _HEADER_SENTINEL.encode() not in content
    finally:
        storage.close()

    assert _legacy_sensitive_backups(database_path) == []


def test_v8_fails_before_backup_when_legacy_wal_checkpoint_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "busy-wal.sqlite3"
    wal_path = Path(f"{database_path}-wal")
    _create_v7_database(database_path, [])
    writer = sqlite3.connect(database_path)
    reader = sqlite3.connect(database_path)
    storage = Storage(str(database_path))
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA busy_timeout=1")
        reader.execute("BEGIN")
        assert reader.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (7,)
        writer.execute(
            """
            INSERT INTO mcp_server_overrides (name, env_json, updated_at)
            VALUES ('busy-secret', ?, '2026-07-14T02:00:00+00:00')
            """,
            (json.dumps({"TOKEN": _ENV_SENTINEL}),),
        )
        writer.commit()
        assert wal_path.exists()
        assert _ENV_SENTINEL.encode() in wal_path.read_bytes()
        checkpoint = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint is not None
        assert checkpoint[0] != 0
        monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)

        failure: RuntimeError | None = None
        try:
            storage.init_db()
        except RuntimeError as error:
            failure = error
            assert str(error) == "sensitive migration WAL checkpoint is busy"
        else:
            applied_version = storage._conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            wal_contains_sentinel = (
                wal_path.exists() and _ENV_SENTINEL.encode() in wal_path.read_bytes()
            )
            pytest.fail(
                "busy sensitive migration succeeded: "
                f"ledger={applied_version}, wal_contains_sentinel={wal_contains_sentinel}"
            )

        assert failure is not None
        assert _ENV_SENTINEL not in str(failure)
        assert storage._conn is None
        assert _legacy_sensitive_backups(database_path) == []
        with closing(sqlite3.connect(database_path)) as current:
            columns = {row[1] for row in current.execute("PRAGMA table_info(mcp_server_overrides)")}
            assert {"env_json", "headers_json"} <= columns
            assert "env_ciphertext" not in columns
            assert current.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (7,)
            assert current.execute(
                "SELECT env_json FROM mcp_server_overrides WHERE name = 'busy-secret'"
            ).fetchone() == (json.dumps({"TOKEN": _ENV_SENTINEL}),)
    finally:
        storage.close()
        reader.rollback()
        reader.close()
        writer.close()

    retry = Storage(str(database_path))
    retry.init_db()
    try:
        [row] = retry.list_mcp_overrides()
        assert row["env"] == {"TOKEN": _ENV_SENTINEL}
        assert (
            retry._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            == MIGRATIONS[-1].version
        )
        for active_file in (database_path, wal_path):
            if active_file.exists():
                assert _ENV_SENTINEL.encode() not in active_file.read_bytes()
    finally:
        retry.close()
    assert _legacy_sensitive_backups(database_path) == []


def test_v8_resumes_post_migration_wal_cleanup_while_backup_marker_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "resume-wal-cleanup.sqlite3"
    wal_path = Path(f"{database_path}-wal")
    _create_v7_database(
        database_path,
        [
            {
                "name": "cleanup-secret",
                "env_json": json.dumps({"TOKEN": _ENV_SENTINEL}),
            }
        ],
    )
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)

    real_run_migrations = storage_base.run_migrations
    preflight_complete = threading.Event()
    reader_pinned = threading.Event()
    phase_calls = 0

    def run_after_reader_pin(connection: sqlite3.Connection) -> None:
        nonlocal phase_calls
        connection.execute("PRAGMA busy_timeout=1")
        phase_calls += 1
        if phase_calls == 1:
            preflight_complete.set()
            assert reader_pinned.wait(timeout=5), "reader did not pin the pre-v8 snapshot"
        real_run_migrations(connection)

    monkeypatch.setattr(storage_base, "run_migrations", run_after_reader_pin)
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)

    first = Storage(str(database_path))
    first_errors: list[BaseException] = []

    def initialize_first() -> None:
        try:
            first.init_db()
        except BaseException as error:
            first_errors.append(error)

    reader: sqlite3.Connection | None = sqlite3.connect(database_path)
    first_thread = threading.Thread(target=initialize_first, daemon=True)
    first_thread.start()
    try:
        assert preflight_complete.wait(timeout=5), "migration did not reach the phase hook"
        reader.execute("BEGIN")
        assert reader.execute(
            "SELECT env_json FROM mcp_server_overrides WHERE name = 'cleanup-secret'"
        ).fetchone() == (json.dumps({"TOKEN": _ENV_SENTINEL}),)
        assert reader.in_transaction
        reader_pinned.set()
        first_thread.join(timeout=5)
        assert not first_thread.is_alive(), "first initialization did not finish"

        assert len(first_errors) == 1
        assert isinstance(first_errors[0], RuntimeError)
        assert str(first_errors[0]) == "sensitive migration WAL checkpoint is busy"
        assert first._conn is None
        [backup_path] = _legacy_sensitive_backups(database_path)
        with closing(sqlite3.connect(database_path)) as current:
            assert current.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (
                MIGRATIONS[-1].version,
            )
        assert _ENV_SENTINEL.encode() in database_path.read_bytes()

        second = Storage(str(database_path))
        try:
            second.init_db()
        except RuntimeError as error:
            assert str(error) == "sensitive migration WAL checkpoint is busy"
        else:
            second.close()
            with closing(sqlite3.connect(database_path)) as current:
                ledger = current.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            sentinel_present = any(
                active_file.exists() and _ENV_SENTINEL.encode() in active_file.read_bytes()
                for active_file in (database_path, wal_path)
            )
            pytest.fail(
                "sensitive cleanup marker was ignored while reader remained pinned: "
                f"ledger={ledger}, active_contains_sentinel={sentinel_present}"
            )
        assert second._conn is None
        assert _legacy_sensitive_backups(database_path) == [backup_path]

        reader.rollback()
        reader.close()
        reader = None

        third = Storage(str(database_path))
        third.init_db()
        try:
            assert (
                third._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                == MIGRATIONS[-1].version
            )
            assert third.list_mcp_overrides()[0]["env"] == {"TOKEN": _ENV_SENTINEL}
            for active_file in (database_path, wal_path):
                if active_file.exists():
                    assert _ENV_SENTINEL.encode() not in active_file.read_bytes()
            assert _legacy_sensitive_backups(database_path) == []
        finally:
            third.close()
    finally:
        reader_pinned.set()
        first_thread.join(timeout=5)
        first.close()
        if reader is not None:
            reader.rollback()
            reader.close()


def test_v8_prior_prefix_upgrade_preserves_yaml_override_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "prior-prefix.sqlite3"
    _create_v7_database(
        database_path,
        [
            {
                "name": "yaml-server",
                "env_json": json.dumps({"TOKEN": _ENV_SENTINEL}),
                "headers_json": json.dumps({"X-Secret": _HEADER_SENTINEL}),
            }
        ],
    )
    assert _legacy_state(database_path)[2][-1][0] == 7
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)

    storage = Storage(str(database_path))
    storage.init_db()
    try:
        [override] = storage.list_mcp_overrides()
        merged = merge_overrides(
            MCPConfig(
                mcp_servers={
                    "yaml-server": MCPServerConfig(
                        name="yaml-server", transport="stdio", command="from-yaml"
                    )
                }
            ),
            # Storage dictionaries intentionally cross the public override boundary here.
            [MCPServerOverride(**override)],
        )
        server = merged.mcp_servers["yaml-server"]
        assert server.command == "from-yaml"
        assert server.env == {"TOKEN": _ENV_SENTINEL}
        assert server.headers == {"X-Secret": _HEADER_SENTINEL}
        ledger = [
            tuple(row)
            for row in storage._conn.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        # 从第 8 条起的全部（正向切片，不随尾部追加迁移而错位）
        assert ledger[7:] == [
            (8, _MIGRATION_NAME),
            (9, _DURABLE_INGRESS_MIGRATION_NAME),
            (10, _TELEGRAM_SEEN_IDENTITY_MIGRATION_NAME),
            (11, _DECISIONS_RUN_STATE_MIGRATION_NAME),
            (12, _DECISION_RUN_STATE_GUARDS_MIGRATION_NAME),
            (13, _GOVERNED_APPLY_DECISION_BINDING_MIGRATION_NAME),
            (14, _EXECUTION_ATTEMPT_LEDGER_MIGRATION_NAME),
            (15, _SIDE_EFFECT_JOURNAL_MIGRATION_NAME),
            (16, _ARTIFACTS_EVIDENCE_MIGRATION_NAME),
            (17, _INTERNAL_NOTIFICATION_DELIVERY_MIGRATION_NAME),
            (18, _GOVERNED_EVOLUTION_CANDIDATES_MIGRATION_NAME),
            (19, "0019_model_providers"),
            (20, "0020_encrypt_llm_config_keys"),
            (21, "0021_app_settings"),
            (22, "0022_legacy_assignment_cleanup"),
            (23, "0023_cost_cache_read_tokens"),
            (24, "0024_notification_channel_progress"),
            (25, "0025_persona_allowed_paths"),
            (26, "0026_persona_tier_enforcement"),
        ]
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("env_json", "expected_message"),
    [
        ("not-json", "MCP legacy secret mapping is invalid"),
        ("[]", "MCP legacy secret mapping is invalid"),
        ('{"TOKEN":7}', "MCP legacy secret mapping is invalid"),
    ],
)
def test_malformed_legacy_mapping_fails_before_schema_or_ledger_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    master_key: str,
    env_json: str,
    expected_message: str,
) -> None:
    database_path = tmp_path / "malformed.sqlite3"
    _create_v7_database(database_path, [{"name": "bad", "env_json": env_json}])
    before = _legacy_state(database_path)
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    storage = Storage(str(database_path))

    with pytest.raises(MigrationExecutionError) as error:
        storage.init_db()

    assert str(_cause(error)) == expected_message
    assert _legacy_state(database_path) == before


@pytest.mark.parametrize("key", [None, "not-a-fernet-key"])
def test_missing_or_malformed_master_key_fails_closed_and_keeps_one_private_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str | None,
) -> None:
    database_path = tmp_path / "missing-key.sqlite3"
    _create_v7_database(
        database_path,
        [{"name": "secret", "env_json": json.dumps({"TOKEN": _ENV_SENTINEL})}],
    )
    before = _legacy_state(database_path)
    if key is not None:
        monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", key)

    for _ in range(2):
        reset_vault()
        storage = Storage(str(database_path))
        with pytest.raises(MigrationExecutionError) as error:
            storage.init_db()
        assert str(_cause(error)) == "MCP secret vault unavailable"
        assert _legacy_state(database_path) == before

    backups = _legacy_sensitive_backups(database_path)
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert _ENV_SENTINEL.encode() in backups[0].read_bytes()
    assert Path(error.value.backup_path) == backups[0]  # type: ignore[attr-defined]


def test_migration_verifies_ciphertext_round_trip_before_schema_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    master_key: str,
) -> None:
    database_path = tmp_path / "round-trip.sqlite3"
    _create_v7_database(
        database_path,
        [{"name": "secret", "env_json": json.dumps({"TOKEN": _ENV_SENTINEL})}],
    )
    before = _legacy_state(database_path)
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    monkeypatch.setattr(
        vault_module,
        "decrypt_canonical_mapping",
        lambda _vault, _ciphertext: {"TOKEN": "changed"},
    )
    storage = Storage(str(database_path))

    with pytest.raises(MigrationExecutionError) as error:
        storage.init_db()

    assert str(_cause(error)) == "MCP secret mapping verification failed"
    assert _legacy_state(database_path) == before


def test_list_mcp_overrides_rejects_wrong_key_and_corrupt_ciphertext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "corrupt.sqlite3"
    _create_v7_database(
        database_path,
        [{"name": "secret", "env_json": json.dumps({"TOKEN": _ENV_SENTINEL})}],
    )
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    storage = Storage(str(database_path))
    storage.init_db()
    storage.close()

    reset_vault()
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    wrong_key_storage = Storage(str(database_path))
    wrong_key_storage.init_db()
    try:
        with pytest.raises(ValueError, match="^credential decryption failed$") as wrong_key:
            wrong_key_storage.list_mcp_overrides()
        assert _ENV_SENTINEL not in str(wrong_key.value)
    finally:
        wrong_key_storage.close()

    reset_vault()
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE mcp_server_overrides SET env_ciphertext = ? WHERE name = 'secret'",
            (b"corrupt-ciphertext",),
        )
        connection.commit()
    corrupt_storage = Storage(str(database_path))
    corrupt_storage.init_db()
    try:
        with pytest.raises(ValueError, match="^credential decryption failed$"):
            corrupt_storage.list_mcp_overrides()
    finally:
        corrupt_storage.close()


def test_upsert_encrypts_secret_mappings_and_preserves_nullable_patch_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    database_path = tmp_path / "upsert.sqlite3"
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    storage = Storage(str(database_path))
    storage.init_db()
    try:
        storage.upsert_mcp_override(
            "server",
            transport="stdio",
            command="npx",
            env={"TOKEN": _ENV_SENTINEL},
            headers={"Authorization": _HEADER_SENTINEL},
        )
        storage.upsert_mcp_override("server", enabled=False)

        [row] = storage.list_mcp_overrides()
        assert row["enabled"] is False
        assert row["env"] == {"TOKEN": _ENV_SENTINEL}
        assert row["headers"] == {"Authorization": _HEADER_SENTINEL}
        persisted = storage._conn.execute(
            """
            SELECT env_ciphertext, env_keys_json, headers_ciphertext, header_keys_json
            FROM mcp_server_overrides WHERE name = 'server'
            """
        ).fetchone()
        assert _ENV_SENTINEL.encode() not in bytes(persisted[0])
        assert persisted[1] == '["TOKEN"]'
        assert _HEADER_SENTINEL.encode() not in bytes(persisted[2])
        assert persisted[3] == '["Authorization"]'
    finally:
        storage.close()


def test_upsert_non_null_mapping_requires_master_key(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "missing-upsert-key.sqlite3"))
    storage.init_db()
    try:
        with pytest.raises(ValueError, match="^MCP secret vault unavailable$"):
            storage.upsert_mcp_override("server", env={"TOKEN": _ENV_SENTINEL})
        assert storage.list_mcp_overrides() == []
    finally:
        storage.close()
