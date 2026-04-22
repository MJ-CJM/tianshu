"""CredentialStore — CRUD + host pattern matching. Spec §4.3."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tianshu.secrets import (
    CredentialCreate,
    CredentialStore,
    CredentialUpdate,
    SecretVault,
)
from tianshu.secrets.vault import reset_vault


@pytest.fixture(autouse=True)
def _reset_vault():
    reset_vault()
    yield
    reset_vault()


@pytest.fixture
def vault() -> SecretVault:
    return SecretVault(Fernet.generate_key().decode())


@pytest.fixture
def store(storage, vault: SecretVault) -> CredentialStore:
    return CredentialStore(storage, vault)


def _mk(name: str, host: str, value: str = "s") -> CredentialCreate:
    return CredentialCreate(
        name=name,
        host_pattern=host,
        header_template="Authorization: Bearer {value}",
        value=value,
    )


def test_create_then_list(store: CredentialStore) -> None:
    cred = store.create(_mk("gh", "api.github.com"))
    assert cred.name == "gh"
    assert cred.host_pattern == "api.github.com"
    assert isinstance(cred.encrypted_value, bytes)

    all_ = store.list_all()
    assert len(all_) == 1
    assert all_[0].id == cred.id


def test_find_for_host_literal_wins_over_wildcard(store: CredentialStore) -> None:
    store.create(_mk("wild", "*.github.com"))
    store.create(_mk("exact", "api.github.com"))

    match = store.find_for_host("api.github.com")
    assert match is not None
    assert match.name == "exact"


def test_find_for_host_wildcard_subdomain(store: CredentialStore) -> None:
    store.create(_mk("wild", "*.github.com"))
    match = store.find_for_host("raw.github.com")
    assert match is not None
    assert match.name == "wild"


def test_find_for_host_no_match(store: CredentialStore) -> None:
    store.create(_mk("gh", "api.github.com"))
    assert store.find_for_host("unknown.example.com") is None


def test_update_changes_encrypted_value(
    store: CredentialStore, vault: SecretVault
) -> None:
    cred = store.create(_mk("gh", "api.github.com", value="v1"))
    original_ct = cred.encrypted_value

    updated = store.update(cred.id, CredentialUpdate(value="v2"))
    assert updated is not None
    assert updated.encrypted_value != original_ct
    assert vault.decrypt(updated.encrypted_value) == "v2"


def test_update_changes_extra_headers(store: CredentialStore) -> None:
    cred = store.create(_mk("gh", "api.github.com"))
    updated = store.update(
        cred.id, CredentialUpdate(extra_headers={"X-Custom": "yes"})
    )
    assert updated is not None
    assert updated.extra_headers == {"X-Custom": "yes"}


def test_update_missing_returns_none(store: CredentialStore) -> None:
    assert store.update("nonexistent-id", CredentialUpdate(value="x")) is None


def test_delete_soft_deletes(store: CredentialStore) -> None:
    cred = store.create(_mk("gh", "api.github.com"))
    assert store.delete(cred.id) is True

    assert store.list_all() == []
    assert store.get(cred.id) is None
    assert store.find_for_host("api.github.com") is None


def test_delete_missing_returns_false(store: CredentialStore) -> None:
    assert store.delete("nonexistent-id") is False


def test_mark_used_updates_last_used_at(store: CredentialStore) -> None:
    cred = store.create(_mk("gh", "api.github.com"))
    assert cred.last_used_at is None

    store.mark_used(cred.id)
    reloaded = store.get(cred.id)
    assert reloaded is not None
    assert reloaded.last_used_at is not None
