"""SecretVault — Fernet 加密封装单元测试。Spec §4.1。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tianshu.secrets.vault import SecretVault, get_vault, reset_vault


@pytest.fixture
def master_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_vault():
    reset_vault()
    yield
    reset_vault()


def test_encrypt_decrypt_roundtrip(master_key: str) -> None:
    v = SecretVault(master_key)
    enc = v.encrypt("secret-token")
    assert isinstance(enc, bytes)
    assert enc != b"secret-token"
    assert v.decrypt(enc) == "secret-token"


def test_decrypt_tampered_raises(master_key: str) -> None:
    v = SecretVault(master_key)
    with pytest.raises(ValueError, match="decryption failed"):
        v.decrypt(b"not-a-valid-token")


def test_decrypt_wrong_key_raises(master_key: str) -> None:
    v1 = SecretVault(master_key)
    enc = v1.encrypt("secret")
    other_key = Fernet.generate_key().decode()
    v2 = SecretVault(other_key)
    with pytest.raises(ValueError, match="decryption failed"):
        v2.decrypt(enc)


def test_get_vault_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIANSHU_SECRET_MASTER_KEY", raising=False)
    assert get_vault() is None


def test_get_vault_singleton(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    v1 = get_vault()
    v2 = get_vault()
    assert v1 is not None
    assert v1 is v2


def test_reset_vault_allows_rekey(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", master_key)
    v1 = get_vault()
    assert v1 is not None

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", new_key)
    # same instance until reset
    assert get_vault() is v1

    reset_vault()
    v2 = get_vault()
    assert v2 is not None
    assert v2 is not v1
