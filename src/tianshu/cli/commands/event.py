"""Event query commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import api_get

app = typer.Typer()
console = Console()


@app.command("list")
def list_events(
    edict_id: str = typer.Option(..., "--edict-id", "-e", help="Edict ID"),
    event_type: str = typer.Option(None, "--type", "-t", help="Filter by event type"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List events for an edict."""
    data = api_get(f"/api/edicts/{edict_id}/events")

    events = data.get("data", [])

    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]

    events = events[:limit]

    if fmt == "json":
        console.print_json(json.dumps(events))
        return

    table = Table(title=f"Events for {edict_id} ({len(events)} shown)")
    table.add_column("时间", style="dim", max_width=20)
    table.add_column("类型", max_width=25)
    table.add_column("生产者", max_width=12)
    table.add_column("摘要", max_width=50)

    for e in events:
        payload = e.get("payload", {})
        producer = payload.get("producer", "—")
        summary_parts = []
        for k in ("tool", "status", "goal", "verdict", "iteration"):
            if k in payload:
                summary_parts.append(f"{k}={payload[k]}")
        summary = " ".join(summary_parts) if summary_parts else "—"

        table.add_row(
            e.get("created_at", "")[:19],
            e.get("event_type", ""),
            str(producer),
            summary[:50],
        )

    console.print(table)
