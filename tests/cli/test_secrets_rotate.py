"""凭证主密钥轮换(迭代 3「深防御」D16)——旧解密新加密 + 中止安全性。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from tianshu.cli.commands.secrets import app
from tianshu.secrets.models import CredentialCreate
from tianshu.secrets.store import CredentialStore
from tianshu.secrets.vault import SecretVault, reset_vault


@pytest.fixture
def keys():
    return Fernet.generate_key().decode(), Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_vault():
    reset_vault()
    yield
    reset_vault()


def _seed(storage, key: str, value: str = "super-secret") -> str:
    store = CredentialStore(storage, SecretVault(key))
    cred = store.create(
        CredentialCreate(name="k1", kind="engine_provider", value=value, provider_name="jina")
    )
    return cred.id


class TestRotate:
    def test_gen_key_outputs_valid_fernet(self):
        result = CliRunner().invoke(app, ["gen-key"])
        assert result.exit_code == 0
        Fernet(result.output.strip().encode())  # 不抛即合法

    def test_rotate_reencrypts(self, storage, monkeypatch, keys):
        old, new = keys
        cred_id = _seed(storage, old)
        monkeypatch.setenv("TIANSHU_DB_PATH", storage._db_path)
        result = CliRunner().invoke(
            app, ["rotate-master-key", "--new-key", new, "--old-key", old, "--yes"]
        )
        assert result.exit_code == 0, result.output
        # 新密钥解得开、旧密钥解不开
        reset_vault()
        assert (
            CredentialStore(storage, SecretVault(new)).decrypt_value(
                CredentialStore(storage, SecretVault(new)).get(cred_id)
            )
            == "super-secret"
        )

    def test_abort_when_old_key_wrong(self, storage, monkeypatch, keys):
        old, new = keys
        _seed(storage, old)
        wrong = Fernet.generate_key().decode()
        monkeypatch.setenv("TIANSHU_DB_PATH", storage._db_path)
        result = CliRunner().invoke(
            app, ["rotate-master-key", "--new-key", new, "--old-key", wrong, "--yes"]
        )
        assert result.exit_code == 1
        # 数据未被破坏:原 old 仍能解密
        reset_vault()
        store = CredentialStore(storage, SecretVault(old))
        creds = store.list_all()
        assert store.decrypt_value(creds[0]) == "super-secret"
