"""CLI commands for provider management."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import get_client

app = typer.Typer()
console = Console()


@app.command("list")
def provider_list(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List registered providers."""
    client = get_client()
    resp = client.get("/api/providers")
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    if not data:
        console.print("[yellow]No providers registered[/yellow]")
        return

    table = Table(title="Providers")
    table.add_column("Name", style="bold")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Priority", justify="right")
    table.add_column("RPM Limit", justify="right")
    table.add_column("Cost/1K Prompt", justify="right")
    for p in data:
        status = p.get("status", "active")
        style = "green" if status == "active" else "red" if status == "disabled" else "yellow"
        table.add_row(
            p["name"],
            p["model"],
            f"[{style}]{status}[/{style}]",
            str(p.get("priority", 100)),
            str(p.get("rpm_limit", "-")),
            f"¥{p.get('cost_per_1k_prompt', 0):.4f}" if p.get("cost_per_1k_prompt") else "-",
        )
    console.print(table)


@app.command("status")
def provider_status(
    name: str = typer.Argument(help="Provider name"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show provider status."""
    client = get_client()
    resp = client.get(f"/api/providers/{name}/status")
    if resp.status_code == 404:
        console.print(f"[red]Provider '{name}' not found[/red]")
        raise typer.Exit(1)
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    for key, value in data.items():
        console.print(f"[bold]{key}:[/bold] {value}")
