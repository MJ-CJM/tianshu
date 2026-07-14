"""Master-key rotation covers every encrypted secret family atomically."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from typer.testing import CliRunner

from tianshu.cli.commands import secrets as secrets_command
from tianshu.cli.commands.secrets import app
from tianshu.secrets.models import CredentialCreate
from tianshu.secrets.store import CredentialStore
from tianshu.secrets.vault import SecretVault, reset_vault
from tianshu.storage import Storage, sqlite_backup, system_audit_repo


@pytest.fixture
def keys() -> tuple[str, str]:
    return Fernet.generate_key().decode(), Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_vault():  # type: ignore[no-untyped-def]
    reset_vault()
    yield
    reset_vault()


def _seed_network_credential(storage: Storage, key: str, value: str = "network-secret") -> str:
    store = CredentialStore(storage, SecretVault(key))
    credential = store.create(
        CredentialCreate(name="k1", kind="engine_provider", value=value, provider_name="jina")
    )
    return credential.id


def _seed_all_families(storage: Storage, key: str) -> tuple[str, dict[str, bytes]]:
    fernet = Fernet(key.encode())
    credential_id = _seed_network_credential(storage, key)
    plaintexts = {
        "network": b"network-secret",
        "channel_config": b"channel-config-secret",
        "channel_instance": b"channel-instance-secret",
        "mcp_env": b'{"TOKEN":"mcp-env-secret"}',
        "mcp_headers": b'{"Authorization":"Bearer mcp-header-secret"}',
    }
    with storage._conn:
        storage._conn.execute(
            """
            INSERT INTO channel_configs (
                channel_type, config_json, encrypted_secret, updated_at
            ) VALUES ('feishu', '{}', ?, '2026-07-14T00:00:00+00:00')
            """,
            (fernet.encrypt(plaintexts["channel_config"]),),
        )
        storage._conn.execute(
            """
            INSERT INTO channel_instances (
                instance_id, channel_type, label, enabled, config_json,
                encrypted_secret, updated_at
            ) VALUES (
                'telegram-primary', 'telegram', 'primary', 1, '{}', ?,
                '2026-07-14T00:00:00+00:00'
            )
            """,
            (fernet.encrypt(plaintexts["channel_instance"]),),
        )
        storage._conn.execute(
            """
            INSERT INTO mcp_server_overrides (
                name, env_ciphertext, env_keys_json, headers_ciphertext,
                header_keys_json, updated_at
            ) VALUES (
                'encrypted-mappings', ?, '["TOKEN"]', ?, '["Authorization"]',
                '2026-07-14T00:00:00+00:00'
            )
            """,
            (
                fernet.encrypt(plaintexts["mcp_env"]),
                fernet.encrypt(plaintexts["mcp_headers"]),
            ),
        )
        storage._conn.execute(
            """
            INSERT INTO mcp_server_overrides (
                name, env_ciphertext, headers_ciphertext, updated_at
            ) VALUES (
                'null-mappings', NULL, NULL, '2026-07-14T00:00:00+00:00'
            )
            """
        )
    return credential_id, plaintexts


def _ciphertexts(storage: Storage, credential_id: str) -> dict[str, bytes | None]:
    network = storage._conn.execute(
        "SELECT encrypted_value FROM network_credentials WHERE id = ?", (credential_id,)
    ).fetchone()
    channel_config = storage._conn.execute(
        "SELECT encrypted_secret FROM channel_configs WHERE channel_type = 'feishu'"
    ).fetchone()
    channel_instance = storage._conn.execute(
        "SELECT encrypted_secret FROM channel_instances WHERE instance_id = 'telegram-primary'"
    ).fetchone()
    mcp = storage._conn.execute(
        """
        SELECT env_ciphertext, headers_ciphertext
        FROM mcp_server_overrides WHERE name = 'encrypted-mappings'
        """
    ).fetchone()
    null_mcp = storage._conn.execute(
        """
        SELECT env_ciphertext, headers_ciphertext
        FROM mcp_server_overrides WHERE name = 'null-mappings'
        """
    ).fetchone()
    assert network is not None
    assert channel_config is not None
    assert channel_instance is not None
    assert mcp is not None
    assert null_mcp is not None
    return {
        "network": network[0],
        "channel_config": channel_config[0],
        "channel_instance": channel_instance[0],
        "mcp_env": mcp[0],
        "mcp_headers": mcp[1],
        "null_mcp_env": null_mcp[0],
        "null_mcp_headers": null_mcp[1],
    }


def _ledger(storage: Storage) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in storage._conn.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
    )


def _audit_rows(storage: Storage) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in storage._conn.execute("SELECT * FROM system_audit_events ORDER BY sequence")
    )


def _invoke_rotation(storage: Storage, monkeypatch: pytest.MonkeyPatch, old: str, new: str):
    monkeypatch.setenv("TIANSHU_DB_PATH", storage._db_path)
    return CliRunner().invoke(
        app,
        ["rotate-master-key", "--new-key", new, "--old-key", old, "--yes"],
    )


class TestRotate:
    def test_gen_key_outputs_valid_fernet(self) -> None:
        result = CliRunner().invoke(app, ["gen-key"])
        assert result.exit_code == 0
        Fernet(result.output.strip().encode())

    def test_rotate_reencrypts_every_non_null_family_and_appends_one_redacted_audit(
        self,
        storage: Storage,
        monkeypatch: pytest.MonkeyPatch,
        keys: tuple[str, str],
    ) -> None:
        old, new = keys
        credential_id, plaintexts = _seed_all_families(storage, old)

        result = _invoke_rotation(storage, monkeypatch, old, new)

        assert result.exit_code == 0, result.output
        ciphertexts = _ciphertexts(storage, credential_id)
        new_fernet = Fernet(new.encode())
        old_fernet = Fernet(old.encode())
        for family, plaintext in plaintexts.items():
            ciphertext = ciphertexts[family]
            assert ciphertext is not None
            assert new_fernet.decrypt(ciphertext) == plaintext
            with pytest.raises(InvalidToken):
                old_fernet.decrypt(ciphertext)
        assert ciphertexts["null_mcp_env"] is None
        assert ciphertexts["null_mcp_headers"] is None
        assert ciphertexts["mcp_env"] != ciphertexts["mcp_headers"]
        audits = storage.list_system_audit()
        assert len(audits) == 1
        assert audits[0].action == "secrets.master_key.rotated"
        assert audits[0].outcome == "succeeded"
        assert audits[0].metadata == {}
        assert "secret_rotation_backup_created" in result.output
        assert "secret_rotation_succeeded" in result.output
        assert "5 条密文" in result.output
        for plaintext in plaintexts.values():
            assert plaintext.decode() not in result.output
            assert plaintext.decode() not in str(_audit_rows(storage))

    def test_wrong_old_key_fails_before_backup_and_leaves_all_persistent_state_unchanged(
        self,
        storage: Storage,
        monkeypatch: pytest.MonkeyPatch,
        keys: tuple[str, str],
    ) -> None:
        old, new = keys
        credential_id, _ = _seed_all_families(storage, old)
        before = _ciphertexts(storage, credential_id), _ledger(storage), _audit_rows(storage)
        backup_calls: list[Path] = []

        def tracking_backup(_source: object, destination: Path) -> Path:
            backup_calls.append(destination)
            return destination

        monkeypatch.setattr(sqlite_backup, "create_online_backup", tracking_backup)
        wrong = Fernet.generate_key().decode()

        result = _invoke_rotation(storage, monkeypatch, wrong, new)

        assert result.exit_code == 1
        assert backup_calls == []
        assert (
            _ciphertexts(storage, credential_id),
            _ledger(storage),
            _audit_rows(storage),
        ) == before
        assert "secret_rotation_validation_failed" in result.output
        assert "轮换完成" not in result.output
        assert "secret_rotation_succeeded" not in result.output

    def test_corrupt_late_family_fails_before_backup_without_partial_network_write(
        self,
        storage: Storage,
        monkeypatch: pytest.MonkeyPatch,
        keys: tuple[str, str],
    ) -> None:
        old, new = keys
        credential_id, _ = _seed_all_families(storage, old)
        with storage._conn:
            storage._conn.execute(
                """
                UPDATE mcp_server_overrides SET headers_ciphertext = ?
                WHERE name = 'encrypted-mappings'
                """,
                (b"corrupt-mcp-header-ciphertext",),
            )
        before = _ciphertexts(storage, credential_id), _ledger(storage), _audit_rows(storage)
        backup_calls: list[Path] = []

        def tracking_backup(_source: object, destination: Path) -> Path:
            backup_calls.append(destination)
            return destination

        monkeypatch.setattr(sqlite_backup, "create_online_backup", tracking_backup)

        result = _invoke_rotation(storage, monkeypatch, old, new)

        assert result.exit_code == 1
        assert backup_calls == []
        assert (
            _ciphertexts(storage, credential_id),
            _ledger(storage),
            _audit_rows(storage),
        ) == before
        assert "secret_rotation_validation_failed" in result.output
        assert "轮换完成" not in result.output
        assert "secret_rotation_succeeded" not in result.output

    def test_audit_failure_rolls_back_every_ciphertext_and_retains_pre_rotation_backup(
        self,
        storage: Storage,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        keys: tuple[str, str],
    ) -> None:
        old, new = keys
        credential_id, _ = _seed_all_families(storage, old)
        before = _ciphertexts(storage, credential_id), _ledger(storage), _audit_rows(storage)
        backup_path = tmp_path / "audit-failure-rotation.bak"

        def fail_audit(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected audit failure")

        monkeypatch.setattr(secrets_command, "_new_rotation_backup_path", lambda _path: backup_path)
        monkeypatch.setattr(system_audit_repo, "_append_system_audit_unlocked", fail_audit)

        result = _invoke_rotation(storage, monkeypatch, old, new)

        assert result.exit_code == 1
        assert backup_path.exists()
        assert (
            _ciphertexts(storage, credential_id),
            _ledger(storage),
            _audit_rows(storage),
        ) == before
        assert "secret_rotation_commit_failed" in result.output
        assert "轮换完成" not in result.output
        assert "secret_rotation_succeeded" not in result.output

    def test_zero_family_is_no_op_without_backup_audit_or_updated_rows_claim(
        self,
        storage: Storage,
        monkeypatch: pytest.MonkeyPatch,
        keys: tuple[str, str],
    ) -> None:
        old, new = keys

        def unexpected_backup(_source: object, _destination: Path) -> Path:
            raise AssertionError("zero-family rotation must not create a backup")

        monkeypatch.setattr(sqlite_backup, "create_online_backup", unexpected_backup)

        result = _invoke_rotation(storage, monkeypatch, old, new)

        assert result.exit_code == 0, result.output
        assert _audit_rows(storage) == ()
        assert "secret_rotation_noop" in result.output
        assert "轮换完成" not in result.output
        assert "条凭证已" not in result.output
        assert "secret_rotation_succeeded" not in result.output
