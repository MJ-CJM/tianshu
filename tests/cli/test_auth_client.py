"""CLI authentication lifecycle and secret handling."""

from __future__ import annotations

import stat
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TIANSHU_API_URL", "https://tianshu.example.com")
    monkeypatch.setenv("TIANSHU_CREDENTIAL_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("TIANSHU_API_TOKEN", raising=False)


def _credential(access: str = "access-old", refresh: str = "refresh-old"):
    from tianshu.cli.client import SessionCredential

    return SessionCredential(
        version=1,
        api_url="https://tianshu.example.com",
        access_token=access,
        refresh_token=refresh,
    )


def test_session_credential_file_is_0600_and_bound_to_api_url(monkeypatch) -> None:
    from tianshu.cli.client import (
        _credential_path,
        load_session_credential,
        save_session_credential,
    )

    save_session_credential(_credential())
    path = _credential_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_session_credential() == _credential()

    monkeypatch.setenv("TIANSHU_API_URL", "https://other.example.com")
    assert load_session_credential() is None


@respx.mock
def test_stored_session_refreshes_once_retries_once_and_rotates_file() -> None:
    from tianshu.cli.client import get_client, load_session_credential, save_session_credential

    save_session_credential(_credential())
    protected = respx.get("https://tianshu.example.com/api/edicts").mock(
        side_effect=[
            httpx.Response(401, json={"detail": "expired"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    refresh = respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json={"principal": {"id": "user:owner"}},
            headers=[
                ("set-cookie", "tianshu_access=access-new; Path=/api; HttpOnly"),
                (
                    "set-cookie",
                    "tianshu_refresh=refresh-new; Path=/api/auth/refresh; HttpOnly",
                ),
            ],
        )
    )

    with get_client() as client:
        response = client.get("/api/edicts")

    assert response.status_code == 200
    assert protected.call_count == 2
    assert refresh.call_count == 1
    assert protected.calls[0].request.headers["authorization"] == "Bearer access-old"
    assert protected.calls[1].request.headers["authorization"] == "Bearer access-new"
    assert "authorization" not in refresh.calls[0].request.headers
    assert refresh.calls[0].request.headers["cookie"] == "tianshu_refresh=refresh-old"
    assert load_session_credential() == _credential("access-new", "refresh-new")


@respx.mock
def test_refresh_rejection_does_not_loop_and_removes_stale_session() -> None:
    from tianshu.cli.client import (
        _credential_path,
        get_client,
        save_session_credential,
    )

    save_session_credential(_credential())
    protected = respx.get("https://tianshu.example.com/api/edicts").mock(
        return_value=httpx.Response(401)
    )
    refresh = respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        return_value=httpx.Response(401)
    )

    with get_client() as client:
        response = client.get("/api/edicts")

    assert response.status_code == 401
    assert protected.call_count == 1
    assert refresh.call_count == 1
    assert not _credential_path().exists()


@respx.mock
def test_refresh_network_failure_preserves_session_for_later_retry() -> None:
    from tianshu.cli.client import _credential_path, get_client, save_session_credential

    save_session_credential(_credential())
    respx.get("https://tianshu.example.com/api/edicts").mock(return_value=httpx.Response(401))
    respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        side_effect=httpx.ConnectError("offline")
    )

    with get_client() as client, pytest.raises(httpx.ConnectError, match="offline"):
        client.get("/api/edicts")

    assert _credential_path().exists()


@respx.mock
def test_refresh_adopts_session_rotated_by_another_cli_process() -> None:
    from tianshu.cli.client import get_client, save_session_credential

    save_session_credential(_credential())
    client = get_client()
    save_session_credential(_credential("access-winner", "refresh-winner"))
    protected = respx.get("https://tianshu.example.com/api/edicts").mock(
        side_effect=[httpx.Response(401), httpx.Response(200)]
    )
    refresh = respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        return_value=httpx.Response(500)
    )

    with client:
        response = client.get("/api/edicts")

    assert response.status_code == 200
    assert refresh.call_count == 0
    assert protected.calls[1].request.headers["authorization"] == "Bearer access-winner"


