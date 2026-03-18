"""CLI commands for runtime LLM config management."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich import print as rprint
from rich.table import Table

from tianshu.cli.client import api_get, api_put

app = typer.Typer()


def _print_config(data: dict, fmt: str) -> None:
    if fmt == "json":
        rprint(json.dumps(data, indent=2))
        return
    table = Table(title="LLM Configuration")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Model", data.get("model", ""))
    table.add_row("API Key", data.get("api_key_masked", ""))
    table.add_row("API Base", data.get("api_base", ""))
    table.add_row("Max Retries", str(data.get("max_retries", "")))
    table.add_row("Temperature", str(data.get("temperature", "")))
    table.add_row("Top P", str(data.get("top_p", "")))
    table.add_row("Max Tokens", str(data.get("max_tokens", "")))
    enabled = data.get("enabled", True)
    table.add_row("Enabled", "[green]Yes[/green]" if enabled else "[red]No[/red]")
    rprint(table)


@app.command("get")
def config_get(
    fmt: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """View current LLM configuration."""
    resp = api_get("/api/config")
    data = resp.get("data", {})
    _print_config(data, fmt)


@app.command("set")
def config_set(
    model: Optional[str] = typer.Option(None, "--model", help="LLM model name"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key"),
    api_base: Optional[str] = typer.Option(None, "--api-base", help="Custom API base URL"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Max retries (0-10)"),
    temperature: Optional[float] = typer.Option(None, "--temperature", help="Temperature (0-2)"),
    top_p: Optional[float] = typer.Option(None, "--top-p", help="Top P (0-1)"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="Max tokens (1-128000)"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", help="Enable/disable LLM"),
) -> None:
    """Update runtime LLM configuration."""
    payload: dict = {}
    if model is not None:
        payload["model"] = model
    if api_key is not None:
        payload["api_key"] = api_key
    if api_base is not None:
        payload["api_base"] = api_base
    if max_retries is not None:
        payload["max_retries"] = max_retries
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if enabled is not None:
        payload["enabled"] = enabled

    if not payload:
        rprint("[yellow]No changes specified.[/yellow]")
        raise typer.Exit()

    resp = api_put("/api/config", payload)
    data = resp.get("data", {})
    rprint("[green]Configuration updated.[/green]")
    _print_config(data, "table")
