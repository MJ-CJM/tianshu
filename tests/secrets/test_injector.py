"""CredentialInjector — host 匹配 + header 注入 + 敏感黑名单。Spec §4.4-4.5。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tianshu.secrets import (
    CredentialConflict,
    CredentialCreate,
    CredentialInjector,
    CredentialStore,
    ForbiddenHeader,
    SecretVault,
    redact_sensitive_headers,
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
def store_with_github(storage, vault: SecretVault) -> CredentialStore:
    store = CredentialStore(storage, vault)
    store.create(
        CredentialCreate(
            name="gh",
            host_pattern="api.github.com",
            header_template="Authorization: Bearer {value}",
            value="secret-abc",
        )
    )
    return store


def test_inject_happy_path(store_with_github: CredentialStore) -> None:
    inj = CredentialInjector(store_with_github)
    r = inj.inject("api.github.com", {"Accept": "application/json"})
    assert r.credential_name == "gh"
    assert r.merged_headers["Authorization"] == "Bearer secret-abc"
    assert r.merged_headers["Accept"] == "application/json"


def test_inject_renders_template_value(store_with_github: CredentialStore) -> None:
    """verify the {value} placeholder gets the decrypted plaintext, not the ciphertext."""
    inj = CredentialInjector(store_with_github)
    r = inj.inject("api.github.com", {})
    # the raw secret must not leak as a stringified bytes object
    assert r.merged_headers["Authorization"] == "Bearer secret-abc"
    assert "b'" not in r.merged_headers["Authorization"]


def test_forbidden_header_authorization(store_with_github: CredentialStore) -> None:
    inj = CredentialInjector(store_with_github)
    with pytest.raises(ForbiddenHeader) as exc:
        inj.inject("api.github.com", {"Authorization": "bogus"})
    assert exc.value.header == "Authorization"


def test_forbidden_header_cookie(store_with_github: CredentialStore) -> None:
    inj = CredentialInjector(store_with_github)
    with pytest.raises(ForbiddenHeader) as exc:
        inj.inject("api.github.com", {"Cookie": "s=abc"})
    assert exc.value.header == "Cookie"


def test_forbidden_header_case_insensitive(
    store_with_github: CredentialStore,
) -> None:
    inj = CredentialInjector(store_with_github)
    with pytest.raises(ForbiddenHeader):
        inj.inject("api.github.com", {"x-api-key": "bogus"})


def test_no_credential_passthrough(store_with_github: CredentialStore) -> None:
    inj = CredentialInjector(store_with_github)
    r = inj.inject("unknown.example.com", {"Accept": "text/plain"})
    assert r.credential_name is None
    assert r.merged_headers == {"Accept": "text/plain"}


def test_credential_conflict_with_extra_headers(
    storage, vault: SecretVault
) -> None:
    store = CredentialStore(storage, vault)
    store.create(
        CredentialCreate(
            name="n",
            host_pattern="x.com",
            header_template="Authorization: Bearer {value}",
            value="v",
            extra_headers={"Accept": "application/json"},
        )
    )
    inj = CredentialInjector(store)
    with pytest.raises(CredentialConflict) as exc:
        inj.inject("x.com", {"Accept": "text/plain"})
    # user header 'Accept' conflicts with injected extra header
    assert exc.value.header == "Accept"


def test_redact_sensitive_headers_masks_all() -> None:
    r = redact_sensitive_headers(
        {
            "Authorization": "Bearer x",
            "Cookie": "s=abc",
            "X-Api-Key": "k",
            "X-Auth-Token": "t",
            "Proxy-Authorization": "p",
            "Accept": "application/json",
        }
    )
    assert r["Authorization"] == "<redacted>"
    assert r["Cookie"] == "<redacted>"
    assert r["X-Api-Key"] == "<redacted>"
    assert r["X-Auth-Token"] == "<redacted>"
    assert r["Proxy-Authorization"] == "<redacted>"
    # non-sensitive passes through
    assert r["Accept"] == "application/json"


def test_redact_case_insensitive() -> None:
    r = redact_sensitive_headers({"authorization": "Bearer x", "cookie": "s=abc"})
    assert r["authorization"] == "<redacted>"
    assert r["cookie"] == "<redacted>"
