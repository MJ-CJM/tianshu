"""CLI bearer propagation for HTTP and WebSocket clients."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_http_helpers_and_reusable_client_use_api_token(monkeypatch) -> None:
    from tianshu.cli import client as api_client

    monkeypatch.setenv("TIANSHU_API_TOKEN", "cli-secret-token")
    response = MagicMock()
    response.json.return_value = {"ok": True}
    context_client = MagicMock()
    context_client.__enter__.return_value = context_client
    context_client.request.return_value = response
    client_factory = MagicMock(return_value=context_client)
    monkeypatch.setattr(api_client.httpx, "Client", client_factory)

    assert api_client.api_get("/api/edicts") == {"ok": True}
    api_client.get_client()

    first_headers = client_factory.call_args_list[0].kwargs["headers"]
    second_headers = client_factory.call_args_list[1].kwargs["headers"]
    assert first_headers == {
        "Authorization": "Bearer cli-secret-token",
        "X-Tianshu-Client": "cli",
    }
    assert second_headers == first_headers


def test_cli_without_token_keeps_trusted_local_compatibility(monkeypatch) -> None:
    from tianshu.cli.client import auth_headers

    monkeypatch.delenv("TIANSHU_API_TOKEN", raising=False)

    assert auth_headers() == {"X-Tianshu-Client": "cli"}


def test_watch_sends_bearer_header_without_query_credentials(monkeypatch) -> None:
    import websockets.sync.client as ws_client

    from tianshu.cli.commands.watch import watch

    monkeypatch.setenv("TIANSHU_API_URL", "https://tianshu.example.com")
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