@respx.mock
def test_environment_pat_has_priority_and_never_falls_back_to_session(monkeypatch) -> None:
    from tianshu.cli.client import get_client, save_session_credential

    save_session_credential(_credential())
    monkeypatch.setenv("TIANSHU_API_TOKEN", "env-pat")
    protected = respx.get("https://tianshu.example.com/api/edicts").mock(
        return_value=httpx.Response(401)
    )
    refresh = respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        return_value=httpx.Response(200)
    )

    with get_client() as client:
        response = client.get("/api/edicts")

    assert response.status_code == 401
    assert protected.calls[0].request.headers["authorization"] == "Bearer env-pat"
    assert refresh.call_count == 0


def test_cli_without_token_keeps_trusted_local_compatibility() -> None:
    from tianshu.cli.client import auth_headers

    assert auth_headers() == {"X-Tianshu-Client": "cli"}


@respx.mock
def test_auth_login_exchanges_pat_without_printing_or_persisting_pat() -> None:
    from tianshu.cli.client import load_session_credential
    from tianshu.cli.commands.auth import app

    respx.post("https://tianshu.example.com/api/auth/session").mock(
        return_value=httpx.Response(
            200,
            json={"principal": {"id": "user:owner", "display_name": "Owner"}},
            headers=[
                ("set-cookie", "tianshu_access=access-login; Path=/api; HttpOnly"),
                (
                    "set-cookie",
                    "tianshu_refresh=refresh-login; Path=/api/auth/refresh; HttpOnly",
                ),
            ],
        )
    )

    result = CliRunner().invoke(app, ["login"], input="pat-input-secret\n")

    assert result.exit_code == 0, result.output
    assert "user:owner" in result.output
    assert "pat-input-secret" not in result.output
    assert "access-login" not in result.output
    assert "refresh-login" not in result.output
    assert load_session_credential() == _credential("access-login", "refresh-login")


@respx.mock
def test_repeated_auth_login_revokes_previous_session_before_replacement() -> None:
    from tianshu.cli.client import load_session_credential, save_session_credential
    from tianshu.cli.commands.auth import app

    save_session_credential(_credential())
    revoke = respx.delete("https://tianshu.example.com/api/auth/session").mock(
        return_value=httpx.Response(204)
    )
    create = respx.post("https://tianshu.example.com/api/auth/session").mock(
        return_value=httpx.Response(
            200,
            json={"principal": {"id": "user:owner"}},
            headers=[
                ("set-cookie", "tianshu_access=access-next; Path=/api; HttpOnly"),
                (
                    "set-cookie",
                    "tianshu_refresh=refresh-next; Path=/api/auth/refresh; HttpOnly",
                ),
            ],
        )
    )

    result = CliRunner().invoke(app, ["login"], input="new-pat\n")

    assert result.exit_code == 0, result.output
    assert revoke.call_count == 1
    assert create.call_count == 1
    assert revoke.calls[0].request.headers["authorization"] == "Bearer access-old"
    assert load_session_credential() == _credential("access-next", "refresh-next")


@respx.mock
def test_auth_login_refuses_remote_plaintext_before_sending_pat(monkeypatch) -> None:
    from tianshu.cli.commands.auth import app

    monkeypatch.setenv("TIANSHU_API_URL", "http://remote.example.com")
    route = respx.post("http://remote.example.com/api/auth/session").mock(
        return_value=httpx.Response(200)
    )

    result = CliRunner().invoke(app, ["login"], input="must-not-leak\n")

    assert result.exit_code == 1
    assert "HTTPS" in result.output
    assert "must-not-leak" not in result.output
    assert route.call_count == 0


