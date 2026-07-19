"""CLI authentication commands."""

from __future__ import annotations

import os

import httpx
import typer

from tianshu.cli.client import (
    TianshuClient,
    _base_url,
    _load_session_credential_file,
    delete_session_credential,
    extract_session_cookies,
    get_client,
    require_secure_api_transport,
    save_session_credential,
)

app = typer.Typer(help="Manage CLI authentication")


def _api_failure(response: httpx.Response) -> None:
    typer.echo(f"Authentication request failed ({response.status_code}).", err=True)
    raise typer.Exit(1)


def _remove_local_credential_or_fail() -> None:
    if not delete_session_credential():
        typer.echo("Unable to remove the local credential file; fix its permissions.", err=True)
        raise typer.Exit(1)


def _revoke_existing_session() -> None:
    credential = _load_session_credential_file()
    if credential is None:
        from tianshu.cli.client import _credential_path

        if _credential_path().exists() or _credential_path().is_symlink():
            _remove_local_credential_or_fail()
        return
    try:
        with TianshuClient(
            base_url=credential.api_url,
            credential=credential,
            ignore_env=True,
        ) as client:
            response = client.delete("/api/auth/session")
    except httpx.HTTPError:
        typer.echo("Cannot revoke the existing CLI session; login was not changed.", err=True)
        raise typer.Exit(1) from None
    if response.status_code not in {204, 401}:
        _api_failure(response)
    _remove_local_credential_or_fail()


@app.command()
def login() -> None:
    """Exchange a PAT for a rotatable CLI session."""
    try:
        require_secure_api_transport()
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    _revoke_existing_session()
    token = os.environ.get("TIANSHU_API_TOKEN", "").strip()
    if not token:
        token = typer.prompt("Personal access token", hide_input=True).strip()
    if not token:
        typer.echo("A personal access token is required.", err=True)
        raise typer.Exit(1)

    try:
        with httpx.Client(
            base_url=_base_url(),
            timeout=30.0,
            headers={"X-Tianshu-Client": "cli"},
        ) as client:
            response = client.post("/api/auth/session", json={"token": token})
    except httpx.HTTPError:
        typer.echo("Cannot connect to Tianshu.", err=True)
        raise typer.Exit(1) from None
    finally:
        token = ""

    if response.is_error:
        _api_failure(response)
    credential = extract_session_cookies(response)
    if credential is None:
        typer.echo("Server did not return a complete CLI session.", err=True)
        raise typer.Exit(1)
    save_session_credential(credential)
    principal = response.json().get("principal", {})
    typer.echo(f"Authenticated as {principal.get('id', 'unknown')}.")


@app.command()
def logout() -> None:
    """Revoke and remove the locally stored CLI session."""
    credential = _load_session_credential_file()
    if credential is not None:
        try:
            with TianshuClient(
                base_url=credential.api_url,
                credential=credential,
                ignore_env=True,
            ) as client:
                response = client.delete("/api/auth/session")
            if response.is_error and response.status_code != 401:
                _api_failure(response)
        except httpx.HTTPError:
            typer.echo("Cannot connect to Tianshu; removing local session only.", err=True)
        finally:
            _remove_local_credential_or_fail()
        typer.echo("CLI session removed.")
    else:
        from tianshu.cli.client import _credential_path

        if _credential_path().exists() or _credential_path().is_symlink():
            _remove_local_credential_or_fail()
            typer.echo("Invalid local credential removed.")
        else:
            typer.echo("No stored CLI session.")
    if os.environ.get("TIANSHU_API_TOKEN", "").strip():
        typer.echo("TIANSHU_API_TOKEN is still set and continues to control authentication.")


@app.command()
def whoami() -> None:
    """Show the authenticated principal without exposing credentials."""
    try:
        with get_client() as client:
            response = client.get("/api/auth/me")
            response.raise_for_status()
    except httpx.HTTPError:
        typer.echo("Unable to resolve the current identity.", err=True)
        raise typer.Exit(1) from None
    payload = response.json()
    principal = payload.get("principal", {})
    typer.echo(f"id: {principal.get('id', 'unknown')}")
    typer.echo(f"name: {principal.get('display_name', 'unknown')}")
    typer.echo(f"kind: {principal.get('kind', 'unknown')}")
    typer.echo(f"source: {payload.get('source', 'unknown')}")


__all__ = ["app"]
