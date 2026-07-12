"""Governed workspace status, preview, approval, and apply commands."""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn
from urllib.parse import quote

import httpx
import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import get_client

app = typer.Typer()
console = Console()

_SECRET_KEY_PARTS = ("token", "authorization", "cookie", "secret", "principal_digest")


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _workspace_path(run_id: str, suffix: str = "") -> str:
    encoded = quote(run_id, safe="")
    return f"/api/workspace-runs/{encoded}{suffix}"


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    try:
        with get_client() as client:
            response = client.request(method, path, **kwargs)
    except (httpx.HTTPError, ValueError):
        _fail("Workspace request failed (transport unavailable).")
    if response.is_error:
        _fail(f"Workspace request failed ({response.status_code}).")
    try:
        payload = response.json()
    except (TypeError, ValueError):
        _fail("Workspace request failed (invalid response).")
    if not isinstance(payload, dict):
        _fail("Workspace request failed (invalid response).")
    if payload.get("success") is False:
        _fail(f"Workspace request failed ({response.status_code}).")
    return payload


def _sanitized(value: Any, *, secret: str | None = None) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            raw_key = str(key)
            if any(part in raw_key.casefold() for part in _SECRET_KEY_PARTS):
                continue
            safe_key = raw_key.replace(secret, "[redacted]") if secret else raw_key
            sanitized[safe_key] = _sanitized(item, secret=secret)
        return sanitized
    if isinstance(value, list):
        return [_sanitized(item, secret=secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitized(item, secret=secret) for item in value)
    if isinstance(value, str) and secret:
        return value.replace(secret, "[redacted]")
    return value


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _render(payload: dict[str, Any], *, fmt: str, title: str, secret: str | None = None) -> None:
    if fmt not in {"table", "json"}:
        _fail("Output format must be table or json.")
    safe_payload = _sanitized(payload, secret=secret)
    if fmt == "json":
        typer.echo(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True))
        return

    data = safe_payload.get("data", safe_payload)
    table = Table(title=title)
    if isinstance(data, dict):
        table.add_column("Field", style="bold")
        table.add_column("Value")
        for key in sorted(data):
            table.add_row(str(key), _format_value(data[key]))
    elif isinstance(data, list):
        table.add_column("Item")
        for item in data:
            table.add_row(_format_value(item))
    else:
        table.add_column("Value")
        table.add_row(_format_value(data))
    console.print(table)


def _issued_decision(payload: dict[str, Any]) -> tuple[str, str]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        _fail("Workspace approval failed (token unavailable).")
    decision = data.get("decision")
    decision_id = decision.get("id") if isinstance(decision, dict) else data.get("decision_id")
    token = data.get("token") or data.get("apply_token")
    if (
        not isinstance(decision_id, str)
        or not decision_id.strip()
        or not isinstance(token, str)
        or not token.strip()
    ):
        _fail("Workspace approval failed (token unavailable).")
    return decision_id.strip(), token.strip()


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


@app.command("status")
def status(
    run_id: str = typer.Argument(..., help="Workspace run ID"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
) -> None:
    """Show the persisted governed-workspace run status."""
    payload = _request("GET", _workspace_path(run_id, "/status"))
    _render(payload, fmt=fmt, title="Workspace status")


@app.command("preview")
def preview(
    run_id: str = typer.Argument(..., help="Workspace run ID"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
) -> None:
    """Show the latest server-generated canonical change preview."""
    payload = _request("GET", _workspace_path(run_id, "/changes"))
    _render(payload, fmt=fmt, title="Workspace changes")


@app.command("approve")
def approve(
    run_id: str = typer.Argument(..., help="Workspace run ID"),
    reason: str = typer.Option(..., "--reason", help="Explicit apply decision reason"),
    apply_now: bool = typer.Option(
        ...,
        "--apply-now",
        help="Issue and consume the decision in this process without printing its token",
    ),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
) -> None:
    """Approve and immediately apply a canonical change set without exporting its token."""
    if not apply_now:
        raise typer.BadParameter("--apply-now is required")

    decision = _request(
        "POST",
        _workspace_path(run_id, "/apply-decisions"),
        body={"reason": reason},
    )
    decision_id, issued_token = _issued_decision(decision)
    try:
        receipt = _request(
            "POST",
            _workspace_path(run_id, "/apply"),
            body={"decision_id": decision_id, "token": issued_token},
        )
        _render(receipt, fmt=fmt, title="Workspace apply", secret=issued_token)
    finally:
        issued_token = ""


@app.command("apply")
def apply(
    run_id: str = typer.Argument(..., help="Workspace run ID"),
    decision_id: str = typer.Option(..., "--decision-id", help="Approved decision ID"),
    token_stdin: bool = typer.Option(
        False,
        "--token-stdin",
        help="Read the one-time apply credential from standard input",
    ),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
) -> None:
    """Apply an approved canonical change set with a hidden or piped credential."""
    if token_stdin and _stdin_is_tty():
        _fail("--token-stdin requires piped stdin; use the default hidden prompt on a terminal.")
    issued_token = (
        sys.stdin.readline().strip()
        if token_stdin
        else typer.prompt("Apply credential", hide_input=True).strip()
    )
    if not issued_token:
        _fail("An apply credential is required.")
    try:
        receipt = _request(
            "POST",
            _workspace_path(run_id, "/apply"),
            body={"decision_id": decision_id, "token": issued_token},
        )
        _render(receipt, fmt=fmt, title="Workspace apply", secret=issued_token)
    finally:
        issued_token = ""


__all__ = ["app"]
