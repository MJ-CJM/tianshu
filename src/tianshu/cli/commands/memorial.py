"""Memorial view commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from tianshu.cli.client import api_get

app = typer.Typer()
console = Console()

_STATUS_COLORS = {
    "completed": "green",
    "running": "blue",
    "submitted": "cyan",
    "failed": "red",
    "cancelled": "yellow",
}


@app.command("get")
def get_memorial(
    memorial_id: str = typer.Argument(help="Memorial ID"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """View memorial details."""
    data = api_get(f"/api/memorials/{memorial_id}")

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    m = data.get("data", {})
    status = m.get("status", "unknown")
    color = _STATUS_COLORS.get(status, "white")
    usage = m.get("usage", {})

    console.print(f"\n[bold]Memorial[/bold]")
    console.print(f"  ID:           {m.get('id')}")
    console.print(f"  Edict ID:     {m.get('edict_id')}")
    console.print(f"  Status:       [{color}]{status}[/{color}]")
    console.print(f"  Created at:   {m.get('created_at')}")
    console.print(f"  Started at:   {m.get('started_at', 'N/A')}")
    console.print(f"  Completed at: {m.get('completed_at', 'N/A')}")

    console.print(f"\n[bold]Token Usage:[/bold]")
    console.print(f"  Prompt:     {usage.get('prompt_tokens', 0)}")
    console.print(f"  Completion: {usage.get('completion_tokens', 0)}")
    console.print(f"  Total:      {usage.get('total_tokens', 0)}")

    if m.get("summary"):
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(m["summary"])
    if m.get("result") and m.get("result") != m.get("summary"):
        console.print(f"\n[bold]Result:[/bold]")
        console.print(m["result"])
    if m.get("error"):
        console.print(f"\n[red]Error:[/red] {m['error']}")


@app.command("list")
def list_memorials(
    status: str = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List memorials."""
    params: dict = {"limit": limit}
    if status:
        params["status"] = status
    data = api_get("/api/memorials", params=params)

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    memorials = data.get("data", [])
    meta = data.get("metadata", {})

    table = Table(title=f"Memorials (total: {meta.get('total', len(memorials))})")
    table.add_column("ID", style="dim", max_width=26)
    table.add_column("Edict ID", style="dim", max_width=26)
    table.add_column("Status")
    table.add_column("Summary", max_width=40)

    for m in memorials:
        s = m.get("status", "")
        color = _STATUS_COLORS.get(s, "white")
        table.add_row(
            m.get("id", ""),
            m.get("edict_id", ""),
            f"[{color}]{s}[/{color}]",
            (m.get("summary") or "")[:40],
        )

    console.print(table)


@app.command("review")
def review(
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table|json"),
):
    """List all memorials pending review (needs_review status)."""
    data = api_get("/api/memorials", params={"status": "needs_review", "limit": limit})

    if fmt == "json":
        console.print_json(json.dumps(data))
        return

    memorials = data.get("data", [])
    meta = data.get("metadata", {})

    if not memorials:
        console.print("[green]No memorials pending review.[/green]")
        return

    table = Table(title=f"Pending Review (total: {meta.get('total', len(memorials))})")
    table.add_column("Memorial ID", style="dim", max_width=26)
    table.add_column("Edict ID", style="dim", max_width=26)
    table.add_column("Audit")
    table.add_column("Instruction", max_width=40)

    for m in memorials:
        audit = m.get("audit", {})
        verdict = audit.get("verdict", "—") if audit else "—"
        verdict_color = {"pass": "green", "flag": "yellow", "block": "red"}.get(verdict, "white")
        table.add_row(
            m.get("id", ""),
            m.get("edict_id", ""),
            f"[{verdict_color}]{verdict}[/{verdict_color}]",
            (m.get("instruction") or "")[:40],
        )

    console.print(table)
    console.print(
        "\n[bold]Use[/bold] [cyan]tianshu decree submit --memorial-id <id> --action approve[/cyan] "
        "[bold]to approve[/bold]"
    )
