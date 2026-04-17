"""Edict management commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import api_get, api_post

app = typer.Typer()
console = Console()

_STATUS_COLORS = {
    "completed": "green",
    "running": "blue",
    "submitted": "cyan",
    "failed": "red",
    "cancelled": "yellow",
}


@app.command()
def submit(
    goal: str = typer.Option(..., "--goal", "-g", help="Task goal"),
    context: str = typer.Option(None, "--context", "-c", help="Additional context"),
    schedule_type: str = typer.Option("immediate", "--schedule-type", help="Schedule: immediate|once|cron"),
    cron: str = typer.Option(None, "--cron", help="Cron expression (for schedule_type=cron)"),
    priority: str = typer.Option("normal", "--priority", "-p", help="Priority: urgent|normal|low"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """Submit a new edict."""
    body: dict = {"goal": goal, "context": context}
    if schedule_type != "immediate":
        body["schedule"] = {"type": schedule_type}
        if cron:
            body["schedule"]["cron"] = cron
    if priority != "normal":
        body["priority"] = priority
    data = api_post("/api/edicts", body)

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    edict = data.get("data", {})
    status = edict.get("status", "unknown")
    color = _STATUS_COLORS.get(status, "white")

    console.print(f"\nEdict submitted:")
    console.print(f"  Edict ID: {edict.get('id', 'N/A')}")
    console.print(f"  Goal:     {edict.get('goal', 'N/A')}")
    console.print(f"  Status:   [{color}]{status}[/{color}]")
    console.print(f"\nUse [bold]tianshu edict get {edict.get('id', '<id>')}[/bold] to check progress")


@app.command("get")
def get_edict(
    edict_id: str = typer.Argument(help="Edict ID"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """View edict details."""
    data = api_get(f"/api/edicts/{edict_id}")

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    edict = data.get("data", {})
    console.print(f"\n[bold]Edict[/bold]")
    console.print(f"  ID:         {edict.get('id')}")
    console.print(f"  Goal:       {edict.get('goal')}")
    console.print(f"  Context:    {edict.get('context', 'N/A')}")
    console.print(f"  Created at: {edict.get('created_at')}")


@app.command("list")
def list_edicts(
    status: str = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List edicts."""
    params: dict = {"limit": limit}
    if status:
        params["status"] = status
    data = api_get("/api/edicts", params=params)

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    edicts = data.get("data", [])
    meta = data.get("metadata", {})

    table = Table(title=f"Edicts (total: {meta.get('total', len(edicts))})")
    table.add_column("ID", style="dim", max_width=26)
    table.add_column("Goal", max_width=50)
    table.add_column("Created at")

    for e in edicts:
        table.add_row(e.get("id", ""), e.get("goal", ""), e.get("created_at", ""))

    console.print(table)
