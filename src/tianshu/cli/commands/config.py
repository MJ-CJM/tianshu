"""CLI commands for multi-config LLM management."""

from __future__ import annotations

import json

import typer
from rich import print as rprint
from rich.table import Table

from tianshu.cli.client import api_delete, api_get, api_post, api_put

app = typer.Typer()


def _print_detail(data: dict, fmt: str) -> None:
    if fmt == "json":
        rprint(json.dumps(data, indent=2))
        return
    table = Table(title=f"LLM Configuration: {data.get('name', '')}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Name", data.get("name", ""))
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


@app.command("list")
def config_list(
    fmt: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """List all LLM configurations."""
    resp = api_get("/api/configs")
    payload = resp.get("data", {})
    configs = payload.get("configs", [])
    active_name = payload.get("active_name", "")

    if fmt == "json":
        rprint(json.dumps(payload, indent=2))
        return

    table = Table(title=f"LLM Configurations (active: {active_name})")
    table.add_column("Name", style="cyan")
    table.add_column("Model")
    table.add_column("Enabled")
    table.add_column("Status")
    for cfg in configs:
        name = cfg.get("name", "")
        enabled = "[green]Yes[/green]" if cfg.get("enabled") else "[red]No[/red]"
        status = "[bold green]● 活跃[/bold green]" if name == active_name else ""
        table.add_row(name, cfg.get("model", ""), enabled, status)
    rprint(table)


@app.command("get")
def config_get(
    name: str = typer.Argument(help="Configuration name"),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """View a specific LLM configuration."""
    resp = api_get("/api/configs")
    payload = resp.get("data", {})
    configs = payload.get("configs", [])

    matched = next((c for c in configs if c.get("name") == name), None)
    if not matched:
        rprint(f"[red]Configuration '{name}' not found.[/red]")
        raise typer.Exit(1)
    _print_detail(matched, fmt)


@app.command("add")
def config_add(
    name: str = typer.Option(..., "--name", help="Configuration name"),
    model: str = typer.Option(..., "--model", help="LLM model name"),
    api_key: str = typer.Option("", "--api-key", help="API key"),
    api_base: str = typer.Option("", "--api-base", help="Custom API base URL"),
    max_retries: int = typer.Option(3, "--max-retries", help="Max retries (0-10)"),
    temperature: float = typer.Option(0.7, "--temperature", help="Temperature (0-2)"),
    top_p: float = typer.Option(1.0, "--top-p", help="Top P (0-1)"),
    max_tokens: int = typer.Option(4096, "--max-tokens", help="Max tokens (1-128000)"),
) -> None:
    """Add a new LLM configuration."""
    body = {
        "name": name,
        "model": model,
        "api_key": api_key,
        "api_base": api_base,
        "max_retries": max_retries,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "enabled": True,
    }
    resp = api_post("/api/configs", body)
    data = resp.get("data", {})
    rprint(f"[green]Configuration '{name}' created.[/green]")
    _print_detail(data, "table")


@app.command("set")
def config_set(
    name: str = typer.Argument(help="Configuration name to update"),
    model: str | None = typer.Option(None, "--model", help="LLM model name"),
    api_key: str | None = typer.Option(None, "--api-key", help="API key"),
    api_base: str | None = typer.Option(None, "--api-base", help="Custom API base URL"),
    max_retries: int | None = typer.Option(None, "--max-retries", help="Max retries (0-10)"),
    temperature: float | None = typer.Option(None, "--temperature", help="Temperature (0-2)"),
    top_p: float | None = typer.Option(None, "--top-p", help="Top P (0-1)"),
    max_tokens: int | None = typer.Option(None, "--max-tokens", help="Max tokens (1-128000)"),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Enable/disable"),
) -> None:
    """Update an existing LLM configuration."""
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

    resp = api_put(f"/api/configs/{name}", payload)
    data = resp.get("data", {})
    rprint(f"[green]Configuration '{name}' updated.[/green]")
    _print_detail(data, "table")


@app.command("rm")
def config_rm(
    name: str = typer.Argument(help="Configuration name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an LLM configuration."""
    if not yes:
        typer.confirm(f"Delete configuration '{name}'?", abort=True)
    api_delete(f"/api/configs/{name}")
    rprint(f"[green]Configuration '{name}' deleted.[/green]")


@app.command("activate")
def config_activate(
    name: str = typer.Argument(help="Configuration name to activate"),
) -> None:
    """Switch the active LLM configuration."""
    resp = api_put(f"/api/configs/{name}/activate", {})
    payload = resp.get("data", {})
    active = payload.get("active_name", name)
    rprint(f"[green]Active configuration switched to '{active}'.[/green]")
