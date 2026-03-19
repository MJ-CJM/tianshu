"""CLI commands for plugin management."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import get_client

app = typer.Typer()
console = Console()


@app.command("list")
def plugin_list(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List installed plugins."""
    client = get_client()
    resp = client.get("/api/plugins")
    resp.raise_for_status()
    data = resp.json()["data"]

    if as_json:
        console.print_json(json.dumps(data))
        return

    if not data:
        console.print("[yellow]No plugins installed[/yellow]")
        return

    table = Table(title="Plugins")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Installed At")
    for p in data:
        status = p.get("status", "active")
        style = "green" if status == "active" else "red"
        table.add_row(
            p["name"],
            p.get("version", "?"),
            f"[{style}]{status}[/{style}]",
            (p.get("installed_at") or "")[:19],
        )
    console.print(table)
