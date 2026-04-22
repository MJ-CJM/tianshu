"""ApiRequestEngine — SSRF / forbidden header / invalid method. Spec §5."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tianshu.secrets import CredentialInjector, CredentialStore, SecretVault
from tianshu.secrets.vault import reset_vault
from tianshu.tools.hongluisi.api_request import ApiRequestEngine


@pytest.fixture(autouse=True)
def _reset_vault():
    reset_vault()
    yield
    reset_vault()


@pytest.fixture
def engine(storage) -> ApiRequestEngine:
    vault = SecretVault(Fernet.generate_key().decode())
    store = CredentialStore(storage, vault)
    injector = CredentialInjector(store)
    return ApiRequestEngine(injector)


async def test_method_not_supported(engine: ApiRequestEngine) -> None:
    resp = await engine.request(url="https://example.com/", method="TRACE")
    assert resp.status == "error"
    assert resp.reason is not None
    assert resp.reason.startswith("method_not_supported:")
    assert "TRACE" in resp.reason


async def test_ssrf_localhost_rejected(engine: ApiRequestEngine) -> None:
    resp = await engine.request(url="http://localhost/foo", method="GET")
    assert resp.status == "error"
    assert resp.reason is not None
    assert resp.reason.startswith("ssrf_")


async def test_ssrf_loopback_ip_rejected(engine: ApiRequestEngine) -> None:
    resp = await engine.request(url="http://127.0.0.1/x", method="GET")
    assert resp.status == "error"
    assert resp.reason is not None
    assert resp.reason.startswith("ssrf_")


async def test_ssrf_bad_scheme(engine: ApiRequestEngine) -> None:
    resp = await engine.request(url="file:///etc/passwd", method="GET")
    assert resp.status == "error"
    assert resp.reason == "ssrf_bad_scheme"


async def test_ssrf_bad_port(engine: ApiRequestEngine) -> None:
    resp = await engine.request(url="http://example.com:22/x", method="GET")
    assert resp.status == "error"
    assert resp.reason == "ssrf_bad_port"


async def test_forbidden_header(engine: ApiRequestEngine) -> None:
    """user passing Authorization must be rejected before network call."""
    # pick a URL that would fail SSRF too, but forbidden_header is evaluated
    # AFTER SSRF check — so use a hostname that passes SSRF to isolate the error.
    # But we don't want real network; the check order is: method -> ssrf -> headers.
    # A .invalid TLD will trigger ssrf_dns_failed. So pick a literal IP cloud fronted
    # is hard. Instead test against a host that resolves publicly (example.com) —
    # SSRF guard passes, then forbidden_header triggers.
    resp = await engine.request(
        url="https://example.com/",
        method="GET",
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status == "error"
    assert resp.reason is not None
    assert resp.reason.startswith("forbidden_header:")


async def test_forbidden_header_cookie(engine: ApiRequestEngine) -> None:
    resp = await engine.request(
        url="https://example.com/",
        method="GET",
        headers={"Cookie": "s=abc"},
    )
    assert resp.status == "error"
    assert resp.reason is not None
    assert resp.reason.startswith("forbidden_header:")