@respx.mock
def test_auth_logout_revokes_file_session_even_when_env_pat_exists(monkeypatch) -> None:
    from tianshu.cli.client import _credential_path, save_session_credential
    from tianshu.cli.commands.auth import app

    save_session_credential(_credential())
    monkeypatch.setenv("TIANSHU_API_TOKEN", "env-pat")
    revoke = respx.delete("https://tianshu.example.com/api/auth/session").mock(
        return_value=httpx.Response(204)
    )

    result = CliRunner().invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert revoke.calls[0].request.headers["authorization"] == "Bearer access-old"
    assert "refresh-old" not in revoke.calls[0].request.headers.get("cookie", "")
    assert not _credential_path().exists()
    assert "TIANSHU_API_TOKEN" in result.output


@respx.mock
def test_auth_logout_refreshes_expired_access_then_revokes_rotated_session() -> None:
    from tianshu.cli.client import _credential_path, save_session_credential
    from tianshu.cli.commands.auth import app

    save_session_credential(_credential())
    revoke = respx.delete("https://tianshu.example.com/api/auth/session").mock(
        side_effect=[httpx.Response(401), httpx.Response(204)]
    )
    refresh = respx.post("https://tianshu.example.com/api/auth/refresh").mock(
        return_value=httpx.Response(
            200,
            headers=[
                ("set-cookie", "tianshu_access=access-new; Path=/api; HttpOnly"),
                (
                    "set-cookie",
                    "tianshu_refresh=refresh-new; Path=/api/auth/refresh; HttpOnly",
                ),
            ],
        )
    )

    result = CliRunner().invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert revoke.call_count == 2
    assert refresh.call_count == 1
    assert revoke.calls[1].request.headers["authorization"] == "Bearer access-new"
    assert not _credential_path().exists()


@respx.mock
def test_auth_logout_revokes_stored_server_even_when_current_api_url_differs() -> None:
    from tianshu.cli.client import SessionCredential, _credential_path, save_session_credential
    from tianshu.cli.commands.auth import app

    save_session_credential(
        SessionCredential(
            version=1,
            api_url="https://old.example.com",
            access_token="old-access",
            refresh_token="old-refresh",
        )
    )
    revoke = respx.delete("https://old.example.com/api/auth/session").mock(
        return_value=httpx.Response(204)
    )

    result = CliRunner().invoke(app, ["logout"])

    assert result.exit_code == 0, result.output
    assert revoke.call_count == 1
    assert revoke.calls[0].request.headers["authorization"] == "Bearer old-access"
    assert not _credential_path().exists()


@respx.mock
def test_auth_whoami_prints_identity_not_credentials(monkeypatch) -> None:
    from tianshu.cli.commands.auth import app

    monkeypatch.setenv("TIANSHU_API_TOKEN", "env-pat-secret")
    respx.get("https://tianshu.example.com/api/auth/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "principal": {
                    "id": "user:owner",
                    "display_name": "Owner",
                    "kind": "human",
                },
                "source": "bearer",
            },
        )
    )

    result = CliRunner().invoke(app, ["whoami"])

    assert result.exit_code == 0, result.output
    assert "user:owner" in result.output
    assert "Owner" in result.output
    assert "human" in result.output
    assert "env-pat-secret" not in result.output


def test_watch_sends_bearer_header_without_query_credentials(monkeypatch) -> None:
    import websockets.sync.client as ws_client

    from tianshu.cli.commands.watch import watch

    monkeypatch.setenv("TIANSHU_API_TOKEN", "cli-secret-token")

    class FakeWebSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def recv(self, timeout: float):
            return '{"type":"execution.completed","edict_id":"edict-1"}'

    connect = MagicMock(return_value=FakeWebSocket())
    monkeypatch.setattr(ws_client, "connect", connect)

    watch("edict-1")

    url = connect.call_args.args[0]
    assert url == "wss://tianshu.example.com/api/ws"
    assert "token" not in url
    assert connect.call_args.kwargs["additional_headers"] == {
        "Authorization": "Bearer cli-secret-token",
        "X-Tianshu-Client": "cli",
    }
